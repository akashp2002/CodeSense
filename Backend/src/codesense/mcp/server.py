from mcp.server.fastmcp import FastMCP
import os
import re
import subprocess
import sys
from functools import wraps
from pathlib import Path
from github import Github
from github import Auth
import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from codesense.guardrails import (
    GuardrailViolation,
    authorize_tool_call,
    sanitize_output,
    validate_repository_path,
)

# Create a FastMCP server
mcp = FastMCP("CodeSense-Refactor-Tools")
MAX_TOOL_FILE_BYTES = 1_000_000
MAX_TOOL_TEXT_CHARS = 50_000


def guarded_tool(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        authorize_tool_call(function.__name__)
        result = function(*args, **kwargs)
        return sanitize_output(result, MAX_TOOL_TEXT_CHARS) if isinstance(result, str) else result

    return guarded

def get_repo_path() -> Path:
    """Helper to get the current loaded repository path."""
    configured_path = os.getenv("CODESENSE_REPO_PATH")
    if configured_path:
        repo_dir = Path(configured_path).resolve()
    else:
        base_dir = Path(os.getcwd())
        repo_dir = base_dir / "repos" / "demo"
    if not repo_dir.is_dir():
        raise GuardrailViolation("No valid repository is configured for tool execution.")
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


def _definition_span(source: bytes, tree, name: str) -> tuple[int, int] | None:
    definitions = []
    for node in _walk_tree(tree.root_node):
        if node.type not in {"function_definition", "class_definition"}:
            continue
        name_node = node.child_by_field_name("name")
        if name_node and name_node.text.decode("utf-8") == name:
            definition = node.parent if node.parent and node.parent.type == "decorated_definition" else node
            line_start = source.rfind(b"\n", 0, definition.start_byte) + 1
            line_end = source.find(b"\n", definition.end_byte)
            if line_end == -1:
                line_end = len(source)
            else:
                line_end += 1
            definitions.append((line_start, line_end))
    return definitions[0] if len(definitions) == 1 else None


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


def _apply_edits(source: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    updated = bytearray(source)
    for start_byte, end_byte, replacement in sorted(edits, reverse=True):
        updated[start_byte:end_byte] = replacement
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
@guarded_tool
def list_files(directory: str = "") -> str:
    """List all Python files in the repository (or a subdirectory)."""
    repo_path = get_repo_path()
    target = _get_safe_path(repo_path, directory)
    try:
        files = sorted(str(f.relative_to(repo_path)) for f in target.rglob("*.py")
                       if ".venv" not in str(f) and "__pycache__" not in str(f))
        return "\n".join(files) if files else "No Python files found."
    except Exception as e:
        return f"Error listing files: {e}"

@mcp.tool()
@guarded_tool
def search_file(query: str, path: str = "") -> str:
    """Search for a string (e.g. function name) across all Python files in the repository."""
    if not query or len(query) > 500:
        raise GuardrailViolation("Search queries must contain 1 to 500 characters.")
    repo_path = get_repo_path()
    target = _get_safe_path(repo_path, path)
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
                    file_path = _get_safe_path(repo_path, file_path)
                except GuardrailViolation:
                    continue
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

def _get_safe_path(repo_path: Path, file_path: str) -> Path:
    return validate_repository_path(repo_path, file_path)

@mcp.tool()
@guarded_tool
def read_file(file_path: str) -> str:
    """Read the contents of a file in the repository, with line numbers included."""
    repo_path = get_repo_path()
    full_path = _get_safe_path(repo_path, file_path)
    try:
        if full_path.stat().st_size > MAX_TOOL_FILE_BYTES:
            raise GuardrailViolation("The requested file exceeds the 1 MB tool-read limit.")
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return "\n".join(f"{i+1:4d} | {line.rstrip()}" for i, line in enumerate(lines))
    except Exception as e:
        return f"Error reading file: {e}"

@mcp.tool()
@guarded_tool
def replace_in_file(file_path: str, search_string: str, replacement_string: str) -> str:
    """Replace an exact string in a file with a new string.
    
    This replaces ALL occurrences of search_string with replacement_string.
    For line replacements, include the exact whitespace/indentation.
    """
    repo_path = get_repo_path()
    full_path = _get_safe_path(repo_path, file_path)
    if not search_string or len(search_string) > MAX_TOOL_TEXT_CHARS:
        raise GuardrailViolation("Search text must contain 1 to 50,000 characters.")
    if len(replacement_string) > MAX_TOOL_TEXT_CHARS:
        raise GuardrailViolation("Replacement text cannot exceed 50,000 characters.")
    try:
        if full_path.stat().st_size > MAX_TOOL_FILE_BYTES:
            raise GuardrailViolation("The target file exceeds the 1 MB edit limit.")
        content = full_path.read_text(encoding="utf-8")
        if search_string not in content:
            return f"Error: '{search_string}' not found in {file_path}."
        
        if content.count(search_string) > 25:
            raise GuardrailViolation("The edit matches more than 25 locations; narrow the search text.")
        updated_content = content.replace(search_string, replacement_string)
        if len(updated_content.encode("utf-8")) > MAX_TOOL_FILE_BYTES:
            raise GuardrailViolation("The edited file would exceed the 1 MB tool limit.")
        full_path.write_text(updated_content, encoding="utf-8")
        return f"Successfully replaced occurrences in {file_path}."
    except Exception as e:
        return f"Error replacing in file: {e}"

@mcp.tool()
@guarded_tool
def rename_symbol(file_path: str, old_name: str, new_name: str) -> str:
    """Rename a Python function or class and its resolved imports across the repository."""
    if not old_name.isidentifier() or not new_name.isidentifier():
        return "Error: old_name and new_name must be valid Python identifiers."
    if old_name == new_name:
        return "Error: old_name and new_name must be different."

    repo_path = get_repo_path()
    full_path = _get_safe_path(repo_path, file_path)
    
    try:
        full_path.relative_to(repo_path.resolve())
    except ValueError:
        return "Error: file_path must be inside the repository."
        
    if full_path.is_file() and full_path.suffix != ".py":
        return "Error: file_path must be a Python file."
    if not full_path.is_file() and not full_path.is_dir() and full_path.suffix != ".py":
        return "Error: file_path must be a Python file or repository directory."

    try:
        original = b""
        replacements = []
        definition_count = 0
        if full_path.is_file():
            if full_path.stat().st_size > MAX_TOOL_FILE_BYTES:
                raise GuardrailViolation("The target file exceeds the 1 MB rename limit.")
            original = full_path.read_bytes()
            _, replacements, definition_count = _rename_identifier_nodes(
                original, old_name, new_name
            )

        if definition_count == 0:
            matches = []
            for candidate in repo_path.rglob("*.py"):
                relative_parts = candidate.relative_to(repo_path).parts
                if candidate == full_path or any(
                    part in {".venv", "__pycache__", ".git", "node_modules"}
                    for part in relative_parts
                ):
                    continue
                try:
                    candidate = validate_repository_path(repo_path, candidate)
                    if candidate.stat().st_size > MAX_TOOL_FILE_BYTES:
                        continue
                    candidate_source = candidate.read_bytes()
                    _, _, candidate_count = _rename_identifier_nodes(
                        candidate_source, old_name, new_name
                    )
                except (GuardrailViolation, OSError, UnicodeError):
                    continue
                if candidate_count:
                    matches.append((candidate, candidate_source, candidate_count))

            if len(matches) > 1 or (matches and matches[0][2] > 1):
                paths = ", ".join(
                    str(candidate.relative_to(repo_path)) for candidate, _, _ in matches
                )
                return (
                    f"Error: '{old_name}' has multiple definitions in the repository"
                    f"{f' ({paths})' if paths else ''}; rename is ambiguous."
                )
            if matches:
                full_path, original, definition_count = matches[0]
                _, replacements, definition_count = _rename_identifier_nodes(
                    original, old_name, new_name
                )
            else:
                return (
                    f"Error: no unique function or class definition named '{old_name}' "
                    "found in the repository."
                )
        if definition_count > 1:
            return f"Error: '{old_name}' has {definition_count} definitions in {file_path}; rename is ambiguous."
        tree, _, conflicting_definition_count = _rename_identifier_nodes(
            original, new_name, old_name
        )
        definition_to_remove = None
        if conflicting_definition_count:
            definition_to_remove = _definition_span(original, tree, old_name)
            if definition_to_remove is None:
                return f"Error: could not safely remove the '{old_name}' definition."
        edits = [
            (start, end, new_name.encode("utf-8"))
            for start, end in replacements
            if definition_to_remove is None
            or not (
                definition_to_remove[0] <= start
                and end <= definition_to_remove[1]
            )
        ]
        if definition_to_remove is not None:
            edits.append((*definition_to_remove, b""))
        if not edits:
            return f"Error: no references resolved for '{old_name}' in {file_path}."
        changes = {
            full_path: (
                original,
                _apply_edits(original, edits),
                len(edits) - (1 if definition_to_remove is not None else 0),
            )
        }
        module_names = _module_names_for_file(repo_path, full_path)
        for candidate in repo_path.rglob("*.py"):
            if candidate == full_path or any(part in {".venv", "__pycache__", ".git"} for part in candidate.parts):
                continue
            try:
                candidate = validate_repository_path(repo_path, candidate)
                if candidate.stat().st_size > MAX_TOOL_FILE_BYTES:
                    continue
            except (GuardrailViolation, OSError):
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
@guarded_tool
def get_git_diff() -> str:
    """Get the git diff of the current repository."""
    repo_path = get_repo_path()
    try:
        result = subprocess.run(
            ["git", "--no-pager", "diff"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip() if result.stdout.strip() else "No changes."
    except Exception as e:
        return f"Error getting git diff: {e}"

@mcp.tool()
@guarded_tool
def create_branch(branch_name: str) -> str:
    """Create and checkout a new git branch."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", branch_name) or ".." in branch_name:
        raise GuardrailViolation("Invalid branch name.")
    repo_path = get_repo_path()
    try:
        subprocess.run(["git", "--no-pager", "checkout", "-b", branch_name], cwd=str(repo_path), stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True)
        return f"Successfully created and checked out branch {branch_name}"
    except subprocess.CalledProcessError as e:
        return f"Error creating branch: {e.stderr}"

@mcp.tool()
@guarded_tool
def commit_changes(commit_message: str) -> str:
    """Stage all changes and commit them."""
    if not commit_message.strip() or len(commit_message) > 200:
        raise GuardrailViolation("Commit messages must contain 1 to 200 characters.")
    repo_path = get_repo_path()
    try:
        subprocess.run(["git", "--no-pager", "add", "."], cwd=str(repo_path), stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True)
        subprocess.run(["git", "--no-pager", "commit", "-m", commit_message], cwd=str(repo_path), stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True)
        return f"Successfully committed changes: {commit_message}"
    except subprocess.CalledProcessError as e:
        return f"Error committing changes: {e.stderr}"

@mcp.tool()
@guarded_tool
def push_branch(branch_name: str) -> str:
    """Push the current branch to origin."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", branch_name) or ".." in branch_name:
        raise GuardrailViolation("Invalid branch name.")
    repo_path = get_repo_path()
    try:
        subprocess.run(["git", "--no-pager", "push", "-u", "origin", branch_name], cwd=str(repo_path), stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True)
        return f"Successfully pushed branch {branch_name}"
    except subprocess.CalledProcessError as e:
        return f"Error pushing branch: {e.stderr}"

@mcp.tool()
@guarded_tool
def create_pull_request(repo_name: str, title: str, head_branch: str, base_branch: str, body: str) -> str:
    """Create a pull request on GitHub using PyGithub."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo_name):
        raise GuardrailViolation("Repository names must use the owner/repository format.")
    if not title.strip() or len(title) > 200 or len(body) > MAX_TOOL_TEXT_CHARS:
        raise GuardrailViolation("PR title or body is empty or exceeds its size limit.")
    if any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", branch) or ".." in branch
        for branch in (head_branch, base_branch)
    ):
        raise GuardrailViolation("Invalid PR branch name.")
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
