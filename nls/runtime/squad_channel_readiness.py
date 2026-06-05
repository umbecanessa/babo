"""Multi-platform squad channel readiness — Discord, Telegram, Slack."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from nls.runtime.channel_agent_config import load_agent_channel_config
from nls.runtime.channel_inspect import resolve_data_root
from nls.runtime.discord_squad_readiness import (
    ChannelFaceStatus,
    audit_squad_discord_channel,
)
from nls.skills.channel_scope import scoped_channels_from_config

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
SLACK_API = "https://slack.com/api"

_DISCORD_ID_RE = re.compile(r"^\d{17,20}$")
_SLACK_CHAN_RE = re.compile(r"^[CG][A-Z0-9]{8,}$")
_TELEGRAM_CHAT_RE = re.compile(r"^-?\d+$")


def resolve_channel_platform(
    channel_id: str,
    *,
    channel: str = "",
) -> str:
    """Infer platform from explicit channel name or channel_id shape."""
    explicit = (channel or "").strip().lower()
    if explicit in ("discord", "telegram", "slack"):
        return explicit
    cid = str(channel_id or "").strip()
    if _SLACK_CHAN_RE.match(cid):
        return "slack"
    if _DISCORD_ID_RE.match(cid):
        return "discord"
    if _TELEGRAM_CHAT_RE.match(cid):
        return "telegram"
    return "discord"


async def audit_squad_channel(
    lead_agent_id: str,
    channel_id: str,
    *,
    platform: str = "",
) -> tuple[list[ChannelFaceStatus], str, dict[str, Any]]:
    """Audit which squad bots can access/listen on a shared coordination surface."""
    plat = resolve_channel_platform(channel_id, channel=platform)
    if plat == "telegram":
        return await audit_squad_telegram_chat(lead_agent_id, channel_id)
    if plat == "slack":
        return await audit_squad_slack_channel(lead_agent_id, channel_id)
    return await audit_squad_discord_channel(lead_agent_id, channel_id)


async def probe_telegram_chat(token: str, chat_id: str) -> bool | None:
    token = (token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not token or not chat_id or "masked" in token:
        return None
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                f"{TELEGRAM_API}/bot{token}/getChat",
                params={"chat_id": chat_id},
            )
            data = resp.json()
            if data.get("ok"):
                return True
            desc = str(data.get("description", "")).lower()
            if "chat not found" in desc or "forbidden" in desc or "not enough rights" in desc:
                return False
            return False
    except Exception as exc:
        logger.debug("Telegram getChat probe failed: %s", exc)
        return None


async def probe_slack_channel(token: str, channel_id: str) -> bool | None:
    token = (token or "").strip()
    channel_id = str(channel_id or "").strip()
    if not token or not channel_id or "masked" in token:
        return None
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                f"{SLACK_API}/conversations.info",
                headers={"Authorization": f"Bearer {token}"},
                params={"channel": channel_id},
            )
            data = resp.json()
            if data.get("ok"):
                return True
            err = str(data.get("error", "")).lower()
            if err in ("channel_not_found", "not_in_channel", "missing_scope", "invalid_auth"):
                return False
            return None
    except Exception as exc:
        logger.debug("Slack conversations.info probe failed: %s", exc)
        return None


async def _telegram_bot_meta(token: str) -> tuple[str, str]:
    token = (token or "").strip()
    if not token or "masked" in token:
        return "", ""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{TELEGRAM_API}/bot{token}/getMe")
            data = resp.json()
            if data.get("ok"):
                r = data.get("result") or {}
                return str(r.get("username") or ""), str(r.get("id") or "")
    except Exception:
        pass
    return "", ""


async def _slack_bot_meta(token: str) -> tuple[str, str]:
    token = (token or "").strip()
    if not token or "masked" in token:
        return "", ""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{SLACK_API}/auth.test",
                headers={"Authorization": f"Bearer {token}"},
            )
            data = resp.json()
            if data.get("ok"):
                return str(data.get("user") or ""), str(data.get("user_id") or "")
    except Exception:
        pass
    return "", ""


def _load_channel_cfg(agent_id: str, platform: str) -> dict[str, Any] | None:
    data_root = resolve_data_root(agent_id)
    if data_root is None:
        return None
    skill = f"{platform}-channel"
    return load_agent_channel_config(data_root, agent_id, platform)


def _telegram_listening(cfg: dict[str, Any] | None, chat_id: str) -> bool:
    if not cfg or not cfg.get("enabled"):
        return False
    groups = cfg.get("groups") or {}
    if str(chat_id) in groups:
        return True
    return bool(groups.get("*"))


def _slack_channel_entry(cfg: dict[str, Any] | None, channel_id: str) -> dict[str, Any] | None:
    if not cfg:
        return None
    entry = (scoped_channels_from_config(cfg).get("channels") or {}).get(channel_id)
    return entry if isinstance(entry, dict) else None


def _issue_telegram(
    *,
    configured: bool,
    api_can_view: bool | None,
    listening: bool,
    bot_username: str,
) -> str:
    if not configured:
        return (
            "Telegram not configured — squad(action='configure_member', skill='telegram-channel', ...)."
        )
    if api_can_view is False:
        label = f"@{bot_username}" if bot_username else "This bot"
        return (
            f"{label} cannot access this chat — add the bot to the group/supergroup and "
            "ensure it can read messages, then squad(action='sync_member_channels', ...)."
        )
    if api_can_view is None and configured:
        return "Could not verify Telegram chat access (token or API issue)."
    if not listening:
        return (
            "Bot can view chat but group policy is not enabled for this chat_id — "
            "sync scope via configure_member / sync_member_channels."
        )
    return "OK — listening"


def _issue_slack(
    *,
    configured: bool,
    api_can_view: bool | None,
    scoped: bool,
    listening: bool,
    platform_access: bool | None,
    bot_username: str,
) -> str:
    if not configured:
        return (
            "Slack not configured — squad(action='configure_member', skill='slack-channel', ...)."
        )
    if api_can_view is False:
        label = bot_username or "This bot"
        return (
            f"{label} cannot access channel — invite the app to the channel in Slack, "
            "then squad(action='sync_member_channels', target_agent_id=...)."
        )
    if api_can_view is None and configured:
        return "Could not verify Slack channel access (token or missing scopes)."
    if not scoped:
        return (
            "Channel not in this bot's synced scope — run "
            "squad(action='sync_member_channels', target_agent_id=...)."
        )
    if platform_access is False:
        return "Scoped but no platform access — check Slack channel membership."
    if not listening:
        return "Scoped but not listening — enable in Tools → Slack or sync scope."
    return "OK — listening"


def _build_generic_playbook(
    faces: list[ChannelFaceStatus],
    channel_id: str,
    *,
    platform: str,
) -> dict[str, Any]:
    next_steps: list[dict[str, Any]] = []
    for face in faces:
        if face.role == "lead" or face.issue == "OK — listening":
            continue
        skill = f"{platform}-channel"
        if not face.configured:
            next_steps.append({
                "order": len(next_steps) + 1,
                "kind": "configure_member",
                "tool": "squad",
                "action": "configure_member",
                "params": {
                    "target_agent_id": face.agent_id,
                    "skill": skill,
                    "owner_confirmed": True,
                },
                "reason": f"{platform.title()} skill not configured on member agent.",
            })
            continue
        if face.api_can_view is False:
            next_steps.append({
                "order": len(next_steps) + 1,
                "kind": "invite_to_channel",
                "tool": "squad",
                "action": "sync_member_channels",
                "params": {"target_agent_id": face.agent_id, "channel": platform},
                "reason": face.issue,
            })
        elif face.api_can_view is True and not face.scoped:
            next_steps.append({
                "order": len(next_steps) + 1,
                "kind": "sync_scope",
                "tool": "squad",
                "action": "sync_member_channels",
                "params": {"target_agent_id": face.agent_id, "channel": platform},
                "reason": "Sync channel scope after platform access is confirmed.",
            })

    blocked = [f for f in faces if f.issue != "OK — listening"]
    return {
        "platform": platform,
        "channel_id": channel_id,
        "all_ready": len(blocked) == 0,
        "oauth_invites": [],
        "next_steps": next_steps,
    }


def _playbook_summary(playbook: dict[str, Any]) -> str:
    if playbook.get("all_ready"):
        return "All squad bots are ready on this channel."
    steps = playbook.get("next_steps") or []
    if not steps:
        return "Some bots are not ready — see face issues above."
    lines = ["Suggested next steps:"]
    for step in steps[:8]:
        lines.append(f"  {step.get('order', '?')}. [{step.get('kind', '?')}] {step.get('reason', '')}")
    return "\n".join(lines)


async def audit_squad_telegram_chat(
    lead_agent_id: str,
    chat_id: str,
) -> tuple[list[ChannelFaceStatus], str, dict[str, Any]]:
    from server.main import app

    sm = getattr(app.state, "squad_manager", None)
    if sm is None:
        raise RuntimeError("Squad manager not available")
    squad = sm.get_squad_for_agent(lead_agent_id)
    if squad is None or not squad.is_lead(lead_agent_id):
        raise PermissionError("Only the squad lead may audit squad channel access")

    chat_id = str(chat_id or "").strip()
    if not chat_id:
        raise ValueError("channel_id (Telegram chat_id) is required")

    chat_label = chat_id
    faces: list[ChannelFaceStatus] = []
    for aid in squad.all_member_ids:
        meta = _agent_meta(sm, aid, squad)
        cfg = _load_channel_cfg(aid, "telegram")
        token = str((cfg or {}).get("bot_token") or "").strip()
        if aid == squad.lead_agent_id and not meta["bot_username"]:
            un, bid = await _telegram_bot_meta(token)
            meta["bot_username"] = un
            meta["bot_id"] = bid
        elif not meta["bot_username"] and token:
            un, bid = await _telegram_bot_meta(token)
            meta["bot_username"] = un
            meta["bot_id"] = bid

        api_view = await probe_telegram_chat(token, chat_id) if cfg else None
        configured = bool(cfg and token and cfg.get("enabled"))
        listening = _telegram_listening(cfg, chat_id) and api_view is not False
        face = ChannelFaceStatus(
            agent_id=aid,
            name=meta["name"],
            role=meta["role"],
            bot_username=meta["bot_username"],
            bot_id=meta["bot_id"],
            configured=configured,
            api_can_view=api_view,
            scoped=listening,
            listening=listening,
            platform_access=api_view,
        )
        face.issue = _issue_telegram(
            configured=face.configured,
            api_can_view=face.api_can_view,
            listening=listening,
            bot_username=face.bot_username,
        )
        faces.append(face)

    playbook = _build_generic_playbook(faces, chat_id, platform="telegram")
    lines = [f"Squad channel readiness — Telegram chat {chat_label}:"]
    for f in faces:
        bot = f"@{f.bot_username}" if f.bot_username else "(no bot)"
        lines.append(f"  • {f.name} [{f.role}] {bot}: {f.issue}")
    summary = _playbook_summary(playbook)
    if summary:
        lines.extend(["", summary])
    return faces, "\n".join(lines), playbook


async def audit_squad_slack_channel(
    lead_agent_id: str,
    channel_id: str,
) -> tuple[list[ChannelFaceStatus], str, dict[str, Any]]:
    from server.main import app

    sm = getattr(app.state, "squad_manager", None)
    if sm is None:
        raise RuntimeError("Squad manager not available")
    squad = sm.get_squad_for_agent(lead_agent_id)
    if squad is None or not squad.is_lead(lead_agent_id):
        raise PermissionError("Only the squad lead may audit squad channel access")

    channel_id = str(channel_id or "").strip()
    if not channel_id:
        raise ValueError("channel_id (Slack channel ID) is required")

    channel_name = channel_id
    lead_cfg = _load_channel_cfg(lead_agent_id, "slack")
    lead_entry = _slack_channel_entry(lead_cfg, channel_id)
    if lead_entry and lead_entry.get("name"):
        channel_name = str(lead_entry["name"])

    faces: list[ChannelFaceStatus] = []
    for aid in squad.all_member_ids:
        meta = _agent_meta(sm, aid, squad)
        cfg = _load_channel_cfg(aid, "slack")
        entry = _slack_channel_entry(cfg, channel_id)
        token = str((cfg or {}).get("bot_token") or "").strip()
        if token and not meta["bot_username"]:
            un, bid = await _slack_bot_meta(token)
            meta["bot_username"] = un
            meta["bot_id"] = bid

        api_view = await probe_slack_channel(token, channel_id) if cfg else None
        configured = bool(cfg and token and cfg.get("enabled"))
        scoped = entry is not None
        listening = bool(entry and entry.get("effective_enabled"))
        platform_access = entry.get("platform_access") if entry else None

        face = ChannelFaceStatus(
            agent_id=aid,
            name=meta["name"],
            role=meta["role"],
            bot_username=meta["bot_username"],
            bot_id=meta["bot_id"],
            configured=configured,
            api_can_view=api_view,
            scoped=scoped,
            listening=listening,
            platform_access=platform_access if isinstance(platform_access, bool) else None,
        )
        face.issue = _issue_slack(
            configured=face.configured,
            api_can_view=face.api_can_view,
            scoped=scoped,
            listening=listening,
            platform_access=face.platform_access,
            bot_username=face.bot_username,
        )
        faces.append(face)

    playbook = _build_generic_playbook(faces, channel_id, platform="slack")
    lines = [f"Squad channel readiness — Slack #{channel_name} ({channel_id}):"]
    for f in faces:
        bot = f"@{f.bot_username}" if f.bot_username else "(no bot)"
        lines.append(f"  • {f.name} [{f.role}] {bot}: {f.issue}")
    summary = _playbook_summary(playbook)
    if summary:
        lines.extend(["", summary])
    return faces, "\n".join(lines), playbook


def _agent_meta(sm: Any, agent_id: str, squad: Any) -> dict[str, str]:
    name = sm._agent_display_name(agent_id) or agent_id
    if hasattr(sm, "_discord_bot_meta"):
        discord = sm._discord_bot_meta(agent_id)
    else:
        discord = {}
    return {
        "name": name,
        "role": "lead" if squad.is_lead(agent_id) else "member",
        "bot_username": discord.get("bot_username", ""),
        "bot_id": discord.get("bot_id", ""),
    }
