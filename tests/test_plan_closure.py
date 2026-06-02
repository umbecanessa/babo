"""Tests for plan closure nudges and auto-complete helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from nls.agentic.breadcrumbs import BreadcrumbContext, BreadcrumbEngine
from nls.agentic.orchestration_policy import build_evaluating_action_breadcrumb
from nls.agentic.plan_store import Plan, PlanStep
from nls.agentic.plan_work import (
    auto_complete_active_plan_if_ready,
    format_plan_closure_nudge,
    format_wave_complete_wake,
    plan_open_step_count,
)
from nls.tools.agent_tools.base import ToolResult


def test_plan_open_step_count():
    plan = Plan(
        id="plan_1",
        title="T",
        steps=[
            PlanStep(id="s1", label="A", status="done"),
            PlanStep(id="s2", label="B", status="pending"),
        ],
    )
    assert plan_open_step_count(plan) == 1


def test_format_plan_closure_nudge():
    msg = format_plan_closure_nudge("plan_abc")
    assert "plan(action='verify'" in msg
    assert "plan(action='complete'" in msg
    assert "task_complete" in msg


def test_wave_complete_wake_includes_closure_when_no_pending_steps():
    msg = format_wave_complete_wake(
        plan_id="plan_x",
        team_id="team_y",
        pending_step_count=0,
    )
    assert "PLAN CLOSURE" in msg
    assert "plan(verify)" in msg


def test_wave_complete_wake_mentions_remaining_steps():
    msg = format_wave_complete_wake(
        plan_id="plan_x",
        team_id="team_y",
        pending_step_count=2,
    )
    assert "2 plan step(s) still open" in msg
    assert "PLAN CLOSURE" not in msg


def test_breadcrumb_plan_verify_passed():
    engine = BreadcrumbEngine()
    hint = engine.evaluate(BreadcrumbContext(
        tool_name="plan",
        action="verify",
        unlocked_tools=frozenset({"plan", "task_complete", "team"}),
        orchestration_profile="orchestrated",
        is_coordinator=True,
        result_details={
            "plan_id": "plan_1",
            "all_criteria_met": True,
        },
    ))
    assert hint is not None
    assert "plan(action='complete'" in hint


def test_breadcrumb_team_advance_plan_ready():
    engine = BreadcrumbEngine()
    hint = engine.evaluate(BreadcrumbContext(
        tool_name="team",
        action="advance",
        unlocked_tools=frozenset({"plan", "task_complete", "team"}),
        orchestration_profile="orchestrated",
        is_coordinator=True,
        result_details={
            "plan_id": "plan_1",
            "plan_ready_to_close": True,
        },
    ))
    assert hint is not None
    assert "PLAN CLOSURE" in hint


def test_evaluating_breadcrumb_when_can_complete():
    plan = Plan(
        id="plan_done",
        title="Done",
        status="in_progress",
        steps=[PlanStep(id="s1", label="Only", status="done")],
    )
    plan.audit.last_verified_at = 1.0
    plan.audit.all_criteria_met = True

    plan_tool = MagicMock()
    plan_tool._store.find_active.return_value = plan
    plan_tool._team_manager = MagicMock()
    plan_tool._team_manager.list_teams.return_value = []

    msg = build_evaluating_action_breadcrumb(plan_tool)
    assert "PLAN CLOSURE" in msg


@pytest.mark.asyncio
async def test_auto_complete_active_plan_if_ready():
    plan = Plan(
        id="plan_done",
        title="Done",
        status="in_progress",
        steps=[PlanStep(id="s1", label="Only", status="done")],
    )
    plan.audit.last_verified_at = 1.0
    plan.audit.all_criteria_met = True

    store = MagicMock()
    store.find_active.return_value = plan

    plan_tool = MagicMock()
    plan_tool.get_store.return_value = store
    plan_tool.execute = AsyncMock(return_value=ToolResult(content="done", is_error=False))
    plan_tool._team_manager = MagicMock()
    plan_tool._team_manager.list_teams.return_value = []

    pid = await auto_complete_active_plan_if_ready(plan_tool)
    assert pid == "plan_done"
    plan_tool.execute.assert_awaited_once()
