"""Tool policy: executing mode must expose bash even on conversational profile."""

from __future__ import annotations

from nls.agentic.orchestration_policy import (
    ToolPolicyInputs,
    resolve_allowed_tools,
    tool_not_allowed_message,
)
from nls.agentic.types import AgentMode


def test_executing_mode_ignores_conversational_profile_deny():
    all_tools = frozenset({"read", "bash", "write", "switch_mode", "contacts"})
    inputs = ToolPolicyInputs(
        mode=AgentMode.EXECUTING,
        must_await_delegates=False,
        delegates_active=False,
        suppress_raw_delegate=False,
        is_coordinator=False,
        all_unlocked=all_tools,
        orchestration_profile="conversational",
    )
    allowed = resolve_allowed_tools(inputs)
    assert "bash" in allowed
    assert "write" in allowed


def test_chat_mode_conversational_profile_denies_bash():
    all_tools = frozenset({"read", "bash", "write", "switch_mode", "contacts"})
    inputs = ToolPolicyInputs(
        mode=AgentMode.CHAT,
        must_await_delegates=False,
        delegates_active=False,
        suppress_raw_delegate=False,
        is_coordinator=False,
        all_unlocked=all_tools,
        orchestration_profile="conversational",
    )
    allowed = resolve_allowed_tools(inputs)
    assert "bash" not in allowed
    assert "switch_mode" in allowed


def test_executing_conversational_block_message_mentions_switch_mode():
    msg = tool_not_allowed_message(
        "bash",
        AgentMode.EXECUTING,
        frozenset(),
        orchestration_profile="conversational",
    )
    assert "switch_mode" in msg
    assert "executing" in msg
