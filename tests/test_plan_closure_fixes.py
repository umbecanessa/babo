"""Tests for plan closure audit, contract errors, and board reconcile."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nls.agentic.plan_store import Plan, PlanStep, PlanStore
from nls.agentic.plan_work import (
    apply_stale_wave_wake_redirect,
    build_board_snapshot_lines,
    needs_board_reconcile,
    resolve_board_reconcile_wake,
)
from nls.agentic.tool_result_semantics import (
    counts_toward_error_budget,
    is_tool_contract_error,
)
from nls.tools.agent_tools.guardrails_registry import (
    AgentGuardrailsRegistry,
    record_tool_contract_guardrail,
)
from nls.tools.agent_tools.base import ToolResult
from nls.tools.agent_tools.plan import PlanTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def test_audit_local_tests_recognizes_tsc_in_notes(workspace: Path) -> None:
    store = PlanStore(workspace)
    plan = Plan(
        id="plan_t",
        title="T",
        status="in_progress",
        steps=[
            PlanStep(
                id="step-1",
                label="Local Verification",
                status="done",
                notes="npx tsc --noEmit exit 0; node --check src/index.js ok",
            ),
        ],
    )
    store.save(plan)
    tool = PlanTool(str(workspace))
    issues = tool._audit_local_tests(plan)
    assert not any("Local tests not recorded" in i for i in issues)


@pytest.mark.asyncio
async def test_accept_partial_rejects_already_done_step(workspace: Path) -> None:
    store = PlanStore(workspace)
    plan = Plan(
        id="plan_t",
        title="T",
        steps=[PlanStep(id="step-9", label="Verify", status="done")],
    )
    store.save(plan)
    tool = PlanTool(str(workspace))

    result = await tool.execute({
        "action": "accept_partial",
        "plan_id": plan.id,
        "step_id": "step-9",
        "notes": "x",
        "reason": "y",
    })
    assert result.is_error
    assert "already done" in result.content
    assert "plan(action='verify'" in result.content


def test_plan_contract_error_not_counted_toward_stall_budget() -> None:
    r = ToolResult(
        content="Error: 'reason' required — explain why accepting partial work.",
        is_error=True,
    )
    assert is_tool_contract_error("plan", r)
    assert not counts_toward_error_budget("plan", r)


def test_plan_operational_error_still_counts_toward_stall() -> None:
    r = ToolResult(
        content="Internal error: database locked while saving plan.json",
        is_error=True,
    )
    assert not is_tool_contract_error("plan", r)
    assert counts_toward_error_budget("plan", r)


def test_plan_complete_gate_is_contract() -> None:
    r = ToolResult(
        content=(
            "Cannot complete plan plan_x — verification reported 2 "
            "issue(s). Fix them or accept_partial where appropriate."
        ),
        is_error=True,
    )
    assert is_tool_contract_error("plan", r)
    assert not counts_toward_error_budget("plan", r)


def test_guardrails_registry_records_and_dedupes(tmp_path: Path) -> None:
    reg = AgentGuardrailsRegistry(tmp_path / "agent")
    record_tool_contract_guardrail(
        reg,
        tool_name="plan",
        content="Error: 'reason' required for accept_partial.",
    )
    record_tool_contract_guardrail(
        reg,
        tool_name="plan",
        content="Error: 'reason' required for accept_partial.",
    )
    assert len(reg.recent_lines()) == 1


def test_needs_board_reconcile_when_steps_done_plan_open(workspace: Path) -> None:
    plan = Plan(
        id="plan_open",
        title="T",
        status="in_progress",
        todo_id="todo_1",
        steps=[PlanStep(id="s1", label="A", status="done")],
    )
    plan.audit.issues = ["verify blocker"]
    todo_store = MagicMock()
    todo_store.get.return_value = MagicMock(status="in_progress")
    assert needs_board_reconcile(plan, todo_store=todo_store)


def test_resolve_board_reconcile_wake(workspace: Path) -> None:
    store = PlanStore(workspace)
    plan = Plan(
        id="plan_x",
        title="T",
        status="in_progress",
        steps=[PlanStep(id="s1", label="A", status="done")],
    )
    store.save(plan)
    plan_tool = PlanTool(str(workspace))
    ctx = resolve_board_reconcile_wake(
        plan_tool=plan_tool,
        team_manager=None,
        stale_reason="wave_already_finalized",
        team_id="team_1",
    )
    assert ctx is not None
    assert ctx.plan_id == "plan_x"
    assert "BOARD CHECK" in ctx.message


def test_apply_stale_wave_wake_redirect_to_board(workspace: Path) -> None:
    store = PlanStore(workspace)
    plan = Plan(
        id="plan_sw",
        title="T",
        status="in_progress",
        steps=[PlanStep(id="s1", label="A", status="done")],
    )
    store.save(plan)
    plan_tool = PlanTool(str(workspace))
    tm = MagicMock()
    tm.stale_wave_review_wake_reason.return_value = "wave_already_finalized"
    src, msg, exit_reason = apply_stale_wave_wake_redirect(
        "team_wave_complete:team_abc",
        team_manager=tm,
        plan_tool=plan_tool,
    )
    assert exit_reason is None
    assert src == "board_reconcile:plan_sw"
    assert msg is not None
    assert "BOARD CHECK" in msg
    tm._drain_wave_complete_dispatch.assert_called_once_with("team_abc")


def test_board_snapshot_lines() -> None:
    plan = Plan(
        id="plan_z",
        title="Z",
        status="in_progress",
        steps=[
            PlanStep(id="s1", label="A", status="done"),
            PlanStep(id="s2", label="B", status="pending"),
        ],
    )
    lines = build_board_snapshot_lines(plan)
    assert any("BOARD SNAPSHOT" in ln for ln in lines)
    assert any("blocking closure" in ln for ln in lines)
