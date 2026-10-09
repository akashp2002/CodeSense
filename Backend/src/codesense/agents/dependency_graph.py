from typing import List, Dict
from codesense.graph_store import GraphStore

class DependencyGraphAgent:
    def __init__(self, graph_store: GraphStore = None):
        self.graph_store = graph_store or GraphStore()

    def get_impact(
        self, symbol_name: str, max_hops: int = 3, direction: str = "dependents"
    ) -> Dict:
        """
        Find symbols that depend on the target or that the target depends on.
        """
        if direction == "dependencies":
            related = self.graph_store.get_dependencies(symbol_name, max_hops=max_hops)
            relation_name = "dependencies"
        else:
            related = self.graph_store.get_dependents(symbol_name, max_hops=max_hops)
            relation_name = "dependents"

        if not related:
            if relation_name == "dependents":
                summary = f"No dependents found for '{symbol_name}'. It is safe to change."
            else:
                summary = f"No dependencies found for '{symbol_name}'."
        else:
            files = sorted(set(item["file_path"] for item in related if item.get("file_path")))
            symbols = sorted(set(item["symbol_name"] for item in related if item.get("symbol_name")))
            if relation_name == "dependents":
                summary = (
                    f"Changing '{symbol_name}' may impact {len(related)} reference(s) "
                    f"across {len(files)} file(s):\n"
                    + "\n".join(f"  - {f}" for f in files)
                )
            else:
                summary = (
                    f"'{symbol_name}' has {len(related)} dependencies "
                    f"across {len(files)} file(s):\n"
                    + "\n".join(f"  - {f}" for f in files)
                )
            if symbols:
                label = "Affected symbols" if relation_name == "dependents" else "Dependencies"
                summary += f"\n\n{label}: {', '.join(symbols)}"

        result = {
            "target_symbol": symbol_name,
            "direction": relation_name,
            "related_count": len(related),
            "related_symbols": related,
            "summary": summary
        }
        if relation_name == "dependents":
            result["dependent_count"] = len(related)
            result["dependents"] = related
        else:
            result["dependency_count"] = len(related)
            result["dependencies"] = related
        return result
