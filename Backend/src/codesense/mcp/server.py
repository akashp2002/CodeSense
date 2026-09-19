from mcp.server.fastmcp import FastMCP
import os
import subprocess
from pathlib import Path
from github import Github
from github import Auth

# Create a FastMCP server
mcp = FastMCP("CodeSense-Refactor-Tools")

def get_repo_path() -> Path:
    """Helper to get the current loaded repository path."""
    configured_path = os.getenv("CODESENSE_REPO_PATH")
    if configured_path:
        repo_dir = Path(configured_path).resolve()
    else:
        base_dir = Path(os.getcwd())
        repo_dir = base_dir / "repos" / "demo"
    if not repo_dir.exists():
        # Fallback to current directory for testing
        return Path(os.getcwd())
    return repo_dir

@mcp.tool()
def list_files(directory: str = "") -> str:
    """List all Python files in the repository (or a subdirectory)."""
    repo_path = get_repo_path()
    target = repo_path / directory
    try:
        files = sorted(str(f.relative_to(repo_path)) for f in target.rglob("*.py")
                       if ".venv" not in str(f) and "__pycache__" not in str(f))
        return "\n".join(files) if files else "No Python files found."
    except Exception as e:
        return f"Error listing files: {e}"

@mcp.tool()
def search_file(query: str, path: str = "") -> str:
    """Search for a string (e.g. function name) across all Python files in the repository."""
    repo_path = get_repo_path()
    target = repo_path / path
    matches = []
    try:
        for root, _, files in os.walk(target):
            if ".venv" in root or "__pycache__" in root or ".git" in root:
                continue
            for file in files:
                if not file.endswith(".py"):
                    continue
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        for i, line in enumerate(f, 1):
                            if query in line:
                                rel_path = os.path.relpath(file_path, repo_path)
                                matches.append(f"{rel_path}:{i}: {line.strip()}")
                except Exception:
                    pass
        if not matches:
            return f"No matches found for '{query}'."
        if len(matches) > 30:
            return "\n".join(matches[:30]) + f"\n... and {len(matches) - 30} more matches"
        return "\n".join(matches)
    except Exception as e:
        return f"Error searching: {e}"

@mcp.tool()
def read_file(file_path: str) -> str:
    """Read the contents of a file in the repository, with line numbers included."""
    repo_path = get_repo_path()
    full_path = repo_path / file_path
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return "\n".join(f"{i+1:4d} | {line.rstrip()}" for i, line in enumerate(lines))
    except Exception as e:
        return f"Error reading file: {e}"

@mcp.tool()
def replace_in_file(file_path: str, old_text: str, new_text: str) -> str:
    """Replace every exact occurrence of old_text in a repository file."""
    repo_path = get_repo_path()
    full_path = repo_path / file_path

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        replacement_count = content.count(old_text)
        if replacement_count == 0:
            return f"No exact match found in {file_path}."

        updated_content = content.replace(old_text, new_text)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(updated_content)

        return f"Successfully replaced {replacement_count} occurrence(s) in {file_path}."
    except Exception as e:
        return f"Error replacing text: {e}"

@mcp.tool()
def replace_lines(file_path: str, start_line: int, end_line: int, replacement_content: str) -> str:
    """Replace a specific range of lines (1-indexed, inclusive) with new content."""
    repo_path = get_repo_path()
    full_path = repo_path / file_path
    
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        if start_line < 1 or end_line > len(lines) or start_line > end_line:
            return f"Error: Invalid line range {start_line}-{end_line}. File has {len(lines)} lines."
            
        # Lines are 1-indexed
        start_idx = start_line - 1
        end_idx = end_line
        
        # We need to make sure the replacement content has a trailing newline if it's meant to replace lines
        if replacement_content and not replacement_content.endswith('\n'):
            replacement_content += '\n'
            
        new_lines = lines[:start_idx] + [replacement_content] + lines[end_idx:]
        
        with open(full_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            
        return f"Successfully updated {file_path} (lines {start_line}-{end_line})"
    except Exception as e:
        return f"Error applying patch: {e}"

@mcp.tool()
def get_git_diff() -> str:
    """Get the git diff of the current repository."""
    repo_path = get_repo_path()
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            temp_name = f.name
            
        subprocess.run(
            f'git --no-pager diff > "{temp_name}" 2>&1',
            cwd=str(repo_path),
            shell=True,
            check=True
        )
        
        with open(temp_name, "r", encoding="utf-8") as f:
            output = f.read().strip()
            
        return output if output else "No changes."
    except Exception as e:
        return f"Error getting git diff: {e}"
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)

@mcp.tool()
def create_branch(branch_name: str) -> str:
    """Create and checkout a new git branch."""
    repo_path = get_repo_path()
    try:
        subprocess.run(["git", "--no-pager", "checkout", "-b", branch_name], cwd=str(repo_path), stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True)
        return f"Successfully created and checked out branch {branch_name}"
    except subprocess.CalledProcessError as e:
        return f"Error creating branch: {e.stderr}"

@mcp.tool()
def commit_changes(commit_message: str) -> str:
    """Stage all changes and commit them."""
    repo_path = get_repo_path()
    try:
        subprocess.run(["git", "--no-pager", "add", "."], cwd=str(repo_path), stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True)
        subprocess.run(["git", "--no-pager", "commit", "-m", commit_message], cwd=str(repo_path), stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True)
        return f"Successfully committed changes: {commit_message}"
    except subprocess.CalledProcessError as e:
        return f"Error committing changes: {e.stderr}"

@mcp.tool()
def push_branch(branch_name: str) -> str:
    """Push the current branch to origin."""
    repo_path = get_repo_path()
    try:
        subprocess.run(["git", "--no-pager", "push", "-u", "origin", branch_name], cwd=str(repo_path), stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True)
        return f"Successfully pushed branch {branch_name}"
    except subprocess.CalledProcessError as e:
        return f"Error pushing branch: {e.stderr}"

@mcp.tool()
def create_pull_request(repo_name: str, title: str, head_branch: str, base_branch: str, body: str) -> str:
    """Create a pull request on GitHub using PyGithub."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return "Error: GITHUB_TOKEN environment variable not set."
        
    try:
        auth = Auth.Token(token)
        g = Github(auth=auth)
        repo = g.get_repo(repo_name) # e.g., 'akashp2002/demo'
        
        pr = repo.create_pull(
            title=title,
            body=body,
            head=head_branch,
            base=base_branch
        )
        return f"Successfully created PR: {pr.html_url}"
    except Exception as e:
        return f"Error creating pull request: {e}"

if __name__ == "__main__":
    # Start the FastMCP server using stdio transport
    mcp.run(transport='stdio')
