from codesense.graph_store import GraphStore
store = GraphStore(user_id="18")
with store.driver.session() as session:
    res = session.run("MATCH (s)-[r]->(t) WHERE t.symbol_name = 'HybridRetriever' AND t.tenant_id='18' RETURN s.symbol_name, type(r), t.symbol_name")
    for r in res:
        print(r)
