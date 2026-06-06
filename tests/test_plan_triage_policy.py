"""Tests for plan-aware triage profile boosting."""

from __future__ import annotations

from dataclasses import dataclass, field

from nls.agentic.goals import TurnTriage
from nls.agentic.plan_store import Plan, PlanStep
from nls.agentic.plan_triage_policy import (
    apply_orchestration_floor,
    apply_user_profile_override,
    apply_active_plan_goals_and_hints,
    boost_triage_for_active_plan,
    build_plan_triage_continuation_block,
    enforce_loop_profile_for_active_plan,
    plan_requires_orchestrated_profile,
)
from nls.agentic.team_manager import Team, TeamMember
from nls.agentic.types import LoopState


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
    requested, effective = apply_user_profile_override(
        triage,
        "solo_structured",
        plan_store=_FakeStore(plan),
        team_manager=_FakeTeamManager([team]),
    )
    assert triage.profile == "orchestrated"
    assert requested == "solo_structured"
    assert effective == "orchestrated"


def test_apply_orchestration_floor_after_job_default_solo():
    plan = Plan(
        id="plan_x",
        title="ICF",
        status="in_progress",
        steps=[PlanStep(id="step-9", label="Railway", status="pending", delegatable=True)],
    )
    team = Team(id="team_w5", plan_id="plan_x", status="created")
    store = _FakeStore(plan)
    tm = _FakeTeamManager([team])
    assert apply_orchestration_floor("solo_structured", store, tm) == "orchestrated"


def test_enforce_loop_profile_lifts_state():
    plan = Plan(
        id="plan_x",
        title="ICF",
        status="in_progress",
        steps=[PlanStep(id="step-9", label="Done step", status="pending")],
    )
    team = Team(id="team_w5", plan_id="plan_x", status="created")
    state = LoopState(orchestration_profile="solo_structured")
    enforce_loop_profile_for_active_plan(
        state,
        _FakeStore(plan),
        _FakeTeamManager([team]),
    )
    assert state.orchestration_profile == "orchestrated"


def test_build_plan_triage_continuation_block_lists_unlaunched_wave():
    plan = Plan(
        id="plan_x",
        title="ICF Coaching",
        status="blocked",
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
    block = build_plan_triage_continuation_block(
        _FakeStore(plan),
        _FakeTeamManager([team]),
    )
    assert "team_w5" in block
    assert "launch" in block.lower()
    assert "orchestrated" in block.lower()


@dataclass
class _LoopStub:
    orchestration_profile: str = "solo_structured"
    hints: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)


def test_apply_active_plan_goals_and_hints_without_triage():
    plan = Plan(
        id="plan_x",
        title="ICF",
        status="in_progress",
        steps=[PlanStep(id="step-9", label="Completion page", status="pending")],
    )
    team = Team(id="team_w5", plan_id="plan_x", status="created")
    state = _LoopStub(hints=["forbid:team"])
    apply_active_plan_goals_and_hints(
        state,
        "please continue from there",
        plan_store=_FakeStore(plan),
        team_manager=_FakeTeamManager([team]),
    )
    assert state.goals
    assert "continuation:plan_orchestration" in state.hints
    assert not any("forbid:team" in h for h in state.hints)
