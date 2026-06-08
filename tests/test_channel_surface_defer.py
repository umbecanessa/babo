"""Channel processing — parallel cross-surface routing when Home is busy."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from nls.engine.events import EventType
from nls.runtime.surface_inbox import clear_agent_inbox, pending_count
from nls.skills.channel_processing import process_channel_message


def setup_function():
    clear_agent_inbox("lead-1")
    from nls.skills import channel_processing as cp

    cp._pending_channel_events.clear()


def test_process_channel_message_records_inbox_when_home_busy():
    runtime = SimpleNamespace(
        agent_id="lead-1",
        is_busy=lambda: True,
        _foreground_session_key="websocket:main",
        _foreground_source="user",
        _team_manager=SimpleNamespace(_copilot_queue=MagicMock()),
        is_agentic_enabled=lambda: True,
        process_message=MagicMock(),
        process_message_agentic_async=AsyncMock(),
    )
    app = SimpleNamespace(state=SimpleNamespace(
        model_manager=MagicMock(),
        adapter_registry=MagicMock(),
        consciousness_scheduler=None,
    ))

    with patch(
        "nls.runtime.squad_channel_policy.channel_delivery_allowed",
        return_value=(True, ""),
    ):
        result = asyncio.run(process_channel_message(
            app,
            runtime,
            "lead-1",
            "[Babo Mod via Discord]: Mod Bot online ✅",
            [],
            channel_adapter=SimpleNamespace(channel_name="discord"),
            reply_target="1511069841887330434",
            session_key="discord:channel:1511069841887330434",
            sender_name="Babo Mod",
            channel_label="#admin-babo",
            raw_content="Mod Bot online ✅",
        ))

    assert result == ""
    assert pending_count("lead-1") == 1
    runtime.process_message.assert_not_called()
    runtime.process_message_agentic_async.assert_not_called()


def test_process_channel_message_cross_surface_still_pushes_inner_loop_event():
    inner_loop = MagicMock()
    runtime = SimpleNamespace(
        agent_id="lead-1",
        is_busy=lambda: True,
        _foreground_session_key="websocket:main",
        _foreground_source="user",
        _team_manager=SimpleNamespace(_copilot_queue=MagicMock()),
        is_agentic_enabled=lambda: True,
        process_message=MagicMock(),
        process_message_agentic_async=AsyncMock(),
        agent_dir=None,
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
            "[User via Telegram]: @bot ping",
            [],
            channel_adapter=SimpleNamespace(channel_name="telegram"),
            reply_target="-100123",
            session_key="telegram:group:-100123",
            sender_name="User",
            raw_content="@bot ping",
        ))

    assert result == ""
    assert pending_count("lead-1") == 1
    inner_loop.push_event.assert_called_once()
    pushed = inner_loop.push_event.call_args[0][0]
    assert pushed.type == EventType.CHANNEL_MESSAGE
    assert pushed.payload["session_key"] == "telegram:group:-100123"
    runtime.process_message_agentic_async.assert_not_called()

    from nls.skills import channel_processing as cp

    assert cp._pending_channel_events.get("lead-1", []) == []
