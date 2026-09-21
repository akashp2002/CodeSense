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
    # imports are module-level, so caller_symbol should be None
    assert all(r.caller_symbol is None for r in import_refs)
    assert {r.symbol_name for r in import_refs} == {"os", "BaseModel"}
    
    inheritance_refs = [r for r in refs if r.reference_type == 'inheritance']
    assert len(inheritance_refs) == 1
    assert inheritance_refs[0].symbol_name == "BaseModel"
    # inheritance is attributed to the class
    assert inheritance_refs[0].caller_symbol == "User"
    
    call_refs = [r for r in refs if r.reference_type == 'call']
    assert len(call_refs) == 2
    call_names = {r.symbol_name for r in call_refs}
    assert "print" in call_names
    assert "os.path.exists" in call_names
    # all calls are inside print_user, so caller_symbol should be print_user
    assert all(r.caller_symbol == "print_user" for r in call_refs)

def test_caller_scope_tracking():
    """Verify A → B and C → D are correctly attributed, not file → B and file → D."""
    parser = CodeParser()
    extractor = SymbolExtractor()

    code = b"""
def A():
    B()

def C():
    D()
"""
    tree, raw = parser.parse_code(code)
    refs = extractor.extract_references(tree.root_node, raw, "test.py")
    
    call_refs = [r for r in refs if r.reference_type == 'call']
    caller_to_callee = {r.caller_symbol: r.symbol_name for r in call_refs}

    assert caller_to_callee.get("A") == "B", "A should call B"
    assert caller_to_callee.get("C") == "D", "C should call D"

