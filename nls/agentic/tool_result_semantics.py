"""Shared soft-error detection for tool results (exit 0 but failed)."""

from __future__ import annotations

import re
from typing import Any

# Plan tool — explicit API / gate guidance (not operational failures).
_PLAN_CONTRACT_EXACT = (
    "Error: 'step_id' is required",
    "Error: 'notes' required",
    "Error: 'reason' required",
    "Error: 'label' is required",
    "Error: 'parent_step_id' and 'title' required",
    "already done — accept_partial is not needed",
    "Step already exists:",
    "Cannot complete plan without verification",
    "Cannot complete plan while a team wave is still running",
    "Cannot complete plan — a partial wave has failed delegate",
    "Cannot complete plan — verification reported",
    "step(s) not properly done",
    "No step '",
    "No active plan found.",
)
_PLAN_CONTRACT_PREFIXES = (
    "Cannot complete plan ",
)

_TEAM_CONTRACT_MARKERS = (
    "Error: 'team_id' is required",
    "Error: 'decision' is required",
)

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


def _plan_contract_match(content: str) -> bool:
    text = content or ""
    if not text:
        return False
    if any(marker in text for marker in _PLAN_CONTRACT_EXACT):
        return True
    if any(text.startswith(prefix) for prefix in _PLAN_CONTRACT_PREFIXES):
        # Narrow: only completion-gate phrasing, not arbitrary "Cannot complete plan X failed"
        if (
            "without verification" in text
            or "while a team wave" in text
            or "partial wave" in text
            or "verification reported" in text
            or "step(s) not properly done" in text
            or "plan_id=" in text
        ):
            return True
    return False


def is_tool_contract_error(tool_name: str, result: Any) -> bool:
    """Structured-tool validation / API contract error (not bash noise)."""
    if not getattr(result, "is_error", False):
        return False
    content = getattr(result, "content", None) or ""
    if tool_name == "plan":
        return _plan_contract_match(content)
    if tool_name == "team":
        return any(m in content for m in _TEAM_CONTRACT_MARKERS)
    return False


def contract_error_rule_id(tool_name: str, content: str) -> str:
    """Stable id for guardrails registry dedup."""
    text = (content or "")[:400]
    if tool_name == "plan":
        if "accept_partial" in text and "already done" in text:
            return "accept_partial_step_done"
        if "'step_id' is required" in text:
            return "accept_partial_missing_step_id"
        if "'notes' required" in text:
            return "accept_partial_missing_notes"
        if "'reason' required" in text:
            return "accept_partial_missing_reason"
        if "Cannot complete plan without verification" in text:
            return "complete_needs_verify"
        if "verification reported" in text:
            return "complete_verify_issues"
        if "team wave is still running" in text:
            return "complete_team_running"
        if "partial wave" in text:
            return "complete_partial_wave"
        if "step(s) not properly done" in text:
            return "complete_steps_open"
        if "Local tests not recorded" in text or "local verification step" in text:
            return "verify_local_tests"
        if "Step already exists:" in text:
            return "add_step_duplicate"
        m = re.search(r"plan\(action='(\w+)'", text)
        if m:
            return f"plan_{m.group(1)}_contract"
    if tool_name == "team":
        if "'decision' is required" in text:
            return "intervene_missing_decision"
        if "'team_id' is required" in text:
            return "team_missing_id"
    return f"{tool_name}_contract"


def counts_toward_error_budget(
    tool_name: str,
    result: Any,
    *,
    args: dict[str, Any] | None = None,
) -> bool:
    """Whether this result should increment consecutive_errors / stall guards."""
    if is_tool_contract_error(tool_name, result):
        return False
    return effective_tool_error(tool_name, result, args=args)


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
