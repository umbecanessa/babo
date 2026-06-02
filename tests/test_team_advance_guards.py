"""Tests for TeamManager.advance_team cross-wave guards."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nls.agentic.team_manager import Team, TeamManager, TeamMember
from nls.tools.agent_tools.plan import Plan, PlanStep, PlanStore


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agent"
    (d / "workspace").mkdir(parents=True)
    return d


@pytest.fixture
def plan_store(agent_dir: Path) -> PlanStore:
    store = PlanStore(agent_dir)
    plan = Plan(
        id="plan_test",
        title="Test Plan",
        project_dir="proj",
        steps=[
            PlanStep(id="s1", label="A", delegatable=True),
            PlanStep(id="s2", label="B", delegatable=True),
        ],
    )
    store.save(plan)
    return store


def test_advance_rejects_terminal_team(agent_dir: Path, plan_store: PlanStore):
    tm = TeamManager(agent_dir, plan_store, delegate_manager=MagicMock())
    team = Team(
        id="team_done",
        name="Wave 0",
        plan_id="plan_test",
        wave_index=0,
        status="completed",
        members=[TeamMember(step_id="s1", task="t", status="done")],
        completion_reported=True,
    )
    tm._teams[team.id] = team

    with pytest.raises(ValueError, match="already finalized"):
        asyncio.run(tm.advance_team(team.id))


def test_advance_rejects_when_sibling_active(agent_dir: Path, plan_store: PlanStore):
    dm = MagicMock()
    dm.has_active_delegates.return_value = False
    tm = TeamManager(agent_dir, plan_store, delegate_manager=dm)
    t0 = Team(
        id="team_w0",
        name="Wave 0",
        plan_id="plan_test",
        wave_index=0,
        status="active",
        members=[TeamMember(step_id="s1", task="t", status="done")],
    )
    t1 = Team(
        id="team_w1",
        name="Wave 1",
        plan_id="plan_test",
        wave_index=1,
        status="active",
        members=[TeamMember(step_id="s2", task="t2", status="running", delegate_number=1)],
    )
    tm._teams[t0.id] = t0
    tm._teams[t1.id] = t1

    with pytest.raises(ValueError, match="another team is still active"):
        asyncio.run(tm.advance_team(t0.id))


def test_advance_reconciles_stale_running_member(agent_dir: Path, plan_store: PlanStore):
    """Delegate finished in DM but member still 'running' — advance should sync first."""
    dm = MagicMock()
    dm.has_active_delegates.return_value = False
    ds = MagicMock()
    ds.state = "done"
    ds.exit_reason = "task_complete"
    ds.batch_id = "batch-1"
    ds.delegate_number = 2
    ds.summary_preview = "done"
    ds.iteration = 5
    ds.total_tool_calls = 10
    ds.elapsed_seconds = 12.0
    ds.last_actions = None
    ds.hint_ack = ""
    dm._delegates = {2: ds}
    dm.is_delegate_live.return_value = False

    tm = TeamManager(agent_dir, plan_store, delegate_manager=dm)
    team = Team(
        id="team_w1",
        name="Wave 1",
        plan_id="plan_test",
        wave_index=1,
        status="active",
        batch_id="batch-1",
        members=[
            TeamMember(
                step_id="s1", task="a", status="done", delegate_number=1,
            ),
            TeamMember(
                step_id="s2",
                task="b",
                status="running",
                delegate_number=2,
            ),
        ],
    )
    tm._teams[team.id] = team

    result = asyncio.run(tm.advance_team(team.id))
    assert result is not None
    assert team.members[1].status == "done"
