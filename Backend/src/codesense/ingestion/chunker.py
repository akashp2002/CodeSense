from tree_sitter import Node
from typing import List, Optional
from codesense.models.core import CodeChunk, LineRange

class SemanticChunker:
    def chunk_node(self, node: Node, code: bytes, file_path: str) -> List[CodeChunk]:
        chunks = []
        self._traverse(node, code, file_path, chunks)
        return chunks

    def _traverse(self, node: Node, code: bytes, file_path: str, chunks: List[CodeChunk]):
        if node.type == 'class_definition':
            chunk = self._create_chunk(node, code, file_path, 'class')
            if chunk:
                chunks.append(chunk)
            # Traverse children to find methods
            for child in node.children:
                if child.type == 'block':
                    for subchild in child.children:
                        if subchild.type == 'function_definition':
                            method_chunk = self._create_chunk(subchild, code, file_path, 'method')
                            if method_chunk:
                                chunks.append(method_chunk)
        elif node.type == 'function_definition':
            # Only add function if it's not a method (i.e. parent is not a class_definition)
            # A bit simplistic, but we handle methods above.
            # To be safe, check if it's a module level function
            if node.parent and node.parent.type == 'module':
                chunk = self._create_chunk(node, code, file_path, 'function')
                if chunk:
                    chunks.append(chunk)
        
        # We only want top level classes and functions, or nested classes if we want, but let's stick to module level traverse
        if node.type == 'module':
            for child in node.children:
                self._traverse(child, code, file_path, chunks)

    def _create_chunk(self, node: Node, code: bytes, file_path: str, chunk_type: str) -> Optional[CodeChunk]:
        name_node = node.child_by_field_name('name')
        if not name_node:
            return None
            
        symbol_name = code[name_node.start_byte:name_node.end_byte].decode('utf8')
        
        # extract docstring
        docstring = None
        body_node = node.child_by_field_name('body')
        if body_node and len(body_node.children) > 0:
            first_stmt = body_node.children[0]
            if first_stmt.type == 'expression_statement':
                string_node = first_stmt.children[0]
                if string_node.type == 'string':
                    docstring = code[string_node.start_byte:string_node.end_byte].decode('utf8')

        # extract signature
        signature = None
        if chunk_type in ('function', 'method'):
            params_node = node.child_by_field_name('parameters')
            if params_node:
                signature = code[params_node.start_byte:params_node.end_byte].decode('utf8')
        elif chunk_type == 'class':
            bases_node = node.child_by_field_name('superclasses')
            if bases_node:
                signature = code[bases_node.start_byte:bases_node.end_byte].decode('utf8')

        raw_code = code[node.start_byte:node.end_byte].decode('utf8')
        
        return CodeChunk(
            file_path=file_path,
            symbol_name=symbol_name,
            signature=signature,
            docstring=docstring,
            line_range=LineRange(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1
            ),
            raw_code=raw_code,
            chunk_type=chunk_type
        )
