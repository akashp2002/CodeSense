from mcp.server.fastmcp import FastMCP
import os
import subprocess
import sys
from pathlib import Path
from github import Github
from github import Auth
import tree_sitter_python as tspython
from tree_sitter import Language, Parser

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


def _walk_tree(node):
    yield node
    for child in node.children:
        yield from _walk_tree(child)


def _parameter_binding_nodes(parameters):
    for parameter in parameters.children:
        if parameter.type == "identifier":
            yield parameter
        elif parameter.type in {"default_parameter", "typed_parameter", "typed_default_parameter"}:
            name_node = parameter.child_by_field_name("name")
            if name_node:
                yield name_node


def _rename_identifier_nodes(source: bytes, old_name: str, new_name: str):
    parser = Parser(Language(tspython.language()))
    tree = parser.parse(source)
    root = tree.root_node
    scopes = {id(root): {"parent": None, "bindings": {}}}
    node_scopes = {}
    definitions = []

    def add_binding(scope, name, node):
        scopes[id(scope)]["bindings"].setdefault(name, []).append(node)

    def collect(node, scope):
        node_scopes[id(node)] = scope

        if node.type in ("function_definition", "class_definition"):
            name_node = node.child_by_field_name("name")
            if name_node:
                name = source[name_node.start_byte:name_node.end_byte].decode("utf-8")
                add_binding(scope, name, name_node)
                if name == old_name:
                    definitions.append(name_node)

            child_scope = node
            scopes[id(child_scope)] = {"parent": scope, "bindings": {}}
            parameters = node.child_by_field_name("parameters")
            if parameters:
                for parameter in _parameter_binding_nodes(parameters):
                    add_binding(child_scope, parameter.text.decode("utf-8"), parameter)
            for child in node.children:
                collect(child, child_scope)
            return

        for child in node.children:
            collect(child, scope)

    collect(root, root)
    if len(definitions) != 1:
        return tree, [], len(definitions)

    target_definition = definitions[0]
    replacements = []
    for node in _walk_tree(root):
        if node.type != "identifier" or node.text.decode("utf-8") != old_name:
            continue
        parent = node.parent
        if parent and parent.type == "attribute" and parent.child_by_field_name("attribute") == node:
            continue

        scope = node_scopes[id(node)]
        binding = None
        while scope is not None:
            candidates = scopes[id(scope)]["bindings"].get(old_name, [])
            if candidates:
                binding = candidates[-1]
                break
            scope = scopes[id(scope)]["parent"]
        if binding is target_definition:
            replacements.append((node.start_byte, node.end_byte))

    return tree, replacements, len(definitions)


def _module_names_for_file(repo_path: Path, file_path: Path) -> set[str]:
    relative_parts = list(file_path.relative_to(repo_path).with_suffix("").parts)
    if relative_parts[-1] == "__init__":
        relative_parts.pop()
    return {".".join(relative_parts[index:]) for index in range(len(relative_parts))}


def _import_reference_replacements(
    source: bytes,
    module_names: set[str],
    old_name: str,
    new_name: str,
):
    parser = Parser(Language(tspython.language()))
    root = parser.parse(source).root_node
    imported_bindings = []
    import_replacements = []

    for node in _walk_tree(root):
        if node.type != "import_from_statement":
            continue
        module_node = node.child_by_field_name("module_name")
        name_node = node.child_by_field_name("name")
        if not module_node or not name_node:
            continue
        module_name = module_node.text.decode("utf-8")
        if module_name not in module_names:
            continue

        imported_node = name_node
        local_name = old_name
        if name_node.type == "aliased_import":
            imported_node = name_node.child_by_field_name("name")
            alias_node = name_node.child_by_field_name("alias")
            if alias_node:
                local_name = alias_node.text.decode("utf-8")
        if not imported_node or imported_node.text.decode("utf-8") != old_name:
            continue

        import_replacements.append((imported_node.start_byte, imported_node.end_byte))
        imported_bindings.append((local_name, imported_node))

    if not imported_bindings:
        return []

    replacements = list(import_replacements)
    for node in _walk_tree(root):
        if node.type != "identifier" or node.text.decode("utf-8") not in {
            binding[0] for binding in imported_bindings
        }:
            continue
        if any(node.start_byte == start and node.end_byte == end for start, end in import_replacements):
            continue
        parent = node.parent
        if parent and parent.type == "attribute" and parent.child_by_field_name("attribute") == node:
            continue

        shadowed = False
        ancestor = parent
        while ancestor:
            if ancestor.type == "function_definition":
                parameters = ancestor.child_by_field_name("parameters")
                if parameters:
                    if any(parameter.text == node.text for parameter in _parameter_binding_nodes(parameters)):
                        shadowed = True
            ancestor = ancestor.parent
        if not shadowed:
            replacements.append((node.start_byte, node.end_byte))

    return replacements


def _apply_replacements(source: bytes, replacements: list[tuple[int, int]], new_name: str) -> bytes:
    updated = bytearray(source)
    for start_byte, end_byte in sorted(replacements, reverse=True):
        updated[start_byte:end_byte] = new_name.encode("utf-8")
    return bytes(updated)


def _validate_python_file(file_path: Path, repo_path: Path) -> str:
    compile_result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(file_path)],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if compile_result.returncode:
        raise RuntimeError(compile_result.stderr.strip() or "Python validation failed.")

    diff_result = subprocess.run(
        ["git", "--no-pager", "diff", "--check"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=False,
    )
    return diff_result.stdout.strip() or diff_result.stderr.strip()

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
                supported = {'.py', '.js', '.jsx', '.ts', '.tsx', '.go', '.rs', '.java'}
                if not any(file.endswith(ext) for ext in supported):
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
def rename_symbol(file_path: str, old_name: str, new_name: str) -> str:
    """Rename a Python function or class and its resolved imports across the repository."""
    if not old_name.isidentifier() or not new_name.isidentifier():
        return "Error: old_name and new_name must be valid Python identifiers."
    if old_name == new_name:
        return "Error: old_name and new_name must be different."

    repo_path = get_repo_path()
    full_path = (repo_path / file_path).resolve()
    if repo_path not in full_path.parents or full_path.suffix != ".py":
        return "Error: file_path must be a Python file inside the repository."

    try:
        original = full_path.read_bytes()
        _, replacements, definition_count = _rename_identifier_nodes(original, old_name, new_name)
        if definition_count == 0:
            return f"Error: no unique function or class definition named '{old_name}' found."
        if definition_count > 1:
            return f"Error: '{old_name}' has {definition_count} definitions in {file_path}; rename is ambiguous."
        if not replacements:
            return f"Error: no references resolved for '{old_name}' in {file_path}."

        changes = {full_path: (original, _apply_replacements(original, replacements, new_name), len(replacements))}
        module_names = _module_names_for_file(repo_path, full_path)
        for candidate in repo_path.rglob("*.py"):
            if candidate == full_path or any(part in {".venv", "__pycache__", ".git"} for part in candidate.parts):
                continue
            candidate_source = candidate.read_bytes()
            candidate_replacements = _import_reference_replacements(
                candidate_source, module_names, old_name, new_name
            )
            if candidate_replacements:
                changes[candidate] = (
                    candidate_source,
                    _apply_replacements(candidate_source, candidate_replacements, new_name),
                    len(candidate_replacements),
                )

        for changed_path, (_, updated, _) in changes.items():
            changed_path.write_bytes(updated)

        try:
            validation_warnings = []
            for changed_path in changes:
                validation_warning = _validate_python_file(changed_path, repo_path)
                if validation_warning:
                    validation_warnings.append(validation_warning)
        except Exception:
            for changed_path, (before, _, _) in changes.items():
                changed_path.write_bytes(before)
            raise

        warning = f" Validation warning: {'; '.join(validation_warnings)}" if validation_warnings else ""
        identifier_count = sum(change[2] for change in changes.values())
        return f"Renamed {identifier_count} resolved identifier(s) across {len(changes)} file(s).{warning}"
    except Exception as e:
        return f"Error renaming symbol: {e}"

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
