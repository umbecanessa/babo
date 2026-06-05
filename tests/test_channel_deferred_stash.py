"""Deferred channel events stash when inner loop is unavailable."""

from __future__ import annotations

from unittest.mock import MagicMock

from nls.engine.events import AgentEvent, EventType
from nls.skills.channel_processing import (
    flush_pending_channel_events,
    stash_deferred_channel_event,
)


def setup_function():
    from nls.skills import channel_processing as cp

    cp._pending_channel_events.clear()


def test_stash_and_flush_channel_events():
    event = AgentEvent(
        type=EventType.CHANNEL_MESSAGE,
        source="discord",
        payload={"user_input": "hello"},
    )
    stash_deferred_channel_event("agent-1", event)
    il = MagicMock()
    n = flush_pending_channel_events("agent-1", il)
    assert n == 1
    il.push_event.assert_called_once_with(event)
    assert flush_pending_channel_events("agent-1", il) == 0
