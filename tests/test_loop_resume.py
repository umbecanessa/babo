"""Loop resume and active-marker lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

from nls.agentic.active_loop_marker import (
    clear_agentic_active,
    count_active_agentic_loops,
    mark_agentic_active,
    read_agentic_active,
)
from nls.agentic.interrupt_recovery import (
    abandon_interrupted_loop,
    build_resume_user_input,
    clear_interrupted_loop_on_success,
    read_interrupted_loop,
    resolve_resume_context,
    save_pending_loop_resume,
    should_notify_loop_interrupted,
    wants_loop_resume,
)


def test_wants_loop_resume_detects_short_continue():
    assert wants_loop_resume("continue") is True
    assert wants_loop_resume("Please continue the install") is True
    assert wants_loop_resume("fix the CSS on the login page") is False


def test_resolve_resume_context_abandons_on_new_task(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    logs = agent_dir / "agentic_logs"
    logs.mkdir(parents=True)
    journal = logs / "loop_journal_a1.jsonl"
    journal.write_text(
        json.dumps({
            "ts": "2026-06-08T14:02:50+00:00",
            "iteration": 5,
            "n_messages": 10,
            "messages": [{"role": "user", "content": "run locally"}],
        })
        + "\n",
        encoding="utf-8",
    )

    recover, text = resolve_resume_context(
        agent_dir,
        "a1",
        "fix the backend import error in transcript.py",
    )
    assert recover is False
    assert "transcript.py" in text
    assert read_interrupted_loop(agent_dir, "a1") is None


def test_resolve_resume_context_keeps_journal_on_explicit_resume(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    logs = agent_dir / "agentic_logs"
    logs.mkdir(parents=True)
    journal = logs / "loop_journal_a1.jsonl"
    journal.write_text(
        json.dumps({
            "ts": "2026-06-08T14:02:50+00:00",
            "iteration": 5,
            "n_messages": 10,
            "messages": [{"role": "user", "content": "run locally"}],
        })
        + "\n",
        encoding="utf-8",
    )
    save_pending_loop_resume(
        agent_dir,
        agent_id="a1",
        user_input="run locally",
        iteration=5,
        interrupted_at="2026-06-08T14:02:50+00:00",
    )

    recover, text = resolve_resume_context(
        agent_dir,
        "a1",
        "continue",
        explicit_resume=True,
    )
    assert recover is True
    assert "run locally" in text
    assert read_interrupted_loop(agent_dir, "a1") is not None


def test_build_resume_user_input_includes_original_task():
    text = build_resume_user_input(last_task="run the backend locally")
    assert "run the backend locally" in text
    assert "Continue" in text


def test_should_notify_loop_interrupted_dedupes(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    token = "2026-06-08T14:02:50+00:00"
    assert should_notify_loop_interrupted(agent_dir, token) is True
    from nls.agentic.interrupt_recovery import mark_loop_interrupt_notified

    mark_loop_interrupt_notified(agent_dir, token)
    assert should_notify_loop_interrupted(agent_dir, token) is False


def test_active_loop_marker_lifecycle(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    mark_agentic_active(agent_dir, agent_id="a1", loop_id="loop-1")
    assert read_agentic_active(agent_dir) is not None
    assert count_active_agentic_loops(tmp_path) == 1
    clear_agentic_active(agent_dir)
    assert read_agentic_active(agent_dir) is None


def test_clear_interrupted_loop_on_success(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    logs = agent_dir / "agentic_logs"
    logs.mkdir(parents=True)
    journal = logs / "loop_journal_a1.jsonl"
    journal.write_text('{"ts":"2026-06-08T14:02:50+00:00","iteration":3,"messages":[]}\n')
    save_pending_loop_resume(
        agent_dir,
        agent_id="a1",
        user_input="task",
        iteration=3,
        interrupted_at="2026-06-08T14:02:50+00:00",
    )
    mark_agentic_active(agent_dir, agent_id="a1")

    clear_interrupted_loop_on_success(agent_dir, "a1")
    assert read_interrupted_loop(agent_dir, "a1") is None
    assert read_agentic_active(agent_dir) is None


def test_abandon_interrupted_loop(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    logs = agent_dir / "agentic_logs"
    logs.mkdir(parents=True)
    journal = logs / "loop_journal_a1.jsonl"
    journal.write_text('{"ts":"2026-06-08T14:02:50+00:00","iteration":3,"messages":[]}\n')
    mark_agentic_active(agent_dir, agent_id="a1")

    abandon_interrupted_loop(agent_dir, "a1")
    assert read_interrupted_loop(agent_dir, "a1") is None
    assert read_agentic_active(agent_dir) is None
