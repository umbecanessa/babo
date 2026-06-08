"""Branch thread persistence (session_key routing)."""

from __future__ import annotations

import json
from pathlib import Path

from nls.runtime.channels import SessionRouter
from nls.runtime.session import (
    append_session_transcript_turn,
    load_session_transcript,
    session_ui_transcript_path,
)
from server.routes.chat.history import (
    _salvage_agentic_context,
    is_main_chat_session,
    persist_conversation_turn,
    persist_partial_agentic_transcript,
)


class _FakeRuntime:
    def __init__(self, agent_dir: Path):
        self.agent_dir = agent_dir
        self.channel_registry = type("CR", (), {
            "session_router": SessionRouter(agent_dir),
        })()
        self.main_history: list[dict] = []
        self.session_histories: dict[str, list[dict]] = {}
        self.main_transcript: list[dict] = []
        self.session_transcripts: dict[str, list[dict]] = {}

    def save_conversation_history(self, history: list[dict]) -> None:
        self.main_history = list(history)

    def save_session_history(
        self,
        history: list[dict],
        session_key: str | None = None,
        max_turns: int = 20,
        metadata: dict | None = None,
    ) -> None:
        self.session_histories[session_key or ""] = list(history)

    def record_chat_turn(self, **kwargs) -> None:
        self.main_transcript.append(kwargs)

    def record_session_turn(self, *, session_key: str, **kwargs) -> None:
        self.session_transcripts.setdefault(session_key, []).append(kwargs)

    def load_session_history(self, session_key: str | None = None, max_turns: int = 20):
        return list(self.session_histories.get(session_key or "", []))


def test_is_main_chat_session():
    assert is_main_chat_session("websocket:main") is True
    assert is_main_chat_session("websocket:thread:abc") is False


def test_persist_conversation_turn_routes_branch(tmp_path: Path):
    runtime = _FakeRuntime(tmp_path)
    branch = "websocket:thread:abc123"
    history = [
        {"role": "user", "content": "hello branch"},
        {
            "role": "assistant",
            "content": "working",
            "metadata": {"agentic": True, "events": [{"step": 1}]},
        },
    ]

    persist_conversation_turn(
        runtime,
        branch,
        history,
        user="hello branch",
        assistant="working",
        metadata={"agentic": True, "events": [{"step": 1}]},
        session_metadata={"channel": "websocket", "label": "Branch 1"},
    )

    assert runtime.main_history == []
    assert branch in runtime.session_histories
    assert runtime.session_transcripts.get(branch)


def test_append_session_transcript_turn_preserves_metadata(tmp_path: Path):
    branch = "websocket:thread:xyz"
    append_session_transcript_turn(
        tmp_path,
        branch,
        user="run locally",
        assistant="installing deps",
        metadata={"agentic": True, "events": [{"step": 2, "tool_calls": [{"name": "bash"}]}]},
    )

    rows = load_session_transcript(tmp_path, branch)
    assert len(rows) >= 2
    assistant = [r for r in rows if r.get("role") == "assistant"][-1]
    assert assistant.get("metadata", {}).get("agentic") is True
    assert session_ui_transcript_path(tmp_path, branch).is_file()


def test_session_router_preserves_assistant_metadata(tmp_path: Path):
    router = SessionRouter(tmp_path)
    history = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "done",
            "reasoning": "thought",
            "metadata": {"agentic": True},
        },
    ]
    router.save_history("websocket:thread:1", history, metadata={"label": "Branch 1"})
    loaded = router.load_history("websocket:thread:1")
    assert loaded[-1]["metadata"]["agentic"] is True
    assert router.list_sessions()["websocket:thread:1"]["label"] == "Branch 1"


def test_delete_session_removes_ui_transcript(tmp_path: Path):
    branch = "websocket:thread:del"
    append_session_transcript_turn(tmp_path, branch, user="x", assistant="y")
    router = SessionRouter(tmp_path)
    router.save_history(branch, [{"role": "user", "content": "x"}])

    assert router.delete_session(branch) is True
    assert not session_ui_transcript_path(tmp_path, branch).is_file()


def test_salvage_agentic_context_routes_branch(tmp_path: Path):
    runtime = _FakeRuntime(tmp_path)
    branch = "websocket:thread:salvage"
    history: list[dict] = []
    shared = [
        {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash"}}]},
        {"role": "tool", "content": "ok", "tool_call_id": "c1"},
    ]
    _salvage_agentic_context(
        history, shared, "run tests", runtime, "agent-1", session_key=branch,
    )
    assert runtime.main_history == []
    assert branch in runtime.session_histories
    assert runtime.session_transcripts.get(branch)


def test_partial_agentic_transcript_updates_branch_history():
    runtime = _FakeRuntime(Path("/unused"))
    branch = "websocket:thread:partial"
    persist_partial_agentic_transcript(
        runtime,
        user_input="deploy app",
        eager_events=[{"step": 1, "tool_calls": [{"name": "bash"}]}],
        session_key=branch,
    )
    assert branch in runtime.session_histories
    assert runtime.session_histories[branch][-1]["metadata"]["agentic"] is True
