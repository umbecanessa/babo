"""Tests for surface session routing and outbound send resolution."""

from nls.skills.surface_send import (
    channel_session_metadata,
    is_surface_session_key,
    resolve_surface_target,
)


def test_is_surface_session_key():
    assert not is_surface_session_key(None)
    assert not is_surface_session_key("websocket:main")
    assert not is_surface_session_key("websocket:thread:abc")
    assert is_surface_session_key("discord:channel:123")
    assert is_surface_session_key("telegram:group:456")


def test_channel_session_metadata_discord():
    normalized = {
        "channel": "discord",
        "session_key": "discord:channel:999",
        "sender_name": "alice",
        "metadata": {"channel_id": "999", "channel_name": "general"},
    }
    meta = channel_session_metadata(normalized)
    assert meta["reply_target"] == "999"
    assert meta["channel"] == "discord"
    assert meta["channel_name"] == "general"


def test_channel_session_metadata_slack_dm():
    normalized = {
        "channel": "slack",
        "session_key": "slack:dm:U123",
        "sender_name": "bob",
        "metadata": {"channel_id": "D456", "thread_ts": "111.222"},
    }
    meta = channel_session_metadata(normalized)
    assert meta["reply_target"] == "D456"
    assert meta["thread_ts"] == "111.222"


def test_resolve_surface_target_from_meta():
    target = resolve_surface_target(
        "discord:dm:123",
        {"reply_target": "dm-channel-id", "sender": "alice"},
    )
    assert target is not None
    assert target.reply_target == "dm-channel-id"
    assert target.channel == "discord"


def test_resolve_surface_target_channel_fallback():
    target = resolve_surface_target("discord:channel:777", {})
    assert target is not None
    assert target.reply_target == "777"


def test_resolve_surface_target_email():
    target = resolve_surface_target(
        "email:thread:abc",
        {"sender": "user@example.com", "subject": "Hello"},
    )
    assert target is not None
    assert target.reply_target == "user@example.com"
    assert target.send_kwargs["subject"] == "Re: Hello"
