"""Wave-complete wake dedup: drain queue after advance/launch."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nls.agentic.breadcrumbs import BreadcrumbContext, BreadcrumbEngine
from nls.agentic.team_manager import Team, TeamManager, TeamMember
from nls.tools.agent_tools.plan import Plan, PlanStep, PlanStore


class _DrainLog:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def __call__(self, source: str) -> int:
        if source.startswith("team_wave_complete:"):
            self.removed.append(source)
            return 1
        return 0


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agent"
    (d / "workspace").mkdir(parents=True)
    return d


@pytest.fixture
def tm(agent_dir: Path) -> TeamManager:
    store = PlanStore(agent_dir)
    plan = Plan(
        id="plan_test",
        title="Test",
        project_dir="proj",
        steps=[
            PlanStep(id="s0", label="W0", delegatable=True),
            PlanStep(id="s1", label="W1a", delegatable=True),
            PlanStep(id="s2", label="W1b", delegatable=True),
        ],
    )
    store.save(plan)
    drain = _DrainLog()
    manager = TeamManager(agent_dir, store)
    manager.set_dispatch_drain(drain)
    manager._test_drain = drain  # type: ignore[attr-defined]
    return manager


def test_advance_drains_wave_complete_dispatch(tm: TeamManager):
    w0 = Team(
        id="team_w0",
        name="Wave 0",
        plan_id="plan_test",
        wave_index=0,
        status="active",
        members=[TeamMember(step_id="s0", task="t", status="done")],
    )
    tm._teams[w0.id] = w0

    asyncio.run(tm.advance_team(w0.id))

    drain: _DrainLog = tm._test_drain  # type: ignore[attr-defined]
    assert f"team_wave_complete:{w0.id}" in drain.removed


def test_stale_wake_when_successor_launched(tm: TeamManager):
    w0 = Team(
        id="team_w0",
        name="Wave 0",
        plan_id="plan_test",
        wave_index=0,
        status="completed",
        completion_reported=True,
        members=[TeamMember(step_id="s0", task="t", status="done")],
    )
    tm._teams[w0.id] = w0

    w1 = Team(
        id="team_w1",
        name="Wave 1",
        plan_id="plan_test",
        wave_index=1,
        status="active",
        batch_id="batch-1",
        members=[
            TeamMember(step_id="s1", task="a", status="running", delegate_number=1),
        ],
    )
    tm._teams[w1.id] = w1

    reason = tm.stale_wave_review_wake_reason(w0.id)
    assert reason.startswith("successor_wave_running:")


def test_breadcrumb_on_accept_partial_needs_advance():
    engine = BreadcrumbEngine()
    hint = engine.evaluate(
        BreadcrumbContext(
            tool_name="plan",
            action="accept_partial",
            is_error=False,
            result_details={
                "wave_needs_advance": True,
                "prior_team_id": "team_old",
                "plan_id": "plan_x",
            },
            unlocked_tools=frozenset({"team", "plan"}),
            is_coordinator=True,
            orchestration_profile="orchestrated",
        ),
    )
    assert hint is not None
    assert "team(action='advance'" in hint
