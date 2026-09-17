import streamlit as st
import requests
from streamlit_agraph import agraph, Node, Edge, Config

st.set_page_config(page_title="Impact Analysis", page_icon="📊", layout="wide")

st.title("📊 Dependency Impact Analysis")
st.markdown("Visualize the blast radius of changing a specific symbol (class or function).")

symbol = st.text_input("Target Symbol", placeholder="VectorStore")
hops = st.slider("Max Traversal Hops", 1, 5, 2)

if st.button("Analyze Impact"):
    if not symbol:
        st.warning("Please enter a symbol.")
    else:
        with st.spinner(f"Traversing graph for '{symbol}'..."):
            try:
                # 1. Fetch text summary
                res_summary = requests.get(f"http://localhost:8000/impact/{symbol}", params={"hops": hops})
                if res_summary.status_code == 200:
                    data = res_summary.json()
                    st.success(f"**Found {data.get('dependent_count', 0)} dependent(s)**")
                    st.text(data.get("summary", ""))
                else:
                    st.error(f"Error fetching summary: {res_summary.text}")

                # 2. Fetch graph for visualization
                res_graph = requests.get(f"http://localhost:8000/graph/{symbol}", params={"hops": hops})
                if res_graph.status_code == 200:
                    g_data = res_graph.json()
                    nodes = []
                    edges = []
                    
                    for n in g_data.get("nodes", []):
                        color = "#F44336" if n["id"] == symbol else "#2196F3"
                        size = 25 if n["id"] == symbol else 15
                        nodes.append(Node(id=n["id"], label=n["id"], size=size, color=color))
                        
                    for e in g_data.get("edges", []):
                        edges.append(Edge(source=e["source"], target=e["target"], label=e["label"]))
                        
                    if nodes:
                        st.markdown("### Graph Visualization")
                        config = Config(width=800, height=500, directed=True, 
                                        physics=True, hierarchical=False)
                        agraph(nodes=nodes, edges=edges, config=config)
                    else:
                        st.info("Graph is empty.")
                else:
                    st.error(f"Error fetching graph: {res_graph.text}")
                    
            except Exception as e:
                st.error(f"API Connection Error: {e}")
