"""Eager agent_end checkpoint + canonical finalize must not duplicate UI rows."""

from __future__ import annotations

from pathlib import Path

from server.routes.chat.history import (
    finalize_agentic_turn_after_checkpoint,
    persist_conversation_turn,
)
from nls.runtime.session import _read_jsonl_messages


class _Runtime:
    def __init__(self, agent_dir: Path):
        self.agent_dir = agent_dir

    def save_conversation_history(self, history):
        from nls.runtime.session import save_conversation_history
        save_conversation_history(self.agent_dir, history)

    def record_chat_turn(self, **kwargs):
        from nls.runtime.session import append_chat_transcript_turn
        append_chat_transcript_turn(self.agent_dir, **kwargs)


def test_finalize_after_checkpoint_does_not_duplicate_user(tmp_path: Path):
    rt = _Runtime(tmp_path)
    history = [{"role": "user", "content": "run git pull"}]

    persist_conversation_turn(
        rt,
        "websocket:main",
        history,
        user="run git pull",
        assistant="done",
        metadata={"agentic": True, "events": []},
    )

    history.append({"role": "assistant", "content": "done", "metadata": {"agentic": True}})
    finalize_agentic_turn_after_checkpoint(
        rt,
        "websocket:main",
        history,
        assistant="done with richer metadata",
        metadata={"agentic": True, "events": [{"step": 1, "prose": "done"}]},
    )

    from nls.runtime.session import transcript_path_for_session
    rows = _read_jsonl_messages(
        transcript_path_for_session(tmp_path, "websocket:main"),
    )
    user_rows = [r for r in rows if r.get("role") == "user"]
    assert len(user_rows) == 1
    assert rows[-1]["role"] == "assistant"
    assert "richer metadata" in rows[-1]["content"]
