"""Tests for ambient group/channel transcript logging."""

from __future__ import annotations

from pathlib import Path

from nls.runtime.channel_ambient import (
    append_channel_ambient,
    append_channel_ambient_reply,
    channel_ambient_stats,
    is_shared_channel_session,
    query_channel_ambient,
    recent_ambient_snippet,
)


def _group_norm(**overrides) -> dict:
    base = {
        "channel": "telegram",
        "session_key": "telegram:group:-1003721736976",
        "sender_id": "977454767",
        "sender_name": "Alice",
        "content": "should we ship Friday?",
        "is_group": True,
        "group_id": "-1003721736976",
        "is_mention": False,
        "message_id": "101",
        "metadata": {"chat_type": "supergroup"},
    }
    base.update(overrides)
    return base


def test_append_and_search_ambient(tmp_path: Path) -> None:
    norm = _group_norm()
    append_channel_ambient(tmp_path, norm, triggered=False)
    append_channel_ambient(
        tmp_path,
        _group_norm(content="@babo_boba_bot thoughts?", is_mention=True, message_id="102"),
        triggered=True,
    )

    rows, total = query_channel_ambient(tmp_path, query="ship")
    assert total == 2
    assert len(rows) == 1
    assert "Friday" in rows[0]["content"]

    stats = channel_ambient_stats(tmp_path)
    assert stats["total"] == 2
    assert stats["sessions"] == 1


def test_dedupe_message_id(tmp_path: Path) -> None:
    norm = _group_norm(message_id="999")
    append_channel_ambient(tmp_path, norm, triggered=False)
    append_channel_ambient(tmp_path, norm, triggered=False)
    rows, total = query_channel_ambient(tmp_path, session_key=norm["session_key"])
    assert total == 1


def test_reply_and_snippet(tmp_path: Path) -> None:
    norm = _group_norm()
    append_channel_ambient(tmp_path, norm, triggered=False)
    append_channel_ambient(
        tmp_path,
        _group_norm(content="ping @bot", message_id="2"),
        triggered=True,
    )
    append_channel_ambient_reply(tmp_path, norm, "On it — checking now.")

    snippet = recent_ambient_snippet(
        tmp_path, norm["session_key"], limit=5, exclude_last=1,
    )
    assert "Recent group activity" in snippet
    assert "Alice" in snippet
    assert "Friday" in snippet

    rows, _ = query_channel_ambient(tmp_path, role="assistant")
    assert len(rows) == 1
    assert "checking" in rows[0]["content"]


def test_skips_non_group(tmp_path: Path) -> None:
    dm = _group_norm(is_group=False, session_key="telegram:dm:123")
    append_channel_ambient(tmp_path, dm, triggered=True)
    stats = channel_ambient_stats(tmp_path)
    assert stats["total"] == 0


def test_is_shared_channel_session() -> None:
    assert is_shared_channel_session("telegram:group:-100")
    assert is_shared_channel_session("discord:channel:123")
    assert is_shared_channel_session("slack:channel:C123")
    assert not is_shared_channel_session("telegram:dm:123")
    assert not is_shared_channel_session("websocket:main")
    assert not is_shared_channel_session(None)


def test_discord_message_id_from_metadata(tmp_path: Path) -> None:
    norm = {
        "channel": "discord",
        "session_key": "discord:channel:999",
        "sender_id": "1",
        "sender_name": "Bob",
        "content": "hello",
        "is_group": True,
        "metadata": {"message_id": "5551212"},
    }
    append_channel_ambient(tmp_path, norm, triggered=False)
    append_channel_ambient(tmp_path, norm, triggered=False)
    rows, total = query_channel_ambient(tmp_path, session_key=norm["session_key"])
    assert total == 1


def test_snippet_for_discord_channel(tmp_path: Path) -> None:
    sk = "discord:channel:999"
    norm = {
        "channel": "discord",
        "session_key": sk,
        "sender_id": "1",
        "sender_name": "Bob",
        "content": "prior msg",
        "is_group": True,
        "message_id": "1",
    }
    append_channel_ambient(tmp_path, norm, triggered=False)
    append_channel_ambient(
        tmp_path,
        {**norm, "content": "ping @bot", "message_id": "2"},
        triggered=True,
    )
    snippet = recent_ambient_snippet(tmp_path, sk, limit=5, exclude_last=1)
    assert "prior msg" in snippet
    assert "Bob" in snippet
