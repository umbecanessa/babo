"""Tests for wave-complete vs recovery wake messages."""

from __future__ import annotations

from nls.agentic.plan_work import format_recovery_wake, format_wave_complete_wake


def test_wave_complete_wake_is_not_recovery_alarm():
    msg = format_wave_complete_wake(
        plan_id="plan_x",
        team_id="team_y",
        team_name="Wave 3",
        outcome="completed",
        ok_count=2,
        fail_count=0,
    )
    assert "[WAVE COMPLETE" in msg
    assert "PLAN RECOVERY REQUIRED" not in msg
    assert "Do NOT team(advance)" in msg


def test_recovery_wake_for_failed_steps():
    msg = format_recovery_wake(
        plan_id="plan_x",
        team_id="team_y",
        failed_step_ids=["step-8"],
    )
    assert "PLAN RECOVERY REQUIRED" in msg
    assert "accept_partial" in msg
