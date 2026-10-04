from codesense.graph_store import GraphStore

try:
    g = GraphStore()
    deps = g.get_dependents("get_db_session")
    print("Dependents:", deps)
except Exception as e:
    import traceback
    traceback.print_exc()
