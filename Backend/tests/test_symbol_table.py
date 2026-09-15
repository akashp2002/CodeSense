import pytest
from codesense.ingestion.parser import CodeParser
from codesense.ingestion.symbol_table import SymbolExtractor

def test_symbol_extractor():
    parser = CodeParser()
    extractor = SymbolExtractor()
    
    code = b"""
import os
from pydantic import BaseModel

class User(BaseModel):
    pass

def print_user(user: User):
    print(user)
    os.path.exists("test")
"""
    tree, raw = parser.parse_code(code)
    refs = extractor.extract_references(tree.root_node, raw, "test.py")
    
    import_refs = [r for r in refs if r.reference_type == 'import']
    assert len(import_refs) == 2
    
    inheritance_refs = [r for r in refs if r.reference_type == 'inheritance']
    assert len(inheritance_refs) == 1
    assert inheritance_refs[0].symbol_name == "BaseModel"
    
    call_refs = [r for r in refs if r.reference_type == 'call']
    assert len(call_refs) == 2
    call_names = {r.symbol_name for r in call_refs}
    assert "print" in call_names
    assert "os.path.exists" in call_names
