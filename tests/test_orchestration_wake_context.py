"""Tests for orchestration wake context hygiene and evaluating breadcrumbs."""

from __future__ import annotations

from nls.agentic.orchestration_policy import (
    build_evaluating_action_breadcrumb,
    build_plan_wake_breadcrumbs,
    sanitize_stall_messages,
    trim_context_for_orchestration_wake,
)


def test_sanitize_stall_messages_replaces_poison_tombstones():
    ctx = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "[Loop stopped: stalled. 36 iterations, 35 tool calls.]",
        },
        {"role": "user", "content": "[Autonomous task]"},
    ]
    out = sanitize_stall_messages(ctx)
    assert "36 iterations" not in (out[1].get("content") or "")
    assert "concrete tool action" in (out[1].get("content") or "").lower()


def test_trim_context_for_orchestration_wake_sanitizes_stalls():
    ctx = [{"role": "system", "content": "sys"}]
    ctx += [{"role": "user", "content": f"u{i}"} for i in range(12)]
    ctx.insert(
        5,
        {
            "role": "assistant",
            "content": "[Loop stopped: stalled. 12 iterations, 14 tool calls.]",
        },
    )
    trimmed = trim_context_for_orchestration_wake(
        ctx,
        "team_completion_review:team_abc",
        keep_tail=4,
    )
    assistant = [m for m in trimmed if m.get("role") == "assistant"]
    for msg in assistant:
        assert "Loop stopped: stalled" not in (msg.get("content") or "")


def test_build_plan_wake_breadcrumbs_lists_verify_path():
    lines = build_plan_wake_breadcrumbs(
        audit_issues=["fastapi not found"],
        incomplete_steps=[],
    )
    joined = "\n".join(lines)
    assert "plan(action='verify')" in joined
    assert "fastapi" in joined


def test_build_evaluating_action_breadcrumb_without_plan():
    msg = build_evaluating_action_breadcrumb(
        None,
        dispatch_source="team_completion_review:team_x",
    )
    assert "EVALUATING" in msg
    assert "team(intervene" in msg
    assert "team(inspect)" in msg
