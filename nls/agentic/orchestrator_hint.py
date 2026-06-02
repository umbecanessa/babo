"""Helpers for steering delegates via SubCryptex orchestrator ring."""

from __future__ import annotations

import re
from typing import Any

from nls.brain.sub_cryptex import SUB_RING_ORCHESTRATOR

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
