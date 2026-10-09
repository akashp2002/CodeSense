from codesense.graph_store import GraphStore
store = GraphStore(user_id="18")
with store.driver.session() as session:
    res = session.run("MATCH (n:Symbol {tenant_id: '18', symbol_name: 'HybridRetriever'}) RETURN n")
    for r in res:
        print(r)
