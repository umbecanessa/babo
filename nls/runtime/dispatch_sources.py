"""Classify agentic turn sources for scheduling and preemption."""

from __future__ import annotations

# Intentional background orchestration (not a human/channel turn).
_ORCHESTRATION_EXACT = frozenset({
    "autonomous",
    "dmn",
    "idle",
    "",
    "delegate_batch_complete",
})
_ORCHESTRATION_PREFIXES = (
    "scheduler",
    "drive:",
    "delegate",
    "team_checkback:",
    "team_wave_complete:",
    "team_completion_review:",
    "team_member_escalation:",
    "pending_wave_launch:",
    "check_back",
)


def is_orchestration_dispatch_source(source: str) -> bool:
    """True for scheduler wake-ups, delegate batch complete, team check-backs, DMN, drives."""
    if source in _ORCHESTRATION_EXACT:
        return True
    return any(source.startswith(p) for p in _ORCHESTRATION_PREFIXES)
