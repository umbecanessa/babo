"""Unified tool policy — resolve_allowed_tools and schema refresh."""

from __future__ import annotations

from nls.agentic.orchestration_policy import (
    ToolPolicyInputs,
    apply_runtime_tool_filter,
    build_tool_policy_inputs,
    compute_tool_policy_fingerprint,
    refresh_tool_schemas,
    resolve_allowed_tools,
)
from nls.agentic.types import AgentMode, LoopState


def _inputs(**kwargs) -> ToolPolicyInputs:
    defaults = dict(
        mode=AgentMode.DELEGATING,
        must_await_delegates=False,
        delegates_active=False,
        suppress_raw_delegate=False,
        is_coordinator=True,
        all_unlocked=frozenset({"team", "delegate", "plan", "bash", "read"}),
    )
    defaults.update(kwargs)
    return ToolPolicyInputs(**defaults)


def test_executing_keeps_bash_while_delegates_active():
    allowed = resolve_allowed_tools(_inputs(
        mode=AgentMode.EXECUTING,
        delegates_active=True,
        suppress_raw_delegate=True,
        is_coordinator=False,
        all_unlocked=frozenset({"bash", "read", "delegate", "write"}),
    ))
    assert "bash" in allowed
    assert "delegate" in allowed


def test_chat_keeps_lookup_tools_while_delegates_active():
    """CHAT mode menu is unchanged when a background delegate runs."""
    allowed = resolve_allowed_tools(_inputs(
        mode=AgentMode.CHAT,
        delegates_active=True,
        suppress_raw_delegate=True,
        is_coordinator=False,
        all_unlocked=frozenset({"read", "web_search", "list_dir"}),
    ))
    assert "read" in allowed
    assert "web_search" in allowed


def test_delegating_hides_delegate_when_plan_suppresses():
    allowed = resolve_allowed_tools(_inputs(
        mode=AgentMode.DELEGATING,
        suppress_raw_delegate=True,
        is_coordinator=True,
    ))
    assert "delegate" not in allowed
    assert "team" in allowed


def test_delegating_shrinks_while_wave_runs():
    allowed = resolve_allowed_tools(_inputs(
        mode=AgentMode.DELEGATING,
        delegates_active=True,
        is_coordinator=True,
    ))
    assert "bash" not in allowed
    assert "team" in allowed


def test_fingerprint_stable_across_identical_inputs():
    a = compute_tool_policy_fingerprint(_inputs())
    b = compute_tool_policy_fingerprint(_inputs())
    assert a == b


def test_refresh_skips_when_fingerprint_unchanged():
    state = LoopState(user_input="x")
    state.active_mode = AgentMode.DELEGATING
    schemas = [{"type": "function", "function": {"name": "team"}}]
    unlocked = {"team", "delegate"}
    out1, u1, c1 = refresh_tool_schemas(
        state, schemas, unlocked, state.active_mode, None, None, force=True,
    )
    assert c1
    out2, u2, c2 = refresh_tool_schemas(
        state, schemas, unlocked, state.active_mode, None, None,
    )
    assert not c2
    assert out2 is schemas


def test_build_tool_policy_inputs_from_state():
    state = LoopState(user_input="x")
    state.active_mode = AgentMode.EXECUTING
    inp = build_tool_policy_inputs(
        state.active_mode, state, None, {"bash", "read"}, None,
    )
    assert inp.mode == AgentMode.EXECUTING
    assert not inp.is_coordinator


def test_block_tool_call_ic_message_before_generic_allowlist():
    from nls.agentic.orchestration_policy import block_tool_call

    state = LoopState(user_input="x")
    state.active_mode = AgentMode.DELEGATING
    msg = block_tool_call(
        "write",
        {},
        state,
        AgentMode.DELEGATING,
        object(),  # delegate_manager stub — delegates_running may be false
        hooks=None,
        all_unlocked={"team", "write", "read", "delegate"},
    )
    # With no running delegates, write is allowed in delegating mode — no block.
    assert msg is None


def test_block_tool_call_suppresses_read_while_wave_runs():
    from nls.agentic.orchestration_policy import block_tool_call

    class _DM:
        @staticmethod
        def has_active_delegates():
            return True

    state = LoopState(user_input="x")
    state.active_mode = AgentMode.DELEGATING
    msg = block_tool_call(
        "read",
        {},
        state,
        AgentMode.DELEGATING,
        _DM(),
        hooks=None,
        all_unlocked={"team", "read", "write", "await_delegates"},
    )
    assert msg is not None
    assert "IC work" in msg or "not available" in msg


def test_apply_runtime_filter_uses_resolve():
    state = LoopState(user_input="x")
    state.active_mode = AgentMode.DELEGATING
    schemas = [
        {"type": "function", "function": {"name": "team"}},
        {"type": "function", "function": {"name": "delegate"}},
    ]
    filtered, tools = apply_runtime_tool_filter(
        schemas,
        {"team", "delegate"},
        AgentMode.DELEGATING,
        state,
        None,
        hooks=None,
        suppress_raw_delegate=True,
    )
    assert "delegate" not in {s["function"]["name"] for s in filtered}
    assert "delegate" not in tools
