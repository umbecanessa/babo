"""Detect and resume agentic loops interrupted by runtime disconnect."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_JOURNAL_MAX_AGE_SECONDS = 3600.0
PENDING_RESUME_FILENAME = "pending_loop_resume.json"
NOTIFY_STATE_FILENAME = "loop_interrupt_notify.json"

_RESUME_PHRASES = (
    r"^continue\b",
    r"^resume\b",
    r"^go on\b",
    r"^carry on\b",
    r"^keep going\b",
    r"^pick up where",
    r"^finish (the|what you)",
    r"^please continue",
    r"^try again\b",
    r"^where were you",
)
_RESUME_RE = re.compile("|".join(_RESUME_PHRASES), re.IGNORECASE)


def loop_journal_path(agent_dir: str | Path, agent_id: str) -> Path:
    base = Path(agent_dir) / "agentic_logs"
    tag = agent_id or "default"
    return base / f"loop_journal_{tag}.jsonl"


def pending_resume_path(agent_dir: str | Path) -> Path:
    return Path(agent_dir) / PENDING_RESUME_FILENAME


def notify_state_path(agent_dir: str | Path) -> Path:
    return Path(agent_dir) / NOTIFY_STATE_FILENAME


def _journal_age_seconds(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - when).total_seconds())
    except Exception:
        return None


def _read_journal_entry(agent_dir: str | Path, agent_id: str) -> dict[str, Any] | None:
    path = loop_journal_path(agent_dir, agent_id)
    if not path.is_file():
        return None
    try:
        last_line = ""
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line
        if not last_line:
            return None
        return json.loads(last_line)
    except Exception as exc:
        logger.debug("_read_journal_entry failed: %s", exc)
        return None


def extract_last_user_task_from_journal(
    agent_dir: str | Path,
    agent_id: str,
) -> str | None:
    """Best-effort last user message from a crash journal."""
    entry = _read_journal_entry(agent_dir, agent_id)
    if not entry:
        return None
    messages = entry.get("messages")
    if not isinstance(messages, list):
        return None
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = str(msg.get("content") or "").strip()
        if content and not content.startswith("[BUDGET"):
            return content[:2000]
    return None


def read_interrupted_loop(
    agent_dir: str | Path,
    agent_id: str,
    *,
    max_age_seconds: float = DEFAULT_JOURNAL_MAX_AGE_SECONDS,
) -> dict[str, Any] | None:
    """Return metadata when a recent crash journal exists from an unfinished loop."""
    entry = _read_journal_entry(agent_dir, agent_id)
    if not entry:
        return None
    try:
        iteration = int(entry.get("iteration") or 0)
        if iteration <= 0:
            return None
        ts = entry.get("ts")
        age = _journal_age_seconds(ts)
        if age is not None and age > max_age_seconds:
            logger.debug(
                "Ignoring stale loop journal (age=%.0fs > %.0fs)",
                age,
                max_age_seconds,
            )
            return None
        last_task = extract_last_user_task_from_journal(agent_dir, agent_id)
        return {
            "iteration": iteration,
            "interrupted_at": ts,
            "message_count": entry.get("n_messages"),
            "recoverable": True,
            "journal_path": str(loop_journal_path(agent_dir, agent_id)),
            "age_seconds": age,
            "last_task_preview": (last_task or "")[:240],
            "resume_token": ts or "",
        }
    except Exception as exc:
        logger.debug("read_interrupted_loop failed: %s", exc)
        return None


def read_pending_loop_resume(agent_dir: str | Path) -> dict[str, Any] | None:
    path = pending_resume_path(agent_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.debug("read_pending_loop_resume failed: %s", exc)
        return None


def save_pending_loop_resume(
    agent_dir: str | Path,
    *,
    agent_id: str,
    user_input: str,
    iteration: int,
    interrupted_at: str | None,
    journal_path: str = "",
) -> None:
    path = pending_resume_path(agent_dir)
    payload = {
        "agent_id": agent_id,
        "last_user_input": (user_input or "")[:4000],
        "iteration": iteration,
        "interrupted_at": interrupted_at or "",
        "journal_path": journal_path,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        logger.debug("save_pending_loop_resume failed: %s", exc)


def clear_pending_loop_resume(agent_dir: str | Path) -> None:
    path = pending_resume_path(agent_dir)
    try:
        if path.is_file():
            path.unlink()
    except Exception as exc:
        logger.debug("clear_pending_loop_resume failed: %s", exc)


def clear_notify_state(agent_dir: str | Path) -> None:
    path = notify_state_path(agent_dir)
    try:
        if path.is_file():
            path.unlink()
    except Exception as exc:
        logger.debug("clear_notify_state failed: %s", exc)


def should_notify_loop_interrupted(
    agent_dir: str | Path,
    resume_token: str,
) -> bool:
    """True when this interrupt has not been surfaced to the client yet."""
    if not resume_token:
        return True
    path = notify_state_path(agent_dir)
    try:
        if path.is_file():
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("last_notified_token") == resume_token:
                return False
            if state.get("dismissed_token") == resume_token:
                return False
    except Exception:
        pass
    return True


def mark_loop_interrupt_notified(
    agent_dir: str | Path,
    resume_token: str,
) -> None:
    if not resume_token:
        return
    path = notify_state_path(agent_dir)
    payload = {
        "last_notified_token": resume_token,
        "notified_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.debug("mark_loop_interrupt_notified failed: %s", exc)


def mark_loop_interrupt_dismissed(
    agent_dir: str | Path,
    resume_token: str,
) -> None:
    path = notify_state_path(agent_dir)
    payload = {
        "dismissed_token": resume_token or "",
        "dismissed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.debug("mark_loop_interrupt_dismissed failed: %s", exc)


def _delete_journal(agent_dir: str | Path, agent_id: str) -> None:
    path = loop_journal_path(agent_dir, agent_id)
    for p in (path, str(path) + ".tmp"):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def abandon_interrupted_loop(agent_dir: str | Path, agent_id: str) -> None:
    """Drop crash journal and pending resume state (user started a new task)."""
    _delete_journal(agent_dir, agent_id)
    clear_pending_loop_resume(agent_dir)
    from nls.agentic.active_loop_marker import clear_agentic_active

    clear_agentic_active(agent_dir)


def clear_interrupted_loop_on_success(agent_dir: str | Path, agent_id: str) -> None:
    """Clean up all interrupt artifacts after a successful loop completion."""
    _delete_journal(agent_dir, agent_id)
    clear_pending_loop_resume(agent_dir)
    clear_notify_state(agent_dir)
    from nls.agentic.active_loop_marker import clear_agentic_active

    clear_agentic_active(agent_dir)


def wants_loop_resume(user_input: str) -> bool:
    text = (user_input or "").strip()
    if not text:
        return False
    if len(text) <= 120 and _RESUME_RE.search(text):
        return True
    return False


def build_resume_user_input(
    *,
    last_task: str | None,
    user_input: str = "",
) -> str:
    """User message that continues an interrupted loop with journal recovery."""
    task = (last_task or "").strip()
    extra = (user_input or "").strip()
    if task and extra and not wants_loop_resume(extra):
        return (
            f"Continue the interrupted task.\n\n"
            f"Original request:\n{task}\n\n"
            f"Additional guidance:\n{extra}"
        )
    if task:
        return (
            "Continue the interrupted task from where you left off. "
            f"Original request:\n{task}"
        )
    if extra:
        return f"Continue the interrupted task. {extra}"
    return (
        "Continue the interrupted task from where you left off. "
        "Use the saved checkpoint if available."
    )


def resolve_resume_context(
    agent_dir: str | Path,
    agent_id: str,
    user_input: str,
    *,
    explicit_resume: bool = False,
) -> tuple[bool, str]:
    """Decide whether to recover the crash journal for this user turn.

    Returns ``(should_recover_journal, effective_user_input)``.
    """
    interrupted = read_interrupted_loop(agent_dir, agent_id)
    if not interrupted:
        return False, user_input

    if explicit_resume or wants_loop_resume(user_input):
        pending = read_pending_loop_resume(agent_dir) or {}
        last_task = (
            pending.get("last_user_input")
            or interrupted.get("last_task_preview")
            or extract_last_user_task_from_journal(agent_dir, agent_id)
        )
        effective = build_resume_user_input(
            last_task=last_task,
            user_input=user_input if not explicit_resume else "",
        )
        return True, effective

    abandon_interrupted_loop(agent_dir, agent_id)
    return False, user_input


def format_interrupted_loop_status(payload: dict[str, Any]) -> str:
    iteration = payload.get("iteration", "?")
    preview = (payload.get("last_task_preview") or "").strip()
    base = (
        f"Previous task was interrupted at step {iteration} when the runtime "
        "reconnected."
    )
    if preview:
        short = preview if len(preview) <= 120 else preview[:117] + "..."
        base += f' Last request: "{short}"'
    base += " Use Continue to resume from the checkpoint, or send a new message to start fresh."
    return base


def sync_pending_from_journal(agent_dir: str | Path, agent_id: str) -> None:
    """Ensure pending resume metadata exists when only the journal is present."""
    if read_pending_loop_resume(agent_dir):
        return
    interrupted = read_interrupted_loop(agent_dir, agent_id)
    if not interrupted:
        return
    last_task = extract_last_user_task_from_journal(agent_dir, agent_id) or ""
    save_pending_loop_resume(
        agent_dir,
        agent_id=agent_id,
        user_input=last_task,
        iteration=int(interrupted.get("iteration") or 0),
        interrupted_at=str(interrupted.get("interrupted_at") or ""),
        journal_path=str(interrupted.get("journal_path") or ""),
    )
