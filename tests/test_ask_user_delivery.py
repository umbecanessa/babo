"""ask_user must surface the question in chat, not only block on copilot_queue."""

from __future__ import annotations

import asyncio

import pytest

from nls.agentic.events import AgentEvent, EventType
from nls.agentic.executor import _handle_ask_user
from nls.agentic.bridge import LoopHooks


@pytest.mark.asyncio
async def test_handle_ask_user_emits_chat_visible_events():
    events: list[AgentEvent] = []

    async def on_event(event: AgentEvent) -> None:
        events.append(event)

    queue: asyncio.Queue[str] = asyncio.Queue()
    queue.put_nowait("token-123")
    hooks = LoopHooks(copilot_queue=queue)

    result = await _handle_ask_user(
        {"question": "What is your Discord bot token?"},
        on_event,
        hooks,
        iteration=3,
        tool_call_id="call_test",
    )

    types = [e.type for e in events]
    assert EventType.ASK_USER in types
    assert EventType.COMMUNICATE in types
    assert EventType.STATUS in types

    ask = next(e for e in events if e.type == EventType.ASK_USER)
    assert ask.data["question"] == "What is your Discord bot token?"
    assert ask.data["request_id"] == "call_test"

    comm = next(e for e in events if e.type == EventType.COMMUNICATE)
    assert comm.data["message"] == "What is your Discord bot token?"
    assert comm.data.get("user_facing") is True

    status = next(e for e in events if e.type == EventType.STATUS)
    assert status.data.get("status") == "waiting_for_user"

    assert "token-123" in result.content
