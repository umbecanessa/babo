"""Mid-loop assistant prose must survive transcript persistence."""

from __future__ import annotations

from nls.agentic.events import AgentEvent
from nls.agentic.types import AgenticResult, EventType
from server.routes.chat.helpers import (
    _build_agentic_metadata,
    _merge_transcript_event_prose,
)


def test_merge_transcript_event_prose_adds_mid_loop_text():
    built = [
        {
            "step": 1,
            "tool_calls": [{"name": "team", "arguments": {"action": "inspect"}}],
            "tool_results": [{"success": True}],
            "duration_ms": 120,
        },
    ]
    eager = [
        {
            "step": 1,
            "tool_calls": [{"name": "team", "arguments": {"action": "inspect"}}],
            "tool_results": [{"success": True}],
            "duration_ms": 120,
            "prose": "Wave 2 is running — I'll monitor delegates.",
        },
    ]
    merged = _merge_transcript_event_prose(built, eager)
    assert len(merged) == 1
    assert merged[0]["prose"] == eager[0]["prose"]


def test_merge_prefers_eager_when_step_counts_match():
    """Regression: equal step counts used to drop prose entirely."""
    built = [{"step": i, "tool_calls": [], "tool_results": []} for i in (1, 2, 3)]
    eager = [
        {"step": 1, "tool_calls": [], "tool_results": [], "prose": "First update"},
        {"step": 2, "tool_calls": [], "tool_results": [], "prose": "Second update"},
        {"step": 3, "tool_calls": [], "tool_results": [], "prose": "Third update"},
    ]
    merged = _merge_transcript_event_prose(built, eager)
    assert [m.get("prose") for m in merged] == [
        "First update", "Second update", "Third update",
    ]


def test_build_agentic_metadata_includes_turn_end_prose():
    result = AgenticResult(
        final_response="All done.",
        iterations=2,
        total_tool_calls=1,
        events=[
            AgentEvent(
                EventType.TOOL_EXECUTION_END,
                {
                    "iteration": 1,
                    "tool_name": "read",
                    "is_error": False,
                    "duration_ms": 40,
                },
            ),
            AgentEvent(
                EventType.TURN_END,
                {
                    "iteration": 1,
                    "response_text": "Reading the config file now.",
                },
            ),
        ],
    )
    meta = _build_agentic_metadata(result)
    events = meta["events"]
    assert len(events) == 1
    assert events[0]["prose"] == "Reading the config file now."
