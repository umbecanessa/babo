"""Tests for job-driven background work."""

from __future__ import annotations

import time
from pathlib import Path

from nls.runtime.job_background import (
    background_wake_due,
    build_job_background_wake_prompt,
    is_stock_job,
    job_allows_background_work,
)
from nls.runtime.job_trust import JobDocument, load_job, save_job


def test_stock_job_not_background_eligible():
    job = JobDocument()
    assert is_stock_job(job)
    assert not job_allows_background_work(job)


def test_custom_job_requires_background_enabled(tmp_path: Path):
    agent_dir = tmp_path / "agent_a"
    agent_dir.mkdir()
    job = JobDocument(title="Moderator", mission="Keep the community safe.")
    save_job(agent_dir, job)
    assert not job_allows_background_work(job, agent_dir)

    job.background_enabled = True
    save_job(agent_dir, job)
    loaded = load_job(agent_dir)
    assert job_allows_background_work(loaded, agent_dir)


def test_background_wake_respects_interval():
    job = JobDocument(
        title="QA Bot",
        mission="Triage bugs.",
        background_enabled=True,
        last_background_wake_at=time.time(),
        background_interval_seconds=3600,
    )
    assert not background_wake_due(job)


def test_wake_prompt_is_channel_agnostic():
    job = JobDocument(
        title="Mod",
        mission="Monitor Telegram and Slack staff channels.",
        playbook="Post weekly status in #staff on whichever platform is linked.",
        background_enabled=True,
    )
    prompt = build_job_background_wake_prompt(job)
    assert "channel_inspect" in prompt
    assert "discord_send" not in prompt.lower()
    assert "Mission:" in prompt
    assert "Playbook:" in prompt
