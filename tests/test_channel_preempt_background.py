"""Channel messages preempt daydream without pausing the inner loop."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from nls.engine.events import EventType
from nls.skills import channel_processing as cp
from nls.skills.channel_processing import process_channel_message


def setup_function():
    cp._pending_channel_events.clear()


def test_process_channel_message_preempts_background_without_pause():
    inner_loop = MagicMock()
    runtime = SimpleNamespace(
        agent_id="lead-1",
        agent_dir=None,
        is_busy=lambda: False,
        _foreground_session_key="",
        _foreground_source="",
        _team_manager=None,
        is_agentic_enabled=lambda: True,
        process_message=MagicMock(),
        process_message_agentic_async=MagicMock(),
    )
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
    ):
        result = asyncio.run(process_channel_message(
            app,
            runtime,
            "lead-1",
            "[User via Telegram]: @bot hello",
            [],
            channel_adapter=SimpleNamespace(channel_name="telegram"),
            reply_target="-100123",
            session_key="telegram:group:-100123",
            sender_name="User",
            raw_content="@bot hello",
        ))

    assert result == ""
    cs.preempt_background.assert_called_once_with("lead-1")
    inner_loop.push_event.assert_called_once()
    event = inner_loop.push_event.call_args[0][0]
    assert event.type == EventType.CHANNEL_MESSAGE
    assert event.payload["user_direct"] is True
