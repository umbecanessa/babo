"""Home chat stays clean — no surface inbox steering injection."""

from __future__ import annotations

from nls.runtime.surface_inbox import (
    clear_agent_inbox,
    drain_surface_inbox_steering,
    record_surface_inbound,
)


def setup_function():
    clear_agent_inbox("lead-1")


def test_home_chat_never_gets_surface_inbox_steering():
    for text in ("Hey babo", "Hey @bot do you copy?", "@bot"):
        record_surface_inbound(
            "lead-1",
            session_key="telegram:group:-100",
            channel="telegram",
            sender_name="Owner",
            content=text,
        )

    assert drain_surface_inbox_steering("lead-1", "websocket:main") == []
