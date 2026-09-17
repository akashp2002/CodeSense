import os
import re
import stat
import shutil
from pathlib import Path
from git import Repo

# Backend/repos/ (go up from ingestion/ -> codesense/ -> src/ -> Backend/)
REPOS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "repos"

def parse_github_url(url: str) -> str:
    """Extract the repository name from a GitHub URL."""
    url = url.strip().rstrip("/").removesuffix(".git")
    parts = url.split("/")
    if len(parts) >= 2:
        return parts[-1]
    raise ValueError(f"Could not parse repository name from URL: {url}")

def _remove_readonly(func, path, _):
    """Error handler for shutil.rmtree on Windows (git marks files read-only)."""
    os.chmod(path, stat.S_IWRITE)
    func(path)

def clone_repository(github_url: str) -> dict:
    """
    Clone a GitHub repository into the local repos/ directory.
    Returns a dict with repo_name and local_path.
    """
    repo_name = parse_github_url(github_url)
    local_path = REPOS_DIR / repo_name

    # If the repo already exists, remove it and re-clone
    if local_path.exists():
        shutil.rmtree(local_path, onerror=_remove_readonly)

    REPOS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Cloning {github_url} into {local_path}...")
    Repo.clone_from(github_url, str(local_path), depth=1)
    
    # Count Python files
    py_files = list(local_path.rglob("*.py"))
    
    return {
        "repo_name": repo_name,
        "local_path": str(local_path),
        "file_count": len(py_files),
    }
