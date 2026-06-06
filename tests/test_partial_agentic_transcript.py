"""Tests for partial agentic transcript persistence."""

from server.routes.chat.history import persist_partial_agentic_transcript


class _FakeRuntime:
    def __init__(self):
        self.calls: list[dict] = []

    def record_chat_turn(self, **kwargs):
        self.calls.append(kwargs)


def test_persist_partial_agentic_transcript_writes_metadata():
    runtime = _FakeRuntime()
    persist_partial_agentic_transcript(
        runtime,
        user_input="Build it",
        eager_events=[
            {
                "step": 1,
                "tool_calls": [{"name": "read"}],
                "tool_results": [{"success": True}],
                "duration_ms": 50,
            },
        ],
    )
    assert len(runtime.calls) == 1
    call = runtime.calls[0]
    assert call["user"] == "Build it"
    meta = call["metadata"]
    assert meta["agentic"] is True
    assert meta["aborted"] is True
    assert len(meta["events"]) == 1


def test_persist_partial_skips_empty():
    runtime = _FakeRuntime()
    persist_partial_agentic_transcript(
        runtime,
        user_input="",
        eager_events=[],
    )
    assert runtime.calls == []


def test_persist_partial_user_only_without_agentic_metadata():
    runtime = _FakeRuntime()
    persist_partial_agentic_transcript(
        runtime,
        user_input="Hello",
        eager_events=[],
    )
    assert len(runtime.calls) == 1
    assert runtime.calls[0]["metadata"] is None
