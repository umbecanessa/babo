"""Brain Context Builder — extracted from ServerRuntime (M-022).

Collects context strings from Theory of Mind, Narrative Self,
Predictive Processing, Network Dynamics, Self State, and ANS.

Per KL #339, these are DISABLED for some model families (base model treats them
as noise). Extracted here so they're available when the Meta expert
is retrained to understand them.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_brain_context(
    *,
    theory_of_mind: Any | None = None,
    narrative_self: Any | None = None,
    predictive: Any | None = None,
    network_dynamics: Any | None = None,
    self_state: Any | None = None,
    ans: Any | None = None,
    moe_mode: bool = False,
) -> str:
    """Build the combined brain context prefix.

    When ``moe_mode=True``, returns empty string (KL #339).
    """
    if moe_mode:
        return ""

    parts: list[str] = []

    if narrative_self is not None:
        try:
            ctx = narrative_self.get_narrative_context()
            if ctx:
                parts.append(ctx)
        except Exception:
            pass

    if theory_of_mind is not None:
        try:
            ctx = theory_of_mind.get_context_string()
            if ctx:
                parts.append(ctx)
        except Exception:
            pass

    if predictive is not None:
        try:
            ctx = predictive.get_context_string()
            if ctx:
                parts.append(ctx)
        except Exception:
            pass

    if network_dynamics is not None:
        try:
            ctx = network_dynamics.get_context_string()
            if ctx:
                parts.append(ctx)
        except Exception:
            pass

    if self_state is not None:
        try:
            heartbeat = self_state.to_json()
            if heartbeat:
                parts.append(heartbeat)
        except Exception:
            pass

    if ans is not None:
        try:
            ctx = ans.get_context_summary()
            if ctx:
                parts.append(ctx)
        except Exception:
            pass

    return "\n\n".join(parts)
