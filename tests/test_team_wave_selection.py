"""Team wave selection: skip guards, deploy blocking, duplicate recreate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nls.agentic.plan_store import (
    Plan,
    PlanStep,
    PlanStore,
    detect_out_of_order_done_steps,
    get_delegation_waves,
    next_pending_wave_index,
    pending_steps_in_wave,
    skipped_pending_steps_before_wave,
)
from nls.agentic.team_manager import Team, TeamManager, TeamMember
from nls.tools.agent_tools.team import TeamTool


def _icf_plan() -> Plan:
    return Plan(
        id="plan_icf",
        title="ICF Platform",
        project_dir="icf-proj",
        status="in_progress",
        steps=[
            PlanStep(
                id="step-1",
                label="Scaffolding & Repository Initialization",
                status="done",
                delegatable=True,
            ),
            PlanStep(
                id="step-2",
                label="Database Schema & Models",
                status="done",
                delegatable=True,
                depends_on=["Scaffolding & Repository Initialization"],
            ),
            PlanStep(
                id="step-3",
                label="Backend API Foundation",
                status="done",
                depends_on=["Scaffolding & Repository Initialization"],
            ),
            PlanStep(
                id="step-4",
                label="Transcription Service",
                status="pending",
                delegatable=True,
                depends_on=["Database Schema & Models"],
            ),
            PlanStep(
                id="step-5",
                label="Analysis Engine",
                status="pending",
                delegatable=True,
                depends_on=["Database Schema & Models"],
            ),
            PlanStep(
                id="step-6",
                label="Complete Backend Pipeline & Endpoints",
                status="done",
                delegatable=True,
                depends_on=[
                    "Backend API Foundation",
                    "Transcription Service",
                    "Analysis Engine",
                ],
            ),
            PlanStep(
                id="step-7",
                label="Frontend Shell & Upload Interface",
                status="done",
                delegatable=True,
                depends_on=["Scaffolding & Repository Initialization"],
            ),
            PlanStep(
                id="step-8",
                label="Evaluation Interface UI",
                status="pending",
                delegatable=True,
                depends_on=["Frontend Shell & Upload Interface"],
            ),
            PlanStep(
                id="step-9",
                label="Railway Deployment Configuration",
                status="pending",
                delegatable=True,
                depends_on=[
                    "Complete Backend Pipeline & Endpoints",
                    "Evaluation Interface UI",
                ],
            ),
        ],
    )


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agent"
    (d / "workspace").mkdir(parents=True)
    (d / "teams").mkdir(parents=True)
    return d


@pytest.fixture
def icf_store(agent_dir: Path) -> PlanStore:
    store = PlanStore(agent_dir)
    store.save(_icf_plan())
    return store


def test_next_pending_wave_after_out_of_order_completion(icf_store: PlanStore):
    plan = icf_store.load("plan_icf")
    assert plan is not None
    waves = get_delegation_waves(plan)
    assert next_pending_wave_index(plan, waves) == 2
    pending = pending_steps_in_wave(plan, 2, waves)
    assert {s.id for s in pending} == {"step-4", "step-5", "step-8"}


def test_detect_out_of_order_done_steps(icf_store: PlanStore):
    plan = icf_store.load("plan_icf")
    assert plan is not None
    pairs = detect_out_of_order_done_steps(plan)
    assert any(done.id == "step-6" for done, _dep in pairs)


def test_create_team_blocks_deploy_wave_when_earlier_pending(
    agent_dir: Path,
    icf_store: PlanStore,
):
    tm = TeamManager(agent_dir, icf_store, delegate_manager=MagicMock())
    waves = get_delegation_waves(icf_store.load("plan_icf"))
    deploy_wave = len(waves) - 1
    team = tm.create_team(
        plan_id="plan_icf",
        wave_index=deploy_wave,
        name="Deploy wave",
    )
    assert team is None
    err = getattr(tm, "_last_create_error", "")
    assert "earlier pending step" in err or "deploy-only wave" in err


def test_create_team_allows_pending_service_wave(
    agent_dir: Path,
    icf_store: PlanStore,
):
    tm = TeamManager(agent_dir, icf_store, delegate_manager=MagicMock())
    team = tm.create_team(
        plan_id="plan_icf",
        wave_index=2,
        name="AI + UI wave",
    )
    assert team is not None
    assert {m.step_id for m in team.members} == {"step-4", "step-5", "step-8"}


@pytest.mark.asyncio
async def test_team_create_auto_wave(agent_dir: Path, icf_store: PlanStore):
    dm = MagicMock()
    dm._max_concurrent = 5
    tm = TeamManager(agent_dir, icf_store, delegate_manager=dm)
    tool = TeamTool(tm)
    result = await tool.execute(
        {
            "action": "create",
            "plan_id": "plan_icf",
            "wave": "auto",
            "name": "Next pending wave",
        }
    )
    assert not result.is_error
    assert result.details.get("team_id") or "team_" in result.content


@pytest.mark.asyncio
async def test_team_create_blocks_duplicate_recreate_loop(
    agent_dir: Path,
    icf_store: PlanStore,
):
    tm = TeamManager(agent_dir, icf_store, delegate_manager=MagicMock())
    tool = TeamTool(tm)
    waves = get_delegation_waves(icf_store.load("plan_icf"))
    deploy_wave = len(waves) - 1

    for i in range(2):
        t = Team(
            id=f"team_dup_{i}",
            name=f"Deploy attempt {i}",
            plan_id="plan_icf",
            wave_index=deploy_wave,
            status="cancelled",
            members=[TeamMember(step_id="step-9", task="Railway")],
            batch_id="",
        )
        tm._teams[t.id] = t

    result = await tool.execute(
        {
            "action": "create",
            "plan_id": "plan_icf",
            "wave": deploy_wave,
            "name": "Deploy again",
        }
    )
    assert result.is_error
    assert result.details.get("duplicate_wave_recreate") or result.details.get(
        "skipped_pending_wave"
    )


def test_try_create_next_wave_skips_when_earlier_wave_pending(
    agent_dir: Path,
    icf_store: PlanStore,
):
    """After out-of-order wave completion, do not auto-create deploy wave."""
    tm = TeamManager(agent_dir, icf_store, delegate_manager=MagicMock())
    waves = get_delegation_waves(icf_store.load("plan_icf"))
    completed = Team(
        id="team_w3",
        name="Pipeline wave",
        plan_id="plan_icf",
        wave_index=len(waves) - 2,
        status="completed",
        completion_reported=True,
        members=[TeamMember(step_id="step-6", task="Pipeline")],
    )
    tm._teams[completed.id] = completed

    nxt = tm._try_create_next_wave(completed)
    assert nxt is None


def test_try_create_next_wave_creates_pending_wave(
    agent_dir: Path,
    icf_store: PlanStore,
):
    tm = TeamManager(agent_dir, icf_store, delegate_manager=MagicMock())
    completed = Team(
        id="team_w1",
        name="Wave 1 done",
        plan_id="plan_icf",
        wave_index=1,
        status="completed",
        completion_reported=True,
        members=[TeamMember(step_id="step-2", task="DB")],
    )
    tm._teams[completed.id] = completed

    nxt = tm._try_create_next_wave(completed)
    assert nxt is not None
    assert nxt.wave_index == 2
    assert {m.step_id for m in nxt.members} == {"step-4", "step-5", "step-8"}


@pytest.mark.asyncio
async def test_team_create_allows_retry_on_correct_wave(
    agent_dir: Path,
    icf_store: PlanStore,
):
    """Legitimate retries on the recommended wave are not blocked."""
    dm = MagicMock()
    dm._max_concurrent = 5
    tm = TeamManager(agent_dir, icf_store, delegate_manager=dm)
    tool = TeamTool(tm)
    pending_wave = 2

    for i in range(2):
        t = Team(
            id=f"team_retry_{i}",
            name=f"AI wave attempt {i}",
            plan_id="plan_icf",
            wave_index=pending_wave,
            status="cancelled",
            members=[
                TeamMember(step_id="step-4", task="Transcription"),
                TeamMember(step_id="step-5", task="Analysis"),
                TeamMember(step_id="step-8", task="Evaluation UI"),
            ],
            batch_id="",
        )
        tm._teams[t.id] = t

    result = await tool.execute(
        {
            "action": "create",
            "plan_id": "plan_icf",
            "wave": pending_wave,
            "name": "AI + UI wave retry",
        }
    )
    assert not result.is_error
    assert not result.details.get("duplicate_wave_recreate")


@pytest.mark.asyncio
async def test_fix_dependencies_recommends_pending_wave(
    agent_dir: Path,
    icf_store: PlanStore,
):
    from nls.tools.agent_tools.plan import PlanTool

    plan_tool = PlanTool(str(agent_dir))
    result = await plan_tool.execute(
        {"action": "fix_dependencies", "plan_id": "plan_icf"},
    )
    assert not result.is_error
    assert result.details.get("recommended_wave") == 2
    assert "Next team wave: wave=2" in result.content
    assert "OUT-OF-ORDER" in result.content
