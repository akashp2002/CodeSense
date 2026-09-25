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
        func_types = {
            'function_definition', 'decorated_definition', 'function_declaration', 
            'method_definition', 'method_declaration', 'function_item', 'arrow_function'
        }
        class_types = {
            'class_definition', 'class_declaration', 'struct_item', 
            'interface_declaration', 'type_declaration', 'impl_item'
        }
        call_types = {
            'call', 'call_expression', 'method_invocation', 'method_call_expression'
        }
        import_types = {
            'import_statement', 'import_from_statement', 'use_declaration', 
            'import_declaration', 'import_spec'
        }

        if node.type in func_types:
            # Resolve the actual function node in case of decorator
            func_node = node if node.type != 'decorated_definition' else next(
                (c for c in node.children if c.type in func_types and c.type != 'decorated_definition'), node
            )
            name_node = func_node.child_by_field_name('name')
            new_scope = code[name_node.start_byte:name_node.end_byte].decode('utf8') if name_node else current_scope

            # Traverse children with the updated scope
            for child in func_node.children:
                self._traverse(child, code, file_path, refs, current_scope=new_scope)
            return  # stop — we've already traversed children above

        elif node.type in class_types:
            name_node = node.child_by_field_name('name')
            class_name = code[name_node.start_byte:name_node.end_byte].decode('utf8') if name_node else current_scope

            # Emit inheritance references (class-level, no caller needed)
            bases_node = node.child_by_field_name('superclasses') or node.child_by_field_name('interfaces')
            if bases_node:
                for child in bases_node.children:
                    if child.type in ('identifier', 'attribute', 'type_identifier'):
                        symbol_name = code[child.start_byte:child.end_byte].decode('utf8')
                        refs.append(SymbolReference(
                            file_path=file_path,
                            caller_symbol=class_name,
                            symbol_name=symbol_name,
                            reference_type='inheritance',
                            line_number=node.start_point[0] + 1
                        ))
            
            # also look for 'class_heritage' in JS/TS
            for child in node.children:
                if child.type == 'class_heritage':
                    for subchild in child.children:
                        if subchild.type == 'identifier':
                            refs.append(SymbolReference(
                                file_path=file_path,
                                caller_symbol=class_name,
                                symbol_name=code[subchild.start_byte:subchild.end_byte].decode('utf8'),
                                reference_type='inheritance',
                                line_number=node.start_point[0] + 1
                            ))

            # Traverse class body with class scope (methods will override it)
            for child in node.children:
                self._traverse(child, code, file_path, refs, current_scope=class_name)
            return

        elif node.type in call_types:
            func_node = node.child_by_field_name('function') or node.child_by_field_name('name')
            if func_node:
                symbol_name = code[func_node.start_byte:func_node.end_byte].decode('utf8')
                
                refs.append(SymbolReference(
                    file_path=file_path,
                    caller_symbol=current_scope,
                    symbol_name=symbol_name,
                    reference_type='call',
                    line_number=node.start_point[0] + 1
                ))

        elif node.type in import_types:
            for child in node.children:
                if child.type in ('dotted_name', 'identifier', 'scoped_identifier'):
                    symbol_name = code[child.start_byte:child.end_byte].decode('utf8').split('.')[-1]
                    refs.append(SymbolReference(
                        file_path=file_path,
                        caller_symbol=None,  # imports are always module-level
                        symbol_name=symbol_name,
                        reference_type='import',
                        line_number=node.start_point[0] + 1
                    ))

        # General case: keep traversing children with the same scope
        for child in node.children:
            self._traverse(child, code, file_path, refs, current_scope=current_scope)
