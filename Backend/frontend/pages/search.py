import streamlit as st
import requests

st.set_page_config(page_title="Semantic Search", page_icon="🔍")

st.title("🔍 Semantic Search")
st.markdown("Search your codebase using natural language, powered by vector embeddings.")

query = st.text_input("Search Query", placeholder="Where is the vector search implemented?")
limit = st.slider("Max Results", 1, 20, 5)

if st.button("Search"):
    if not query:
        st.warning("Please enter a query.")
    else:
        with st.spinner("Searching..."):
            try:
                response = requests.get(f"http://localhost:8000/search", params={"q": query, "limit": limit})
                if response.status_code == 200:
                    results = response.json().get("results", [])
                    if not results:
                        st.info("No results found.")
                    else:
                        for res in results:
                            with st.expander(f"[{res['rank']}] {res['chunk_type'].upper()} **{res['symbol_name']}** in `{res['file_path']}` (Lines {res['line_range']})"):
                                st.code(res['snippet'], language='python')
                else:
                    st.error(f"Error: {response.text}")
            except Exception as e:
                st.error(f"API Connection Error: {e}")
