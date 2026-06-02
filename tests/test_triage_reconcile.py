"""Tests for triage profile reconciliation (classifier contradiction repair)."""

from __future__ import annotations

from nls.agentic.goals import TurnTriage
from nls.agentic.profile_guard_policy import reconcile_triage_orchestration_depth


def test_reconcile_spurious_forbid_team_on_multi_goal_build():
    profile, hints = reconcile_triage_orchestration_depth(
        profile="solo_structured",
        goals=[
            "Read PRD and extract requirements",
            "Scaffold monorepo",
            "Implement backend",
            "Implement frontend",
            "Configure deployment",
        ],
        hints=["forbid:team"],
        intent="TASK_THINK",
    )
    assert profile == "orchestrated"
    assert hints == []


def test_reconcile_honors_explicit_orchestration_solo():
    profile, hints = reconcile_triage_orchestration_depth(
        profile="solo_structured",
        goals=["Build platform end-to-end"],
        hints=["forbid:team", "orchestration:solo"],
        intent="TASK_THINK",
    )
    assert profile == "solo_structured"
    assert "forbid:team" in hints


def test_turn_triage_reconcile_method():
    triage = TurnTriage(
        intent="TASK_THINK",
        thinking=True,
        profile="solo_structured",
        goals=["A", "B", "C"],
        hints=["forbid:team"],
    )
    triage.cap_profile_from_hints()
    triage.reconcile_orchestration_depth()
    assert triage.profile == "orchestrated"
    assert triage.hints == []
