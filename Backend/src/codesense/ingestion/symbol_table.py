from tree_sitter import Node
from typing import List, Optional
from codesense.models.core import SymbolReference

class SymbolExtractor:
    def extract_references(self, node: Node, code: bytes, file_path: str) -> List[SymbolReference]:
        refs = []
        self._traverse(node, code, file_path, refs, current_scope=None)
        return refs

    def _traverse(self, node: Node, code: bytes, file_path: str, refs: List[SymbolReference], current_scope: Optional[str]):
        """
        Recursively traverse the AST. When we enter a function/method definition,
        we update `current_scope` so that any calls found inside it are attributed
        to the correct caller: A → CALLS → B, not File → CALLS → B.
        """
        if node.type in ('function_definition', 'decorated_definition'):
            # Resolve the actual function node in case of decorator
            func_node = node if node.type == 'function_definition' else next(
                (c for c in node.children if c.type == 'function_definition'), node
            )
            name_node = func_node.child_by_field_name('name')
            new_scope = code[name_node.start_byte:name_node.end_byte].decode('utf8') if name_node else current_scope

            # Traverse children with the updated scope
            for child in func_node.children:
                self._traverse(child, code, file_path, refs, current_scope=new_scope)
            return  # stop — we've already traversed children above

        elif node.type == 'class_definition':
            name_node = node.child_by_field_name('name')
            class_name = code[name_node.start_byte:name_node.end_byte].decode('utf8') if name_node else current_scope

            # Emit inheritance references (class-level, no caller needed)
            bases_node = node.child_by_field_name('superclasses')
            if bases_node:
                for child in bases_node.children:
                    if child.type in ('identifier', 'attribute'):
                        symbol_name = code[child.start_byte:child.end_byte].decode('utf8')
                        refs.append(SymbolReference(
                            file_path=file_path,
                            caller_symbol=class_name,
                            symbol_name=symbol_name,
                            reference_type='inheritance',
                            line_number=node.start_point[0] + 1
                        ))

            # Traverse class body with class scope (methods will override it)
            for child in node.children:
                self._traverse(child, code, file_path, refs, current_scope=class_name)
            return

        elif node.type == 'call':
            func_node = node.child_by_field_name('function')
            if func_node:
                symbol_name = code[func_node.start_byte:func_node.end_byte].decode('utf8')
                
                refs.append(SymbolReference(
                    file_path=file_path,
                    caller_symbol=current_scope,
                    symbol_name=symbol_name,
                    reference_type='call',
                    line_number=node.start_point[0] + 1
                ))

        elif node.type == 'import_statement':
            for child in node.children:
                if child.type == 'dotted_name':
                    symbol_name = code[child.start_byte:child.end_byte].decode('utf8')
                    refs.append(SymbolReference(
                        file_path=file_path,
                        caller_symbol=None,  # imports are always module-level
                        symbol_name=symbol_name,
                        reference_type='import',
                        line_number=node.start_point[0] + 1
                    ))

        elif node.type == 'import_from_statement':
            module_name_node = node.child_by_field_name('module_name')
            if module_name_node:
                module_name = code[module_name_node.start_byte:module_name_node.end_byte].decode('utf8')
                for child in node.children:
                    if child.type == 'dotted_name' and child != module_name_node:
                        symbol_name = code[child.start_byte:child.end_byte].decode('utf8').split('.')[-1]
                        refs.append(SymbolReference(
                            file_path=file_path,
                            caller_symbol=None,
                            symbol_name=symbol_name,
                            reference_type='import',
                            line_number=node.start_point[0] + 1
                        ))

        # General case: keep traversing children with the same scope
        for child in node.children:
            self._traverse(child, code, file_path, refs, current_scope=current_scope)
