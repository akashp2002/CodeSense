from typing import List, Dict
from codesense.vector_store import VectorStore

class SemanticSearchAgent:
    def __init__(self, vector_store: VectorStore = None):
        self.vector_store = vector_store or VectorStore()

    def search_codebase(self, query: str, limit: int = 5) -> List[Dict]:
        """
        Retrieves the most relevant CodeChunks for the given semantic query.
        Returns a formatted dictionary for each result.
        """
        results = self.vector_store.search(query, limit=limit)
        
        formatted_results = []
        for rank, chunk in enumerate(results, 1):
            formatted_results.append({
                "rank": rank,
                "file_path": chunk.file_path,
                "symbol_name": chunk.symbol_name,
                "chunk_type": chunk.chunk_type,
                "line_range": f"{chunk.line_range.start_line}-{chunk.line_range.end_line}",
                "snippet": chunk.raw_code
            })
            
        return formatted_results
