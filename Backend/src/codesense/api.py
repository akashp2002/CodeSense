import os
import uvicorn
from pathlib import Path
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from codesense.agents.supervisor import SupervisorAgent
from codesense.ingestion.github_loader import (
    clone_repository,
    create_pull_request,
    delete_repository,
)
from codesense.cli import index_repo, graph_index_repo
from codesense.graph_store import GraphStore, NEO4J_URI
from codesense.vector_store import DEFAULT_QDRANT_PATH
from qdrant_client import QdrantClient

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

class DeleteRepositoryRequest(BaseModel):
    repo_path: str

class QueryRequest(BaseModel):
    question: str

class QueryResponse(BaseModel):
    answer: str
    requires_approval: bool = False
    diff_data: Optional[str] = None

class CreatePRRequest(BaseModel):
    prompt: str
    repo_name: str

# ── Endpoints ──

@app.get("/health")
def health_check():
    """Report readiness of the API's external dependencies without loading the LLM."""
    checks = {}

    groq_key = os.getenv("GROQ_API_KEY", "")
    checks["llm"] = {
        "status": "ok" if groq_key and not groq_key.startswith("your_") else "error",
        "configured": bool(groq_key and not groq_key.startswith("your_")),
    }

    qdrant_client = None
    try:
        qdrant_client = QdrantClient(path=str(DEFAULT_QDRANT_PATH))
        qdrant_client.get_collections()
        checks["qdrant"] = {"status": "ok", "path": str(DEFAULT_QDRANT_PATH)}
    except Exception as error:
        checks["qdrant"] = {"status": "error", "detail": str(error)}
    finally:
        if qdrant_client is not None:
            qdrant_client.close()

    graph_store = None
    try:
        graph_store = GraphStore()
        graph_store.driver.verify_connectivity()
        checks["neo4j"] = {"status": "ok", "uri": NEO4J_URI}
    except Exception as error:
        checks["neo4j"] = {"status": "error", "detail": str(error)}
    finally:
        if graph_store is not None:
            graph_store.close()

    failed = [name for name, check in checks.items() if check["status"] != "ok"]
    response = {
        "status": "ok" if not failed else "degraded",
        "checks": checks,
        "failed_checks": failed,
    }
    if failed:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=response)
    return response

@app.post("/clone")
def api_clone(request: CloneRequest):
    """Clone a GitHub repository to the local repos/ directory."""
    try:
        result = clone_repository(request.github_url)
        os.environ["CODESENSE_REPO_PATH"] = result["local_path"]
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/delete-repository")
def api_delete_repository(request: DeleteRepositoryRequest):
    """Delete the currently selected cloned repository."""
    reset_supervisor()
    try:
        delete_repository(request.repo_path)
        if os.environ.get("CODESENSE_REPO_PATH") == request.repo_path:
            os.environ.pop("CODESENSE_REPO_PATH", None)
        return {"status": "deleted"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not delete repository: {e}") from e

@app.post("/index-vectors")
def api_index_vectors(request: IndexRequest):
    """Build the semantic vector index for a local repository path."""
    if not os.path.exists(request.repo_path):
        raise HTTPException(status_code=400, detail="Repository path does not exist.")
    
    # Reset supervisor so it picks up the new index
    reset_supervisor()
    
    try:
        from pathlib import Path
        from codesense.ingestion.parser import CodeParser
        from codesense.ingestion.chunker import SemanticChunker
        from codesense.vector_store import VectorStore
        
        SKIP_DIRS = {".venv", "__pycache__", "tests", ".git", ".qdrant_db", "node_modules", ".tox"}
        
        path = Path(request.repo_path)
        
        parser = CodeParser()
        chunker = SemanticChunker()
        vector_store = VectorStore()
        vector_store.clear_collection()
        
        all_chunks = []
        parse_errors = []
        for file_path in path.rglob("*.py"):
            # Use relative path so we only skip dirs INSIDE the repo, not parent dirs
            rel_parts = file_path.relative_to(path).parts
            if any(part in SKIP_DIRS for part in rel_parts):
                continue
            try:
                tree, code = parser.parse_file(str(file_path))
                chunks = chunker.chunk_node(tree.root_node, code, str(file_path))
                all_chunks.extend(chunks)
            except Exception as exc:
                parse_errors.append(f"{file_path}: {exc}")

        try:
            vector_store.index_chunks(all_chunks)
            return {
                "chunk_count": len(all_chunks),
                "parse_error_count": len(parse_errors),
                "parse_errors": parse_errors[:10],
            }
        finally:
            vector_store.client.close()
    except RuntimeError as e:
        if "already accessed by another instance" in str(e):
            raise HTTPException(
                status_code=409,
                detail=(
                    "The semantic index is in use by another CodeSense process. "
                    "Stop other Uvicorn/CodeSense processes and retry indexing."
                ),
            ) from e
        raise HTTPException(status_code=500, detail=str(e)) from e
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
        graph_store.index_references(all_refs)
        graph_store.close()
        
        return {"symbol_count": len(all_chunks), "relationship_count": len(all_refs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from fastapi import WebSocket, WebSocketDisconnect
import asyncio
import traceback

@app.websocket("/ws/query")
async def websocket_query(websocket: WebSocket):
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        question = data.get("question")
        if not question:
            await websocket.send_json({"type": "error", "message": "No question provided"})
            return
            
        agent = get_supervisor()
        
        await websocket.send_json({"type": "status", "message": "Starting agent workflow..."})
        
        from codesense.models.state import CodeSenseState
        initial_state = CodeSenseState(
            question=question,
            intent=None,
            search_results=None,
            impact_results=None,
            final_answer=None,
            error=None,
            requires_approval=False,
            diff_data=None
        )
        
        # We run the graph stream in a thread so it doesn't block asyncio
        def run_stream():
            final_state = initial_state
            for chunk in agent.graph.stream(initial_state):
                for node_name, node_state in chunk.items():
                    final_state = node_state
                    # Use a threadsafe queue or asyncio.run_coroutine_threadsafe in real prod
                    # But since this is just yield, we can't await easily inside thread.
                    pass
            return final_state

        loop = asyncio.get_running_loop()
        
        async def async_stream():
            # LangGraph actually has astream!
            final_state = initial_state
            try:
                async for chunk in agent.graph.astream(initial_state):
                    for node_name, node_state in chunk.items():
                        final_state = node_state
                        
                        # Map node names to friendly messages
                        msg_map = {
                            "supervisor": "Classifying your intent...",
                            "semantic_search": "Searching codebase for relevant context...",
                            "dependency_graph": "Traversing dependency graph to find impact...",
                            "explainer": "Drafting final response...",
                            "refactor_node": "Refactoring code using AST resolution..."
                        }
                        friendly_msg = msg_map.get(node_name, f"Running {node_name}...")
                        
                        await websocket.send_json({"type": "status", "message": friendly_msg})
            except Exception as inner_e:
                final_state["error"] = str(inner_e)
            return final_state
            
        result_state = await async_stream()
        
        if result_state.get("error"):
            await websocket.send_json({"type": "error", "message": result_state["error"]})
        else:
            await websocket.send_json({
                "type": "result",
                "answer": result_state.get("final_answer", "No answer generated."),
                "requires_approval": result_state.get("requires_approval", False),
                "diff_data": result_state.get("diff_data")
            })
            
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WS Error: {e}")
        traceback.print_exc()
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass

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
        result_state = agent.run(request.question)
        if "error" in result_state and result_state["error"]:
            raise HTTPException(status_code=500, detail=result_state["error"])
            
        answer = result_state.get("final_answer", "No answer generated.")
        
        return {
            "answer": answer,
            "requires_approval": result_state.get("requires_approval", False),
            "diff_data": result_state.get("diff_data")
        }
    except Exception as e:
        import traceback
        print(f"\n{'='*60}")
        print(f"ERROR in /query endpoint:")
        print(f"{'='*60}")
        traceback.print_exc()
        print(f"{'='*60}\n")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/create-pr")
def api_create_pr(request: CreatePRRequest):
    """Resume the refactor flow to push the PR after human approval.
    
    This is where incremental indexing happens — only after the user
    explicitly approves the changes, and only for the files that changed.
    """
    try:
        repo_path = os.getenv("CODESENSE_REPO_PATH")
        if not repo_path:
            repo_path = str(Path("repos") / request.repo_name)

        agent = get_supervisor()
        
        # Step 1: Incrementally refresh indexes using the existing connections!
        from codesense.cli import incremental_refresh_indexes
        index_status = incremental_refresh_indexes(
            repo_path,
            vector_store=agent.search_agent.vector_store,
            graph_store=agent.graph_agent.graph_store
        )
        print(f"Index refresh on approval: {index_status}")

        # Step 2: Create the PR
        pull_request_url = create_pull_request(repo_path, request.prompt)
        return {
            "status": "success",
            "message": f"Pull Request created: {pull_request_url}",
            "index_status": index_status,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not create Pull Request: {e}") from e

def start():
    """Entry point for the CLI to start the server."""
    uvicorn.run("codesense.api:app", host="0.0.0.0", port=8000, reload=True, reload_dirs=["src"])

if __name__ == "__main__":
    start()
