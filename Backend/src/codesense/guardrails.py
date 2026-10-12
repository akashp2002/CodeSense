import os
import re
import threading
import unicodedata
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

_HOMOGLYPH_MAP = {
    'а': 'a', 'е': 'e', 'і': 'i', 'ο': 'o', 'р': 'p', 'с': 'c', 'х': 'x',
    'у': 'y', 'һ': 'h', 'Α': 'A', 'Β': 'B', 'Ε': 'E', 'Η': 'H', 'Ι': 'I',
    'Κ': 'K', 'Ο': 'O', 'Ρ': 'P', 'Τ': 'T', 'Χ': 'X', 'Υ': 'Y',
}


def _normalize_for_guardrails(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    for source, replacement in _HOMOGLYPH_MAP.items():
        normalized = normalized.replace(source, replacement)
    normalized = normalized.casefold()
    normalized = normalized.replace("```", " ")
    normalized = normalized.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    normalized = normalized.replace("\ufeff", "")
    normalized = re.sub(r"[\s\-_`~]+", " ", normalized)
    return normalized.strip()


_INPUT_OVERRIDE_PATTERNS = (
    re.compile(r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+)?(?:previous|prior|system|developer|hidden|initial)\s+(?:instru(?:ction|ctions)|prompt|rules?|safeguards?)\b"),
    re.compile(r"\b(?:reveal|show|print|repeat|display|echo|expose)\s+(?:the\s+)?(?:system|developer|hidden|internal)\s+(?:prompt|instructions?|message)\b"),
    re.compile(r"\b(?:bypass|disable|skip|override|remove|silence)\s+(?:the\s+)?(?:guardrails?|safety|approval|policy|checks?)\b"),
    re.compile(r"\b(?:pretend|act)\s+(?:you\s+are|as)\s+(?:the\s+)?(?:developer|system|admin|owner|operator)\b"),
    re.compile(r"\b(?:developer|system)\s+(?:override|directive|instruction|prompt)\b"),
    re.compile(r"\b(?:ignore|disregard|forget)\s+(?:all|any|previous|prior|system|developer)\s+(?:meta|hidden|internal)?\s*(?:instructions?|rules?|prompts?)\b"),
    re.compile(r"\b(?:you\s+are\s+now|from\s+now\s+on|for\s+all\s+future\s+requests)\b.*\b(?:ignore|override|disregard|forget)\b"),
)
_SECRET_PATTERNS = (
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ASIA|A3T|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|AWS)[A-Z0-9]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b"),
    re.compile(r"(?i)\b(?:jwt|token)\s*[:=]\s*['\"]?eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9._~+/=-]+['\"]?"),
    re.compile(r"(?i)\b(?:session|refresh|auth|access)[_-]?(?:id|token|cookie)\b\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{16,}['\"]?"),
    re.compile(r"(?i)\b(?:refresh[_-]?token|refresh_token|rt)\b\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{16,}['\"]?"),
    re.compile(r"(?i)\b(?:aws_session_token|session_token|aws_sessiontoken)\b\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{16,}['\"]?"),
    re.compile(
        r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|password|session[_-]?cookie)\b"
        r"(\s*[:=]\s*)(['\"]?)[A-Za-z0-9_./+=:-]{8,}\3"
    ),
    re.compile(r"(?i)(?:https?://[^\s\"'<>]*?(?:token|api[_-]?key|secret|password|access[_-]?token|refresh[_-]?token|session)[^\s\"'<>]*=[A-Za-z0-9._~+/%=-]{8,})"),
    re.compile(r"(?i)(?:\b(?:token|api[_-]?key|secret|password|access[_-]?token|refresh[_-]?token|session)[A-Za-z0-9_-]*\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{8,}['\"]?)"),
    re.compile(r"(?i)\b(?:xox[baprs]-[A-Za-z0-9-]{10,}|sk_live_[A-Za-z0-9+/=]{16,}|sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{35})\b"),
    re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----.*?"
        r"-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
        re.S,
    ),
    re.compile(
        r"-----BEGIN OPENSSH PRIVATE KEY-----.*?-----END OPENSSH PRIVATE KEY-----",
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

    normalized_question = _normalize_for_guardrails(question)
    if any(pattern.search(normalized_question) for pattern in _INPUT_OVERRIDE_PATTERNS):
        raise GuardrailViolation("The query appears to request bypassing system instructions or safeguards.")

    if (
        any(word in normalized_question for word in ("ignore", "disregard", "forget"))
        and any(token in normalized_question for token in ("instruction", "prompt", "system", "developer", "safety", "guardrail"))
    ) or (
        any(word in normalized_question for word in ("pretend", "act"))
        and any(token in normalized_question for token in ("developer", "system", "admin", "owner"))
    ):
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
