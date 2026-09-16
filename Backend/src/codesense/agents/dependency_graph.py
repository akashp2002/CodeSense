from typing import List, Dict
from codesense.graph_store import GraphStore

class DependencyGraphAgent:
    def __init__(self, graph_store: GraphStore = None):
        self.graph_store = graph_store or GraphStore()

    def get_impact(self, symbol_name: str, max_hops: int = 3) -> Dict:
        """
        Find the blast radius for a given symbol.
        Returns: dict with symbol_name, dependents list, and a plain-text summary.
        """
        dependents = self.graph_store.get_dependents(symbol_name, max_hops=max_hops)

        if not dependents:
            summary = f"No dependents found for '{symbol_name}'. It is safe to change."
        else:
            files = sorted(set(d["file_path"] for d in dependents if d.get("file_path")))
            symbols = sorted(set(d["symbol_name"] for d in dependents if d.get("symbol_name")))
            summary = (
                f"Changing '{symbol_name}' may impact {len(dependents)} reference(s) "
                f"across {len(files)} file(s):\n"
                + "\n".join(f"  - {f}" for f in files)
            )
            if symbols:
                summary += f"\n\nAffected symbols: {', '.join(symbols)}"

        return {
            "target_symbol": symbol_name,
            "dependent_count": len(dependents),
            "dependents": dependents,
            "summary": summary
        }
