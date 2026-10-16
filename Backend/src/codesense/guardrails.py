import os
import copy
import json
import logging
import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_INPUT_CHARS = 4_000
MAX_ANSWER_CHARS = 12_000
MAX_DIFF_CHARS = 50_000
MAX_TOOL_CALLS = {"refactor": 6, "push": 4}
_AUDIT_INPUT_CHARS = 4_000
_audit_logger = logging.getLogger("codesense.audit")
_TRUNCATION_MARKER = "[Output truncated by CodeSense guardrails.]"
_DIFF_START = re.compile(r"(?m)^diff --git ")

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


def log_guardrail_denial(
    action: str,
    blocked_input: Any,
    reason: str,
    *,
    user_id: str | int | None = None,
    session_id: str | None = None,
) -> None:
    """Write a structured, secret-redacted audit event for a denied action."""
    event = {
        "event": "guardrail_denied",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": str(user_id) if user_id is not None else os.getenv("CODESENSE_AUDIT_USER_ID"),
        "session_id": session_id or os.getenv("CODESENSE_AUDIT_SESSION_ID"),
        "action": action,
        "blocked_input": sanitize_output(
            blocked_input if isinstance(blocked_input, str) else repr(blocked_input),
            _AUDIT_INPUT_CHARS,
        ),
        "reason": sanitize_output(reason, 1_000),
    }
    _audit_logger.warning(json.dumps(event, ensure_ascii=True, sort_keys=True))


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


def _serialize_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


_SENSITIVE_JSON_KEY = re.compile(
    r"(?i)^(?:api[_-]?key|secret(?:[_-]?key)?|password|passwd|token|jwt|"
    r"authorization|cookie|session[_-]?(?:id|token|cookie)|"
    r"(?:access|refresh|auth)[_-]?(?:token|cookie|id)|"
    r"(?:aws[_-]?)?session[_-]?token|(?:ssh[_-]?)?private[_-]?key)$"
)


def _sanitize_json_values(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, child in value.items():
            if isinstance(key, str) and _SENSITIVE_JSON_KEY.fullmatch(key) and child is not None:
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = _sanitize_json_values(child)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_json_values(child) for child in value]
    if isinstance(value, str):
        for pattern in _SECRET_PATTERNS:
            value = pattern.sub("[REDACTED]", value)
    return value


def _shrink_json_value(value: Any) -> bool:
    """Remove the least useful tail content while keeping the value valid JSON."""
    if isinstance(value, dict):
        nested = sorted(
            (
                key for key in value
                if key not in {"_codesense_truncated", "_codesense_summary"}
            ),
            key=lambda key: len(_serialize_json(value[key])),
            reverse=True,
        )
        for key in nested:
            child = value[key]
            if isinstance(child, str) and len(child) > 2:
                value[key] = child[: max(1, len(child) // 2)] + "…"
                return True
            if _shrink_json_value(child):
                return True
        removable = next(
            (
                key for key in reversed(list(value))
                if key not in {"_codesense_truncated", "_codesense_summary"}
            ),
            None,
        )
        if removable is not None:
            del value[removable]
            return True
        return False

    if isinstance(value, list):
        is_truncated = bool(
            value
            and isinstance(value[-1], dict)
            and value[-1].get("_codesense_truncated") is True
        )
        content_length = len(value) - int(is_truncated)
        nested = sorted(
            range(content_length),
            key=lambda index: len(_serialize_json(value[index])),
            reverse=True,
        )
        for index in nested:
            child = value[index]
            if isinstance(child, str) and len(child) > 2:
                value[index] = child[: max(1, len(child) // 2)] + "…"
                return True
            if _shrink_json_value(child):
                return True
        if content_length:
            del value[content_length - 1]
            return True
        return False

    return False


def _truncate_json(value: Any, max_chars: int, original_length: int) -> str:
    summary = f"Output shortened; {original_length} original characters."
    if max_chars < len('{"_codesense_truncated":true}'):
        return "0"

    if isinstance(value, dict):
        candidate = copy.deepcopy(value)
        candidate["_codesense_truncated"] = True
        candidate["_codesense_summary"] = summary
    elif isinstance(value, list):
        candidate = copy.deepcopy(value)
        candidate.append({"_codesense_truncated": True, "_codesense_summary": summary})
    else:
        candidate = {
            "_codesense_truncated": True,
            "_codesense_summary": summary,
            "preview": value,
        }

    while len(_serialize_json(candidate)) > max_chars:
        if not _shrink_json_value(candidate):
            break

    serialized = _serialize_json(candidate)
    if len(serialized) <= max_chars:
        return serialized
    fallback = {"_codesense_truncated": True, "_codesense_summary": summary}
    serialized = _serialize_json(fallback)
    return serialized if len(serialized) <= max_chars else "0"


def _text_boundary(text: str, limit: int) -> int:
    boundary = max(
        text.rfind("\n\n", 0, limit),
        text.rfind("\n", 0, limit),
    )
    if boundary >= limit // 2:
        return boundary
    boundary = text.rfind(" ", 0, limit)
    return boundary if boundary >= limit // 2 else limit


def _truncate_markdown(text: str, max_chars: int) -> str:
    marker = _TRUNCATION_MARKER
    if max_chars <= len(marker):
        return marker[:max_chars]

    content_limit = max_chars - 48
    boundary = _text_boundary(text, content_limit)
    content = text[:boundary].rstrip()
    if not content:
        content = text[:content_limit]

    while True:
        fence_count = len(re.findall(r"(?m)^\s*```", content))
        closing_fence = "\n```" if fence_count % 2 else ""
        omitted = len(text) - len(content)
        truncation_note = f"[Output truncated; {omitted} characters omitted.]"
        result = content + closing_fence + "\n\n" + truncation_note
        if len(result) <= max_chars:
            return result
        if not content:
            return truncation_note[:max_chars]
        excess = len(result) - max_chars
        next_limit = max(0, len(content) - excess - 1)
        next_content = text[:_text_boundary(text, next_limit)].rstrip()
        if len(next_content) >= len(content):
            next_content = content[:max(0, len(content) - excess - 1)].rstrip()
        content = next_content


def _truncate_diff(text: str, max_chars: int) -> str:
    sections = _DIFF_START.split(text)
    if len(sections) < 2:
        return _truncate_markdown(text, max_chars)

    patches = [
        "diff --git " + section
        for section in sections[1:]
    ]
    selected = []
    for patch in patches:
        candidate = "\n".join([*selected, patch]).rstrip()
        if len(candidate) + 85 > max_chars:
            break
        selected.append(patch)

    omitted_files = len(patches) - len(selected)
    summary = (
        f"[Diff truncated by CodeSense guardrails; {omitted_files} file(s) omitted.]"
    )
    if not selected:
        return summary[:max_chars]

    content = "\n".join(selected).rstrip()
    return f"{content}\n\n{summary}"


def sanitize_output(
    value: str, max_chars: int = MAX_ANSWER_CHARS, *, format_hint: str | None = None
) -> str:
    output = value if isinstance(value, str) else str(value)
    if max_chars <= 0:
        return ""

    parsed_json = None
    is_json = False
    try:
        parsed_json = json.loads(output)
        is_json = True
    except json.JSONDecodeError:
        pass

    if is_json:
        sanitized_json = _sanitize_json_values(parsed_json)
        if sanitized_json != parsed_json:
            output = json.dumps(sanitized_json, ensure_ascii=False)
    else:
        for pattern in _SECRET_PATTERNS:
            output = pattern.sub("[REDACTED]", output)

    if len(output) > max_chars:
        detected_format = format_hint or ("json" if is_json else None)
        if detected_format is None:
            detected_format = "diff" if _DIFF_START.search(output) else "markdown"

        if detected_format == "json":
            try:
                output = _truncate_json(json.loads(output), max_chars, len(output))
            except json.JSONDecodeError:
                output = _truncate_markdown(output, max_chars)
        elif detected_format == "diff":
            output = _truncate_diff(output, max_chars)
        else:
            output = _truncate_markdown(output, max_chars)
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
        reason = f"Tool '{tool_name}' is not allowed during the {active_phase} phase."
        log_guardrail_denial(
            "tool_call",
            {"tool": tool_name, "phase": active_phase},
            reason,
        )
        raise GuardrailViolation(reason)
    if active_phase == "push" and os.getenv("CODESENSE_TOOL_APPROVED") != "1":
        reason = "External repository actions require explicit human approval."
        log_guardrail_denial(
            "tool_call",
            {"tool": tool_name, "phase": active_phase},
            reason,
        )
        raise GuardrailViolation(reason)

    with _tool_call_lock:
        call_count = _tool_call_counts.get(active_phase, 0) + 1
        if call_count > MAX_TOOL_CALLS[active_phase]:
            reason = f"The {active_phase} phase exceeded its tool-call limit."
            log_guardrail_denial(
                "tool_call",
                {"tool": tool_name, "phase": active_phase},
                reason,
            )
            raise GuardrailViolation(reason)
        _tool_call_counts[active_phase] = call_count


def reset_tool_call_budget() -> None:
    with _tool_call_lock:
        _tool_call_counts.clear()
