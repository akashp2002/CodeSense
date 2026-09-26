from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.http import models
from sentence_transformers import SentenceTransformer
from fastembed import SparseTextEmbedding
from codesense.models.core import CodeChunk
from typing import List

DEFAULT_QDRANT_PATH = Path(__file__).resolve().parents[2] / ".qdrant_db"


class VectorStore:
    def __init__(self, collection_name: str = "code_chunks", path: str | None = None):
        storage_path = str(DEFAULT_QDRANT_PATH if path is None else path)
        self.client = QdrantClient(path=storage_path)
        self.collection_name = collection_name
        # Using a fast, lightweight local embedding model
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        self.sparse_model = SparseTextEmbedding("Qdrant/bm25")
        
        self._ensure_collection()

    def _ensure_collection(self):
        try:
            info = self.client.get_collection(self.collection_name)
            # Check if schema has named vectors (hybrid). If not, wipe and recreate.
            vectors_config = info.config.params.vectors
            if not isinstance(vectors_config, dict) or "dense" not in vectors_config:
                print(f"[VectorStore] Migrating collection to hybrid schema...")
                self.client.delete_collection(self.collection_name)
                raise Exception("Recreate needed")
        except Exception:
            # Collection does not exist or needs migration
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    "dense": models.VectorParams(
                        size=self.model.get_embedding_dimension(),
                        distance=models.Distance.COSINE
                    )
                },
                sparse_vectors_config={
                    "bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)
                }
            )

    def clear_collection(self):
        """Delete and recreate the collection to wipe all old data."""
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self._ensure_collection()

    def delete_file_chunks(self, file_path: str):
        """Delete all chunks belonging to a specific file for incremental updates."""
        try:
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="file_path",
                                match=models.MatchValue(value=file_path)
                            )
                        ]
                    )
                )
            )
        except Exception as e:
            print(f"Error deleting chunks for {file_path}: {e}")
            
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
        sparse_embeddings = list(self.sparse_model.embed(texts))
        
        vectors = {
            "dense": embeddings.tolist(),
            "bm25": [
                models.SparseVector(
                    indices=sparse.indices.tolist(),
                    values=sparse.values.tolist()
                ) for sparse in sparse_embeddings
            ]
        }
        
        self.client.upsert(
            collection_name=self.collection_name,
            points=models.Batch(
                ids=ids,
                vectors=vectors,
                payloads=payloads
            )
        )
        
    def search(self, query: str, limit: int = 5) -> List[CodeChunk]:
        query_dense = self.model.encode(query).tolist()
        query_sparse = next(self.sparse_model.embed([query]))
        
        results = self.client.query_points(
            collection_name=self.collection_name,
            prefetch=[
                models.Prefetch(
                    query=query_dense,
                    using="dense",
                    limit=limit,
                ),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=query_sparse.indices.tolist(),
                        values=query_sparse.values.tolist(),
                    ),
                    using="bm25",
                    limit=limit,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit
        ).points
        
        return [CodeChunk(**(hit.payload or {})) for hit in results]
