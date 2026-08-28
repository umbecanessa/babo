"""Orchestration-aware sleep policy — preserve squad/delegate context."""

from __future__ import annotations

from typing import Any


def should_preserve_orchestration_on_sleep(runtime: Any) -> bool:
    """True when active teams or delegates must survive a sleep cycle."""
    tm = getattr(runtime, "_team_manager", None)
    if tm is not None:
        try:
            if tm.has_active_orchestration():
                return True
        except Exception:
            pass
    dm = getattr(runtime, "delegate_manager", None)
    if dm is None:
        _tm = tm
        if _tm is not None:
            dm = getattr(_tm, "delegate_manager", None)
    if dm is not None:
        try:
            if dm.has_active_delegates():
                return True
        except Exception:
            pass
    return False
