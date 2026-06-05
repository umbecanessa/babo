"""Cross-surface inbox — defer parallel channel turns, drain into active loop."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from nls.runtime.surface_inbox import (
    clear_agent_inbox,
    drain_surface_inbox_steering,
    mark_session_inbox_handled,
    pending_count,
    record_surface_inbound,
    should_defer_cross_surface,
    try_feed_active_copilot,
)


def _runtime(*, busy: bool, session_key: str = "websocket:main") -> SimpleNamespace:
    return SimpleNamespace(
        agent_id="lead-1",
        is_busy=lambda: busy,
        _foreground_session_key=session_key,
        _foreground_source="user",
        _team_manager=None,
    )


def setup_function():
    clear_agent_inbox("lead-1")


def test_record_and_drain_cross_surface():
    record_surface_inbound(
        "lead-1",
        session_key="discord:channel:123",
        channel="discord",
        channel_label="#admin-babo",
        sender_name="Babo Mod",
        content="Mod Bot online ✅",
    )
    assert pending_count("lead-1") == 1

    msgs = drain_surface_inbox_steering("lead-1", "websocket:main")
    assert len(msgs) == 1
    assert "SURFACE INBOX" in msgs[0]["content"]
    assert "Mod Bot online" in msgs[0]["content"]
    assert pending_count("lead-1") == 1


def test_same_surface_not_drained_into_self():
    record_surface_inbound(
        "lead-1",
        session_key="discord:channel:123",
        channel="discord",
        sender_name="Babo Mod",
        content="ping",
    )
    msgs = drain_surface_inbox_steering("lead-1", "discord:channel:123")
    assert msgs == []
    assert pending_count("lead-1") == 1


def test_should_defer_when_busy_on_different_surface():
    rt = _runtime(busy=True, session_key="websocket:main")
    assert should_defer_cross_surface(rt, "discord:channel:123") is True
    assert should_defer_cross_surface(rt, "websocket:main") is False


def test_should_not_defer_when_idle():
    rt = _runtime(busy=False, session_key="websocket:main")
    assert should_defer_cross_surface(rt, "discord:channel:123") is False


def test_mark_session_handled_prevents_later_drain():
    record_surface_inbound(
        "lead-1",
        session_key="discord:channel:123",
        channel="discord",
        sender_name="Mod",
        content="done",
    )
    mark_session_inbox_handled("lead-1", "discord:channel:123")
    msgs = drain_surface_inbox_steering("lead-1", "websocket:main")
    assert msgs == []


def test_copilot_feed():
    rt = _runtime(busy=True)
    q = MagicMock()
    rt._team_manager = SimpleNamespace(_copilot_queue=q)
    assert try_feed_active_copilot(rt, "[Mod via Discord]: online") is True
    q.put_nowait.assert_called_once()
