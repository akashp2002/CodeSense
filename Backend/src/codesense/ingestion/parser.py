import tree_sitter_python as tspython
from tree_sitter import Language, Parser

class CodeParser:
    def __init__(self):
        self.language = Language(tspython.language())
        self.parser = Parser(self.language)

    def parse_file(self, file_path: str):
        with open(file_path, "rb") as f:
            code = f.read()
        tree = self.parser.parse(code)
        return tree, code

    def parse_code(self, code: bytes):
        tree = self.parser.parse(code)
        return tree, code
