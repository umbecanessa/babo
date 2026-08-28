"""Foreground WebSocket agentic events carry the active thread session_key."""

from __future__ import annotations

from unittest.mock import MagicMock

from server.routes.chat.ws_handler import _foreground_ws_tags


def test_foreground_ws_tags_use_websocket_state_session():
    ws = MagicMock()
    ws.state.session_key = "websocket:thread:mq5jizlq"
    runtime = MagicMock()
    assert _foreground_ws_tags(ws, runtime) == {
        "session_key": "websocket:thread:mq5jizlq",
    }


def test_foreground_ws_tags_fallback_to_promoted_home():
    ws = MagicMock()
    ws.state.session_key = "websocket:main"
    runtime = MagicMock()
    runtime.get_default_home_session_key.return_value = "websocket:thread:home1"
    assert _foreground_ws_tags(ws, runtime) == {
        "session_key": "websocket:thread:home1",
    }


def test_foreground_ws_tags_default_main():
    ws = MagicMock()
    ws.state.session_key = ""
    runtime = MagicMock()
    runtime.get_default_home_session_key.return_value = "websocket:main"
    assert _foreground_ws_tags(ws, runtime) == {
        "session_key": "websocket:main",
    }
