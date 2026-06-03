"""Normalize file-path tool arguments (cross-tool, cross-platform).

Handles model mistakes like path='{"path":"src/foo.ts"}' and redundant
project-folder prefixes.  Used by read/write/edit and the executor.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .file_ledger import normalize_ledger_path, strip_redundant_project_prefix

logger = logging.getLogger(__name__)

PATH_ARG_KEYS = frozenset({"path", "source", "destination", "target", "dest"})

_MALFORMED_PATH_RE = re.compile(r'^\s*\{\s*"(?:path|source|destination|target)"')


def unwrap_embedded_json_path(raw: str, key: str = "path") -> str | None:
    """If *raw* is JSON (or truncated JSON) wrapping a path key, return the inner path."""
    if not isinstance(raw, str):
        return None
    sv = raw.strip()
    if not sv.startswith("{"):
        return None
    for candidate in (sv, sv + "}", sv + '"}', sv + '"}'):
        try:
            inner = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(inner, dict):
            for k in (key, "path", "source", "destination", "target", "dest"):
                if k in inner and isinstance(inner[k], str) and inner[k].strip():
                    return inner[k].strip()
    m = re.search(
        r'"(?:path|source|destination|target|dest)"\s*:\s*"([^"]+)"',
        sv,
    )
    if m:
        return m.group(1).strip()
    return None


def path_arg_looks_malformed(path_str: str) -> bool:
    """True when the path string is still JSON-shaped after normalization."""
    if not path_str:
        return True
    s = path_str.strip()
    if s.startswith("{") or s.startswith('{"'):
        return True
    return bool(_MALFORMED_PATH_RE.match(s))


def normalize_tool_path_arg(
    raw: Any,
    *,
    cwd: str = "",
    key: str = "path",
) -> tuple[str, str | None]:
    """Return ``(normalized_path, error_message)``."""
    if raw is None:
        return "", f"Error: '{key}' is required."
    if not isinstance(raw, str):
        return "", f"Error: '{key}' must be a string."
    path_str = raw.strip()
    if not path_str:
        return "", f"Error: '{key}' is required."

    embedded = unwrap_embedded_json_path(path_str, key)
    if embedded is not None:
        logger.warning(
            "normalize_tool_path_arg: unwrapped embedded JSON %s: %r -> %r",
            key, path_str[:80], embedded,
        )
        path_str = embedded

    path_str = normalize_ledger_path(path_str) or path_str
    if cwd:
        path_str = strip_redundant_project_prefix(path_str, cwd)

    if path_arg_looks_malformed(path_str):
        return path_str, (
            f"Error: malformed {key} — pass a plain relative path "
            f"(e.g. {key}='src/main.ts'), not JSON like "
            f'{{"{key}": "..."}}.'
        )
    return path_str, None


def normalize_path_fields_in_args(
    args: dict[str, Any],
    *,
    cwd: str = "",
) -> dict[str, Any]:
    """In-place normalize all path-like keys in a tool args dict."""
    for k in PATH_ARG_KEYS:
        v = args.get(k)
        if not isinstance(v, str):
            continue
        normalized, err = normalize_tool_path_arg(v, cwd=cwd, key=k)
        if err is None:
            args[k] = normalized
    return args


CONTENT_ARG_KEYS = ("content", "text", "body", "file_content", "data")


def extract_content_arg(params: dict[str, Any]) -> str | None:
    """Return file content from standard or alternate parameter keys."""
    for key in CONTENT_ARG_KEYS:
        value = params.get(key)
        if isinstance(value, str):
            return value
    return None


def _decode_json_string_fragment(raw: str) -> str | None:
    """Best-effort decode of a JSON string value, including truncated tails."""
    if not raw:
        return None
    cleaned = raw.rstrip()
    while cleaned.endswith(('"', "}", ",")):
        cleaned = cleaned[:-1].rstrip()
    if cleaned.endswith("\\"):
        cleaned = cleaned[:-1]
    try:
        decoded = json.loads(f'"{cleaned}"')
        if isinstance(decoded, str):
            return decoded
    except json.JSONDecodeError:
        pass
    text = cleaned.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    text = text.replace("\\\\", "\\")
    return text or None


def unwrap_embedded_write_args(raw: str) -> tuple[str | None, str | None]:
    """When the model stuffs write() JSON into ``path``, extract path and content."""
    if not isinstance(raw, str):
        return None, None
    sv = raw.strip()
    if not sv.startswith("{"):
        return None, None

    path = unwrap_embedded_json_path(sv, "path")
    content_match = re.search(r'"content"\s*:\s*"(.*)', sv, re.DOTALL)
    if not content_match:
        return path, None

    content = _decode_json_string_fragment(content_match.group(1))
    return path, content


def build_write_missing_content_error(
    raw_path: str,
    *,
    resolved_path: str | None = None,
    content_key_absent: bool = False,
) -> str:
    """Actionable error when write() has a path but no content."""
    target = resolved_path
    if not target and isinstance(raw_path, str):
        target = unwrap_embedded_json_path(raw_path, "path")
    if target is None and isinstance(raw_path, str) and raw_path.strip():
        target = raw_path.strip()

    lines = [
        "Error: 'content' is required for write().",
        "Pass path and content as separate top-level fields — not JSON inside path.",
    ]
    embedded_json_path = isinstance(raw_path, str) and raw_path.strip().startswith("{")
    if embedded_json_path or content_key_absent:
        lines.append(
            "This often happens when the tool call is truncated at the output token "
            "limit while writing a large file (only the opening JSON fragment arrives)."
        )
        if target and not target.strip().startswith("{"):
            lines.append(f"Target file: {target}")
        lines.extend([
            "Retry strategy:",
            "  1. write() a short stub (~30–80 lines) first,",
            "  2. then use edit() to add sections, or split into multiple smaller writes.",
            "Do not nest {\"path\": ..., \"content\": ...} inside the path field.",
        ])
    return "\n".join(lines)


def recover_write_tool_args(params: dict[str, Any]) -> tuple[str, str | None]:
    """Normalize path/content from common malformed write() payloads."""
    raw_path = params.get("path", "")
    if not isinstance(raw_path, str):
        raw_path = str(raw_path or "")

    content = extract_content_arg(params)
    path_str = raw_path.strip()

    if content is None and path_str.startswith("{"):
        embedded_path, embedded_content = unwrap_embedded_write_args(path_str)
        if embedded_path:
            path_str = embedded_path
        if embedded_content is not None:
            content = embedded_content

    return path_str, content
