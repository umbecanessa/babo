"""Channel skill credentials — WM slots must not duplicate Discord bot tokens."""

from __future__ import annotations

import re
from typing import Any

# Discord bot tokens: base64.user_id.signature (rough shape; never log full value).
_DISCORD_BOT_TOKEN_RE = re.compile(
    r"^[MN][A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}$",
)

_WM_DISCORD_CREDENTIAL_GUIDANCE = (
    "Discord credentials live in discord-channel skill config "
    "(data/skills/discord-channel/agents/{agent_id}.json). "
    "Prefer squad / channel_manage / discord_send (plan A). "
    "Bash/curl with tokens is plan B only."
)


def is_discord_bot_token(value: str) -> bool:
    return bool(_DISCORD_BOT_TOKEN_RE.match((value or "").strip()))


def is_discord_credential_domain(domain: str) -> bool:
    return (domain or "").strip().lower().startswith("project.credential.discord")


def prepare_wm_credential_slot(domain: str, fact: str) -> str | None:
    """Return WM slot content, or None to skip storing this credential."""
    domain = (domain or "").strip()
    fact = (fact or "").strip()
    if not domain or not fact:
        return None

    if is_discord_credential_domain(domain):
        if is_discord_bot_token(fact):
            return _WM_DISCORD_CREDENTIAL_GUIDANCE.format(agent_id="<member-agent-id>")
        lowered = fact.lower()
        if "needs fresh token" in lowered or "developer portal" in lowered:
            return None
        if "authorization: bot" in lowered:
            return None
        return _WM_DISCORD_CREDENTIAL_GUIDANCE.format(agent_id="<member-agent-id>")

    if is_discord_bot_token(fact):
        return None

    return fact


def upsert_wm_credential(
    wm: Any,
    *,
    domain: str,
    fact: str,
    source: str = "ans",
    salience: float = 1.0,
) -> bool:
    """Write credential slot with Discord token redaction; return False if skipped."""
    prepared = prepare_wm_credential_slot(domain, fact)
    if prepared is None or wm is None:
        return False
    try:
        wm.upsert_credential(
            domain=domain,
            content=prepared,
            source=source,
            salience=salience,
        )
        return True
    except Exception:
        return False
