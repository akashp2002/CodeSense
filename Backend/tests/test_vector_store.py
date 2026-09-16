import pytest
from codesense.vector_store import VectorStore
from codesense.models.core import CodeChunk, LineRange

def test_vector_store_indexing_and_search():
    store = VectorStore(collection_name="test_collection", path=":memory:")
    
    chunks = [
        CodeChunk(
            file_path="src/math.py",
            symbol_name="add",
            chunk_type="function",
            line_range=LineRange(start_line=1, end_line=3),
            raw_code="def add(a, b):\n    return a + b",
            docstring="Adds two numbers."
        ),
        CodeChunk(
            file_path="src/math.py",
            symbol_name="subtract",
            chunk_type="function",
            line_range=LineRange(start_line=5, end_line=7),
            raw_code="def subtract(a, b):\n    return a - b",
            docstring="Subtracts two numbers."
        )
    ]
    
    store.index_chunks(chunks)
    
    results = store.search("addition", limit=1)
    
    assert len(results) == 1
    assert results[0].symbol_name == "add"
    
    results = store.search("minus", limit=1)
    
    assert len(results) == 1
    assert results[0].symbol_name == "subtract"
