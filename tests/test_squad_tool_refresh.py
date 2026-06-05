"""Tests for post-squad_setup agentic tool refresh."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from nls.agentic.squad_tool_refresh import (
    POST_SQUAD_SETUP_NUDGE,
    SQUAD_SETUP_TOOL_NAME,
    apply_if_squad_setup_created,
    is_successful_squad_setup_create,
    merge_squad_tool_schemas,
    refresh_agentic_tools_after_squad_setup,
)
from nls.tools.agent_tools.base import AgentTool
from nls.tools.agent_tools.squad import SquadTool


class _NamedTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeSquadTool(AgentTool):
    @property
    def name(self) -> str:
        return "squad"

    @property
    def description(self) -> str:
        return "squad tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, params, signal=None):
        raise NotImplementedError


def test_is_successful_squad_setup_create():
    ok = SimpleNamespace(is_error=False)
    bad = SimpleNamespace(is_error=True)
    assert is_successful_squad_setup_create(
        SQUAD_SETUP_TOOL_NAME, {"action": "create"}, ok,
    )
    assert not is_successful_squad_setup_create("squad", {"action": "create"}, ok)
    assert not is_successful_squad_setup_create(
        SQUAD_SETUP_TOOL_NAME, {"action": "create"}, bad,
    )
    assert not is_successful_squad_setup_create(
        SQUAD_SETUP_TOOL_NAME, {"action": "other"}, ok,
    )


def test_merge_squad_tool_schemas_swaps_setup_for_squad():
    setup_schema = {"function": {"name": "squad_setup"}}
    tools = {"squad": _FakeSquadTool()}
    merged = merge_squad_tool_schemas(
        [setup_schema],
        tools,
        added=["squad"],
        removed=["squad_setup"],
    )
    names = {(s.get("function") or {}).get("name") for s in merged}
    assert "squad_setup" not in names
    assert "squad" in names


@patch("nls.agentic.squad_tool_refresh.refresh_agentic_tools_after_squad_setup")
def test_apply_if_squad_setup_created_updates_schemas(mock_refresh):
    mock_refresh.return_value = (["squad"], ["squad_setup"])
    tools = {"squad": _FakeSquadTool(), SQUAD_SETUP_TOOL_NAME: object()}
    all_schemas = [{"function": {"name": "squad_setup"}}]
    base_schemas = [{"function": {"name": "squad_setup"}}]
    all_unlocked = {"squad_setup", "bash"}
    state = SimpleNamespace(unlocked_tools={"squad_setup", "bash"})

    nudge = apply_if_squad_setup_created(
        SQUAD_SETUP_TOOL_NAME,
        {"action": "create"},
        SimpleNamespace(is_error=False),
        agent_id="agent-1",
        tools=tools,
        all_schemas=all_schemas,
        all_unlocked=all_unlocked,
        base_schemas=base_schemas,
        state=state,
    )

    assert nudge == POST_SQUAD_SETUP_NUDGE
    assert "squad" in all_unlocked
    assert "squad_setup" not in all_unlocked
    assert any(
        (s.get("function") or {}).get("name") == "squad"
        for s in all_schemas
    )
    assert "squad" in state.unlocked_tools


@patch("server.main.app")
def test_refresh_agentic_tools_after_squad_setup(mock_app):
    squad_tool = _NamedTool("squad")
    msg_tool = _NamedTool("squad_message")
    setup_tool = _NamedTool("squad_setup")
    runtime = SimpleNamespace(
        _agent_tools=[setup_tool, squad_tool, msg_tool],
        sync_squad_tools=MagicMock(),
    )
    mock_app.state.agent_manager.get_runtime.return_value = runtime

    tools = {SQUAD_SETUP_TOOL_NAME: setup_tool, "bash": _NamedTool("bash")}
    added, removed = refresh_agentic_tools_after_squad_setup("agent-x", tools)

    runtime.sync_squad_tools.assert_called_once()
    assert "squad" in added
    assert "squad_message" in added
    assert removed == [SQUAD_SETUP_TOOL_NAME]
    assert SQUAD_SETUP_TOOL_NAME not in tools
    assert "squad" in tools


def test_squad_tool_spawn_member_passes_action_once():
    sm = MagicMock()
    sm.get_squad_for_agent.return_value = SimpleNamespace(
        id="sq1",
        is_lead=lambda _aid: True,
        is_member=lambda _aid: True,
    )
    sm.resolve_squad_for_caller.return_value = sm.get_squad_for_agent.return_value
    sm.handle_action_async = AsyncMock(return_value={"agent_id": "member-1"})

    tool = SquadTool(sm, "lead-1")
    result = asyncio.run(tool.execute({
        "action": "spawn_member",
        "name": "Mod Agent",
        "description": "Moderate channels",
    }))

    assert not result.is_error
    sm.handle_action_async.assert_awaited_once_with(
        "lead-1",
        "spawn_member",
        name="Mod Agent",
        description="Moderate channels",
    )


def test_squad_tool_configure_member_passes_action_once():
    sm = MagicMock()
    sm.get_squad_for_agent.return_value = SimpleNamespace(
        id="sq1",
        is_lead=lambda _aid: True,
        is_member=lambda _aid: True,
    )
    sm.resolve_squad_for_caller.return_value = sm.get_squad_for_agent.return_value
    sm.handle_action_async = AsyncMock(return_value={"agent_id": "m1"})

    tool = SquadTool(sm, "lead-1")
    result = asyncio.run(tool.execute({
        "action": "configure_member",
        "target_agent_id": "m1",
        "channel": "discord",
        "skill_config": {"owner_identity": "owner"},
        "interaction_mode": "shared_only",
    }))

    assert not result.is_error
    sm.handle_action_async.assert_awaited_once_with(
        "lead-1",
        "configure_member",
        target_agent_id="m1",
        channel="discord",
        skill_config={"owner_identity": "owner"},
        interaction_mode="shared_only",
    )
