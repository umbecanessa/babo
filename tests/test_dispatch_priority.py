"""Dispatch queue priority: plan recovery must drain before scheduler check-backs."""

from __future__ import annotations

from nls.engine.inner_loop import InnerLoop, dispatch_priority


def test_dispatch_priority_plan_recovery_highest():
    assert dispatch_priority(
        "team_wave_complete:team_abc",
        "[PLAN RECOVERY REQUIRED] advance blocked plan",
    ) < dispatch_priority("scheduler:checkback:job_1", "check back")


def test_pop_highest_priority_prefers_wave_complete_over_scheduler():
    il = InnerLoop.__new__(InnerLoop)
    il._pending_dispatches = [
        ("scheduler wake", "scheduler:checkback:job_1"),
        ("wave review", "team_wave_complete:team_abc"),
        (
            "[PLAN RECOVERY REQUIRED] unblock plan",
            "team_wave_complete:team_xyz",
        ),
    ]

    prompt, source = il._pop_highest_priority_dispatch()
    assert source == "team_wave_complete:team_xyz"
    assert "PLAN RECOVERY" in prompt

    prompt2, source2 = il._pop_highest_priority_dispatch()
    assert source2 == "team_wave_complete:team_abc"

    prompt3, source3 = il._pop_highest_priority_dispatch()
    assert source3 == "scheduler:checkback:job_1"
