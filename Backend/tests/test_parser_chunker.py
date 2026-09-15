import pytest
from codesense.ingestion.parser import CodeParser
from codesense.ingestion.chunker import SemanticChunker

def test_parser_and_chunker():
    parser = CodeParser()
    chunker = SemanticChunker()
    
    code = b"""
def hello(name: str):
    '''Say hello.'''
    print(f"Hello {name}")

class Greeter:
    '''A simple greeter class.'''
    def __init__(self, greeting: str):
        self.greeting = greeting
        
    def greet(self, name):
        print(f"{self.greeting} {name}")
"""
    tree, raw = parser.parse_code(code)
    chunks = chunker.chunk_node(tree.root_node, raw, "test.py")
    
    assert len(chunks) == 4
    
    func_chunk = next(c for c in chunks if c.symbol_name == "hello")
    assert func_chunk.chunk_type == "function"
    assert "Say hello." in func_chunk.docstring
    
    class_chunk = next(c for c in chunks if c.symbol_name == "Greeter")
    assert class_chunk.chunk_type == "class"
    assert "A simple greeter class." in class_chunk.docstring
    
    method_chunk = next(c for c in chunks if c.symbol_name == "greet")
    assert method_chunk.chunk_type == "method"
