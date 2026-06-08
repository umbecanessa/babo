"""Persist in-flight agentic loop markers for health checks and restart deferral."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MARKER_FILENAME = ".agentic_active.json"
DEFAULT_MARKER_MAX_AGE_SECONDS = 600.0


def marker_path(agent_dir: str | Path) -> Path:
    return Path(agent_dir) / MARKER_FILENAME


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when
    except Exception:
        return None


def mark_agentic_active(
    agent_dir: str | Path,
    *,
    agent_id: str,
    loop_id: str = "",
    user_input_preview: str = "",
) -> None:
    """Record that an agentic loop is running (survives WS disconnect)."""
    path = marker_path(agent_dir)
    payload = {
        "agent_id": agent_id,
        "loop_id": loop_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "user_input_preview": (user_input_preview or "")[:500],
    }
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        logger.debug("mark_agentic_active failed: %s", exc)
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def clear_agentic_active(agent_dir: str | Path) -> None:
    """Remove the in-flight marker after loop completion or abandonment."""
    path = marker_path(agent_dir)
    try:
        if path.is_file():
            path.unlink()
    except Exception as exc:
        logger.debug("clear_agentic_active failed: %s", exc)


def read_agentic_active(
    agent_dir: str | Path,
    *,
    max_age_seconds: float = DEFAULT_MARKER_MAX_AGE_SECONDS,
) -> dict[str, Any] | None:
    path = marker_path(agent_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        started = _parse_ts(payload.get("started_at"))
        if started is not None:
            age = (datetime.now(timezone.utc) - started).total_seconds()
            if age > max_age_seconds:
                try:
                    path.unlink()
                except Exception:
                    pass
                return None
            payload["age_seconds"] = age
        return payload
    except Exception as exc:
        logger.debug("read_agentic_active failed: %s", exc)
        return None


def count_active_agentic_loops(
    agents_dir: str | Path,
    *,
    max_age_seconds: float = DEFAULT_MARKER_MAX_AGE_SECONDS,
) -> int:
    """Count agents with a recent on-disk agentic-active marker."""
    root = Path(agents_dir)
    if not root.is_dir():
        return 0
    count = 0
    for agent_home in root.iterdir():
        if not agent_home.is_dir():
            continue
        if read_agentic_active(agent_home, max_age_seconds=max_age_seconds):
            count += 1
    return count
