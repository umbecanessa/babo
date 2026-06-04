"""Owner-facing channels (WhatsApp/Telegram) route to squad lead only."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Channels treated as owner-private when agent is a squad member (not lead).
_OWNER_CHANNEL_NAMES = frozenset({
    "whatsapp",
    "telegram",
})


def _squad_registry(app: Any) -> Any | None:
    return getattr(app.state, "squad_registry", None)


def channel_delivery_allowed(
    app: Any,
    agent_id: str,
    channel_name: str,
) -> tuple[bool, str]:
    """Return (allowed, refusal_message)."""
    ch = (channel_name or "").strip().lower()
    if ch not in _OWNER_CHANNEL_NAMES:
        return True, ""
    reg = _squad_registry(app)
    if reg is None:
        return True, ""
    squad = reg.get_for_agent(agent_id)
    if squad is None:
        return True, ""
    if squad.is_lead(agent_id):
        return True, ""
    lead = squad.lead_agent_id or "your squad lead"
    return (
        False,
        f"This {ch} channel is configured for the squad lead ({lead}). "
        "Members cannot receive owner messages on this channel.",
    )
