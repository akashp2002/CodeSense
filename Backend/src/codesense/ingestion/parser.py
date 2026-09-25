import os
import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
import tree_sitter_go as tsgo
import tree_sitter_rust as tsrust
import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

SUPPORTED_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java"}

class CodeParser:
    def __init__(self):
        # Build a Language object for each supported file extension
        languages = {
            ".py": Language(tspython.language()),
            ".js": Language(tsjavascript.language()),
            ".jsx": Language(tsjavascript.language()),
            ".ts": Language(tstypescript.language_typescript()),
            ".tsx": Language(tstypescript.language_tsx()),
            ".go": Language(tsgo.language()),
            ".rs": Language(tsrust.language()),
            ".java": Language(tsjava.language()),
        }
        # Pre-create a Parser for each language (v0.26 API requires language in constructor)
        self.parsers = {ext: Parser(lang) for ext, lang in languages.items()}

    def parse_file(self, file_path: str):
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.parsers:
            raise ValueError(f"Unsupported file extension: {ext}")

        with open(file_path, "rb") as f:
            code = f.read()
        tree = self.parsers[ext].parse(code)
        return tree, code

    def parse_code(self, code: bytes, ext: str = ".py"):
        if ext not in self.parsers:
            raise ValueError(f"Unsupported file extension: {ext}")

        tree = self.parsers[ext].parse(code)
        return tree, code
