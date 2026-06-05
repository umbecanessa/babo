"""Tests for generation output-budget detection and nudges."""

from __future__ import annotations

import json

from nls.agentic.generation_budget import (
    TRUNCATED_WRITE_ESCALATE_AFTER,
    TruncatedFileToolEvent,
    analyze_generation_budget,
    build_file_tool_recovery_nudge,
    build_thinking_length_nudge,
    build_thinking_spiral_nudge_tier1,
    build_thinking_spiral_nudge_tier2,
    build_thinking_spiral_recovery_nudge,
    classify_truncated_file_tool,
    clear_truncated_write_attempt,
    content_looks_truncated,
    file_tool_call_looks_truncated,
    first_pass_thinking_spiral,
    is_thinking_spiral,
    output_budget_exhausted,
    record_truncated_file_events,
    should_suppress_error_recovery,
)
from nls.agentic.types import GenerationResult, LoopConfig
from nls.brain.thinking import build_reasoning_prefill


def test_output_budget_exhausted_at_cap():
    assert output_budget_exhausted(4096, 4096)
    assert output_budget_exhausted(15992, 16000)
    assert not output_budget_exhausted(3000, 16000)


def test_output_budget_exhausted_on_finish_length():
    assert output_budget_exhausted(0, 16000, finish_reason="length")
    assert output_budget_exhausted(100, 16000, finish_reason="length")


def test_truncated_write_tool_call():
    raw = '{"path": "workspace/discord-channel/adapter.py"'
    assert file_tool_call_looks_truncated("write", raw)


def test_truncated_write_missing_content_valid_path():
    args = {"path": "workspace/discord-channel/adapter.py"}
    assert file_tool_call_looks_truncated("write", args)


def test_complete_write_not_truncated():
    args = {
        "path": "foo.py",
        "content": "print('hi')\n",
    }
    assert not file_tool_call_looks_truncated("write", args)


def test_partial_content_detected():
    body = (
        "def adapter():\n    pass\n\n"
        "class DiscordAdapter:\n    def __init__(self, token: str):\n        self._token = "
    )
    assert len(body) >= 80
    assert content_looks_truncated(body)
    kind = classify_truncated_file_tool(
        "write",
        {"path": "adapter.py", "content": body},
        budget_hit=True,
    )
    assert kind == "partial_content"


def test_analyze_partial_content_on_length_finish():
    body = "x = 1\n" + ("line()\n" * 40) + 'text = "unclosed'
    response = GenerationResult(
        tool_calls=[{
            "function": {
                "name": "write",
                "arguments": json.dumps({
                    "path": "adapter.py",
                    "content": body,
                }),
            },
        }],
        completion_tokens=16000,
        finish_reason="length",
    )
    config = LoopConfig(max_new_tokens=16000)
    analysis = analyze_generation_budget(response, config)
    assert analysis.truncated_file_events
    assert analysis.truncated_file_events[0].kind == "partial_content"


def test_analyze_truncated_file_tools_on_budget_hit():
    response = GenerationResult(
        tool_calls=[{
            "function": {
                "name": "write",
                "arguments": '{"path": "workspace/discord-channel/adapter.py"',
            },
        }],
        completion_tokens=4096,
        finish_reason="tool_calls",
    )
    config = LoopConfig(max_new_tokens=4096)
    analysis = analyze_generation_budget(response, config)
    assert analysis.output_budget_exhausted
    assert analysis.truncated_file_tools == ["write"]
    assert analysis.truncated_file_events[0].kind == "missing_content"
    assert not analysis.thinking_budget_exhausted


def test_analyze_thinking_only_length_stop():
    response = GenerationResult(
        thinking="plan " * 500,
        completion_tokens=16000,
        finish_reason="length",
    )
    config = LoopConfig(max_new_tokens=16000)
    analysis = analyze_generation_budget(response, config)
    assert analysis.thinking_budget_exhausted
    assert not analysis.truncated_file_tools


def test_analyze_thinking_exhausted_without_finish_reason():
    response = GenerationResult(
        thinking="x" * 1200,
        completion_tokens=15995,
        finish_reason="stop",
    )
    config = LoopConfig(max_new_tokens=16000)
    analysis = analyze_generation_budget(response, config)
    assert analysis.thinking_budget_exhausted


def test_escalated_recovery_after_two_attempts():
    events = [
        TruncatedFileToolEvent("write", "adapter.py", "missing_content"),
    ]
    attempts = {"adapter.py": TRUNCATED_WRITE_ESCALATE_AFTER}
    msg = build_file_tool_recovery_nudge(events, 16000, attempts)
    assert "MANDATORY" in msg
    assert "attempt 2" in msg


def test_record_and_clear_attempts():
    attempts: dict[str, int] = {}
    events = [TruncatedFileToolEvent("write", "adapter.py", "missing_content")]
    touched = record_truncated_file_events(attempts, events)
    assert touched["adapter.py"] == 1
    record_truncated_file_events(attempts, events)
    assert attempts["adapter.py"] == 2
    clear_truncated_write_attempt(attempts, "adapter.py")
    assert "adapter.py" not in attempts


def test_suppress_error_recovery_when_file_nudge_sent():
    assert should_suppress_error_recovery(True)
    assert not should_suppress_error_recovery(False)


def test_nudge_messages_actionable():
    msg = build_file_tool_recovery_nudge(
        [TruncatedFileToolEvent("write", "a.py", "missing_content")],
        16000,
        {},
    )
    assert "16000" in msg
    assert "stub" in msg.lower()

    tier1 = build_thinking_spiral_nudge_tier1()
    assert "THINKING LOOP" in tier1
    assert "tool" in tier1.lower()

    tier2 = build_thinking_spiral_nudge_tier2()
    assert "DISABLED" in tier2

    assert build_thinking_spiral_recovery_nudge(1) == tier1
    assert build_thinking_spiral_recovery_nudge(2) == tier2

    think_nudge = build_thinking_length_nudge(12000, 16000)
    assert "THINKING LOOP" in think_nudge


def test_is_thinking_spiral_budget_exhausted():
    response = GenerationResult(
        thinking="plan " * 500,
        completion_tokens=16000,
        finish_reason="length",
    )
    config = LoopConfig(max_new_tokens=16000)
    budget = analyze_generation_budget(response, config)
    assert is_thinking_spiral(response, budget)


def test_is_thinking_spiral_long_thinking_no_tools():
    response = GenerationResult(
        thinking="x" * 2500,
        completion_tokens=5000,
        finish_reason="stop",
    )
    config = LoopConfig(max_new_tokens=16000)
    budget = analyze_generation_budget(response, config)
    assert is_thinking_spiral(response, budget)


def test_is_thinking_spiral_not_when_tools_called():
    response = GenerationResult(
        thinking="x" * 2500,
        tool_calls=[{"function": {"name": "read", "arguments": "{}"}}],
        finish_reason="tool_calls",
    )
    config = LoopConfig(max_new_tokens=16000)
    budget = analyze_generation_budget(response, config)
    assert not is_thinking_spiral(response, budget)


def test_first_pass_thinking_spiral_rescued():
    assert first_pass_thinking_spiral(
        thinking_rescued=True,
        completion_tokens=8192,
        initial_thinking_len=500,
        had_tool_calls=False,
        needs_tools=True,
    )


def test_first_pass_thinking_spiral_budget_without_rescue_flag():
    assert first_pass_thinking_spiral(
        thinking_rescued=False,
        completion_tokens=8192,
        initial_thinking_len=2000,
        had_tool_calls=False,
        needs_tools=True,
    )


def test_first_pass_thinking_spiral_not_when_tools_or_no_goals():
    assert not first_pass_thinking_spiral(
        thinking_rescued=True,
        completion_tokens=8192,
        initial_thinking_len=2000,
        had_tool_calls=True,
        needs_tools=True,
    )
    assert not first_pass_thinking_spiral(
        thinking_rescued=True,
        completion_tokens=8192,
        initial_thinking_len=2000,
        had_tool_calls=False,
        needs_tools=False,
    )


def test_build_reasoning_prefill():
    prefill = build_reasoning_prefill("I'll read the file next.", "continue")
    assert prefill is not None
    assert prefill["role"] == "assistant"
    assert "<think>" in prefill["content"]
    assert "read the file" in prefill["content"]
    assert "</think>" not in prefill["content"]

    assert build_reasoning_prefill("", "continue") is None
    assert build_reasoning_prefill("x", "restart") is None

    eval_prefill = build_reasoning_prefill(
        "retrying", "evaluate", last_error="file not found",
    )
    assert eval_prefill is not None
    assert "file not found" in eval_prefill["content"]
