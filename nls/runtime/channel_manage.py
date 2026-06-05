"""Channel-agnostic admin dispatch — sync, scope, permissions via skill adapters.

Bundled workspace channels (Discord, Slack) implement ``manage_channel`` on their
adapter. Custom channel skills may either implement the same method or call
``SkillContext.register_channel_manage(channel_key, handler)`` at register time.

Never expose credentials to the agent — all actions use server-side saved config.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from nls.runtime.channel_policy_profiles import CHANNEL_TO_SKILL

logger = logging.getLogger(__name__)

ManageHandler = Callable[
    [str, str, dict[str, Any]],
    Awaitable[tuple[bool, str]],
]

_custom_handlers: dict[str, ManageHandler] = {}


def register_channel_manage_handler(
    channel_key: str,
    handler: ManageHandler,
) -> None:
    """Register admin handler for a custom channel (overrides adapter discovery)."""
    _custom_handlers[channel_key.strip().lower()] = handler


def list_manageable_channels() -> list[str]:
    """Channel keys that expose manage_channel (bundled + custom)."""
    keys = set(_custom_handlers.keys())
    try:
        from server.main import app

        sl = getattr(app.state, "skill_loader", None)
        if sl is not None:
            for skill_name, sk in sl.skills.items():
                adapter = getattr(getattr(sk, "context", None), "adapter", None)
                if adapter is not None and hasattr(adapter, "manage_channel"):
                    ch = getattr(adapter, "channel_name", "") or skill_name.replace(
                        "-channel", "",
                    )
                    if ch:
                        keys.add(ch.lower())
    except Exception:
        pass
    return sorted(keys)


def resolve_skill_name(channel: str) -> str:
    ch = (channel or "").strip().lower()
    if not ch:
        return ""
    if ch in CHANNEL_TO_SKILL:
        return CHANNEL_TO_SKILL[ch]
    if ch.endswith("-channel"):
        return ch
    return f"{ch}-channel"


def _load_adapter(skill_name: str) -> Any | None:
    try:
        from server.main import app

        sl = getattr(app.state, "skill_loader", None)
        if sl is None:
            return None
        sk = sl.skills.get(skill_name)
        if sk is None or not sk.context:
            return None
        return getattr(sk.context, "adapter", None)
    except Exception:
        return None


def channel_manage_actions(channel: str) -> list[str]:
    ch = (channel or "").strip().lower()
    if ch in _custom_handlers:
        fn = _custom_handlers[ch]
        meta = getattr(fn, "manage_actions", None)
        if isinstance(meta, (list, tuple)):
            return list(meta)
        return ["sync", "list", "enable", "grant_bot_access", "squad_readiness"]

    skill = resolve_skill_name(ch)
    adapter = _load_adapter(skill)
    if adapter is None:
        return []
    actions_fn = getattr(adapter, "channel_manage_actions", None)
    if callable(actions_fn):
        return list(actions_fn())
    if hasattr(adapter, "manage_channel"):
        return ["sync", "list", "enable"]
    return []


async def dispatch_channel_manage(
    agent_id: str,
    channel: str,
    action: str,
    params: dict[str, Any],
) -> tuple[bool, str]:
    ch = (channel or "").strip().lower()
    act = (action or "").strip().lower()
    if not ch:
        return False, "Error: channel is required (e.g. discord, slack, telegram)."
    if not act:
        return False, "Error: action is required."

    if ch in _custom_handlers:
        return await _custom_handlers[ch](agent_id, act, params)

    skill = resolve_skill_name(ch)
    adapter = _load_adapter(skill)
    if adapter is None:
        return False, (
            f"Error: channel skill '{skill}' is not loaded. "
            "Install/enable the channel skill first."
        )
    manage = getattr(adapter, "manage_channel", None)
    if not callable(manage):
        return False, (
            f"Channel '{ch}' has no admin tooling yet. "
            "Use channel_inspect(action='get') for read-only status. "
            "Custom channel skills: implement adapter.manage_channel or "
            "ctx.register_channel_manage(channel_key, handler) in register()."
        )
    return await manage(agent_id, act, params)


def format_scoped_channel_status(
    channel_label: str,
    status: dict[str, Any],
) -> str:
    """Human-readable scoped channel list (Discord/Slack get_status shape)."""
    bot_label = status.get("bot_username") or status.get("team_name") or "?"
    bot_id = status.get("bot_id") or status.get("team_id") or "?"
    lines = [
        f"{channel_label} bot {bot_label} (id={bot_id})",
        f"  active channels: {status.get('active_channel_count', 0)} / "
        f"{status.get('scoped_channel_count', 0)} scoped",
    ]
    err = str(status.get("sync_error") or "").strip()
    if err:
        lines.append(f"  sync_error: {err}")
    for ch in status.get("channels") or []:
        if not isinstance(ch, dict):
            continue
        name = ch.get("name") or ch.get("id")
        prefix = "#" if channel_label.lower() == "discord" else ""
        flags: list[str] = []
        if ch.get("effective_enabled"):
            flags.append("listening")
        if ch.get("require_mention"):
            flags.append("mention-required")
        if ch.get("platform_access") is False:
            flags.append("no-platform-access")
        lines.append(
            f"  • {prefix}{name} ({ch.get('id')}) — {', '.join(flags) or 'scoped'}",
        )
    return "\n".join(lines)


def format_simple_channel_status(channel_label: str, status: dict[str, Any]) -> str:
    lines = [f"{channel_label}: connected={status.get('connected', False)}"]
    if status.get("bot_username"):
        lines.append(f"  bot: @{status['bot_username']}")
    if status.get("linked_phone"):
        lines.append(f"  phone: {status['linked_phone']}")
    if status.get("enabled") is not None:
        lines.append(f"  enabled: {status['enabled']}")
    return "\n".join(lines)
