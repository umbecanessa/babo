"""Interrupted loop recovery metadata."""

from __future__ import annotations

import json
from pathlib import Path

from nls.agentic.interrupt_recovery import (
    format_interrupted_loop_status,
    loop_journal_path,
    read_interrupted_loop,
)


def test_read_interrupted_loop_from_journal(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    logs = agent_dir / "agentic_logs"
    logs.mkdir()
    journal = logs / "loop_journal_agent-1.jsonl"
    journal.write_text(
        json.dumps({
            "ts": "2026-06-08T14:02:50Z",
            "iteration": 19,
            "n_messages": 63,
            "messages": [],
        })
        + "\n",
        encoding="utf-8",
    )

    payload = read_interrupted_loop(agent_dir, "agent-1")
    assert payload is not None
    assert payload["iteration"] == 19
    assert payload["recoverable"] is True
    assert "loop_journal_agent-1.jsonl" in payload["journal_path"]


def test_read_interrupted_loop_missing_file(tmp_path: Path):
    assert read_interrupted_loop(tmp_path, "missing") is None


def test_read_interrupted_loop_ignores_stale_journal(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    logs = agent_dir / "agentic_logs"
    logs.mkdir()
    journal = logs / "loop_journal_agent-1.jsonl"
    journal.write_text(
        json.dumps({
            "ts": "2020-01-01T00:00:00+00:00",
            "iteration": 19,
            "n_messages": 63,
            "messages": [],
        })
        + "\n",
        encoding="utf-8",
    )

    assert read_interrupted_loop(agent_dir, "agent-1", max_age_seconds=3600) is None


def test_format_interrupted_loop_status():
    text = format_interrupted_loop_status({"iteration": 19})
    assert "step 19" in text
    assert "Continue" in text
