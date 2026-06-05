"""Job-driven autonomous background work — charter-first, channel-agnostic.

Background wakes run only when the agent's Job explicitly enables them and
contains owner-authored charter content (not stock defaults).  The Job
mission/playbook/priorities define *what* to do; channel tools are discovered
via channel_inspect, not hardcoded per platform.

Priority (enforced in inner_loop): user/channel tasks → idle todos → plan work
→ job background → curiosity drives → DMN daydreaming.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from nls.runtime.job_trust import (
    DEFAULT_JOB_MISSION,
    DEFAULT_JOB_TITLE,
    JobDocument,
    job_path,
    load_job,
    save_job,
)

DEFAULT_BACKGROUND_INTERVAL_SECONDS = 3600
MIN_BACKGROUND_INTERVAL_SECONDS = 300


def is_stock_job(job: JobDocument) -> bool:
    """True when the job is still the generic factory default."""
    title = (job.title or "").strip()
    mission = (job.mission or "").strip()
    if title and title != DEFAULT_JOB_TITLE:
        return False
    if mission and mission != DEFAULT_JOB_MISSION:
        return False
    if (job.playbook or "").strip():
        return False
    if job.strategic_priorities:
        return False
    if job.in_scope:
        return False
    if (job.persona or "").strip():
        return False
    return True


def job_allows_background_work(
    job: JobDocument,
    agent_dir: Path | None = None,
) -> bool:
    """Background work only when explicitly enabled and charter is non-stock."""
    if not getattr(job, "background_enabled", False):
        return False
    if is_stock_job(job):
        return False
    if agent_dir is not None and not job_path(agent_dir).is_file():
        return False
    return True


def background_interval_seconds(job: JobDocument) -> int:
    raw = int(getattr(job, "background_interval_seconds", 0) or 0)
    if raw <= 0:
        raw = DEFAULT_BACKGROUND_INTERVAL_SECONDS
    return max(MIN_BACKGROUND_INTERVAL_SECONDS, raw)


def background_wake_due(job: JobDocument, now: float | None = None) -> bool:
    if not job_allows_background_work(job):
        return False
    ts = now if now is not None else time.time()
    last = float(getattr(job, "last_background_wake_at", 0) or 0)
    if last <= 0:
        return True
    return (ts - last) >= background_interval_seconds(job)


def record_background_wake(agent_dir: Path, job: JobDocument | None = None) -> None:
    job = job or load_job(agent_dir)
    job.last_background_wake_at = time.time()
    save_job(agent_dir, job)


def build_job_background_wake_prompt(
    job: JobDocument,
    *,
    wake_label: str = "JOB BACKGROUND",
    squad_blurb: str = "",
) -> str:
    """Prompt body from Job charter only — no platform-specific heuristics."""
    lines: list[str] = [f"[{wake_label}]"]
    if squad_blurb.strip():
        lines.append(squad_blurb.strip())
    if job.display_title:
        lines.append(f"Role: {job.display_title}")
    if (job.mission or "").strip():
        lines.append(f"Mission:\n{job.mission.strip()}")
    if (job.playbook or "").strip():
        lines.append(f"Playbook:\n{job.playbook.strip()[:1600]}")
    if job.strategic_priorities:
        pri = [p.strip() for p in job.strategic_priorities if p and p.strip()]
        if pri:
            lines.append("Priorities:\n" + "\n".join(f"- {p}" for p in pri[:8]))
    if job.in_scope:
        scope = [x.strip() for x in job.in_scope if x and x.strip()]
        if scope:
            lines.append("In scope:\n" + "\n".join(f"- {x}" for x in scope[:12]))

    lines.append(
        "Autonomous background turn — follow your Job charter. User/channel tasks "
        "and idle todos take precedence; this runs before private daydreaming.\n"
        "Discover linked surfaces with channel_inspect(action='get'); use the send "
        "tool for whichever channel your playbook names (Discord, Telegram, Slack, "
        "WhatsApp, etc.). Peer agent traffic on shared coordination channels is normal.\n"
        "Cross-surface inbound while busy elsewhere appears as [SURFACE INBOX] steering.\n"
        "In a squad: squad(action='propose') for lead approval; squad_escalate when blocked.\n"
        "If your charter gives nothing actionable right now, reply NOOP and stop."
    )
    return "\n".join(lines)


def job_background_blocked(
    *,
    has_pending_todos: bool = False,
    plan_work_open: bool = False,
    team_active: bool = False,
    user_busy: bool = False,
) -> bool:
    return bool(
        user_busy or has_pending_todos or plan_work_open or team_active
    )


def job_background_due_for_runtime(
    rt: Any,
    *,
    has_pending_todos: bool = False,
    plan_work_open: bool = False,
    team_active: bool = False,
) -> bool:
    if job_background_blocked(
        has_pending_todos=has_pending_todos,
        plan_work_open=plan_work_open,
        team_active=team_active,
        user_busy=getattr(rt, "is_user_busy", getattr(rt, "is_busy", False)),
    ):
        return False
    agent_dir = Path(getattr(rt, "agent_dir", "") or "")
    if not agent_dir.is_dir():
        return False
    job = load_job(agent_dir)
    return background_wake_due(job)
