"""Surface inbox follow-through — handled vs steering policy."""

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


def test_external_channel_sessions_never_get_inbox_steering():
    record_surface_inbound(
        "lead-1",
        session_key="discord:channel:123",
        channel="discord",
        sender_name="Mod",
        content="Mod Bot online",
    )
    assert pending_count("lead-1") == 1

    for active in (
        "websocket:main",
        "discord:channel:999",
        "telegram:group:-100123",
        "slack:channel:C456",
        "whatsapp:dm:+391234",
    ):
        assert drain_surface_inbox_steering("lead-1", active) == []

    assert pending_count("lead-1") == 1


def test_handled_clears_pending():
    record_surface_inbound(
        "lead-1",
        session_key="discord:channel:123",
        channel="discord",
        sender_name="Mod",
        content="status update",
    )
    mark_session_inbox_handled("lead-1", "discord:channel:123")
    assert pending_count("lead-1") == 0


def test_same_surface_not_drained_into_self():
    record_surface_inbound(
        "lead-1",
        session_key="discord:channel:123",
        channel="discord",
        sender_name="Babo Mod",
        content="ping",
    )
    assert drain_surface_inbox_steering("lead-1", "discord:channel:123") == []
    assert pending_count("lead-1") == 1


def test_resurface_cooldown_does_not_apply_when_steering_disabled(monkeypatch):
    record_surface_inbound(
        "lead-1",
        session_key="discord:channel:123",
        channel="discord",
        sender_name="Mod",
        content="ping again",
    )
    import nls.runtime.surface_inbox as si

    monkeypatch.setattr(si, "_RESURFACE_AFTER_SECONDS", 0.0)
    assert drain_surface_inbox_steering("lead-1", "discord:channel:999") == []
    assert drain_surface_inbox_steering("lead-1", "discord:channel:999") == []
