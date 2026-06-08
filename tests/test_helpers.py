"""Shared helpers for tests (importable from test modules)."""

from __future__ import annotations

from datetime import datetime, timezone


def recent_journal_ts() -> str:
    """ISO timestamp fresh enough for DEFAULT_JOURNAL_MAX_AGE_SECONDS (1h)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
