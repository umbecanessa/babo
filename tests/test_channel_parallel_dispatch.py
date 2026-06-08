"""Channel turns keep full tools while Home holds the primary deep slot."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nls.engine.events import AgentEvent, EngagementDepth, EventType
from nls.engine.inner_loop import InnerLoop


def _rt(*, user_busy: bool = True, deep_busy: bool = True):
    slot_mgr = SimpleNamespace(deep=SimpleNamespace(is_busy=deep_busy))
    return SimpleNamespace(
        agent_id="lead-1",
        is_busy=user_busy,
        is_user_busy=user_busy,
        is_agentic_enabled=lambda: True,
        inference_available=lambda: True,
        config={"agency": {"agentic_loop": {"use_v2": True}}},
        channel_registry=None,
        _slot_manager=slot_mgr,
        _team_manager=None,
        process_message_agentic_async=AsyncMock(),
    )


def test_can_dispatch_event_queue_allows_channel_while_user_busy():
    il = InnerLoop.__new__(InnerLoop)
    il._autonomous_executing = False
    il._use_model_a = False
    il.event_queue = MagicMock()
    il.event_queue.is_empty = False
    il.event_queue.peek_types.return_value = [EventType.CHANNEL_MESSAGE]

    rt = _rt(user_busy=True)
    rt.config = {"agency": {"agentic_loop": {"use_v2": True}}}

    assert il._can_dispatch_v2(rt) is False
    assert il._can_dispatch_event_queue(rt) is True


def test_resolve_channel_parallel_context_uses_session_key():
    ctx = InnerLoop._resolve_channel_parallel_context(
        session_key="whatsapp:dm:+391234",
        channel_name="whatsapp",
        reply_target="+391234",
        primary_deep_busy=True,
    )
    assert ctx == "whatsapp:dm:+391234"


@pytest.mark.asyncio
async def test_dispatch_channel_focus_uses_parallel_context_when_home_busy():
    il = InnerLoop.__new__(InnerLoop)
    il.preempt_background = MagicMock()

    class FakeRuntime(SimpleNamespace):
        pass

    rt = FakeRuntime(
        agent_id="lead-1",
        is_busy=True,
        is_user_busy=True,
        is_agentic_enabled=lambda: True,
        inference_available=lambda: True,
        config={"agency": {"agentic_loop": {"use_v2": True}}},
        channel_registry=None,
        _slot_manager=SimpleNamespace(deep=SimpleNamespace(is_busy=True)),
        _team_manager=None,
    )
    rt.process_message_agentic_async = AsyncMock(
        return_value=SimpleNamespace(final_response="done"),
    )

    event = AgentEvent(
        type=EventType.CHANNEL_MESSAGE,
        source="whatsapp",
        payload={
            "user_input": "[User via WhatsApp]: run the log script",
            "session_key": "whatsapp:dm:+391234",
            "channel_name": "whatsapp",
            "reply_target": "+391234",
            "agent_id": "lead-1",
            "needs_thinking": True,
            "history": [],
            "user_direct": True,
        },
    )

    with patch(
        "nls.runtime.AgentRuntime",
        FakeRuntime,
    ), patch(
        "nls.runtime.response_cleanup.sanitize_channel_outbound",
        return_value="done",
    ):
        handled = await il._dispatch_channel_event(
            event, EngagementDepth.FOCUS, rt,
        )

    assert handled is True
    rt.process_message_agentic_async.assert_awaited_once()
    kwargs = rt.process_message_agentic_async.await_args.kwargs
    assert kwargs["source"] == "user:channel"
    assert kwargs["session_key"] == "whatsapp:dm:+391234"
    assert kwargs["context_id"] == "whatsapp:dm:+391234"
    il.preempt_background.assert_not_called()
