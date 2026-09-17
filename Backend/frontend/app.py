import streamlit as st
import requests
import time

API_BASE = "http://localhost:8000"

st.set_page_config(page_title="CodeSense AI", page_icon="🧠", layout="wide")

# ── Session State Initialization ──
if "repo_ready" not in st.session_state:
    st.session_state.repo_ready = False
if "repo_name" not in st.session_state:
    st.session_state.repo_name = ""
if "repo_path" not in st.session_state:
    st.session_state.repo_path = ""
if "repo_stats" not in st.session_state:
    st.session_state.repo_stats = {}
if "messages" not in st.session_state:
    st.session_state.messages = []

# ── Header ──
st.title("🧠 CodeSense")
st.markdown("Understand your codebase with AI")

# ════════════════════════════════════════════════════════
# STATE 1: No repository connected → Show URL input
# ════════════════════════════════════════════════════════
if not st.session_state.repo_ready:
    st.markdown("---")
    st.subheader("Connect a Repository")

    github_url = st.text_input(
        "GitHub Repository",
        placeholder="https://github.com/user/my-project",
        label_visibility="visible"
    )

    if st.button("🔍 Analyze Repository", type="primary", use_container_width=True):
        if not github_url:
            st.warning("Please enter a GitHub repository URL.")
        else:
            progress = st.container()

            # ── Step 1: Clone ──
            with progress:
                with st.status("Cloning repository...", expanded=True) as status:
                    st.write("Validating repository URL...")
                    try:
                        res = requests.post(f"{API_BASE}/clone", json={"github_url": github_url}, timeout=120)
                        if res.status_code != 200:
                            st.error(f"Failed to clone: {res.json().get('detail', res.text)}")
                            status.update(label="❌ Clone failed", state="error")
                            st.stop()
                        
                        clone_data = res.json()
                        repo_name = clone_data["repo_name"]
                        repo_path = clone_data["local_path"]
                        file_count = clone_data["file_count"]
                        
                        st.write(f"✅ Repository cloned — **{file_count}** Python files found")
                        status.update(label="✅ Repository cloned", state="complete")
                    except requests.exceptions.ConnectionError:
                        st.error("Could not connect to CodeSense API. Is FastAPI running on port 8000?")
                        status.update(label="❌ Connection failed", state="error")
                        st.stop()
                    except Exception as e:
                        st.error(f"Error: {e}")
                        status.update(label="❌ Clone failed", state="error")
                        st.stop()

            # ── Step 2: Vector Index ──
            with progress:
                with st.status("Building semantic index...", expanded=True) as status:
                    st.write("Parsing AST and embedding code chunks...")
                    try:
                        res = requests.post(f"{API_BASE}/index-vectors", json={"repo_path": repo_path}, timeout=300)
                        if res.status_code != 200:
                            st.error(f"Vector indexing failed: {res.json().get('detail', res.text)}")
                            status.update(label="❌ Semantic index failed", state="error")
                            st.stop()
                        
                        chunk_count = res.json()["chunk_count"]
                        st.write(f"✅ **{chunk_count}** code chunks indexed")
                        status.update(label=f"✅ {chunk_count} code chunks indexed", state="complete")
                    except Exception as e:
                        st.error(f"Error: {e}")
                        status.update(label="❌ Semantic index failed", state="error")
                        st.stop()

            # ── Step 3: Graph Index ──
            with progress:
                with st.status("Building dependency graph...", expanded=True) as status:
                    st.write("Extracting call relationships and building Neo4j graph...")
                    try:
                        res = requests.post(f"{API_BASE}/index-graph", json={"repo_path": repo_path}, timeout=300)
                        if res.status_code != 200:
                            st.error(f"Graph indexing failed: {res.json().get('detail', res.text)}")
                            status.update(label="❌ Dependency graph failed", state="error")
                            st.stop()
                        
                        graph_data = res.json()
                        symbol_count = graph_data["symbol_count"]
                        rel_count = graph_data["relationship_count"]
                        st.write(f"✅ **{symbol_count}** symbols, **{rel_count}** relationships indexed")
                        status.update(label=f"✅ {rel_count} relationships indexed", state="complete")
                    except Exception as e:
                        st.error(f"Error: {e}")
                        status.update(label="❌ Dependency graph failed", state="error")
                        st.stop()

            # ── All done! Save state and rerun ──
            st.session_state.repo_ready = True
            st.session_state.repo_name = repo_name
            st.session_state.repo_path = repo_path
            st.session_state.repo_stats = {
                "files": file_count,
                "chunks": chunk_count,
                "symbols": symbol_count,
                "relationships": rel_count,
            }
            st.balloons()
            time.sleep(1)
            st.rerun()

# ════════════════════════════════════════════════════════
# STATE 2: Repository is ready → Show stats + chat
# ════════════════════════════════════════════════════════
else:
    stats = st.session_state.repo_stats

    # ── Repository info bar ──
    st.success(
        f"🟢 **Repository: {st.session_state.repo_name}**  ·  "
        f"{stats.get('files', '?')} files  ·  "
        f"{stats.get('chunks', '?')} chunks  ·  "
        f"{stats.get('symbols', '?')} symbols  ·  "
        f"{stats.get('relationships', '?')} dependencies"
    )

    # Button to disconnect and start over
    if st.button("Disconnect Repository"):
        st.session_state.repo_ready = False
        st.session_state.repo_name = ""
        st.session_state.repo_path = ""
        st.session_state.repo_stats = {}
        st.session_state.messages = []
        st.rerun()

    st.markdown("---")
    st.subheader("💬 Ask CodeSense")
    st.markdown("You can now ask questions about your codebase.")

    # Display chat messages from history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # React to user input
    if prompt := st.chat_input("Ask a question about your codebase..."):
        st.chat_message("user").markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("assistant"):
            with st.spinner("Analyzing..."):
                try:
                    response = requests.post(f"{API_BASE}/query", json={"question": prompt}, timeout=120)
                    if response.status_code == 200:
                        answer = response.json().get("answer", "No answer returned.")
                        st.markdown(answer)
                        st.session_state.messages.append({"role": "assistant", "content": answer})
                    else:
                        st.error(f"Error: {response.text}")
                except requests.exceptions.ConnectionError:
                    st.error("Could not connect to CodeSense API. Is FastAPI running?")
                except Exception as e:
                    st.error(f"Failed: {e}")
