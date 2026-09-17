import os
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from codesense.agents.supervisor import SupervisorAgent
from codesense.ingestion.github_loader import clone_repository
from codesense.cli import index_repo, graph_index_repo

app = FastAPI(title="CodeSense API", description="AI Agent for Codebase QA & Impact Analysis")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Lazy-loaded supervisor (shared singleton to avoid Qdrant lock issues)
_supervisor: Optional[SupervisorAgent] = None

def get_supervisor():
    global _supervisor
    if _supervisor is None:
        _supervisor = SupervisorAgent()
    return _supervisor

def reset_supervisor():
    """Close the old supervisor's Qdrant client and force re-initialization."""
    global _supervisor
    if _supervisor is not None:
        try:
            # Explicitly close the Qdrant client to release the file lock
            _supervisor.search_agent.vector_store.client.close()
        except Exception:
            pass
        try:
            _supervisor.graph_agent.graph_store.close()
        except Exception:
            pass
    _supervisor = None

# ── Request / Response Models ──

class CloneRequest(BaseModel):
    github_url: str

class IndexRequest(BaseModel):
    repo_path: str

class QueryRequest(BaseModel):
    question: str

class QueryResponse(BaseModel):
    answer: str

# ── Endpoints ──

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/clone")
def api_clone(request: CloneRequest):
    """Clone a GitHub repository to the local repos/ directory."""
    try:
        result = clone_repository(request.github_url)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/index-vectors")
def api_index_vectors(request: IndexRequest):
    """Build the semantic vector index for a local repository path."""
    if not os.path.exists(request.repo_path):
        raise HTTPException(status_code=400, detail="Repository path does not exist.")
    
    # Reset supervisor so it picks up the new index
    reset_supervisor()
    
    try:
        import shutil
        from pathlib import Path
        from codesense.ingestion.parser import CodeParser
        from codesense.ingestion.chunker import SemanticChunker
        from codesense.vector_store import VectorStore
        
        SKIP_DIRS = {".venv", "__pycache__", "tests", ".git", ".qdrant_db", "node_modules", ".tox"}
        
        path = Path(request.repo_path)
        
        # ── NUCLEAR FIX: Physically delete the database folder to guarantee wipe ──
        db_path = Path(".qdrant_db")
        if db_path.exists():
            # Try to release locks by garbage collecting first
            import gc
            gc.collect()
            try:
                shutil.rmtree(db_path, ignore_errors=True)
            except Exception:
                pass
        
        parser = CodeParser()
        chunker = SemanticChunker()
        vector_store = VectorStore()
        
        all_chunks = []
        for file_path in path.rglob("*.py"):
            # Use relative path so we only skip dirs INSIDE the repo, not parent dirs
            rel_parts = file_path.relative_to(path).parts
            if any(part in SKIP_DIRS for part in rel_parts):
                continue
            try:
                tree, code = parser.parse_file(str(file_path))
                chunks = chunker.chunk_node(tree.root_node, code, str(file_path))
                all_chunks.extend(chunks)
            except Exception:
                pass
        
        vector_store.index_chunks(all_chunks)
        # Close the Qdrant client to release the file lock
        vector_store.client.close()
        
        return {"chunk_count": len(all_chunks)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/index-graph")
def api_index_graph(request: IndexRequest):
    """Build the Neo4j dependency graph for a local repository path."""
    if not os.path.exists(request.repo_path):
        raise HTTPException(status_code=400, detail="Repository path does not exist.")
    
    try:
        from pathlib import Path
        from codesense.ingestion.parser import CodeParser
        from codesense.ingestion.chunker import SemanticChunker
        from codesense.ingestion.symbol_table import SymbolExtractor
        from codesense.graph_store import GraphStore
        
        SKIP_DIRS = {".venv", "__pycache__", "tests", ".git", ".qdrant_db", "node_modules", ".tox"}
        
        path = Path(request.repo_path)
        parser = CodeParser()
        chunker = SemanticChunker()
        extractor = SymbolExtractor()
        graph_store = GraphStore()
        
        graph_store.clear_graph()
        
        all_chunks = []
        all_refs = []
        for file_path in path.rglob("*.py"):
            rel_parts = file_path.relative_to(path).parts
            if any(part in SKIP_DIRS for part in rel_parts):
                continue
            try:
                tree, code = parser.parse_file(str(file_path))
                chunks = chunker.chunk_node(tree.root_node, code, str(file_path))
                refs = extractor.extract_references(tree.root_node, code, str(file_path))
                all_chunks.extend(chunks)
                all_refs.extend(refs)
            except Exception:
                pass
        
        graph_store.index_chunks(all_chunks)
        graph_store.index_references(all_refs)
        graph_store.close()
        
        return {"symbol_count": len(all_chunks), "relationship_count": len(all_refs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/search")
def api_search(q: str, limit: int = 5):
    """Semantic search across codebase."""
    agent = get_supervisor().search_agent
    results = agent.search_codebase(q, limit=limit)
    return {"results": results}

@app.get("/impact/{symbol}")
def api_impact(symbol: str, hops: int = 3):
    """Dependency impact analysis for a symbol."""
    agent = get_supervisor().graph_agent
    results = agent.get_impact(symbol, max_hops=hops)
    return results

@app.get("/graph/{symbol}")
def api_graph_neighborhood(symbol: str, hops: int = 2):
    """Get raw graph nodes and edges for visualization."""
    store = get_supervisor().graph_agent.graph_store
    return store.get_neighborhood(symbol, max_hops=hops)

@app.post("/query", response_model=QueryResponse)
def api_query(request: QueryRequest):
    """Natural language Q&A using the LangGraph Supervisor."""
    agent = get_supervisor()
    try:
        answer = agent.run(request.question)
        return {"answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def start():
    """Entry point for the CLI to start the server."""
    uvicorn.run("codesense.api:app", host="0.0.0.0", port=8000, reload=True)

if __name__ == "__main__":
    start()
