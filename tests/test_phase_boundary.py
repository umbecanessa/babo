"""Tests for phase-boundary context trim."""

from __future__ import annotations

from nls.agentic.phase_boundary import (
    ensure_user_query_in_context,
    trim_context_for_phase_boundary,
)


def test_trim_context_drops_completion_review_messages():
    context = [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "build the app"},
        {
            "role": "assistant",
            "content": "[COMPLETION REVIEW — DELEGATE #2]\nWave done.",
        },
        {"role": "user", "content": "run it locally"},
    ]
    trimmed = trim_context_for_phase_boundary(
        context,
        user_input="run it locally",
        goals=["Start backend locally"],
        plan_id="plan_abc",
    )
    texts = " ".join(m.get("content", "") for m in trimmed)
    assert "COMPLETION REVIEW" not in texts
    assert "NEW TASK PHASE" in texts
    assert "run it locally" in texts.lower() or "Start backend" in texts


def test_trim_context_injects_user_when_tail_is_tool_only():
    """Crash journal snapshots are often assistant/tool-only after trim."""
    context = [
        {"role": "system", "content": "You are an agent."},
        *[
            {"role": "assistant", "content": f"step {i}", "tool_calls": [{"id": f"c{i}"}]}
            for i in range(12)
        ],
        *[
            {"role": "tool", "content": f"result {i}", "tool_call_id": f"c{i}"}
            for i in range(12)
        ],
    ]
    resume_input = (
        "Continue the interrupted task from where you left off. "
        "Original request:\nHey Babo, run it locally"
    )
    trimmed = trim_context_for_phase_boundary(
        context,
        user_input=resume_input,
        goals=["Verify backend health"],
        plan_id="plan_abc",
    )
    assert any(m.get("role") == "user" for m in trimmed)
    user_msgs = [m for m in trimmed if m.get("role") == "user"]
    assert resume_input in user_msgs[0]["content"]


def test_ensure_user_query_in_context_noop_when_present():
    context = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    assert ensure_user_query_in_context(context, "ignored") is False
    assert len(context) == 2
