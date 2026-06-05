"""Discord channel ops — squad/channel tools are plan A; bash/tokens are plan B."""

from __future__ import annotations

import json
import re
from typing import Any

DISCORD_ADMIN_TOOL_NAMES = frozenset({
    "channel_manage",
    "channel_inspect",
    "squad",
    "discord_send",
    "discord_setup",
    "configure_member",
})

_ESCALATION_TOOL_NAMES = frozenset({"bash", "write", "edit"})

_AUTH_BOT_MARKERS = (
    "authorization: bot",
    "authorization bot",
    "'authorization': 'bot",
    '"authorization": "bot',
)

_TOKEN_FRAGMENT_RE = re.compile(
    r"[MN][A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}",
)

DISCORD_PLAN_A_PIPELINE = (
    "squad(action='check_channel_readiness', channel_id=...) "
    "→ follow next_steps / oauth_invite_url in JSON "
    "→ squad(action='invite_squad_bots', channel_id=...) "
    "→ squad(action='sync_member_channels', target_agent_id=..., channel='discord') "
    "→ discord_send(channel_id=..., text='<@member_bot_id> ...')"
)


def agent_has_discord_admin_tools(tools: dict[str, Any]) -> bool:
    return bool(DISCORD_ADMIN_TOOL_NAMES.intersection(tools.keys()))


def _text_from_tool_args(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name == "bash":
        return str(args.get("command") or "")
    if tool_name == "write":
        return str(args.get("content") or "")
    if tool_name == "edit":
        return str(args.get("new_string") or args.get("content") or "")
    try:
        return json.dumps(args, default=str)
    except Exception:
        return str(args)


def _contains_discord_bot_token(raw: str) -> bool:
    from nls.runtime.channel_credential_policy import is_discord_bot_token

    text = (raw or "").strip()
    if not text:
        return False
    if is_discord_bot_token(text):
        return True
    for match in _TOKEN_FRAGMENT_RE.finditer(text):
        if is_discord_bot_token(match.group(0)):
            return True
    return False


def uses_discord_bot_credential_escalation(tool_name: str, args: dict[str, Any]) -> bool:
    """Detect plan-B paths (bash/write/edit with bot creds) for telemetry/tests."""
    if tool_name not in _ESCALATION_TOOL_NAMES:
        return False
    raw = _text_from_tool_args(tool_name, args)
    text = raw.lower()
    if any(m in text for m in _AUTH_BOT_MARKERS):
        return True
    return _contains_discord_bot_token(raw)


uses_discord_api_outside_channel_tools = uses_discord_bot_credential_escalation


def discord_channel_primary_guidance(
    unlocked_tools: set[str] | frozenset[str] | None = None,
) -> str | None:
    """Plan-A guidance when Discord admin tools are registered — no blocking."""
    unlocked = set(unlocked_tools or ())
    if not DISCORD_ADMIN_TOOL_NAMES.intersection(unlocked):
        return None
    return (
        "Discord channel ops — plan A (preferred): "
        f"{DISCORD_PLAN_A_PIPELINE}. "
        "Tokens stay server-side in skill config; tool JSON includes next_steps.\n"
        "Plan B (fallback only if plan A cannot cover the case): bash/curl or WM "
        "credentials — useful for edge debugging but lower priority and tokens may "
        "appear in logs."
    )


def skill_discovery_prompt(unlocked_tools: set[str] | frozenset[str] | None = None) -> str:
    """Stall recovery — surface plan A only; do not advertise plan B."""
    unlocked = set(unlocked_tools or ())
    lines = [
        "SKILL DISCOVERY — you appear stuck. Try these before retrying the same command:",
        "1. clawhub(action='search', query='<keyword>') — find community skills",
        "2. discover_tools(query='<keyword>') — find deferred tools",
        "3. clawhub(action='install', slug='...') then follow skill instructions",
    ]
    if DISCORD_ADMIN_TOOL_NAMES.intersection(unlocked):
        lines.append(
            f"4. Discord multi-face (plan A): {DISCORD_PLAN_A_PIPELINE}"
        )
    else:
        lines.append(
            "4. wm(action='borrow', domain='Project.Credential.*') for non-Discord auth gaps"
        )
    lines.append("Do NOT loop on the same failing bash command.")
    return "\n".join(lines)
