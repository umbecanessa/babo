"""Simple delegate() monitoring — solo_structured without team waves."""

from __future__ import annotations

from unittest.mock import MagicMock

from nls.agentic.orchestration_policy import (
    SIMPLE_DELEGATE_MONITOR_TOOLS,
    build_simple_delegate_wake_message,
    build_tool_policy_inputs,
    is_simple_delegate_monitoring,
    resolve_allowed_tools,
)
from nls.agentic.types import AgentMode, LoopState


def test_is_simple_delegate_when_solo_and_no_team():
    state = LoopState(orchestration_profile="solo_structured")
    dm = MagicMock()
    dm.has_active_delegates.return_value = True
    tm = MagicMock()
    tm.list_teams.return_value = []
    assert is_simple_delegate_monitoring(state, dm, team_manager=tm) is True


def test_not_simple_when_orchestrated_profile():
    state = LoopState(orchestration_profile="orchestrated")
    dm = MagicMock()
    dm.has_active_delegates.return_value = True
    assert is_simple_delegate_monitoring(state, dm) is False


def test_not_simple_when_active_team():
    state = LoopState(orchestration_profile="solo_structured")
    dm = MagicMock()
    dm.has_active_delegates.return_value = True
    team = MagicMock()
    team.status = "running"
    tm = MagicMock()
    tm.list_teams.return_value = [team]
    assert is_simple_delegate_monitoring(state, dm, team_manager=tm) is False


def test_simple_delegate_tool_surface_post_launch():
    state = LoopState(
        orchestration_profile="solo_structured",
        simple_delegate_monitoring=True,
        must_await_delegates=True,
        active_mode=AgentMode.MONITORING,
    )
    dm = MagicMock()
    dm.has_active_delegates.return_value = True
    all_unlocked = set(SIMPLE_DELEGATE_MONITOR_TOOLS) | {
        "plan", "todo", "team", "read", "bash", "wait", "scheduler",
    }
    inputs = build_tool_policy_inputs(
        AgentMode.MONITORING, state, dm, all_unlocked, None,
    )
    allowed = resolve_allowed_tools(inputs)
    assert "await_delegates" in allowed
    assert "delegate_status" in allowed
    assert "plan" not in allowed
    assert "team" not in allowed
    assert "read" not in allowed


def test_simple_delegate_tool_surface_monitoring():
    state = LoopState(
        orchestration_profile="solo_structured",
        simple_delegate_monitoring=True,
        must_await_delegates=False,
        active_mode=AgentMode.MONITORING,
    )
    dm = MagicMock()
    dm.has_active_delegates.return_value = True
    all_unlocked = set(SIMPLE_DELEGATE_MONITOR_TOOLS) | {
        "plan", "todo", "team", "read", "bash", "wait",
    }
    inputs = build_tool_policy_inputs(
        AgentMode.MONITORING, state, dm, all_unlocked, None,
    )
    allowed = resolve_allowed_tools(inputs)
    assert "scheduler" in allowed
    assert "delegate_status" in allowed
    assert "plan" not in allowed


def test_build_tool_policy_inputs_uses_cached_team_manager():
    state = LoopState(orchestration_profile="solo_structured")
    dm = MagicMock()
    dm.has_active_delegates.return_value = True
    team = MagicMock()
    team.status = "running"
    tm = MagicMock()
    tm.list_teams.return_value = [team]
    hooks = MagicMock()
    hooks._cached_team_manager = tm
    inputs = build_tool_policy_inputs(
        AgentMode.MONITORING,
        state,
        dm,
        {"delegate_status", "await_delegates"},
        hooks,
    )
    assert inputs.simple_delegate_monitoring is False


def test_simple_delegate_wake_message():
    msg = build_simple_delegate_wake_message(
        dispatch_source="scheduler",
        delegate_summary="1 delegate(s) running",
    )
    assert "[DELEGATE MONITOR]" in msg
    assert "await_delegates" in msg
    assert "not to re-read the repo" in msg
