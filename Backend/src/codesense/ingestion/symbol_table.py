from tree_sitter import Node
from typing import List
from codesense.models.core import SymbolReference

class SymbolExtractor:
    def extract_references(self, node: Node, code: bytes, file_path: str) -> List[SymbolReference]:
        refs = []
        self._traverse(node, code, file_path, refs)
        return refs

    def _traverse(self, node: Node, code: bytes, file_path: str, refs: List[SymbolReference]):
        if node.type == 'call':
            func_node = node.child_by_field_name('function')
            if func_node:
                # Get the name of the function being called
                symbol_name = code[func_node.start_byte:func_node.end_byte].decode('utf8')
                refs.append(SymbolReference(
                    file_path=file_path,
                    symbol_name=symbol_name,
                    reference_type='call',
                    line_number=node.start_point[0] + 1
                ))
        
        elif node.type == 'import_statement':
            # import x, y
            for child in node.children:
                if child.type == 'dotted_name':
                    symbol_name = code[child.start_byte:child.end_byte].decode('utf8')
                    refs.append(SymbolReference(
                        file_path=file_path,
                        symbol_name=symbol_name,
                        reference_type='import',
                        line_number=node.start_point[0] + 1
                    ))
                    
        elif node.type == 'import_from_statement':
            # from module import x
            module_name_node = node.child_by_field_name('module_name')
            if module_name_node:
                module_name = code[module_name_node.start_byte:module_name_node.end_byte].decode('utf8')
                for child in node.children:
                    if child.type == 'dotted_name' and child != module_name_node:
                        symbol_name = f"{module_name}.{code[child.start_byte:child.end_byte].decode('utf8')}"
                        refs.append(SymbolReference(
                            file_path=file_path,
                            symbol_name=symbol_name,
                            reference_type='import',
                            line_number=node.start_point[0] + 1
                        ))
                    elif child.type == 'aliased_import':
                        # handle aliases if necessary
                        pass

        elif node.type == 'class_definition':
            bases_node = node.child_by_field_name('superclasses')
            if bases_node:
                for child in bases_node.children:
                    if child.type in ('identifier', 'attribute'):
                        symbol_name = code[child.start_byte:child.end_byte].decode('utf8')
                        refs.append(SymbolReference(
                            file_path=file_path,
                            symbol_name=symbol_name,
                            reference_type='inheritance',
                            line_number=node.start_point[0] + 1
                        ))

        for child in node.children:
            self._traverse(child, code, file_path, refs)
