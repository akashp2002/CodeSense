from pydantic import BaseModel
from typing import Optional

class LineRange(BaseModel):
    start_line: int
    end_line: int

class CodeChunk(BaseModel):
    file_path: str
    symbol_name: str
    signature: Optional[str] = None
    docstring: Optional[str] = None
    line_range: LineRange
    raw_code: str
    chunk_type: str  # 'function', 'class', 'method', 'module'

class SymbolReference(BaseModel):
    file_path: str
    caller_symbol: Optional[str] = None  # which function/method is making this reference
    symbol_name: str
    reference_type: str  # 'call', 'import', 'inheritance'
    line_number: int
