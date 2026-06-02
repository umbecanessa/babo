"""Plan recovery: single active root, continue_work, delegatable done guards."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nls.agentic.plan_store import Plan, PlanStep, PlanStore
from nls.agentic.orchestration_policy import (
    block_terminate_intervention,
    parse_escalation_steering,
)
from nls.agentic.types import LoopState
from nls.tools.agent_tools.plan import PlanTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture
def store(workspace: Path) -> PlanStore:
    return PlanStore(workspace)


def test_find_active_roots_returns_multiple(store: PlanStore):
    a = Plan(id="plan_a", title="A", status="in_progress", steps=[])
    b = Plan(id="plan_b", title="B", status="in_progress", steps=[])
    store.save(a)
    store.save(b)
    roots = store.find_active_roots()
    assert {p.id for p in roots} == {"plan_a", "plan_b"}


def test_archive_sibling_active_roots(store: PlanStore):
    keep = Plan(id="plan_keep", title="Keep", status="in_progress", steps=[])
    other = Plan(id="plan_old", title="Old", status="in_progress", steps=[])
    store.save(keep)
    store.save(other)
    archived = store.archive_sibling_active_roots("plan_keep", "test")
    assert archived == ["plan_old"]
    assert store.load("plan_old").status == "archived"
    assert store.find_active().id == "plan_keep"


def test_parse_escalation_steering():
    text = (
        "[TEAM MEMBER HELP REQUEST — PROACTIVE]\n"
        "Team: Wave 2 [team_6d8bfb67]\n"
        "Member #1 (delegate #2): Backend\n"
        "Context: writes: 12\n"
    )
    meta = parse_escalation_steering(text)
    assert meta["member_idx"] == 1
    assert meta["team_id"] == "team_6d8bfb67"
    assert meta["writes"] == 12


def test_block_terminate_when_writes_reported():
    state = LoopState()
    state.has_pending_escalation = True
    state.pending_escalation_writes = 5
    state.pending_escalation_member_idx = 1
    msg = block_terminate_intervention(
        state,
        {"decision": "terminate", "member": 1},
        has_pending_escalation=True,
    )
    assert msg is not None
    assert "BLOCKED" in msg
    assert "extend" in msg.lower()


def test_block_terminate_wrong_member():
    state = LoopState()
    state.has_pending_escalation = True
    state.pending_escalation_member_idx = 1
    msg = block_terminate_intervention(
        state,
        {"decision": "terminate", "member": 2},
        has_pending_escalation=True,
    )
    assert msg is not None
    assert "member #1" in msg


@pytest.mark.asyncio
async def test_continue_work_imports_and_archives_source(workspace: Path, store: PlanStore):
    old = Plan(
        id="plan_old",
        title="Old",
        status="in_progress",
        project_dir="proj",
        steps=[
            PlanStep(id="step-1", label="Upload UI", status="pending", delegatable=True),
            PlanStep(id="step-2", label="Done step", status="done"),
        ],
    )
    new = Plan(
        id="plan_new",
        title="New",
        status="in_progress",
        project_dir="proj",
        steps=[PlanStep(id="step-1", label="Scaffold", status="done")],
    )
    store.save(old)
    store.save(new)

    tool = PlanTool(workspace)
    tool._store = store
    tool._team_manager = None
    result = await tool.execute(
        {"action": "continue_work", "source_plan_id": "plan_old"},
    )
    assert not result.is_error
    assert "Upload UI" in result.content
    assert store.load("plan_old").status == "archived"
    active = store.load("plan_new")
    labels = [s.label for s in active.steps]
    assert "Upload UI" in labels


@pytest.mark.asyncio
async def test_create_archives_other_active_roots(workspace: Path, store: PlanStore):
    existing = Plan(
        id="plan_existing",
        title="Existing",
        status="in_progress",
        steps=[],
    )
    store.save(existing)

    tool = PlanTool(workspace)
    tool._store = store
    tool._team_manager = None
    tool._todo_start_fn = None
    result = await tool.execute(
        {
            "action": "create",
            "title": "Replacement",
            "force_new": True,
            "steps": [{"label": "Only step", "delegatable": True}],
        },
    )
    assert not result.is_error
    assert store.load("plan_existing").status == "archived"


@pytest.mark.asyncio
async def test_block_delegatable_done_without_delegate(workspace: Path, store: PlanStore):
    plan = Plan(
        id="plan_x",
        title="X",
        status="in_progress",
        steps=[
            PlanStep(
                id="step-3",
                label="Backend API",
                status="pending",
                delegatable=True,
            ),
        ],
    )
    store.save(plan)
    tool = PlanTool(workspace)
    tool._store = store
    tool._team_manager = MagicMock()
    tool._team_manager.list_teams.return_value = []
    result = await tool.execute(
        {
            "action": "update",
            "plan_id": "plan_x",
            "step_id": "step-3",
            "status": "done",
            "notes": "phantom",
        },
    )
    assert result.is_error
    assert "delegatable" in result.content.lower()


@pytest.mark.asyncio
async def test_accept_partial_verified_on_disk_no_delegate(
    workspace: Path, store: PlanStore,
):
    proj = workspace / "myapp"
    schema = proj / "backend" / "prisma" / "schema.prisma"
    schema.parent.mkdir(parents=True)
    schema.write_text("model User { id String @id }", encoding="utf-8")

    plan = Plan(
        id="plan_x",
        title="X",
        status="in_progress",
        project_dir="myapp",
        steps=[
            PlanStep(
                id="step-2",
                label="Database schema",
                status="pending",
                delegatable=True,
                output_files=["backend/prisma/schema.prisma"],
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
            "action": "accept_partial",
            "plan_id": "plan_x",
            "step_id": "step-2",
            "reason": "delivered during wave 0 step-1",
            "notes": "schema.prisma has User model",
        },
    )
    assert not result.is_error
    assert store.load("plan_x").get_step("step-2").status == "done"
    assert "[verified_on_disk]" in store.load("plan_x").get_step("step-2").notes


@pytest.mark.asyncio
async def test_update_done_with_verified_artifacts_no_delegate(
    workspace: Path, store: PlanStore,
):
    proj = workspace / "myapp"
    schema = proj / "backend" / "prisma" / "schema.prisma"
    schema.parent.mkdir(parents=True)
    schema.write_text("model User { id String @id }", encoding="utf-8")

    plan = Plan(
        id="plan_x",
        title="X",
        status="in_progress",
        project_dir="myapp",
        steps=[
            PlanStep(
                id="step-2",
                label="Database schema",
                status="pending",
                delegatable=True,
                output_files=["backend/prisma/schema.prisma"],
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
            "step_id": "step-2",
            "status": "done",
            "notes": "Prisma schema complete",
        },
    )
    assert not result.is_error
    step = store.load("plan_x").get_step("step-2")
    assert step.status == "done"
    assert "[verified_on_disk]" in step.notes


@pytest.mark.asyncio
async def test_complete_rejects_force(workspace: Path, store: PlanStore):
    plan = Plan(
        id="plan_force",
        title="T",
        status="in_progress",
        steps=[PlanStep(id="step-1", label="A", status="pending")],
    )
    store.save(plan)
    tool = PlanTool(str(workspace))
    tool._store = store
    tool._team_manager = MagicMock()
    tool._team_manager.list_teams.return_value = []
    result = await tool.execute(
        {"action": "complete", "plan_id": "plan_force", "force": True},
    )
    assert result.is_error
    assert "not allowed" in result.content.lower()


@pytest.mark.asyncio
async def test_complete_requires_all_steps_done(workspace: Path, store: PlanStore):
    plan = Plan(
        id="plan_inc",
        title="T",
        status="in_progress",
        audit=__import__(
            "nls.agentic.plan_store", fromlist=["PlanAudit"],
        ).PlanAudit(last_verified_at=1.0, all_criteria_met=True, issues=[]),
        steps=[
            PlanStep(id="step-1", label="A", status="done"),
            PlanStep(id="step-2", label="B", status="pending"),
        ],
    )
    store.save(plan)
    tool = PlanTool(str(workspace))
    tool._store = store
    tool._team_manager = MagicMock()
    tool._team_manager.list_teams.return_value = []
    result = await tool.execute({"action": "complete", "plan_id": "plan_inc"})
    assert result.is_error
    assert "not properly done" in result.content.lower()


@pytest.mark.asyncio
async def test_find_recoverable_reopens_false_done(workspace: Path, store: PlanStore):
    from nls.agentic.plan_work import AUTO_SKIP_FORCE_TAG

    plan = Plan(
        id="plan_bad_done",
        title="T",
        status="done",
        steps=[
            PlanStep(
                id="step-1",
                label="A",
                status="skipped",
                notes=f"left behind {AUTO_SKIP_FORCE_TAG}",
            ),
        ],
    )
    store.save(plan)
    recovered = store.find_recoverable(reopen=True)
    assert recovered is not None
    assert recovered.id == "plan_bad_done"
    assert store.load("plan_bad_done").status == "blocked"


@pytest.mark.asyncio
async def test_accept_partial_on_blocked_done_plan(workspace: Path, store: PlanStore):
    proj = workspace / "app"
    f = proj / "api.py"
    f.parent.mkdir(parents=True)
    f.write_text("ok", encoding="utf-8")

    plan = Plan(
        id="plan_blocked",
        title="T",
        status="blocked",
        project_dir="app",
        steps=[
            PlanStep(
                id="step-1",
                label="API",
                status="failed",
                delegatable=True,
                output_files=["api.py"],
            ),
        ],
    )
    store.save(plan)
    tool = PlanTool(str(workspace))
    tool._store = store
    tm = MagicMock()
    member = MagicMock()
    member.step_id = "step-1"
    member.status = "failed"
    member.delegate_number = 1
    team = MagicMock()
    team.plan_id = "plan_blocked"
    team.status = "partial"
    team.members = [member]
    tm.list_teams.return_value = [team]
    tool._team_manager = tm

    result = await tool.execute(
        {
            "action": "accept_partial",
            "plan_id": "plan_blocked",
            "step_id": "step-1",
            "reason": "router exists on disk",
            "notes": "verified: api.py",
        },
    )
    assert not result.is_error
    assert store.load("plan_blocked").get_step("step-1").status == "done"
    assert result.details.get("orchestrator_recovery") is True
