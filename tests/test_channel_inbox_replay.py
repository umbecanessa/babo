"""Surface inbox channel replay + record-on-defer only."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from nls.runtime.surface_inbox import (
    build_channel_replay_events,
    clear_agent_inbox,
    pending_count,
    record_surface_inbound,
)
from nls.skills.channel_processing import process_channel_message


def setup_function():
    clear_agent_inbox("lead-1")


def test_build_channel_replay_events_from_pending_inbox():
    record_surface_inbound(
        "lead-1",
        session_key="telegram:group:-1003721736976",
        channel="telegram",
        sender_name="Umberto",
        content="@babo_boba_bot black screen logs",
    )
    events = build_channel_replay_events("lead-1")
    assert len(events) == 1
    assert events[0].payload["reply_target"] == "-1003721736976"
    assert "black screen logs" in events[0].payload["user_input"]


def test_direct_channel_turn_does_not_record_inbox():
    runtime = SimpleNamespace(
        agent_id="lead-1",
        is_busy=lambda: False,
        _foreground_session_key="websocket:main",
        _foreground_source="user",
        _team_manager=None,
        is_agentic_enabled=lambda: True,
        process_message_agentic_async=AsyncMock(),
        agent_dir=None,
        channel_registry=None,
    )
    inner_loop = MagicMock()
    cs = SimpleNamespace(
        get_inner_loop=lambda _aid: inner_loop,
        preempt_background=MagicMock(),
    )
    app = SimpleNamespace(state=SimpleNamespace(
        model_manager=MagicMock(),
        consciousness_scheduler=cs,
    ))

    with patch(
        "nls.runtime.squad_channel_policy.channel_delivery_allowed",
        return_value=(True, ""),
    ), patch(
        "nls.skills.channel_processing._update_channels_ring",
    ), patch(
        "nls.skills.channel_processing.flush_pending_channel_events",
    ):
        asyncio.run(process_channel_message(
            app,
            runtime,
            "lead-1",
            "[User via Telegram]: @bot ping",
            [],
            channel_adapter=SimpleNamespace(channel_name="telegram"),
            reply_target="-100123",
            session_key="telegram:group:-100123",
            sender_name="User",
            raw_content="@bot ping",
        ))

    assert pending_count("lead-1") == 0
    inner_loop.push_event.assert_called_once()
