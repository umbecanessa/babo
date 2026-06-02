"""Tests for mid-loop orchestration depth reconsideration."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from nls.agentic.profile_depth_policy import (
    apply_orchestration_profile_adoption,
    enrich_profile_blocked_message,
    evaluate_after_tool,
    evaluate_switch_mode_success,
    evaluate_wm_profile_mismatch,
    suppress_depth_nudges,
    validate_profile_adoption,
)
from nls.agentic.types import AgentMode, LoopState, ToolResult


@dataclass
class _Hooks:
    _has_plan: bool = False

    def has_active_plan(self) -> bool:
        return self._has_plan


def _state(
    profile: str = "conversational",
    *,
    hints: list[str] | None = None,
    goals: list[str] | None = None,
) -> LoopState:
    return LoopState(
        orchestration_profile=profile,
        hints=hints or [],
        goals=goals or [],
        user_input="install and configure discord bot",
        unlocked_tools=frozenset({
            "read", "bash", "plan", "todo", "switch_mode",
            "adopt_orchestration_profile", "get_tool_schema",
        }),
    )


def test_suppress_when_forbid_tools():
    st = _state(hints=["forbid:tools"])
    assert suppress_depth_nudges(st) is True


def test_t1_switch_executing_nudge():
    st = _state(
        goals=["Read SKILL.md", "Verify bot online"],
        hints=["setup:instruction_skill"],
    )
    n = evaluate_switch_mode_success(st, "executing")
    assert n is not None
    assert n.trigger_id == "T1_switch_executing"
    assert n.suggested_profile == "solo_structured"
    assert evaluate_switch_mode_success(st, "executing") is None


def test_t2_enrich_blocked_plan():
    st = _state()
    msg = enrich_profile_blocked_message(
        "plan",
        "BLOCKED: tool 'plan' is not available",
        st,
        mode=AgentMode.CHAT,
    )
    assert "ORCHESTRATION DEPTH" in msg
    assert "adopt_orchestration_profile" in msg


def test_t5_sustained_ic_nudge():
    st = _state(
        goals=["Configure discord", "Verify bot"],
        hints=["setup:instruction_skill"],
    )
    st.tool_successes["bash"] = 2
    st.tool_successes["write"] = 1
    ok = ToolResult(content="ok", is_error=False)
    n = evaluate_after_tool(
        st, "bash", {"command": "npm install"}, ok, mode=AgentMode.EXECUTING,
    )
    assert n is not None
    assert n.trigger_id == "T5_sustained_ic"


def test_adopt_solo_structured():
    st = _state()
    hooks = _Hooks()
    ok, msg, details = apply_orchestration_profile_adoption(
        st, "solo_structured", reason="need plan", hooks=hooks,
    )
    assert ok is True
    assert st.orchestration_profile == "solo_structured"
    assert details["adopted_profile"] == "solo_structured"
    assert st.pending_profile_anchor


def test_adopt_rejects_orchestrated_when_forbid_team():
    st = _state(hints=["forbid:team"])
    err = validate_profile_adoption(st, "orchestrated", enable_delegation=True)
    assert err is not None
    assert "forbid" in err.lower() or "orchestrated" in err.lower()


def test_adopt_no_downgrade_with_plan():
    st = _state(profile="solo_structured")
    hooks = _Hooks(_has_plan=True)
    err = validate_profile_adoption(st, "conversational", hooks=hooks)
    assert err is not None


def test_t11_wm_plan_mismatch():
    st = _state()
    n = evaluate_wm_profile_mismatch(
        st, wm_has_strategic_goals=False, wm_has_plan_position=True,
    )
    assert n is not None
    assert n.trigger_id == "T11_wm_mismatch"


def test_t7_coordinator_mode_switch():
    st = _state(profile="conversational")
    n = evaluate_switch_mode_success(st, "planning", enable_delegation=True)
    assert n is not None
    assert n.suggested_profile == "orchestrated"


def test_t7_respects_forbid_team():
    st = _state(profile="conversational", hints=["forbid:team"])
    n = evaluate_switch_mode_success(st, "planning", enable_delegation=True)
    assert n is not None
    assert n.suggested_profile == "solo_structured"


def test_enrich_mode_switch_block_solo():
    from nls.agentic.profile_depth_policy import enrich_mode_switch_block_message

    st = _state(profile="solo_structured")
    msg = enrich_mode_switch_block_message(
        "planning",
        "BLOCKED: switch_mode not available",
        st,
    )
    assert "solo_structured" in msg
    assert "executing mode" in msg.lower()
