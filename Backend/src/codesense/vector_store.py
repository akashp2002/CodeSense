from qdrant_client import QdrantClient
from qdrant_client.http import models
from sentence_transformers import SentenceTransformer
from codesense.models.core import CodeChunk
from typing import List

class VectorStore:
    def __init__(self, collection_name: str = "code_chunks", path: str = ".qdrant_db"):
        self.client = QdrantClient(path=path)
        self.collection_name = collection_name
        # Using a fast, lightweight local embedding model
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        
        self._ensure_collection()

    def _ensure_collection(self):
        try:
            self.client.get_collection(self.collection_name)
        except Exception:
            # Collection does not exist
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.model.get_embedding_dimension(),
                    distance=models.Distance.COSINE
                )
            )
            
    def index_chunks(self, chunks: List[CodeChunk]):
        if not chunks:
            return
            
        texts = []
        payloads = []
        ids = []
        
        for chunk in chunks:
            text = f"{chunk.chunk_type} {chunk.symbol_name}\n"
            if chunk.docstring:
                text += f"Docstring: {chunk.docstring}\n"
            text += f"Code: {chunk.raw_code}"
            
            texts.append(text)
            payloads.append(chunk.model_dump())
            # Simple unique positive integer ID based on hash
            ids.append(abs(hash(chunk.file_path + str(chunk.line_range.start_line) + chunk.symbol_name)) % (10 ** 12))
            
        embeddings = self.model.encode(texts)
        
        self.client.upsert(
            collection_name=self.collection_name,
            points=models.Batch(
                ids=ids,
                vectors=embeddings.tolist(),
                payloads=payloads
            )
        )
        
    def search(self, query: str, limit: int = 5) -> List[CodeChunk]:
        query_vector = self.model.encode(query).tolist()
        
        results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=limit
        ).points
        
        return [CodeChunk(**(hit.payload or {})) for hit in results]
