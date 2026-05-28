"""Plan dependency graph: cycles, fix_dependencies, delete guard."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nls.agentic.plan_store import (
    Plan,
    PlanStep,
    PlanStore,
    break_service_before_api_edges,
    detect_dependency_cycles,
)
from nls.tools.agent_tools.plan import PlanTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture
def store(workspace: Path) -> PlanStore:
    return PlanStore(workspace)


def test_detect_dependency_cycle(store: PlanStore):
    plan = Plan(
        id="plan_cycle",
        title="Cycle",
        status="in_progress",
        steps=[
            PlanStep(
                id="step-3",
                label="AssemblyAI Transcription Service",
                status="pending",
                depends_on=["Anthropic Analysis Service"],
                delegatable=True,
            ),
            PlanStep(
                id="step-4",
                label="Anthropic Analysis Service",
                status="pending",
                depends_on=["Backend API & Email Service"],
                delegatable=True,
            ),
            PlanStep(
                id="step-5",
                label="Backend API & Email Service",
                status="pending",
                depends_on=["AssemblyAI Transcription Service"],
                delegatable=True,
            ),
        ],
    )
    cycles = detect_dependency_cycles(plan)
    assert cycles


def test_break_service_before_api_edges(store: PlanStore):
    plan = Plan(
        id="plan_fix",
        title="Fix",
        status="in_progress",
        steps=[
            PlanStep(
                id="step-2",
                label="Database Schema & Seed Data",
                status="done",
            ),
            PlanStep(
                id="step-4",
                label="Anthropic Analysis Service",
                status="pending",
                depends_on=[
                    "Database Schema & Seed Data",
                    "Backend API & Email Service",
                ],
                delegatable=True,
            ),
            PlanStep(
                id="step-5",
                label="Backend API & Email Service",
                status="pending",
                depends_on=["Anthropic Analysis Service"],
                delegatable=True,
            ),
        ],
    )
    patched = break_service_before_api_edges(plan)
    assert patched >= 1
    anthropic = plan.get_step("step-4")
    assert anthropic is not None
    assert "Backend API" not in " ".join(anthropic.depends_on)


@pytest.mark.asyncio
async def test_delete_blocked_when_done_and_pending(workspace: Path, store: PlanStore):
    plan = Plan(
        id="plan_x",
        title="X",
        status="in_progress",
        steps=[
            PlanStep(id="step-1", label="Scaffold", status="done"),
            PlanStep(id="step-2", label="API", status="pending", delegatable=True),
        ],
    )
    store.save(plan)
    tool = PlanTool(str(workspace))
    tool._store = store
    tool._team_manager = MagicMock()
    tool._team_manager.list_teams.return_value = []

    result = await tool.execute(
        {"action": "delete", "plan_id": "plan_x", "reason": "test"},
    )
    assert result.is_error
    assert "BLOCKED" in result.content
    assert "fix_dependencies" in result.content
    assert store.load("plan_x").status == "in_progress"


@pytest.mark.asyncio
async def test_update_depends_on(workspace: Path, store: PlanStore):
    plan = Plan(
        id="plan_x",
        title="X",
        status="in_progress",
        steps=[
            PlanStep(
                id="step-4",
                label="Anthropic Analysis Service",
                status="pending",
                depends_on=["Backend API & Email Service"],
                delegatable=True,
            ),
            PlanStep(
                id="step-5",
                label="Backend API & Email Service",
                status="pending",
                delegatable=True,
            ),
        ],
    )
    store.save(plan)
    tool = PlanTool(str(workspace))
    tool._store = store
    tool._team_manager = MagicMock()
    tool._team_manager.list_teams.return_value = []

    result = await tool.execute(
        {
            "action": "update",
            "plan_id": "plan_x",
            "step_id": "step-4",
            "depends_on": ["Database Schema & Seed Data"],
        },
    )
    assert not result.is_error
    step = store.load("plan_x").get_step("step-4")
    assert step is not None
    assert step.depends_on == ["Database Schema & Seed Data"]


@pytest.mark.asyncio
async def test_update_blocks_premature_in_progress(workspace: Path, store: PlanStore):
    plan = Plan(
        id="plan_x",
        title="X",
        status="in_progress",
        steps=[
            PlanStep(id="step-1", label="Scaffold", status="in_progress", delegatable=True),
            PlanStep(
                id="step-4",
                label="AssemblyAI Transcription Service",
                status="pending",
                depends_on=["Core Backend API Services"],
                delegatable=True,
            ),
            PlanStep(
                id="step-3",
                label="Core Backend API Services",
                status="pending",
                depends_on=["step-1"],
                delegatable=True,
            ),
        ],
    )
    store.save(plan)
    tool = PlanTool(str(workspace))
    tool._store = store
    tool._team_manager = MagicMock()
    tool._team_manager.list_teams.return_value = []

    result = await tool.execute(
        {
            "action": "update",
            "plan_id": "plan_x",
            "step_id": "step-4",
            "status": "in_progress",
            "step_description": "Enriched description",
        },
    )
    assert not result.is_error
    assert "status kept as pending" in result.content.lower()
    step = store.load("plan_x").get_step("step-4")
    assert step is not None
    assert step.status == "pending"


@pytest.mark.asyncio
async def test_fix_dependencies_breaks_service_api_cycle(
    workspace: Path, store: PlanStore,
):
    plan = Plan(
        id="plan_x",
        title="X",
        status="in_progress",
        steps=[
            PlanStep(
                id="step-2",
                label="Database Schema & Seed Data",
                status="done",
            ),
            PlanStep(
                id="step-4",
                label="Anthropic Analysis Service",
                status="pending",
                depends_on=[
                    "Database Schema & Seed Data",
                    "Backend API & Email Service",
                ],
                delegatable=True,
            ),
            PlanStep(
                id="step-5",
                label="Backend API & Email Service",
                status="pending",
                depends_on=["Anthropic Analysis Service"],
                delegatable=True,
            ),
        ],
    )
    store.save(plan)
    tool = PlanTool(str(workspace))
    tool._store = store
    tool._team_manager = MagicMock()
    tool._team_manager.list_teams.return_value = []
    tool._dep_inference_fn = None
    tool._inference_fn = None

    result = await tool.execute(
        {"action": "fix_dependencies", "plan_id": "plan_x"},
    )
    assert not result.is_error
    assert "Waves" in result.content
    anthropic = store.load("plan_x").get_step("step-4")
    assert anthropic is not None
    assert "Backend API" not in " ".join(anthropic.depends_on)
