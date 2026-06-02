"""Wave milestone chat surfacing and copy."""

from __future__ import annotations

from pathlib import Path

import pytest

from nls.agentic.plan_store import Plan, PlanStep, PlanStore
from nls.agentic.team_manager import Team, TeamManager, TeamMember


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agent"
    (d / "workspace").mkdir(parents=True)
    return d


@pytest.fixture
def tm(agent_dir: Path) -> TeamManager:
    store = PlanStore(agent_dir)
    plan = Plan(
        id="plan_waves",
        title="Multi-wave plan",
        project_dir="proj",
        steps=[
            PlanStep(id="s0", label="Scaffold", delegatable=True),
            PlanStep(id="s1", label="Backend", delegatable=True, depends_on=["s0"]),
            PlanStep(id="s2", label="Frontend", delegatable=True, depends_on=["s1"]),
        ],
    )
    store.save(plan)
    return TeamManager(agent_dir, store)


def _team(
    wave_index: int,
    *,
    status: str = "active",
    member_status: str = "done",
) -> Team:
    return Team(
        id=f"team_w{wave_index}",
        name=f"Wave {wave_index}",
        plan_id="plan_waves",
        wave_index=wave_index,
        status=status,
        members=[
            TeamMember(
                step_id=f"s{wave_index}",
                task=f"Task for wave {wave_index}",
                status=member_status,
            ),
        ],
    )


def test_milestone_chat_first_wave_launch_only(tm: TeamManager):
    assert tm._milestone_surface_chat(_team(0), "launched") is True
    assert tm._milestone_surface_chat(_team(2), "launched") is False


def test_milestone_chat_complete_routine_middle_wave_silent(tm: TeamManager):
    team = _team(1)
    assert tm._milestone_surface_chat(team, "complete") is False


def test_milestone_chat_complete_final_wave(tm: TeamManager):
    team = _team(2)
    assert tm._plan_wave_count(team.plan_id) == 3
    assert tm._milestone_surface_chat(team, "complete") is True


def test_milestone_chat_complete_on_failure(tm: TeamManager):
    team = _team(1, member_status="failed")
    assert tm._milestone_surface_chat(team, "complete") is True


def test_milestone_copy_compact(tm: TeamManager):
    team = _team(0)
    team.members[0].task = "Database Schema and Models"
    launched = tm._wave_launched_milestone(team, 1)
    assert launched.startswith("Wave 1/3 started")
    assert "Database Schema" in launched
    assert "sub-agent(s) running" not in launched

    complete = tm._wave_complete_milestone(team)
    assert "Wave 1/3 complete" in complete
    assert "reviewing before the next wave" not in complete
