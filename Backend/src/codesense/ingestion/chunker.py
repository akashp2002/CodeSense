from tree_sitter import Node
from typing import List, Optional
from codesense.models.core import CodeChunk, LineRange

class SemanticChunker:
    def chunk_node(self, node: Node, code: bytes, file_path: str) -> List[CodeChunk]:
        chunks = []
        self._traverse(node, code, file_path, chunks)
        return chunks

    def _traverse(self, node: Node, code: bytes, file_path: str, chunks: List[CodeChunk]):
        class_types = {
            'class_definition', 'class_declaration', 'class', 
            'struct_item', 'impl_item', 'interface_declaration',
            'type_declaration'
        }
        method_types = {
            'function_definition', 'method_definition', 'public_field_definition',
            'method_declaration', 'function_item'
        }
        function_types = {
            'function_definition', 'function_declaration', 'arrow_function',
            'generator_function_declaration', 'function_item'
        }

        if node.type in class_types:
            chunk = self._create_chunk(node, code, file_path, 'class')
            if chunk:
                chunks.append(chunk)
            # Traverse children to find methods
            for child in node.children:
                if child.type == 'block' or child.type == 'class_body' or child.type == 'declaration_list':
                    for subchild in child.children:
                        if subchild.type in method_types:
                            method_chunk = self._create_chunk(subchild, code, file_path, 'method')
                            if method_chunk:
                                chunks.append(method_chunk)
        elif node.type in function_types:
            # Only add function if it's not a method (simplistic check for module-level or top-level)
            parent = node.parent
            if parent and parent.type in ('module', 'program', 'source_file', 'translation_unit'):
                chunk = self._create_chunk(node, code, file_path, 'function')
                if chunk:
                    chunks.append(chunk)
        
        # Traverse top level container nodes
        if node.type in ('module', 'program', 'source_file', 'translation_unit'):
            for child in node.children:
                self._traverse(child, code, file_path, chunks)

    def _create_chunk(self, node: Node, code: bytes, file_path: str, chunk_type: str) -> Optional[CodeChunk]:
        # Resolve symbol name — different languages use different field names
        name_node = node.child_by_field_name('name')
        if not name_node:
            # Rust impl blocks: `impl Foo { ... }` — use the type field
            name_node = node.child_by_field_name('type')
        if not name_node:
            return None
            
        symbol_name = code[name_node.start_byte:name_node.end_byte].decode('utf8')
        
        # Extract docstring — Python triple-quote or JSDoc /** ... */ comment
        docstring = None
        body_node = node.child_by_field_name('body')
        if body_node and len(body_node.children) > 0:
            first_stmt = body_node.children[0]
            # Python docstrings
            if first_stmt.type == 'expression_statement' and first_stmt.children:
                string_node = first_stmt.children[0]
                if string_node.type == 'string':
                    docstring = code[string_node.start_byte:string_node.end_byte].decode('utf8')
        # JSDoc / block comments immediately preceding the node
        if docstring is None:
            prev = node.prev_sibling
            if prev and prev.type == 'comment':
                comment_text = code[prev.start_byte:prev.end_byte].decode('utf8')
                if comment_text.startswith('/**') or comment_text.startswith('//'):
                    docstring = comment_text

        # Extract signature — try multiple field names used across languages
        signature = None
        if chunk_type in ('function', 'method'):
            params_node = (
                node.child_by_field_name('parameters') or 
                node.child_by_field_name('formal_parameters') or
                node.child_by_field_name('type_parameters')
            )
            if params_node:
                signature = code[params_node.start_byte:params_node.end_byte].decode('utf8')
        elif chunk_type == 'class':
            bases_node = (
                node.child_by_field_name('superclasses') or
                node.child_by_field_name('interfaces') or
                node.child_by_field_name('superclass')
            )
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
