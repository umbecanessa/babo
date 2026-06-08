"""Heuristics for when new todos should be idle-eligible."""

from __future__ import annotations

import re
from typing import Any

_BUG_IDLE_RE = re.compile(
    r"\b("
    r"bug|black\s*screen|crash|login\s*fail|investigate|"
    r"repro(?:duce| steps)?|qa\b|fix\s+(?:todo|this)"
    r")\b",
    re.IGNORECASE,
)

_IDLE_SOURCES = frozenset(("channel", "job", "system"))
_IDLE_TAGS = frozenset(("bug", "qa", "investigate", "idle"))


def infer_idle_eligible(
    params: dict[str, Any],
    *,
    title: str,
    description: str = "",
) -> bool:
    """Return whether a new todo should be picked up during idle time."""
    if "idle_eligible" in params:
        return bool(params["idle_eligible"])

    source = str(params.get("source") or "user").lower()
    if source in _IDLE_SOURCES:
        return True

    tags = {str(t).lower() for t in (params.get("tags") or []) if str(t).strip()}
    if tags & _IDLE_TAGS:
        return True

    blob = f"{title} {description}".strip()
    if blob and _BUG_IDLE_RE.search(blob):
        return True

    return False


def looks_like_investigation_todo(title: str, description: str = "", tags: list[str] | None = None) -> bool:
    """True when completing the todo should require evidence of work."""
    tag_set = {str(t).lower() for t in (tags or []) if str(t).strip()}
    if tag_set & _IDLE_TAGS:
        return True
    blob = f"{title} {description}".strip()
    return bool(blob and _BUG_IDLE_RE.search(blob))
