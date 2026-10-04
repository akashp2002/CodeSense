import time
from codesense.graph_store import GraphStore

g = GraphStore()
start = time.time()
print("Starting query...")
deps = g.get_dependents("search_adzuna_via_mcp", max_hops=3)
print(f"Query finished in {time.time() - start:.2f} seconds")
print(f"Found {len(deps)} dependents")
