import os
from typing import Literal
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, START, END
from codesense.models.state import CodeSenseState
from codesense.agents.semantic_search import SemanticSearchAgent
from codesense.agents.dependency_graph import DependencyGraphAgent
from codesense.agents.explainer import ExplainerAgent

class IntentClassification(BaseModel):
    intent: Literal["search", "impact", "explain"] = Field(
        description="The classified intent of the user's question. "
                    "'search' for finding where something is or how it's implemented. "
                    "'impact' for questions about blast radius, dependencies, or what breaks if something changes. "
                    "'explain' for general architectural or 'how does it work' questions."
    )
    extracted_symbol: str | None = Field(
        description="If the intent is 'impact', the specific symbol (function/class name) the user is asking about. Otherwise null.",
        default=None
    )

class SupervisorAgent:
    def __init__(self, model_name: str = "openai/gpt-oss-20b"):
        self.llm = ChatGroq(model_name=model_name, temperature=0).with_structured_output(IntentClassification)
        
        # Initialize specialist tools
        self.search_agent = SemanticSearchAgent()
        self.graph_agent = DependencyGraphAgent()
        self.explainer_agent = ExplainerAgent(model_name=model_name)
        
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
            state["error"] = f"Failed to classify intent: {e}"
        return state

    def _route(self, state: CodeSenseState) -> str:
        """Conditional router based on intent."""
        if state.get("error"):
            return END
            
        intent = state.get("intent")
        if intent == "impact":
            return "dependency_graph"
        elif intent == "search":
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
        symbol = state.get("_target_symbol")
        
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

    def _route_after_search(self, state: CodeSenseState) -> str:
        """Route to explainer only if the intent was 'explain'."""
        if state.get("intent") == "explain":
            return "explainer"
        return END

    def _build_graph(self):
        workflow = StateGraph(CodeSenseState)

        # Add nodes
        workflow.add_node("supervisor", self._classify_intent)
        workflow.add_node("semantic_search", self._run_semantic_search)
        workflow.add_node("dependency_graph", self._run_dependency_graph)
        workflow.add_node("explainer", self.explainer_agent.generate_explanation)

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
        
        # After explainer, we're done
        workflow.add_edge("explainer", END)

        return workflow.compile()

    def run(self, question: str) -> str:
        """Execute the LangGraph workflow for a given question."""
        if not os.getenv("GROQ_API_KEY"):
            return "Error: GROQ_API_KEY environment variable is missing. Please set it in your .env file."
            
        initial_state = CodeSenseState(
            question=question,
            intent=None,
            search_results=None,
            impact_results=None,
            final_answer=None,
            error=None
        )
        
        result_state = self.graph.invoke(initial_state)
        
        if result_state.get("error"):
            return f"Error: {result_state['error']}"
            
        # Return the final explainer answer, or format the raw results if it bypassed the explainer
        if result_state.get("final_answer"):
            return result_state["final_answer"]
            
        if result_state.get("intent") == "search" and result_state.get("search_results"):
            # Format search results
            out = ["Search Results:"]
            for r in result_state["search_results"]:
                out.append(f"[{r['rank']}] {r['file_path']} - {r['symbol_name']}")
            return "\n".join(out)
            
        return "No answer generated."
