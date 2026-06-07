"""Structured orchestrator hint gates (no NL heuristics)."""

from __future__ import annotations

import time

from nls.agentic.team_hint_policy import (
    HINT_COOLDOWN_SECONDS,
    clear_member_escalation,
    evaluate_hint_suppression_for_member,
    open_member_escalation,
    record_hint_delivery,
    record_member_inspected,
    should_block_orchestrator_hint,
    sync_member_escalation_progress,
    team_tool_resolves_escalation,
)


class _Member:
    last_hint_at = 0.0
    last_hint_preview = ""
    tool_calls = 0
    tool_calls_at_last_hint = 0
    iterations = 0
    escalation_opened_at = 0.0
    escalation_tool_calls = 0
    escalation_iterations = 0
    hints_for_escalation = 0
    last_inspected_at = 0.0
    last_progress_at = 0.0


def test_one_hint_per_escalation_episode():
    member = _Member()
    member.tool_calls = 5
    open_member_escalation(member)
    record_hint_delivery(member, "First hint")
    suppress, reason = should_block_orchestrator_hint(member, "Second hint")
    assert suppress is True
    assert "one orchestrator hint" in reason.lower()


def test_progress_closes_escalation_episode():
    member = _Member()
    member.tool_calls = 3
    open_member_escalation(member)
    member.tool_calls = 7
    assert sync_member_escalation_progress(member) is True
    assert member.escalation_opened_at == 0


def test_hint_blocked_after_delegate_progress_since_last_hint():
    member = _Member()
    member.last_hint_at = time.time() - 10
    member.last_progress_at = time.time()
    suppress, reason = should_block_orchestrator_hint(member, "Any message")
    assert suppress is True
    assert "advanced since your last hint" in reason.lower()


def test_hint_requires_inspect_after_previous_hint():
    member = _Member()
    member.last_hint_at = time.time() - 5
    member.last_inspected_at = 0.0
    suppress, reason = should_block_orchestrator_hint(member, "Follow up")
    assert suppress is True
    assert "inspect" in reason.lower()


def test_identical_hint_blocked_within_cooldown():
    member = _Member()
    now = time.time()
    member.last_hint_at = now - 10
    member.last_hint_preview = "Use edit() on sessions.py"
    member.tool_calls = 4
    member.tool_calls_at_last_hint = 4
    suppress, _ = should_block_orchestrator_hint(
        member, "Use edit() on sessions.py", now=now,
    )
    assert suppress is True


def test_hint_allowed_after_inspect_and_no_progress_since_hint():
    member = _Member()
    now = time.time()
    member.last_hint_at = now - 120
    member.last_inspected_at = now - 60
    member.last_progress_at = now - 180
    member.tool_calls = 10
    member.tool_calls_at_last_hint = 8
    clear_member_escalation(member)
    open_member_escalation(member)
    member.tool_calls = 10
    suppress, _ = should_block_orchestrator_hint(member, "New guidance", now=now)
    assert suppress is False


def test_team_inspect_does_not_resolve_escalation():
    class _R:
        is_error = False
        content = "Team status..."
        details = {"action": "inspect"}

    assert team_tool_resolves_escalation(_R(), action="inspect") is False


def test_suppressed_hint_does_not_resolve_escalation():
    class _R:
        is_error = False
        content = "Hint blocked — inspect first"
        details = {"action": "hint"}

    assert team_tool_resolves_escalation(_R(), action="hint") is False


def test_evaluate_wrapper_delegates():
    member = _Member()
    open_member_escalation(member)
    record_hint_delivery(member, "done")
    suppress, _ = evaluate_hint_suppression_for_member(member, "again")
    assert suppress is True


def test_record_inspect_enables_next_hint_when_no_other_blocks():
    member = _Member()
    now = time.time()
    member.last_hint_at = now - HINT_COOLDOWN_SECONDS - 5
    member.last_hint_preview = "old"
    member.tool_calls = 3
    member.tool_calls_at_last_hint = 3
    member.last_progress_at = 0
    record_member_inspected(member, now=now - 1)
    suppress, _ = should_block_orchestrator_hint(member, "new topic", now=now)
    assert suppress is False
