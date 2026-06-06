"""Tests for plan-aware triage profile boosting."""

from __future__ import annotations

from dataclasses import dataclass

from nls.agentic.goals import TurnTriage
from nls.agentic.plan_store import Plan, PlanStep
from nls.agentic.plan_triage_policy import (
    apply_user_profile_override,
    boost_triage_for_active_plan,
    plan_requires_orchestrated_profile,
)
from nls.agentic.team_manager import Team, TeamMember


@dataclass
class _FakeStore:
    _plan: Plan | None = None

    def find_active(self) -> Plan | None:
        return self._plan


class _FakeTeamManager:
    def __init__(self, teams: list[Team]) -> None:
        self._teams = teams

    def list_teams(self, include_terminal: bool = False) -> list[Team]:
        return list(self._teams)


def test_plan_requires_orchestrated_when_unlaunched_wave():
    plan = Plan(
        id="plan_x",
        title="ICF",
        status="in_progress",
        steps=[
            PlanStep(id="step-9", label="Completion page", status="pending", delegatable=True),
        ],
    )
    team = Team(
        id="team_w5",
        plan_id="plan_x",
        wave_index=4,
        status="created",
        members=[TeamMember(step_id="step-9", task="Completion page")],
    )
    assert plan_requires_orchestrated_profile(plan, _FakeTeamManager([team]))


def test_boost_triage_lifts_solo_to_orchestrated():
    plan = Plan(
        id="plan_x",
        title="ICF",
        status="blocked",
        steps=[
            PlanStep(id="step-8", label="Frontend", status="done", delegatable=True),
            PlanStep(id="step-9", label="Completion", status="pending", delegatable=True),
        ],
    )
    team = Team(
        id="team_w5",
        plan_id="plan_x",
        wave_index=4,
        status="created",
        members=[TeamMember(step_id="step-9", task="Completion page")],
    )
    triage = TurnTriage(
        intent="TASK_THINK",
        profile="solo_structured",
        thinking=True,
        hints=["forbid:team"],
    )
    boost_triage_for_active_plan(
        triage,
        "just continue from there — plan and team already exist",
        plan_store=_FakeStore(plan),
        team_manager=_FakeTeamManager([team]),
    )
    assert triage.profile == "orchestrated"
    assert not any("forbid:team" in h for h in triage.hints)
    assert triage.goals


def test_user_override_auto_respects_plan_floor():
    plan = Plan(
        id="plan_x",
        title="ICF",
        status="in_progress",
        steps=[PlanStep(id="step-9", label="Done step", status="pending")],
    )
    team = Team(id="team_w5", plan_id="plan_x", status="created")
    triage = TurnTriage(profile="solo_structured")
    apply_user_profile_override(
        triage,
        "solo_structured",
        plan_store=_FakeStore(plan),
        team_manager=_FakeTeamManager([team]),
    )
    assert triage.profile == "orchestrated"
