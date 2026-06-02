"""Offline tests for agentic loop final-response backfill and generator helpers."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nls.agentic.generator import (  # noqa: E402
    is_transient,
    sanitize_generation_error_for_user,
)
from nls.agentic.loop import apply_final_response_backfill  # noqa: E402
from nls.agentic.types import LoopState  # noqa: E402


def test_apply_final_response_respects_existing():
    st = LoopState()
    st.final_response = "already set"
    st.exit_reason = "task_complete"
    apply_final_response_backfill(st, "")
    assert st.final_response == "already set"


def test_apply_final_task_complete_uses_last_substantive():
    st = LoopState()
    st.exit_reason = "task_complete"
    st.iteration = 3
    st.total_tool_calls = 2
    apply_final_response_backfill(st, "Hello from prior turn")
    assert st.final_response == "Hello from prior turn"


def test_apply_final_task_complete_uses_last_iter_text():
    st = LoopState()
    st.exit_reason = "task_complete"
    st.iteration = 2
    st.total_tool_calls = 1
    st._last_iter_text = "Short"
    apply_final_response_backfill(st, "")
    assert st.final_response == "Short"


def test_apply_final_task_complete_uses_cumulative_actions():
    st = LoopState()
    st.exit_reason = "task_complete"
    st.iteration = 4
    st.total_tool_calls = 3
    st.cumulative_actions = ["bash: OK", "read: OK"]
    apply_final_response_backfill(st, "")
    assert "Task completed" in st.final_response
    assert "bash: OK" in st.final_response


def test_apply_final_task_complete_minimal_bracket():
    st = LoopState()
    st.exit_reason = "task_complete"
    st.iteration = 1
    st.total_tool_calls = 0
    apply_final_response_backfill(st, "")
    assert "no visible summary" in st.final_response


def test_apply_final_generation_error_surfaces_sanitized():
    st = LoopState()
    st.exit_reason = "generation_error"
    st.iteration = 1
    st.total_tool_calls = 0
    st.last_generation_error = "HTTP 400 bad request: invalid payload"
    apply_final_response_backfill(st, "")
    assert "Generation failed" in st.final_response
    assert "400" in st.final_response


def test_sanitize_redacts_github_pat():
    raw = "failed ghp_abcdefghijklmnopqrstuvwxyz1234567890 end"
    out = sanitize_generation_error_for_user(raw)
    assert "ghp_" not in out
    assert "[token_redacted]" in out


def test_is_transient_covers_rate_limit():
    assert is_transient("Error 429 Too Many Requests")
    assert is_transient("rate limit exceeded")
    assert is_transient("Read timed out")

