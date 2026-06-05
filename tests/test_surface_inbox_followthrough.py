"""Surface inbox follow-through — steered vs handled."""

from __future__ import annotations

import time

from nls.runtime.surface_inbox import (
    clear_agent_inbox,
    drain_surface_inbox_steering,
    mark_session_inbox_handled,
    pending_count,
    record_surface_inbound,
)


def setup_function():
    clear_agent_inbox("lead-1")


def test_drain_steers_without_clearing_pending():
    record_surface_inbound(
        "lead-1",
        session_key="discord:channel:123",
        channel="discord",
        sender_name="Mod",
        content="Mod Bot online",
    )
    assert pending_count("lead-1") == 1

    msgs = drain_surface_inbox_steering("lead-1", "websocket:main")
    assert len(msgs) == 1
    assert pending_count("lead-1") == 1

    # Immediate re-drain suppressed by resurface cooldown
    assert drain_surface_inbox_steering("lead-1", "websocket:main") == []


def test_handled_clears_pending():
    record_surface_inbound(
        "lead-1",
        session_key="discord:channel:123",
        channel="discord",
        sender_name="Mod",
        content="status update",
    )
    drain_surface_inbox_steering("lead-1", "websocket:main")
    mark_session_inbox_handled("lead-1", "discord:channel:123")
    assert pending_count("lead-1") == 0


def test_resurface_after_cooldown(monkeypatch):
    record_surface_inbound(
        "lead-1",
        session_key="discord:channel:123",
        channel="discord",
        sender_name="Mod",
        content="ping again",
    )
    drain_surface_inbox_steering("lead-1", "websocket:main")
    import nls.runtime.surface_inbox as si

    monkeypatch.setattr(si, "_RESURFACE_AFTER_SECONDS", 0.0)
    msgs = drain_surface_inbox_steering("lead-1", "websocket:main")
    assert len(msgs) == 1
