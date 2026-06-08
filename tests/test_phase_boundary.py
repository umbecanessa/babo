"""Tests for phase-boundary context trim."""

from __future__ import annotations

from nls.agentic.phase_boundary import trim_context_for_phase_boundary


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
