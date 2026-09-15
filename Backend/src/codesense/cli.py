import sys
from pathlib import Path
from codesense.ingestion.parser import CodeParser
from codesense.ingestion.chunker import SemanticChunker
from codesense.ingestion.symbol_table import SymbolExtractor

def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python -m codesense.cli <file_path>")
        sys.exit(1)
        
    file_path = sys.argv[1]
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        print(f"Error: {file_path} is not a valid file.")
        sys.exit(1)
        
    parser = CodeParser()
    chunker = SemanticChunker()
    extractor = SymbolExtractor()
    
    print(f"--- Ingesting {file_path} ---")
    tree, code = parser.parse_file(file_path)
    
    chunks = chunker.chunk_node(tree.root_node, code, file_path)
    print(f"\nExtracted {len(chunks)} chunks:")
    for c in chunks:
        print(f"  - [{c.chunk_type.upper()}] {c.symbol_name} (Lines {c.line_range.start_line}-{c.line_range.end_line})")
        if c.signature:
            print(f"      Signature: {c.signature}")
            
    refs = extractor.extract_references(tree.root_node, code, file_path)
    print(f"\nExtracted {len(refs)} symbol references:")
    for r in refs:
        print(f"  - [{r.reference_type.upper()}] {r.symbol_name} (Line {r.line_number})")

if __name__ == "__main__":
    main()
