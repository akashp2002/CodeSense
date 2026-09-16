import sys
import argparse
from pathlib import Path
from codesense.ingestion.parser import CodeParser
from codesense.ingestion.chunker import SemanticChunker
from codesense.ingestion.symbol_table import SymbolExtractor
from codesense.vector_store import VectorStore
from codesense.graph_store import GraphStore
from codesense.agents.semantic_search import SemanticSearchAgent
from codesense.agents.dependency_graph import DependencyGraphAgent

SKIP_DIRS = {".venv", "__pycache__", "tests", ".git", ".qdrant_db"}

def _iter_py_files(path: Path):
    for file_path in path.rglob("*.py"):
        if not any(part in SKIP_DIRS for part in file_path.parts):
            yield file_path

def index_repo(repo_path: str):
    path = Path(repo_path)
    if not path.exists() or not path.is_dir():
        print(f"Error: {repo_path} is not a valid directory.")
        sys.exit(1)
        
    parser = CodeParser()
    chunker = SemanticChunker()
    vector_store = VectorStore()
    
    print(f"--- Indexing repository {repo_path} ---")
    all_chunks = []
    for file_path in _iter_py_files(path):
        try:
            tree, code = parser.parse_file(str(file_path))
            chunks = chunker.chunk_node(tree.root_node, code, str(file_path))
            all_chunks.extend(chunks)
            print(f"Parsed {file_path.name}: {len(chunks)} chunks")
        except Exception as e:
            print(f"Error parsing {file_path}: {e}")
            
    print(f"\nExtracted a total of {len(all_chunks)} chunks. Embedding and indexing...")
    vector_store.index_chunks(all_chunks)
    print("Vector indexing complete.")

def graph_index_repo(repo_path: str):
    path = Path(repo_path)
    if not path.exists() or not path.is_dir():
        print(f"Error: {repo_path} is not a valid directory.")
        sys.exit(1)
        
    parser = CodeParser()
    chunker = SemanticChunker()
    extractor = SymbolExtractor()
    graph_store = GraphStore()
    
    print(f"--- Graph-indexing repository {repo_path} ---")
    print("Clearing existing graph...")
    graph_store.clear_graph()
    
    all_chunks = []
    all_refs = []
    for file_path in _iter_py_files(path):
        try:
            tree, code = parser.parse_file(str(file_path))
            chunks = chunker.chunk_node(tree.root_node, code, str(file_path))
            refs = extractor.extract_references(tree.root_node, code, str(file_path))
            all_chunks.extend(chunks)
            all_refs.extend(refs)
            print(f"Parsed {file_path.name}: {len(chunks)} symbols, {len(refs)} references")
        except Exception as e:
            print(f"Error parsing {file_path}: {e}")
    
    print(f"\nInserting {len(all_chunks)} nodes into Neo4j...")
    graph_store.index_chunks(all_chunks)
    print(f"Inserting {len(all_refs)} edges into Neo4j...")
    graph_store.index_references(all_refs)
    graph_store.close()
    print("Graph indexing complete.")

def search_repo(query: str):
    agent = SemanticSearchAgent()
    print(f"--- Searching codebase for: '{query}' ---")
    
    results = agent.search_codebase(query)
    
    if not results:
        print("No results found.")
        return
        
    for res in results:
        print(f"\n[{res['rank']}] {res['chunk_type'].upper()} {res['symbol_name']} in {res['file_path']} (Lines {res['line_range']})")
        lines = res['snippet'].splitlines()
        preview = "\n    ".join(lines[:5])
        if len(lines) > 5:
            preview += "\n    ..."
        print(f"    {preview}")

def impact_analysis(symbol_name: str, max_hops: int):
    agent = DependencyGraphAgent()
    print(f"--- Impact analysis for symbol: '{symbol_name}' (max {max_hops} hops) ---")
    
    result = agent.get_impact(symbol_name, max_hops=max_hops)
    print(f"\n{result['summary']}")

def main():
    parser = argparse.ArgumentParser(description="CodeSense CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    index_parser = subparsers.add_parser("index", help="Vector-index a repository")
    index_parser.add_argument("repo_path", help="Path to the repository to index")
    
    graph_index_parser = subparsers.add_parser("graph-index", help="Build a call graph in Neo4j")
    graph_index_parser.add_argument("repo_path", help="Path to the repository to graph-index")
    
    search_parser = subparsers.add_parser("search", help="Semantic search")
    search_parser.add_argument("query", help="Query string")
    
    impact_parser = subparsers.add_parser("impact", help="Dependency impact analysis")
    impact_parser.add_argument("symbol", help="Symbol name to analyze")
    impact_parser.add_argument("--hops", type=int, default=3, help="Max traversal hops (default: 3)")
    
    args = parser.parse_args()
    
    if args.command == "index":
        index_repo(args.repo_path)
    elif args.command == "graph-index":
        graph_index_repo(args.repo_path)
    elif args.command == "search":
        search_repo(args.query)
    elif args.command == "impact":
        impact_analysis(args.symbol, args.hops)

if __name__ == "__main__":
    main()
