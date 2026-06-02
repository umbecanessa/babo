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
