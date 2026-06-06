"""Hooks for recording ambient group traffic before mention policy gates."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def resolve_agent_dir(
    runtime_or_dir: Any = None,
    *,
    app: Any = None,
    agent_id: str = "",
) -> Path | None:
    """Best-effort agent data directory for ambient persistence."""
    if isinstance(runtime_or_dir, Path):
        return runtime_or_dir
    if runtime_or_dir is not None:
        agent_dir = getattr(runtime_or_dir, "agent_dir", None)
        if agent_dir is not None:
            return Path(agent_dir)
    if app is None or not agent_id:
        return None
    try:
        am = getattr(app.state, "agent_manager", None)
        if am is None:
            return None
        runtime = am.get_runtime(agent_id)
        if runtime is not None:
            agent_dir = getattr(runtime, "agent_dir", None)
            if agent_dir is not None:
                return Path(agent_dir)
        agents_dir = getattr(am, "agents_dir", None)
        if agents_dir:
            return Path(agents_dir) / agent_id
    except Exception:
        return None
    return None


def record_inbound_ambient(
    runtime_or_dir: Any,
    normalized: dict[str, Any],
    *,
    triggered: bool,
    app: Any = None,
    agent_id: str = "",
) -> None:
    """Persist a group message regardless of whether we will reply."""
    agent_dir = resolve_agent_dir(runtime_or_dir, app=app, agent_id=agent_id)
    if agent_dir is None or not normalized:
        return
    try:
        from nls.runtime.channel_ambient import append_channel_ambient

        append_channel_ambient(agent_dir, normalized, triggered=triggered)
    except Exception as exc:
        logger.debug("ambient inbound record failed: %s", exc)


def record_outbound_ambient(
    runtime_or_dir: Any,
    normalized: dict[str, Any],
    content: str,
    *,
    app: Any = None,
    agent_id: str = "",
) -> None:
    agent_dir = resolve_agent_dir(runtime_or_dir, app=app, agent_id=agent_id)
    if agent_dir is None or not normalized:
        return
    try:
        from nls.runtime.channel_ambient import append_channel_ambient_reply

        append_channel_ambient_reply(agent_dir, normalized, content)
    except Exception as exc:
        logger.debug("ambient outbound record failed: %s", exc)
