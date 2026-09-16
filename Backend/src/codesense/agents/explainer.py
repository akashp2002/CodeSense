import json
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate
from codesense.models.state import CodeSenseState

class ExplainerAgent:
    def __init__(self, model_name: str = "openai/gpt-oss-20b"):
        self.llm = ChatGroq(model_name=model_name, temperature=0)
        self.prompt = PromptTemplate(
            template="""You are an expert software engineer analyzing a codebase.
            
You have been asked the following question:
<question>
{question}
</question>

Here is the context retrieved from our semantic search and dependency graph:
<context>
{context}
</context>

Provide a clear, concise, and accurate answer to the question using ONLY the provided context.
If the context does not contain enough information to answer the question, state that clearly.
Do not hallucinate or guess details not present in the context.
            """,
            input_variables=["question", "context"]
        )

    def generate_explanation(self, state: CodeSenseState) -> CodeSenseState:
        """
        Takes the results from previous nodes (search or impact) and generates a plain English explanation.
        """
        question = state.get("question", "")
        
        # Build context from available data
        context_parts = []
        if state.get("search_results"):
            context_parts.append("Semantic Search Results:")
            for res in state["search_results"]:
                context_parts.append(f"- File: {res['file_path']}\n  Symbol: {res['symbol_name']}\n  Code:\n{res['snippet']}")
                
        if state.get("impact_results"):
            context_parts.append("Dependency Impact Results:")
            context_parts.append(json.dumps(state["impact_results"], indent=2))
            
        context = "\n\n".join(context_parts)
        
        if not context:
            state["final_answer"] = "I couldn't find any relevant context in the codebase to answer your question."
            return state

        # Generate answer
        chain = self.prompt | self.llm
        response = chain.invoke({"question": question, "context": context})
        
        state["final_answer"] = response.content
        return state
