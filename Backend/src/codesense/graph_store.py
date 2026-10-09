import os

from neo4j import GraphDatabase
from typing import List
from codesense.models.core import CodeChunk, SymbolReference

# Connection constants - can be overridden via env vars
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "codesense_password")

class GraphStore:
    def __init__(self, user_id: str = "default", uri: str = NEO4J_URI, user: str = NEO4J_USER, password: str = NEO4J_PASSWORD):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.user_id = user_id

    def close(self):
        self.driver.close()

    def clear_graph(self):
        """Clear all nodes and edges (useful for re-indexing)."""
        with self.driver.session() as session:
            session.run("MATCH (n) WHERE n.tenant_id = $tenant_id DETACH DELETE n", tenant_id=self.user_id)

    def delete_file_nodes(self, file_path: str):
        """Delete all nodes associated with a specific file to support incremental updates.
        Also deletes File nodes that match the path, and any edges connected to them.
        """
        with self.driver.session() as session:
            session.run(
                """
                MATCH (n)
                WHERE ((n:Symbol AND n.file_path = $file_path) OR (n:File AND n.path = $file_path)) AND n.tenant_id = $tenant_id
                DETACH DELETE n
                """,
                file_path=file_path,
                tenant_id=self.user_id
            )

    def index_chunks(self, chunks: List[CodeChunk]):
        """Create nodes for each code symbol."""
        with self.driver.session() as session:
            for chunk in chunks:
                session.run(
                    """
                    MERGE (s:Symbol {
                        tenant_id: $tenant_id,
                        file_path: $file_path,
                        symbol_name: $symbol_name,
                        start_line: $start_line
                    })
                    SET s.chunk_type = $chunk_type,
                        s.end_line = $end_line,
                        s.signature = $signature,
                        s.docstring = $docstring
                    """,
                    tenant_id=self.user_id,
                    file_path=chunk.file_path,
                    symbol_name=chunk.symbol_name,
                    start_line=chunk.line_range.start_line,
                    end_line=chunk.line_range.end_line,
                    chunk_type=chunk.chunk_type,
                    signature=chunk.signature,
                    docstring=chunk.docstring
                )

    def index_references(self, references: List[SymbolReference]):
        """Create edges between symbols based on references.
        
        When caller_symbol is known (e.g. function calling another function),
        we create a Symbol→Symbol edge: A -[:CALLS]-> B.
        
        For module-level imports with no caller, we create a File→Symbol edge.
        """
        with self.driver.session() as session:
            for ref in references:
                rel_type = {
                    "call": "CALLS",
                    "import": "IMPORTS",
                    "inheritance": "INHERITS_FROM"
                }.get(ref.reference_type, "REFERENCES")

                if ref.caller_symbol:
                    # Precise edge: Symbol → [REL] → Symbol
                    session.run(
                        f"""
                        MERGE (source:Symbol {{tenant_id: $tenant_id, symbol_name: $caller_symbol}})
                        MERGE (target:Symbol {{tenant_id: $tenant_id, symbol_name: $callee_symbol}})
                        MERGE (source)-[:{rel_type} {{line: $line_number, file: $file_path}}]->(target)
                        """,
                        tenant_id=self.user_id,
                        caller_symbol=ref.caller_symbol,
                        callee_symbol=ref.symbol_name,
                        line_number=ref.line_number,
                        file_path=ref.file_path
                    )
                else:
                    # Module-level imports should point at indexed definitions when available.
                    session.run(
                        f"""
                        MERGE (source:File {{tenant_id: $tenant_id, path: $file_path}})
                        WITH source
                        OPTIONAL MATCH (target:Symbol {{tenant_id: $tenant_id, symbol_name: $symbol_name}})
                        FOREACH (resolved_target IN CASE WHEN target IS NULL THEN [] ELSE [target] END |
                            MERGE (source)-[:{rel_type} {{line: $line_number}}]->(resolved_target))
                        """,
                        tenant_id=self.user_id,
                        file_path=ref.file_path,
                        symbol_name=ref.symbol_name,
                        line_number=ref.line_number
                    )


    def get_dependents(self, symbol_name: str, max_hops: int = 3) -> List[dict]:
        """
        Find all files/symbols that transitively depend on a given symbol.
        i.e., the blast radius if this symbol changes.
        """
        with self.driver.session() as session:
            result = session.run(
                f"""
                     MATCH (target:Symbol)
                     WHERE target.tenant_id = $tenant_id AND (target.symbol_name = $symbol_name
                         OR toLower(target.file_path) CONTAINS toLower($symbol_name))
                MATCH (source)-[:CALLS|IMPORTS|INHERITS_FROM*1..{max_hops}]->(target)
                  RETURN DISTINCT coalesce(source.file_path, source.path) AS file_path,
                      source.symbol_name AS symbol_name,
                       labels(source) AS node_type
                """,
                tenant_id=self.user_id,
                symbol_name=symbol_name
            )
            return [dict(record) for record in result]

    def get_dependencies(self, symbol_name: str, max_hops: int = 3) -> List[dict]:
        """Find symbols that the requested symbol transitively depends on."""
        with self.driver.session() as session:
            result = session.run(
                f"""
                MATCH (source:Symbol)
                WHERE source.tenant_id = $tenant_id AND source.symbol_name = $symbol_name
                OPTIONAL MATCH (source)-[:CALLS|IMPORTS|INHERITS_FROM*1..{max_hops}]->(called:Symbol)
                OPTIONAL MATCH (file:File {{tenant_id: $tenant_id, path: source.file_path}})-[:IMPORTS]->(imported:Symbol)
                WITH collect(DISTINCT called) + collect(DISTINCT imported) AS targets
                UNWIND targets AS target
                RETURN DISTINCT target.symbol_name AS symbol_name,
                    target.file_path AS file_path,
                    labels(target) AS node_type
                """,
                tenant_id=self.user_id,
                symbol_name=symbol_name
            )
            return [dict(record) for record in result]

    def get_callees(self, symbol_name: str) -> List[dict]:
        """
        Find all symbols that a given symbol depends on (outward edges).
        """
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (source:Symbol {tenant_id: $tenant_id, symbol_name: $symbol_name})-[:CALLS|IMPORTS]->(target)
                RETURN DISTINCT target.symbol_name AS symbol_name, target.file_path AS file_path
                """,
                tenant_id=self.user_id,
                symbol_name=symbol_name
            )
            return [dict(record) for record in result]

    def get_neighborhood(self, symbol_name: str, max_hops: int = 2) -> dict:
        """
        Get the local graph neighborhood around a symbol for visualization.
        Returns a dict with 'nodes' and 'edges'.
        """
        with self.driver.session() as session:
            # Query for paths up to max_hops away (both incoming and outgoing)
            result = session.run(
                f"""
                MATCH path = (start:Symbol {{tenant_id: $tenant_id, symbol_name: $symbol_name}})-[*1..{max_hops}]-(other)
                RETURN path
                """,
                tenant_id=self.user_id,
                symbol_name=symbol_name
            )
            
            nodes = {}
            edges = set()
            
            # Also add the starting node itself in case it has no edges
            start_result = session.run(
                "MATCH (n:Symbol {tenant_id: $tenant_id, symbol_name: $symbol_name}) RETURN n", 
                tenant_id=self.user_id,
                symbol_name=symbol_name
            )
            for record in start_result:
                node = record["n"]
                nodes[node["symbol_name"]] = dict(node)
                
            for record in result:
                path = record["path"]
                for node in path.nodes:
                    if "symbol_name" in node:
                        nodes[node["symbol_name"]] = dict(node)
                    elif "path" in node: # File node
                        nodes[node["path"]] = dict(node)
                        
                for rel in path.relationships:
                    start_node = rel.start_node
                    end_node = rel.end_node
                    start_id = start_node.get("symbol_name") or start_node.get("path")
                    end_id = end_node.get("symbol_name") or end_node.get("path")
                    if start_id and end_id:
                        edges.add((start_id, end_id, type(rel).__name__))
                        
            return {
                "nodes": [{"id": k, **v} for k, v in nodes.items()],
                "edges": [{"source": s, "target": t, "label": l} for s, t, l in edges]
            }

