from codesense.cli import refresh_indexes, graph_index_repo
import sys

# Test graph index explicitly
try:
    print("Running graph index for repo 18...")
    graph_index_repo("repos/18/CLOUDFLARE_ASSISTANT")
    print("Finished successfully")
except Exception as e:
    print(f"Exception: {e}")
