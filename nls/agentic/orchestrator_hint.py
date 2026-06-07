"""Helpers for steering delegates via SubCryptex orchestrator ring."""

from __future__ import annotations

import re
from typing import Any

from nls.brain.sub_cryptex import SUB_RING_ORCHESTRATOR

HINT_DELIVERY_BOTH = "both"
HINT_DELIVERY_RING = "ring"
HINT_CHAT_MAX_CHARS = 4000

_FINALIZE_RE = re.compile(
    r"\b(stop|finalize|task_complete|exit|terminate|done\s+now|wrap\s+up)\b",
    re.IGNORECASE,
)
_EXTEND_RE = re.compile(
    r"\b(extend|more\s+iterations|continue\s+working)\b",
    re.IGNORECASE,
)


def infer_directive_domain(message: str, *, action: str = "") -> str:
    """Classify a hint/intervene message for replace-on-write domain keys."""
    if action in ("terminate", "approve"):
        return "finalize"
    if action == "extend":
        return "extend"
    if _FINALIZE_RE.search(message or ""):
        return "finalize"
    if _EXTEND_RE.search(message or ""):
        return "extend"
    return "hint"


def build_orchestrator_ring_ops(
    message: str,
    *,
    domain: str | None = None,
    salience: float = 0.95,
) -> list[dict[str, Any]]:
    """Ring ops list for DelegateManager.hint / intervene."""
    dom = domain or infer_directive_domain(message)
    return [{
        "ring": SUB_RING_ORCHESTRATOR,
        "domain": dom,
        "content": message.strip(),
        "salience": salience,
    }]


def apply_orchestrator_directive(
    sub_cryptex: Any,
    message: str,
    *,
    domain: str | None = None,
    salience: float = 0.95,
) -> bool:
    if sub_cryptex is None or not message.strip():
        return False
    dom = domain or infer_directive_domain(message)
    return sub_cryptex.upsert_orchestrator_directive(
        message,
        domain=dom,
        salience=salience,
        replace_domain=True,
    )


def normalize_delivery_mode(delivery: str | None) -> str | None:
    """Return ``both`` or ``ring``, or None when *delivery* is invalid."""
    if delivery is None or not str(delivery).strip():
        return HINT_DELIVERY_BOTH
    mode = str(delivery).strip().lower()
    if mode in (HINT_DELIVERY_BOTH, HINT_DELIVERY_RING):
        return mode
    return None


def resolve_hint_delivery(
    *,
    delivery: str | None = None,
    also_chat_hint: bool | None = None,
) -> tuple[bool, bool, str]:
    """Return ``(use_ring, use_chat, label)`` for hint delivery mode."""
    if also_chat_hint is not None:
        label = HINT_DELIVERY_BOTH if also_chat_hint else HINT_DELIVERY_RING
        return True, also_chat_hint, label
    mode = normalize_delivery_mode(delivery) or HINT_DELIVERY_BOTH
    if mode == HINT_DELIVERY_RING:
        return True, False, HINT_DELIVERY_RING
    return True, True, HINT_DELIVERY_BOTH


def intervention_dict_to_steering_msg(item: dict) -> dict | None:
    """Convert an ``intervene()`` queue dict to a chat steering message.

    Returns None when the item should stay on the queue for blocking waits
    (``delivery='ring'``, ``terminate``, ``approve``, etc.).
    """
    if not isinstance(item, dict) or "action" not in item:
        return None
    delivery = normalize_delivery_mode(item.get("delivery")) or HINT_DELIVERY_BOTH
    if delivery == HINT_DELIVERY_RING:
        return None
    action = item.get("action", "")
    message = (item.get("message") or "").strip()
    if action == "hint":
        return build_orchestrator_chat_hint(message or "Try a different approach.")
    if action == "extend":
        text = message or "Iteration budget extended."
        return {"role": "user", "content": f"[ORCHESTRATOR] {text}"}
    return None


def build_orchestrator_chat_hint(message: str) -> dict[str, str]:
    """Chat-turn payload for the delegate steering queue (ack path)."""
    text = (message or "").strip()
    if len(text) > HINT_CHAT_MAX_CHARS:
        text = text[:HINT_CHAT_MAX_CHARS] + "..."
    return {
        "role": "user",
        "content": f"[ORCHESTRATOR HINT] {text}",
    }
