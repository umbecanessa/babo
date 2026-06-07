"""Orchestrator hint policy — structured gates, not message heuristics."""

from __future__ import annotations

import time
from typing import Any

HINT_COOLDOWN_SECONDS = 75.0


def _member_int(member: Any, name: str, default: int = 0) -> int:
    try:
        return int(getattr(member, name, default) or default)
    except (TypeError, ValueError):
        return default


def _member_float(member: Any, name: str, default: float = 0.0) -> float:
    try:
        return float(getattr(member, name, default) or default)
    except (TypeError, ValueError):
        return default


def member_escalation_is_open(member: Any) -> bool:
    return _member_float(member, "escalation_opened_at") > 0


def open_member_escalation(member: Any, *, now: float | None = None) -> None:
    """Mark an orchestrator-facing escalation episode (snapshot delegate counters)."""
    ts = now if now is not None else time.time()
    member.escalation_opened_at = ts
    member.escalation_tool_calls = _member_int(member, "tool_calls")
    member.escalation_iterations = _member_int(member, "iterations")
    member.hints_for_escalation = 0


def clear_member_escalation(member: Any) -> None:
    member.escalation_opened_at = 0.0
    member.escalation_tool_calls = 0
    member.escalation_iterations = 0
    member.hints_for_escalation = 0


def member_made_progress_since_escalation(member: Any) -> bool:
    if not member_escalation_is_open(member):
        return False
    return (
        _member_int(member, "tool_calls") > _member_int(member, "escalation_tool_calls")
        or _member_int(member, "iterations") > _member_int(member, "escalation_iterations")
    )


def sync_member_escalation_progress(member: Any) -> bool:
    """Close open escalation when delegate counters advance. Returns True if cleared."""
    if member_made_progress_since_escalation(member):
        clear_member_escalation(member)
        return True
    return False


def should_block_orchestrator_hint(
    member: Any,
    message: str,
    *,
    now: float | None = None,
) -> tuple[bool, str]:
    """Structured hint gates — no natural-language pattern matching."""
    _ = (message or "").strip()
    ts = now if now is not None else time.time()

    sync_member_escalation_progress(member)

    if member_escalation_is_open(member) and _member_int(member, "hints_for_escalation") >= 1:
        return True, (
            "Hint blocked — one orchestrator hint already sent for this escalation. "
            "Run team(action='inspect') and wait for delegate progress before hinting again."
        )

    last_hint_at = _member_float(member, "last_hint_at")
    last_progress_at = _member_float(member, "last_progress_at")
    if (
        last_hint_at > 0
        and last_progress_at > last_hint_at
        and not member_escalation_is_open(member)
    ):
        return True, (
            "Hint blocked — delegate tool activity advanced since your last hint. "
            "Use team(action='inspect') on live status; another hint requires "
            "a new member escalation."
        )

    last_inspected_at = _member_float(member, "last_inspected_at")
    if last_hint_at > 0 and last_inspected_at <= last_hint_at:
        return True, (
            "Hint blocked — run team(action='inspect') after the previous hint "
            "before sending another."
        )

    if last_hint_at > 0:
        elapsed = ts - last_hint_at
        if elapsed < HINT_COOLDOWN_SECONDS:
            if _member_int(member, "tool_calls") <= _member_int(member, "tool_calls_at_last_hint"):
                return True, (
                    f"Hint blocked — no new delegate tool activity since the last hint "
                    f"({elapsed:.0f}s ago). Use team(action='inspect') or wait()."
                )
            preview = (getattr(member, "last_hint_preview", None) or "").strip()
            if preview and preview == _.strip():
                return True, (
                    "Hint blocked — identical guidance was already sent. "
                    "Inspect live delegate status before repeating."
                )

    return False, ""


def evaluate_hint_suppression_for_member(
    member: Any,
    message: str,
    *,
    now: float | None = None,
) -> tuple[bool, str]:
    return should_block_orchestrator_hint(member, message, now=now)


def record_member_inspected(member: Any, *, now: float | None = None) -> None:
    member.last_inspected_at = now if now is not None else time.time()


def record_hint_delivery(member: Any, message: str, *, now: float | None = None) -> None:
    """Persist last-hint metadata on a TeamMember row."""
    ts = now if now is not None else time.time()
    member.last_hint_at = ts
    member.last_hint_preview = (message or "").strip()[:400]
    member.tool_calls_at_last_hint = _member_int(member, "tool_calls")
    if member_escalation_is_open(member):
        member.hints_for_escalation = _member_int(member, "hints_for_escalation") + 1


def team_tool_resolves_escalation(
    result: Any,
    *,
    action: str = "",
    decision: str = "",
) -> bool:
    """True when a team tool call actually handled a pending member escalation."""
    if getattr(result, "is_error", False):
        return False
    content = (getattr(result, "content", None) or "").lower()
    if "suppressed" in content or "duplicate hint" in content or "hint blocked" in content:
        return False
    details = getattr(result, "details", None) or {}
    act = str(action or details.get("action") or "").lower()
    dec = str(decision or details.get("decision") or "").lower()
    if act == "hint":
        return True
    if act == "grant_paths":
        return True
    if act == "rewake":
        return True
    if act == "intervene" and dec in ("hint", "extend", "approve", "terminate"):
        return True
    return False
