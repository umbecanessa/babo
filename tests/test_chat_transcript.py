"""UI chat transcript persistence — append-only JSONL."""

from __future__ import annotations

import json
from pathlib import Path

from nls.runtime.session import (
    append_chat_transcript_turn,
    chat_transcript_stats,
    load_chat_transcript,
    query_chat_transcript,
    save_conversation_history,
)
from nls.tools.agent_tools.chat_history import create_chat_history_tool


def test_chat_transcript_append_and_load(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    append_chat_transcript_turn(
        agent_dir,
        user="Hello Babo",
        assistant="Hi there!",
    )
    append_chat_transcript_turn(
        agent_dir,
        user="Build discord-channel skill",
        assistant="On it.",
        metadata={"agentic": True, "iterations": 3},
    )

    transcript = load_chat_transcript(agent_dir)
    assert len(transcript) == 4
    assert transcript[0]["role"] == "user"
    assert transcript[0]["content"] == "Hello Babo"
    assert transcript[-1]["metadata"]["agentic"] is True

    jsonl = agent_dir / "chat_transcript.jsonl"
    assert jsonl.is_file()
    lines = [ln for ln in jsonl.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 4
    first = json.loads(lines[0])
    assert first["role"] == "user"
    assert "ts" in first


def test_transcript_never_compacted(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    for i in range(50):
        append_chat_transcript_turn(
            agent_dir,
            user=f"user message {i}",
            assistant=f"reply {i}",
        )
    stats = chat_transcript_stats(agent_dir)
    assert stats["total"] == 100


def test_query_chat_transcript_search(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    append_chat_transcript_turn(
        agent_dir,
        user="Let's build a native discord-channel skill",
        assistant="I'll scaffold it.",
    )
    append_chat_transcript_turn(
        agent_dir,
        user="What about ClawHub?",
        assistant="Reference only.",
    )

    matches, total = query_chat_transcript(agent_dir, query="discord")
    assert total == 4
    assert len(matches) == 1
    assert matches[0]["role"] == "user"
    assert "discord" in matches[0]["content"].lower()


def test_chat_history_tool_search(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    append_chat_transcript_turn(
        agent_dir,
        user="We discussed native skills yesterday",
        assistant="Yes, telegram-channel pattern.",
    )
    tool = create_chat_history_tool(agent_dir)
    import asyncio

    result = asyncio.run(tool.execute({
        "action": "search",
        "query": "native skills",
    }))
    assert "native skills" in result.content.lower()
    assert not result.is_error


def test_migrate_transcript_from_conversation_history(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    save_conversation_history(
        agent_dir,
        [
            {"role": "user", "content": "Naming turn"},
            {"role": "assistant", "content": "I'm Babo"},
            {"role": "tool", "content": "noise"},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "Done", "metadata": {"agentic": True}},
        ],
    )

    transcript = load_chat_transcript(agent_dir)
    roles = [m["role"] for m in transcript]
    assert roles == ["user", "assistant", "assistant"]
    assert (agent_dir / "chat_transcript.jsonl").is_file()


def test_migrate_legacy_chat_transcript_json(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    legacy = agent_dir / "chat_transcript.json"
    legacy.write_text(
        json.dumps([
            {"role": "user", "content": "Old format"},
            {"role": "assistant", "content": "Migrated"},
        ]),
        encoding="utf-8",
    )

    transcript = load_chat_transcript(agent_dir)
    assert len(transcript) == 2
    assert (agent_dir / "chat_transcript.jsonl").is_file()


def test_load_chat_transcript_ui_limit(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    for i in range(30):
        append_chat_transcript_turn(agent_dir, user=f"u{i}", assistant=f"a{i}")

    full = load_chat_transcript(agent_dir, limit=None)
    assert len(full) == 60
    tail = load_chat_transcript(agent_dir, limit=10)
    assert len(tail) == 10
    assert tail[-1]["content"] == "a29"
