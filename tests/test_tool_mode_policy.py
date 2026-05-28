"""Tests for tool-driven AgentMode transitions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nls.agentic.tool_mode_policy import (
    ModeTransition,
    apply_dispatch_mode,
    apply_tool_mode_transition,
    compute_tool_mode_transition,
    user_mode_switch_blocks_auto,
)
from nls.agentic.types import AgentMode, LoopState
from nls.tools.agent_tools.base import ToolResult
from nls.tools.agent_tools.plan import Plan, PlanStep, PlanStore


@pytest.fixture
def state() -> LoopState:
    s = LoopState(user_input="test")
    s.iteration = 10
    s.user_mode_switch_iter = -10
    return s


@pytest.fixture
def plan_store(tmp_path: Path) -> PlanStore:
    agent_dir = tmp_path / "agent"
    (agent_dir / "workspace").mkdir(parents=True)
    store = PlanStore(agent_dir)
    plan = Plan(
        id="plan_t",
        title="Build App",
        project_dir="proj",
        steps=[
            PlanStep(id="s1", label="Scaffold", delegatable=True),
            PlanStep(id="s2", label="API", delegatable=True, depends_on=["s1"]),
        ],
    )
    store.save(plan)
    return store


@pytest.fixture
def plan_tool(plan_store: PlanStore) -> MagicMock:
    tool = MagicMock()
    tool.get_store.return_value = plan_store
    return tool


def _tc(name: str, action: str, **extra) -> dict:
    import json
    args = {"action": action, **extra}
    return {"function": {"name": name, "arguments": json.dumps(args)}}


def test_team_launch_from_evaluating(state: LoopState):
    state.active_mode = AgentMode.EVALUATING
    tcs = [_tc("team", "launch", team_id="team_x")]
    results = [ToolResult(content="ok", details={"action": "launch"})]
    tr = compute_tool_mode_transition(
        state, tcs, results, enable_delegation=True, plan_tool=None,
    )
    assert tr is not None
    assert tr.to_mode == AgentMode.MONITORING
    assert tr.reason == "team_launch"


def test_team_create_from_planning(state: LoopState):
    state.active_mode = AgentMode.PLANNING
    tcs = [_tc("team", "create", plan_id="plan_t", wave=0)]
    results = [ToolResult(content="ok", details={"action": "create"})]
    tr = compute_tool_mode_transition(
        state, tcs, results, enable_delegation=True, plan_tool=None,
    )
    assert tr is not None
    assert tr.to_mode == AgentMode.DELEGATING


def test_team_advance_to_evaluating(state: LoopState):
    state.active_mode = AgentMode.MONITORING
    tcs = [_tc("team", "advance", team_id="team_w0")]
    results = [ToolResult(content="ok", details={"action": "advance"})]
    tr = compute_tool_mode_transition(
        state, tcs, results, enable_delegation=True, plan_tool=None,
    )
    assert tr is not None
    assert tr.to_mode == AgentMode.EVALUATING


def test_team_advance_next_wave_to_delegating(state: LoopState):
    state.active_mode = AgentMode.EVALUATING
    tcs = [_tc("team", "advance", team_id="team_w0")]
    results = [
        ToolResult(
            content="ok",
            details={"action": "advance", "next_team": True, "team_id": "team_w2"},
        ),
    ]
    tr = compute_tool_mode_transition(
        state, tcs, results, enable_delegation=True, plan_tool=None,
    )
    assert tr is not None
    assert tr.to_mode == AgentMode.DELEGATING
    assert tr.reason == "team_advance_next_wave"


def test_plan_create_team_plan(state: LoopState, plan_tool: MagicMock):
    state.active_mode = AgentMode.EXECUTING
    tcs = [_tc("plan", "create", title="Big Build")]
    results = [
        ToolResult(
            content="ok",
            details={"action": "create", "plan_id": "plan_t"},
        ),
    ]
    tr = compute_tool_mode_transition(
        state, tcs, results, enable_delegation=True, plan_tool=plan_tool,
    )
    assert tr is not None
    assert tr.to_mode == AgentMode.DELEGATING
    assert tr.reason == "plan_create_team"


def test_plan_fix_dependencies_to_planning(state: LoopState):
    state.active_mode = AgentMode.DELEGATING
    tcs = [_tc("plan", "fix_dependencies")]
    results = [ToolResult(content="ok", details={"action": "fix_dependencies"})]
    tr = compute_tool_mode_transition(
        state, tcs, results, enable_delegation=True, plan_tool=None,
    )
    assert tr is not None
    assert tr.to_mode == AgentMode.PLANNING


def test_plan_delete_to_planning(state: LoopState):
    state.active_mode = AgentMode.EVALUATING
    tcs = [_tc("plan", "delete")]
    results = [ToolResult(content="ok", details={"action": "delete"})]
    tr = compute_tool_mode_transition(
        state, tcs, results, enable_delegation=True, plan_tool=None,
    )
    assert tr is not None
    assert tr.to_mode == AgentMode.PLANNING


def test_plan_accept_partial_evaluating(state: LoopState):
    state.active_mode = AgentMode.MONITORING
    tcs = [_tc("plan", "accept_partial", step_id="s1")]
    results = [
        ToolResult(
            content="ok",
            details={"action": "accept_partial", "wave_needs_advance": True},
        ),
    ]
    tr = compute_tool_mode_transition(
        state, tcs, results, enable_delegation=True, plan_tool=None,
    )
    assert tr is not None
    assert tr.to_mode == AgentMode.EVALUATING


def test_todo_add_build_no_plan(state: LoopState):
    state.active_mode = AgentMode.EXECUTING
    tcs = [_tc("todo", "add", title="Build platform end-to-end")]
    results = [ToolResult(content="ok", details={"action": "add"})]
    tr = compute_tool_mode_transition(
        state, tcs, results, enable_delegation=True, plan_tool=None,
    )
    assert tr is not None
    assert tr.to_mode == AgentMode.PLANNING


def test_user_switch_grace_blocks(state: LoopState):
    state.user_mode_switch_iter = 9
    state.active_mode = AgentMode.DELEGATING
    tcs = [_tc("team", "launch")]
    results = [ToolResult(content="ok", details={"action": "launch"})]
    assert compute_tool_mode_transition(
        state, tcs, results, enable_delegation=True, plan_tool=None,
    ) is None
    assert user_mode_switch_blocks_auto(state)


def test_dispatch_pending_launch(state: LoopState):
    state.active_mode = AgentMode.EVALUATING
    tr = apply_dispatch_mode(
        state, "pending_wave_launch:team_abc", enable_delegation=True,
    )
    assert tr is not None
    assert tr.to_mode == AgentMode.DELEGATING


def test_apply_transition_updates_state(state: LoopState):
    tr = ModeTransition(
        AgentMode.PLANNING,
        AgentMode.DELEGATING,
        reason="team_create",
        refresh_schemas=True,
    )
    state.active_mode = AgentMode.PLANNING
    state._mode_schemas_applied = True
    assert apply_tool_mode_transition(state, tr) is True
    assert state.active_mode == AgentMode.DELEGATING
    assert state._mode_schemas_applied is False
