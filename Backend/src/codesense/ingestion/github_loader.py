import os
import re
import stat
import shutil
import uuid
from pathlib import Path
from github import Auth, Github
from git import Repo
from git.exc import InvalidGitRepositoryError

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

    # If the repo already exists, update it instead of deleting (which fails due to DB locks)
    if local_path.exists():
        print(f"Repository {repo_name} already exists. Pulling latest changes...")
        try:
            repo = Repo(str(local_path))
            origin = repo.remotes.origin
            origin.fetch()
            # Find default branch from remote refs, usually main or master
            default_branch = "main"
            for ref in origin.refs:
                if ref.name == "origin/main" or ref.name == "origin/master":
                    default_branch = ref.name.split('/')[-1]
                    break
            
            # Reset hard to remote branch to discard any local modifications
            repo.git.reset('--hard', f"origin/{default_branch}")
            # Also clean untracked files
            repo.git.clean('-fd')
        except InvalidGitRepositoryError:
            print(f"Repository {repo_name} is corrupted (likely due to partial deletion). Re-initializing...")
            repo = Repo.init(str(local_path))
            if 'origin' in [r.name for r in repo.remotes]:
                origin = repo.remotes.origin
            else:
                origin = repo.create_remote('origin', github_url)
            origin.fetch()
            
            # Determine default branch
            default_branch = "origin/main"
            for ref in origin.refs:
                if ref.name == "origin/main" or ref.name == "origin/master":
                    default_branch = ref.name
                    break
                    
            repo.git.reset('--hard', default_branch)
            print(f"Successfully recovered {repo_name}.")
        except Exception as e:
            raise RuntimeError(f"Repository already exists but failed to update: {e}")
    else:
        REPOS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Cloning {github_url} into {local_path}...")
        Repo.clone_from(github_url, str(local_path), depth=1)
    
    # Count all supported source files
    supported_exts = {'.py', '.js', '.jsx', '.ts', '.tsx', '.go', '.rs', '.java'}
    source_files = [f for f in local_path.rglob("*") if f.suffix.lower() in supported_exts]
    
    return {
        "repo_name": repo_name,
        "local_path": str(local_path),
        "file_count": len(source_files),
    }


def delete_repository(local_path: str) -> None:
    """Delete a cloned repository managed by CodeSense."""
    repos_root = REPOS_DIR.resolve()
    target = Path(local_path).resolve()

    if target == repos_root or repos_root not in target.parents:
        raise ValueError("Only repositories inside the managed repos directory can be deleted.")

    if target.exists():
        shutil.rmtree(target, onerror=_remove_readonly)


def create_pull_request(local_path: str, prompt: str) -> str:
    """Commit the approved change, push a branch, and create its GitHub PR."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN environment variable is not set.")

    repo = Repo(local_path)
    if not repo.is_dirty(untracked_files=True) and not repo.active_branch.name.startswith("codesense/refactor-"):
        raise RuntimeError("There are no approved changes to commit.")

    remote_url = repo.remotes.origin.url.strip().rstrip("/")
    remote_path = remote_url.removesuffix(".git")
    if "github.com/" in remote_path:
        github_repo_name = remote_path.split("github.com/", 1)[1]
    elif "github.com:" in remote_path:
        github_repo_name = remote_path.split("github.com:", 1)[1]
    else:
        raise RuntimeError(f"Could not determine GitHub repository from remote: {remote_url}")

    github = Github(auth=Auth.Token(token))
    github_repo = github.get_repo(github_repo_name)
    base_branch = github_repo.default_branch

    current_branch = repo.active_branch.name
    if current_branch.startswith("codesense/refactor-"):
        branch_name = current_branch
    else:
        branch_name = f"codesense/refactor-{uuid.uuid4().hex[:8]}"
        repo.git.checkout("-b", branch_name)

    if repo.is_dirty(untracked_files=True):
        repo.git.add(A=True)
        repo.index.commit(f"Refactor: {prompt[:60]}")
    
    repo.git.push("--set-upstream", "origin", branch_name)

    pull_request = github_repo.create_pull(
        title=f"Refactor: {prompt[:80]}",
        body=f"Automated refactor requested through CodeSense:\n\n{prompt}",
        head=branch_name,
        base=base_branch,
    )
    return pull_request.html_url
