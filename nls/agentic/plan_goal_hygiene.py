"""Plan-aligned tactical goal hygiene — no keyword blocklists.

A tactical goal is stale only when it clearly refers to a plan step that is
already ``done`` or ``skipped``, using normalized label overlap (not fixed
phrases like "github" or "FastAPI").
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Strip punctuation for comparison; keep word characters and spaces.
_NON_WORD_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")
_BLOCKER_GOAL_RE = re.compile(r"^\s*BLOCKER\s*:", re.IGNORECASE)


def normalize_plan_text(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text).lower()
    t = _NON_WORD_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


def _content_words(text: str, *, min_len: int = 4) -> set[str]:
    return {w for w in normalize_plan_text(text).split() if len(w) >= min_len}


def goal_references_plan_step(goal_text: str, step: Any) -> bool:
    """True when *goal_text* is about this plan step (label-level match)."""
    g = normalize_plan_text(goal_text)
    label = normalize_plan_text(getattr(step, "label", "") or "")
    if not g or not label:
        return False

    if g == label:
        return True

    # Substring match only for substantial labels (avoids matching "api", "db").
    if len(label) >= 12 and label in g:
        return True
    if len(g) >= 12 and g in label:
        return True

    g_words = _content_words(g)
    label_words = _content_words(label)
    if not g_words or not label_words:
        return False

    inter = len(g_words & label_words)
    union = len(g_words | label_words)
    if union == 0:
        return False
    return (inter / union) >= 0.55


def goal_is_stale_for_plan(goal_text: str, plan: Any) -> bool:
    """True if goal duplicates a completed/skipped plan step."""
    if not goal_text or plan is None:
        return False
    for step in getattr(plan, "steps", ()) or ():
        status = getattr(step, "status", "")
        if status not in ("done", "skipped"):
            continue
        if goal_references_plan_step(goal_text, step):
            return True
    return False


def filter_stale_tactical_goals(
    goals: list[str],
    plan: Any | None = None,
) -> list[str]:
    """Drop tactical goal strings superseded by done plan steps."""
    filtered = [
        g for g in goals
        if g and not _BLOCKER_GOAL_RE.match(g.strip())
    ]
    if not plan:
        return filtered
    return [
        g for g in filtered
        if not goal_is_stale_for_plan(g, plan)
    ]


def prune_stale_tactical_goals_for_plan(
    wm: Any,
    plan_store: Any,
    plan_id: str,
) -> int:
    """Remove WM tactical goals that only describe finished plan steps."""
    if wm is None or plan_store is None or not plan_id:
        return 0
    try:
        plan = plan_store.load(plan_id)
    except Exception:
        return 0
    if plan is None:
        return 0

    def _stale(g: Any) -> bool:
        text = (getattr(g, "content", None) or str(g) or "").strip()
        return bool(text) and goal_is_stale_for_plan(text, plan)

    if hasattr(wm, "remove_goals_where"):
        return wm.remove_goals_where(_stale)
    return 0
