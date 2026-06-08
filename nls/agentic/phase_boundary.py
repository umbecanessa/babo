"""Phase-boundary hygiene when a user starts a new task after plan build."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_REVIEW_MARKERS = (
    "COMPLETION REVIEW",
    "EM TURN — COMPLETION REVIEW",
    "COMPLETION REVIEW REQUIRED",
    "[COMPLETION REVIEW",
)


def _message_text(msg: dict[str, Any]) -> str:
    content = msg.get("content") or ""
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def _is_completion_review_message(msg: dict[str, Any]) -> bool:
    if msg.get("role") not in ("assistant", "user", "system"):
        return False
    text = _message_text(msg)
    return any(marker in text for marker in _REVIEW_MARKERS)


def trim_context_for_phase_boundary(
    context: list[dict[str, Any]],
    *,
    user_input: str,
    goals: list[str] | None = None,
    plan_id: str = "",
) -> list[dict[str, Any]]:
    """Drop stale completion-review turns; inject a fresh mission anchor."""
    if not context:
        return context

    system_msgs = [m for m in context if m.get("role") == "system"]
    other = [m for m in context if m.get("role") != "system"]
    review_count = sum(1 for m in other if _is_completion_review_message(m))
    kept = [m for m in other if not _is_completion_review_message(m)]

    if review_count == 0 and len(other) <= 14:
        return context

    tail = 8 if review_count else 10
    if len(kept) > tail:
        kept = kept[-tail:]

    goal_line = "; ".join(g for g in (goals or []) if g)[:400]
    if not goal_line:
        goal_line = (user_input or "").strip()[:400]
    boundary = {
        "role": "system",
        "content": (
            "[NEW TASK PHASE — prior build plan "
            f"{plan_id or 'ledger complete'}; deliverables are on disk]\n"
            f"Focus only on the current request: {goal_line}\n"
            "Ignore old completion-review / delegate wrap-up turns above."
        ),
    }

    trimmed = (system_msgs[:1] if system_msgs else []) + [boundary] + kept
    logger.info(
        "Phase boundary: trimmed context %d → %d (dropped %d review messages)",
        len(context),
        len(trimmed),
        review_count,
    )
    return trimmed
