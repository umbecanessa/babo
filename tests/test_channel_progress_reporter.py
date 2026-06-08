"""ChannelProgressReporter — progress messages during channel agentic loops."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nls.agentic.events import AgentEvent, EventType
from nls.runtime.channels import ChannelProgressReporter, _rich_tool_label


def test_rich_tool_label_bash_is_not_silent():
    label = _rich_tool_label("bash", {"command": "ssh root@host tail -80 app.log"})
    assert label is not None
    assert "ssh root@host" in label


def test_rich_tool_label_grep_and_read():
    assert "Searching code" in (_rich_tool_label("grep", {"pattern": "PrimusError"}) or "")
    assert "Reading:" in (_rich_tool_label("read", {"path": "src/main.py"}) or "")


@pytest.mark.asyncio
async def test_reporter_sends_start_and_first_bash_progress():
    adapter = SimpleNamespace(send=AsyncMock())
    reporter = ChannelProgressReporter(adapter, "chat-123", "agent-1")

    await reporter.on_event(AgentEvent(EventType.AGENT_START, {"max_iterations": 40}))
    await reporter.on_event(AgentEvent(
        EventType.TOOL_START,
        {"tool_name": "bash", "arguments": {"command": "npm test"}},
    ))

    assert adapter.send.await_count == 2
    first = adapter.send.await_args_list[0].args[1]
    second = adapter.send.await_args_list[1].args[1]
    assert "Working on it" in first
    assert "npm test" in second


@pytest.mark.parametrize("channel_name", ["discord", "telegram", "slack", "whatsapp"])
@pytest.mark.asyncio
async def test_reporter_works_for_all_channel_adapters(channel_name: str):
    adapter = SimpleNamespace(send=AsyncMock())
    reporter = ChannelProgressReporter(adapter, f"{channel_name}-target", "agent-1")

    await reporter.on_event(AgentEvent(
        EventType.COMMUNICATE,
        {"message": f"Update via {channel_name}"},
    ))

    adapter.send.assert_awaited_once_with(
        f"{channel_name}-target",
        f"Update via {channel_name}",
        agent_id="agent-1",
    )
