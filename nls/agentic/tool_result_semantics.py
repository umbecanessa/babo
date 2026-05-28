"""Shared soft-error detection for tool results (exit 0 but failed)."""

from __future__ import annotations

from typing import Any

_BASH_SOFT_ERROR_PATTERNS = (
    "is not recognized",
    "unknown flag",
    "unknown shorthand flag",
    "unknown command",
    "not found",
    "permission denied",
    "not logged in",
    "not authenticated",
    "fatal:",
    "commandnotfoundexception",
    "gh auth login",
    "to get started with github cli",
    "authentication failed",
    "bad credentials",
)

_GH_CMD_MARKERS = ("gh ", " gh", "gh.exe")


def effective_tool_error(
    tool_name: str,
    result: Any,
    *,
    args: dict[str, Any] | None = None,
) -> bool:
    """True when the tool result should count as failure for stall/eval."""
    if getattr(result, "is_error", False):
        return True
    if tool_name != "bash":
        return False
    content = (getattr(result, "content", None) or "")[:800].lower()
    if not content:
        return False
    if not any(p in content for p in _BASH_SOFT_ERROR_PATTERNS):
        return False
    cmd = ""
    if args:
        cmd = str(args.get("command", "") or "").lower()
    if cmd and any(m in cmd for m in _GH_CMD_MARKERS):
        return True
    if "github" in content or "gh " in content:
        return True
    return any(p in content for p in (
        "is not recognized",
        "unknown flag",
        "unknown command",
        "commandnotfoundexception",
        "permission denied",
    ))
