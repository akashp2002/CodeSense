import os
import re
import threading
from pathlib import Path
from typing import Any


MAX_INPUT_CHARS = 4_000
MAX_ANSWER_CHARS = 12_000
MAX_DIFF_CHARS = 50_000
MAX_TOOL_CALLS = {"refactor": 6, "push": 4}

TOOL_ALLOWLIST = {
    "refactor": {
        "list_files",
        "search_file",
        "read_file",
        "replace_in_file",
        "rename_symbol",
        "get_git_diff",
    },
    "push": {
        "get_git_diff",
        "create_branch",
        "commit_changes",
        "push_branch",
        "create_pull_request",
    },
}

_INPUT_OVERRIDE_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions\b", re.I),
    re.compile(r"\b(?:reveal|show|print|repeat)\s+(?:the\s+)?system\s+prompt\b", re.I),
    re.compile(r"\b(?:bypass|disable|skip)\s+(?:the\s+)?(?:guardrails?|safety|approval)\b", re.I),
)
_SECRET_PATTERNS = (
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|password)\b"
        r"(\s*[:=]\s*)(['\"]?)[A-Za-z0-9_./+=:-]{8,}\3"
    ),
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
        r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        re.S,
    ),
)


class GuardrailViolation(ValueError):
    """Raised when a request or proposed action violates a guardrail."""


def validate_repository_path(repo_root: Path, path: str | Path) -> Path:
    root = repo_root.resolve()
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        relative_path = resolved.relative_to(root)
    except ValueError as error:
        raise GuardrailViolation("Tool file paths must remain inside the selected repository.") from error

    for part in relative_path.parts:
        lowered = part.lower()
        if (
            lowered == ".git"
            or lowered == ".env"
            or lowered.startswith(".env.")
            or lowered in {"id_rsa", "id_ed25519", "credentials", "secrets"}
            or Path(lowered).suffix in {".pem", ".key", ".p12", ".pfx"}
        ):
            raise GuardrailViolation("Tools cannot access credential or repository metadata files.")
    return resolved


def validate_user_input(value: str) -> str:
    if not isinstance(value, str):
        raise GuardrailViolation("The query must be text.")

    question = value.strip()
    if not question:
        raise GuardrailViolation("Enter a question before submitting.")
    if len(question) > MAX_INPUT_CHARS:
        raise GuardrailViolation(f"Queries cannot exceed {MAX_INPUT_CHARS} characters.")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in question):
        raise GuardrailViolation("The query contains unsupported control characters.")
    if any(pattern.search(question) for pattern in _INPUT_OVERRIDE_PATTERNS):
        raise GuardrailViolation("The query appears to request bypassing system instructions or safeguards.")
    return question


def sanitize_output(value: str, max_chars: int = MAX_ANSWER_CHARS) -> str:
    output = value if isinstance(value, str) else str(value)
    for pattern in _SECRET_PATTERNS:
        output = pattern.sub("[REDACTED]", output)
    if len(output) > max_chars:
        output = output[:max_chars] + "\n\n[Output truncated by CodeSense guardrails.]"
    return output


def contains_sensitive_data(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


def sanitize_data(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_output(value)
    if isinstance(value, list):
        return [sanitize_data(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_data(item) for key, item in value.items()}
    return value


_tool_call_counts: dict[str, int] = {}
_tool_call_lock = threading.Lock()

def authorize_tool_call(tool_name: str, phase: str | None = None) -> None:
    active_phase = phase or os.getenv("CODESENSE_TOOL_PHASE", "refactor")
    if active_phase not in TOOL_ALLOWLIST or tool_name not in TOOL_ALLOWLIST[active_phase]:
        raise GuardrailViolation(f"Tool '{tool_name}' is not allowed during the {active_phase} phase.")
    if active_phase == "push" and os.getenv("CODESENSE_TOOL_APPROVED") != "1":
        raise GuardrailViolation("External repository actions require explicit human approval.")

    with _tool_call_lock:
        call_count = _tool_call_counts.get(active_phase, 0) + 1
        if call_count > MAX_TOOL_CALLS[active_phase]:
            raise GuardrailViolation(f"The {active_phase} phase exceeded its tool-call limit.")
        _tool_call_counts[active_phase] = call_count


def reset_tool_call_budget() -> None:
    with _tool_call_lock:
        _tool_call_counts.clear()
