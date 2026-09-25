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
    def __init__(self, model_name: str = "qwen/qwen3.8-27b"):
        self.llm = get_llm(
            purpose="fast",
            temperature=0,
            max_tokens=128,
        ).with_structured_output(IntentClassification)
        
        # Initialize specialist tools
        self.search_agent = SemanticSearchAgent()
        self.graph_agent = DependencyGraphAgent()
        self.explainer_agent = ExplainerAgent(model_name=model_name)
        
        from codesense.agents.refactor import RefactorAgent
        self.refactor_agent = RefactorAgent()  # Uses its own tool-calling model
        
        # Build the graph
        self.graph = self._build_graph()

    def _classify_intent(self, state: CodeSenseState) -> CodeSenseState:
        """Node 1: Classify the user's intent."""
        print(f"Supervisor: Analyzing intent for '{state['question']}'")
        try:
            result = self.llm.invoke(f"Classify the following codebase question: {state['question']}")
            state["intent"] = result.intent
            
            # Store the extracted symbol temporarily in the state if it's an impact query
            if result.intent == "impact" and result.extracted_symbol:
                # We reuse the 'question' field for the symbol to pass to the next node
                # A more robust way is adding it to state, but we'll pack it here for simplicity.
                state["_target_symbol"] = result.extracted_symbol
            
            print(f"Supervisor: Classified intent as '{state['intent']}'")
        except Exception as e:
            fallback = self._fallback_intent(state["question"], e)
            if fallback:
                state["intent"], state["_target_symbol"] = fallback
                print(
                    f"Supervisor: Model classification unavailable; "
                    f"using fallback intent '{state['intent']}'"
                )
            else:
                state["error"] = f"Failed to classify intent: {e}"
        return state

    @staticmethod
    def _fallback_intent(question: str, error: Exception):
        """Keep simple impact questions usable when the classifier is rate-limited."""
        error_text = str(error).lower()
        if "429" not in error_text and "rate_limit" not in error_text and "token" not in error_text:
            return None

        words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question)
        lowered = {word.lower() for word in words}
        impact_terms = {"impact", "effect", "affect", "depend", "dependencies", "files", "break"}
        if lowered & impact_terms:
            symbols = [word for word in words if "_" in word or word[:1].isupper()]
            symbol = next((word for word in symbols if word.lower() not in {"what", "files"}), None)
            return "impact", symbol
        return None

    def _route(self, state: CodeSenseState) -> str:
        """Conditional router based on intent."""
        if state.get("error"):
            return END
            
        intent = state.get("intent")
        if intent == "impact":
            return "dependency_graph"
        elif intent == "search":
            return "semantic_search"
        elif intent == "refactor":
            return "refactor_node"
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
        
        results = self.graph_agent.get_impact(symbol)
        state["impact_results"] = results
        return state

    @staticmethod
    def _normalize_impact_target(extracted_symbol: str | None, question: str) -> str:
        """Avoid generic classifier words becoming the dependency target."""
        generic = {
            "a", "affect", "change", "changing", "effect", "effected", "files",
            "impact", "need", "other", "replace", "replacing", "what", "will",
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

    def _run_refactor(self, state: CodeSenseState) -> CodeSenseState:
        """Node: Refactor Specialist (MCP)
        
        NOTE: Indexes are NOT refreshed here. They are deferred until the user
        approves the changes via the /create-pr endpoint, where we do an
        incremental refresh of only the changed files.
        """
        print(f"Refactor Agent: Processing request '{state['question']}'")
        try:
            diff_result = self.refactor_agent.run_sync(state["question"], phase="refactor")
            state["diff_data"] = diff_result
            state["requires_approval"] = True
            state["final_answer"] = (
                "I have drafted the refactor. Please review the diff below and approve to create a PR."
            )
        except TimeoutError as error:
            # The agent may have completed the edit before its final response timed out.
            repo_path = os.getenv("CODESENSE_REPO_PATH") or str(
                Path.cwd() / "repos" / "demo"
            )
            if repo_path:
                diff_result = subprocess.run(
                    ["git", "--no-pager", "diff"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout.strip()
            else:
                diff_result = ""

            if diff_result:
                state["diff_data"] = diff_result
                state["requires_approval"] = True
                state["final_answer"] = (
                    "The refactor was applied, but the agent timed out while preparing its response. "
                    "Please review the recovered diff below."
                )
            else:
                state["error"] = str(error)
        except Exception as e:
            state["error"] = f"Refactor failed: {e}"
        return state

    def _refresh_indexes(self) -> str:
        """Rebuild search and dependency indexes after a working-tree refactor."""
        repo_path = os.getenv("CODESENSE_REPO_PATH") or str(Path.cwd() / "repos" / "demo")
        try:
            self.search_agent.vector_store.client.close()
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
        """Always route to explainer to synthesize search results into a natural language answer."""
        return "explainer"

    def _build_graph(self):
        workflow = StateGraph(CodeSenseState)

        # Add nodes
        workflow.add_node("supervisor", self._classify_intent)
        workflow.add_node("semantic_search", self._run_semantic_search)
        workflow.add_node("dependency_graph", self._run_dependency_graph)
        workflow.add_node("explainer", self.explainer_agent.generate_explanation)
        workflow.add_node("refactor_node", self._run_refactor)

        # Edges
        workflow.add_edge(START, "supervisor")
        
        # Router from supervisor
        workflow.add_conditional_edges(
            "supervisor",
            self._route,
            {
                "semantic_search": "semantic_search",
                "dependency_graph": "dependency_graph",
                "refactor_node": "refactor_node",
                END: END
            }
        )
        
        # After search, either END (if intent was just search) or go to explainer (if intent was explain)
        workflow.add_conditional_edges(
            "semantic_search",
            self._route_after_search,
            {
                "explainer": "explainer",
                END: END
            }
        )
        
        # After dependency graph, always go to explainer to synthesize
        workflow.add_edge("dependency_graph", "explainer")
        
        # After refactor, we pause for UI approval
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
            search_results=None,
            impact_results=None,
            final_answer=None,
            error=None,
            requires_approval=False,
            diff_data=None
        )
        
        result_state = self.graph.invoke(initial_state)
        
        return result_state
