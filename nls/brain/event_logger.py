"""Event Logger — convenience wrapper (M-027).

Re-exports ``EventLogger`` from ``nls.engine.logger`` and provides
a factory for creating per-agent loggers.  This module exists so
all agent runtimes use the same construction pattern.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .logger import EventLogger


def create_event_logger(
    agent_dir: Path,
    config: dict[str, Any] | None = None,
) -> EventLogger:
    """Create an EventLogger for the given agent directory."""
    cfg = (config or {}).get("logging", {})
    return EventLogger(
        log_dir=agent_dir / "events",
        enabled=cfg.get("enabled", True),
        max_size_mb=cfg.get("rotation_mb", 50),
    )


def wire_event_logger(
    event_logger: EventLogger,
    *,
    hypothalamus: Any | None = None,
    ans: Any | None = None,
    calibrator: Any | None = None,
    drive_engine: Any | None = None,
) -> None:
    """Wire an event logger into brain components."""
    if not event_logger.enabled:
        return
    for component in (hypothalamus, ans, calibrator, drive_engine):
        if component is not None:
            component._event_logger = event_logger
