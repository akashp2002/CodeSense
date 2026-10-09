import os
import re
import subprocess
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
from codesense.llm_manager import get_llm
from langgraph.graph import StateGraph, START, END
from codesense.models.state import CodeSenseState
from codesense.agents.semantic_search import SemanticSearchAgent
from codesense.agents.dependency_graph import DependencyGraphAgent
from codesense.agents.explainer import ExplainerAgent
from codesense.vector_store import VectorStore
from codesense.graph_store import GraphStore

class IntentClassification(BaseModel):
    intent: Literal["search", "impact", "explain", "refactor"] = Field(
        description="The classified intent of the user's question. "
                    "'search' for finding where something is or how it's implemented. "
                    "'impact' for questions about blast radius, dependencies, or what breaks if something changes. "
                    "'explain' for general architectural or 'how does it work' questions."
                    "'refactor' for requests to change, rename, rewrite, or refactor code."
    )
    extracted_symbol: str | None = Field(
        description="If the intent is 'impact', the specific symbol (function/class name) the user is asking about. Otherwise null.",
        default=None
    )

class SupervisorAgent:
    def __init__(self, user_id: str = "default", model_name: str = "qwen/qwen3.8-27b"):
        self.user_id = user_id
        self.llm = get_llm(
            purpose="fast",
            temperature=0,
            max_tokens=128,
        ).with_structured_output(IntentClassification)
        
        # Initialize specialized isolated stores
        self.vector_store = VectorStore(user_id=self.user_id)
        self.graph_store = GraphStore(user_id=self.user_id)

        # Initialize specialist tools with isolated stores
        self.search_agent = SemanticSearchAgent(vector_store=self.vector_store)
        self.graph_agent = DependencyGraphAgent(graph_store=self.graph_store)
        self.explainer_agent = ExplainerAgent(model_name=model_name)
        
        from codesense.agents.refactor import RefactorAgent
        self.refactor_agent = RefactorAgent(vector_store=self.vector_store)  # Uses its own tool-calling model
        
        # Build the graph
        self.graph = self._build_graph()

    def _classify_intent(self, state: CodeSenseState) -> CodeSenseState:
        """Node 1: Classify the user's intent."""
        print(f"Supervisor: Analyzing intent for '{state['question']}'")
        try:
            result = self.llm.invoke(f"Classify the following codebase question: {state['question']}")
            direction = self._dependency_direction(state["question"])
            state["intent"] = "impact" if direction else result.intent
            state["_dependency_direction"] = direction
            
            if result.intent == "impact" and result.extracted_symbol:
                state["_target_symbol"] = result.extracted_symbol
            
            print(f"Supervisor: Classified intent as '{state['intent']}'")
        except Exception as e:
            # Always try keyword fallback first — handles rate limits AND tool_use_failed
            intent, symbol = self._fallback_intent(state["question"])
            direction = self._dependency_direction(state["question"])
            state["intent"] = "impact" if direction else intent
            state["_dependency_direction"] = direction
            if symbol:
                state["_target_symbol"] = symbol
            print(
                f"Supervisor: Structured classification failed ({type(e).__name__}); "
                f"using keyword fallback intent '{intent}'"
            )
        return state

    @staticmethod
    def _fallback_intent(question: str) -> tuple[str, str | None]:
        """Rule-based intent classifier used when the LLM structured output fails for any reason."""
        q = question.lower()
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question)
        
        # Symbols: snake_case or CamelCase words that aren't common words
        stopwords = {"what", "where", "how", "does", "is", "it", "the", "a", "an",
                     "to", "if", "i", "do", "will", "be", "in", "of", "and", "or"}
        symbol_candidates = [w for w in words if ("_" in w or w[:1].isupper()) and w.lower() not in stopwords]
        top_symbol = symbol_candidates[0] if symbol_candidates else None
        
        refactor_terms = {"rename", "refactor", "replace", "rewrite", "move"}
        impact_terms  = {"dependent", "dependant", "depend", "dependencies", "impact", "affect",
                         "effect", "break", "uses", "callers", "calls", "blast"}
        search_terms  = {"where", "find", "locate", "which", "file", "defined", "implemented", "show"}
        
        if any(t in q for t in refactor_terms):
            return "refactor", top_symbol
        if any(t in q for t in impact_terms):
            return "impact", top_symbol
        if any(t in q for t in search_terms):
            return "search", top_symbol
        return "explain", top_symbol

    @staticmethod
    def _dependency_direction(question: str) -> str | None:
        """Return the requested edge direction for a dependency question."""
        q = question.lower()
        if not re.search(r"\bdepend(?:s|ed|ent|ents|ant|ants|enc(?:y|ies))?\b", q):
            return None

        if (
            re.search(r"\b(?:what|which|who)\s+depend(?:s|ed)?\s+on\b", q)
        ):
            return "dependents"

        if (
            re.search(r"\bdepend(?:s|ed|ent)?\s+on\b", q)
            or re.search(r"\bdependenc(?:y|ies)\s+(?:of|for)\b", q)
            or re.search(r"\b[A-Za-z_][A-Za-z0-9_]*['’]s\s+dependenc(?:y|ies)\b", question, re.IGNORECASE)
        ):
            return "dependencies"
        return "dependents"

    def _route(self, state: CodeSenseState) -> str:
        """Conditional router based on intent."""
        if state.get("error"):
            return END
            
        intent = state.get("intent")
        if intent == "impact":
            return "dependency_graph"
        elif intent == "search" or intent == "refactor":
            return "semantic_search"
        else:
            # Explain usually requires searching first to get context, so we route to search then explain
            return "semantic_search"

    def _run_semantic_search(self, state: CodeSenseState) -> CodeSenseState:
        """Node: Semantic Search Specialist"""
        print("Semantic Search: Retrieving code chunks...")
        results = self.search_agent.search_codebase(state["question"], limit=5)
        state["search_results"] = results
        return state

    def _run_dependency_graph(self, state: CodeSenseState) -> CodeSenseState:
        """Node: Dependency Graph Specialist"""
        symbol = self._normalize_impact_target(
            state.get("_target_symbol"), state["question"]
        )
        
        if not symbol:
            # Fallback heuristic: find CamelCase or snake_case words if LLM failed to extract
            import re
            words = re.findall(r'[A-Za-z_][A-Za-z0-9_]*', state["question"])
            # Filter out common capitalized sentence starters
            ignore = {"What", "If", "How", "Do", "I", "Need", "The"}
            potential = [w for w in words if w not in ignore and (w[0].isupper() or '_' in w)]
            symbol = potential[0] if potential else state["question"].split()[0]
            
        print(f"Dependency Graph: Traversing graph for '{symbol}'...")
        
        results = self.graph_agent.get_impact(
            symbol, direction=state.get("_dependency_direction") or "dependents"
        )
        state["impact_results"] = results
        return state

    @staticmethod
    def _normalize_impact_target(extracted_symbol: str | None, question: str) -> str:
        """Avoid generic classifier words becoming the dependency target."""
        generic = {
            "a", "affect", "change", "changing", "effect", "effected", "files",
            "depend", "dependent", "dependents", "dependant", "dependants",
            "dependencies", "dependency", "impact", "need", "other", "replace",
            "replacing", "what", "will",
        }
        if extracted_symbol and extracted_symbol.lower() not in generic:
            return extracted_symbol

        words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question)
        ignored = generic | {"the", "to", "does", "is", "it", "i"}
        candidates = [word for word in words if word.lower() not in ignored]
        identifier_candidates = [
            word for word in candidates if "_" in word or word[:1].isupper()
        ]
        if identifier_candidates:
            return identifier_candidates[0]
        if candidates:
            return candidates[0]
        return extracted_symbol or question.split()[0]

    def _refactor_node(self, state: CodeSenseState) -> CodeSenseState:
        """Node: Run the refactor agent in a sandbox.
        
        Creates a lightweight staging area (skipping .git to avoid slow
        Windows permission copies) so the agent cannot corrupt the live
        working tree.  The diff is generated from the sandbox.
        """
        import shutil
        import stat
        print(f"Refactor Agent: Processing request '{state['question']}'")
        
        repo_path = os.getenv("CODESENSE_REPO_PATH") or str(Path.cwd() / "repos" / "demo")
        staging_path = repo_path + "_staging"
        
        def remove_readonly(func, path, excinfo):
            os.chmod(path, stat.S_IWRITE)
            try:
                func(path)
            except Exception:
                pass

        # 1. Create a lightweight staging area (skip .git for speed)
        if os.path.exists(staging_path):
            shutil.rmtree(staging_path, onerror=remove_readonly)
        shutil.copytree(repo_path, staging_path, ignore=shutil.ignore_patterns('.git'))
        
        # Init a fresh git repo so get_git_diff still works
        subprocess.run(["git", "init"], cwd=staging_path, capture_output=True, check=False)
        subprocess.run(["git", "add", "."], cwd=staging_path, capture_output=True, check=False)
        subprocess.run(["git", "commit", "-m", "baseline", "--allow-empty"], cwd=staging_path,
                        capture_output=True, check=False, env={**os.environ, "GIT_AUTHOR_NAME": "CodeSense",
                        "GIT_AUTHOR_EMAIL": "bot@codesense", "GIT_COMMITTER_NAME": "CodeSense",
                        "GIT_COMMITTER_EMAIL": "bot@codesense"})
        
        original_env_path = os.getenv("CODESENSE_REPO_PATH")
        os.environ["CODESENSE_REPO_PATH"] = staging_path
        
        # Build a context-enriched prompt with RELATIVE paths so the agent edits the sandbox, not live repo.
        # res['file_path'] from the vector store is an absolute path to the original repo.
        # We convert it to be relative to repo_path. The MCP server resolves it against
        # CODESENSE_REPO_PATH (staging_path), so edits go into the sandbox — not the live working tree.
        context = ""
        if state.get("search_results"):
            context = "Relevant code from the repository:\n"
            for res in state["search_results"][:5]:
                abs_path = res['file_path']
                try:
                    rel_path = os.path.relpath(abs_path, repo_path)
                except ValueError:
                    # Different drive on Windows — use basename as best effort
                    rel_path = os.path.basename(abs_path)
                context += f"- File: `{rel_path}` (Lines {res['line_range']})\n"
                context += f"```\n{res['snippet']}\n```\n\n"

        prompt = f"{context}\nUser Request: {state['question']}"
        
        try:
            agent_reply = self.refactor_agent.run_sync(prompt, phase="refactor")
            
            # Run git diff ourselves — the agent's text reply is NOT the diff
            diff_result = subprocess.run(
                ["git", "--no-pager", "diff"],
                cwd=staging_path, capture_output=True, text=True, check=False,
            ).stdout.strip()
            
            if diff_result:
                state["diff_data"] = diff_result
                state["requires_approval"] = True
                state["final_answer"] = (
                    "I have drafted the refactor in a secure sandbox. "
                    "Please review the diff below and approve to create a PR."
                )
            else:
                # Agent ran but made no file changes
                state["final_answer"] = agent_reply or "The agent completed but no file changes were detected."
        except TimeoutError as error:
            # Fallback if the agent timed out but made edits
            diff_result = subprocess.run(
                ["git", "--no-pager", "diff"],
                cwd=staging_path,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()

            if diff_result:
                state["diff_data"] = diff_result
                state["requires_approval"] = True
                state["final_answer"] = (
                    "The refactor was applied to the sandbox, but the agent timed out while preparing its response. "
                    "Please review the recovered diff below."
                )
            else:
                state["error"] = str(error)
        except Exception as e:
            state["error"] = f"Refactor failed: {e}"
        finally:
            if original_env_path is not None:
                os.environ["CODESENSE_REPO_PATH"] = original_env_path
            else:
                os.environ.pop("CODESENSE_REPO_PATH", None)
                
        return state

    def _refresh_indexes(self) -> str:
        """Rebuild search and dependency indexes after a working-tree refactor."""
        repo_path = os.getenv("CODESENSE_REPO_PATH") or str(Path.cwd() / "repos" / "demo")
        try:
            # DO NOT close vector_store.client
            self.graph_agent.graph_store.close()

            from codesense.cli import refresh_indexes
            from codesense.agents.semantic_search import SemanticSearchAgent
            from codesense.agents.dependency_graph import DependencyGraphAgent

            refresh_indexes(repo_path)
            self.search_agent = SemanticSearchAgent()
            self.graph_agent = DependencyGraphAgent()
            return "Semantic and dependency indexes refreshed from the edited working tree."
        except Exception as error:
            return f"Index refresh failed; search results may be stale: {error}"

    def _route_after_search(self, state: CodeSenseState) -> str:
        if state.get("intent") == "refactor":
            return "refactor_node"
        return "explainer"

    def _build_graph(self):
        workflow = StateGraph(CodeSenseState)

        # Add nodes
        workflow.add_node("supervisor", self._classify_intent)
        workflow.add_node("semantic_search", self._run_semantic_search)
        workflow.add_node("dependency_graph", self._run_dependency_graph)
        workflow.add_node("explainer", self.explainer_agent.generate_explanation)
        workflow.add_node("refactor_node", self._refactor_node)

        # Edges
        workflow.add_edge(START, "supervisor")
        
        # Router from supervisor
        workflow.add_conditional_edges(
            "supervisor",
            self._route,
            {
                "semantic_search": "semantic_search",
                "dependency_graph": "dependency_graph",
                END: END
            }
        )
        
        # After search, route to explainer or refactor based on intent
        workflow.add_conditional_edges(
            "semantic_search",
            self._route_after_search,
            {
                "explainer": "explainer",
                "refactor_node": "refactor_node",
                END: END
            }
        )
        
        # After dependency graph, always go to explainer to synthesize
        workflow.add_edge("dependency_graph", "explainer")
        
        # After refactor, we're done (UI shows approval)
        workflow.add_edge("refactor_node", END)
        
        # After explainer, we're done
        workflow.add_edge("explainer", END)

        return workflow.compile()

    def run(self, question: str) -> dict:
        """Execute the LangGraph workflow for a given question and return the state dict."""
        if not os.getenv("GROQ_API_KEY"):
            return {"error": "Error: GROQ_API_KEY environment variable is missing. Please set it in your .env file."}
            
        initial_state = CodeSenseState(
            question=question,
            intent=None,
            _target_symbol=None,
            _dependency_direction=None,
            search_results=None,
            impact_results=None,
            final_answer=None,
            plan=None,
            error=None,
            requires_approval=False,
            diff_data=None
        )
        
        result_state = self.graph.invoke(initial_state)
        
        return result_state
