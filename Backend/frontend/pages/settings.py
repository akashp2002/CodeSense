import streamlit as st
import requests

API_BASE = "http://localhost:8000"

st.set_page_config(page_title="Settings", page_icon="⚙️")

st.title("⚙️ Settings")

# ── API Health ──
st.subheader("API Status")
try:
    res = requests.get(f"{API_BASE}/health", timeout=5)
    if res.status_code == 200:
        st.success("✅ CodeSense API is online")
    else:
        st.error(f"❌ API returned status code {res.status_code}")
except Exception:
    st.error("❌ Could not connect to CodeSense API")

st.divider()

# ── Current Repository ──
st.subheader("Current Repository")
if st.session_state.get("repo_ready"):
    st.info(f"**{st.session_state.get('repo_name', 'Unknown')}** — `{st.session_state.get('repo_path', '')}`")
    
    if st.button("🗑️ Clear & Disconnect Repository"):
        st.session_state.repo_ready = False
        st.session_state.repo_name = ""
        st.session_state.repo_path = ""
        st.session_state.repo_stats = {}
        st.session_state.messages = []
        st.success("Repository disconnected. Return to the home page to connect a new one.")
else:
    st.warning("No repository connected. Go to the home page to connect one.")

st.divider()

# ── Debug Info ──
st.subheader("Debug Information")
with st.expander("Session State"):
    st.json({
        "repo_ready": st.session_state.get("repo_ready", False),
        "repo_name": st.session_state.get("repo_name", ""),
        "repo_path": st.session_state.get("repo_path", ""),
        "repo_stats": st.session_state.get("repo_stats", {}),
        "message_count": len(st.session_state.get("messages", [])),
    })
