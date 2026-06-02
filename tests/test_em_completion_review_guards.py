"""EM completion-review guards from forensic run 05282165."""

from __future__ import annotations

from nls.agentic.coordinator_guard import block_em_executing_during_review
from nls.agentic.orchestration_policy import (
    PARTIAL_COMPLETION_REVIEW_TOOLS,
    ToolPolicyInputs,
    block_tool_call,
    build_tool_policy_inputs,
    resolve_allowed_tools,
    should_force_coordinator_yield,
)
from nls.agentic.types import AgentMode, LoopState
from nls.tools.agent_tools.file_ledger import (
    append_must_read_scaffold_hint,
    strip_redundant_project_prefix,
)


def test_block_executing_during_completion_review():
    msg = block_em_executing_during_review(
        AgentMode.EXECUTING,
        active_mode=AgentMode.MONITORING,
        dispatch_source="team_completion_review:team_abc",
        has_pending_completion_reviews=True,
        enable_delegation=True,
        is_delegate_loop=False,
    )
    assert msg is not None
    assert "engineering manager" in msg.lower()


def test_executing_allowed_in_evaluating_when_no_pending_review():
    msg = block_em_executing_during_review(
        AgentMode.EXECUTING,
        active_mode=AgentMode.EVALUATING,
        dispatch_source="team_wave_complete:team_abc",
        has_pending_completion_reviews=False,
        enable_delegation=True,
        is_delegate_loop=False,
    )
    assert msg is None


def test_partial_completion_review_tool_set_excludes_bash():
    inputs = ToolPolicyInputs(
        mode=AgentMode.EVALUATING,
        must_await_delegates=False,
        delegates_active=True,
        suppress_raw_delegate=False,
        is_coordinator=True,
        all_unlocked=frozenset({"bash", "team", "read", "write", "plan"}),
        orchestration_profile="orchestrated",
        evaluating_wave_delivery=False,
    )
    allowed = resolve_allowed_tools(inputs)
    assert "team" in allowed
    assert "read" in allowed
    assert "bash" not in allowed
    assert "write" not in allowed


def test_wait_blocked_in_evaluating_mode():
    state = LoopState()
    state.coordinator_mode = True
    state.dispatch_source = "team_completion_review:team_x"
    msg = block_tool_call(
        "wait",
        {"seconds": 30},
        state,
        AgentMode.EVALUATING,
        delegate_manager=object(),
    )
    assert msg is not None
    assert "wait(30s)" in msg


class _ActiveDelegates:
    def has_active_delegates(self) -> bool:
        return True


def test_idle_yield_suppressed_with_pending_completion_reviews():
    state = LoopState()
    state.active_mode = AgentMode.EVALUATING
    state.idle_monitor_cycles = 5
    force, reason = should_force_coordinator_yield(
        state,
        delegate_manager=_ActiveDelegates(),
        dispatch_source="team_completion_review:team_x",
        has_pending_completion_reviews=True,
    )
    assert force is False
    assert reason == ""


def test_burn_cap_suppressed_with_pending_completion_reviews():
    state = LoopState()
    state.active_mode = AgentMode.MONITORING
    state.coordinator_burn_iters = 10
    force, reason = should_force_coordinator_yield(
        state,
        delegate_manager=_ActiveDelegates(),
        has_pending_completion_reviews=True,
    )
    assert force is False
    assert reason == ""


def test_strip_redundant_project_prefix():
    cwd = "/workspace/icf-coaching-session-evaluation-platform"
    assert (
        strip_redundant_project_prefix(
            "icf-coaching-session-evaluation-platform/backend/app/main.py",
            cwd,
        )
        == "backend/app/main.py"
    )


def test_must_read_scaffold_hint():
    raw = "MUST READ FIRST: foo.py exists"
    out = append_must_read_scaffold_hint(raw)
    assert "bash/npm/pnpm scaffolded" in out
