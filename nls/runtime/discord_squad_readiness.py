"""Squad Discord channel access audit — which bots can see / listen in a channel."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from nls.runtime.channel_agent_config import load_agent_channel_config
from nls.runtime.channel_inspect import resolve_data_root
from nls.skills.channel_scope import scoped_channels_from_config

logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"


@dataclass
class ChannelFaceStatus:
    agent_id: str
    name: str
    role: str
    bot_username: str
    bot_id: str
    configured: bool
    api_can_view: bool | None
    scoped: bool
    listening: bool
    platform_access: bool | None
    in_guild: bool | None = None
    issue: str = ""


async def probe_bot_channel_view(token: str, channel_id: str) -> bool | None:
    """True if Discord API lets this bot read the channel (GET /channels/{id})."""
    token = (token or "").strip()
    if not token or "masked" in token:
        return None
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                f"{DISCORD_API}/channels/{channel_id}",
                headers={"Authorization": f"Bot {token}"},
            )
            if resp.status_code == 200:
                return True
            if resp.status_code in (403, 404):
                return False
            logger.debug("Discord channel probe HTTP %s for %s", resp.status_code, channel_id)
            return False
    except Exception as exc:
        logger.debug("Discord channel probe failed: %s", exc)
        return None


def _channel_entry(cfg: dict[str, Any] | None, channel_id: str) -> dict[str, Any] | None:
    if not cfg:
        return None
    entry = (scoped_channels_from_config(cfg).get("channels") or {}).get(channel_id)
    return entry if isinstance(entry, dict) else None


def _issue_for_face(
    *,
    configured: bool,
    api_can_view: bool | None,
    scoped: bool,
    listening: bool,
    platform_access: bool | None,
    bot_username: str,
    in_guild: bool | None = None,
) -> str:
    if not configured:
        return "Discord not configured on this agent — squad(action='configure_member', ...)."
    if in_guild is False:
        label = f"@{bot_username}" if bot_username else "This bot"
        return (
            f"{label} is not in this Discord server — owner must open oauth_invite_url "
            "from invite_squad_bots / check_channel_readiness next_steps."
        )
    if api_can_view is False:
        label = f"@{bot_username}" if bot_username else "This bot"
        return (
            f"{label} cannot view this channel — run "
            "squad(action='invite_squad_bots', channel_id=...) or "
            "channel_manage(action='grant_bot_access', channel_id=..., bot_user_id=...)."
        )
    if api_can_view is None and configured:
        label = f"@{bot_username}" if bot_username else "This bot"
        return (
            f"{label} — could not verify channel access (token or Discord API issue). "
            "Re-save bot_token via configure_member if needed."
        )
    if not scoped:
        return (
            "Channel not in this bot's synced scope — after the bot can view the channel, "
            "run squad(action='sync_member_channels', target_agent_id=...)."
        )
    if platform_access is False:
        return "Scoped but no platform access — check Discord channel permissions."
    if not listening:
        return "Scoped but not listening — enable in Tools → Discord or sync scope."
    return "OK — listening"


async def audit_squad_discord_channel(
    lead_agent_id: str,
    channel_id: str,
) -> tuple[list[ChannelFaceStatus], str, dict[str, Any]]:
    from server.main import app
    from nls.runtime.discord_squad_playbook import (
        build_playbook,
        fetch_channel_guild_id,
        playbook_summary,
        probe_bot_in_guild,
    )

    sm = getattr(app.state, "squad_manager", None)
    if sm is None:
        raise RuntimeError("Squad manager not available")

    squad = sm.get_squad_for_agent(lead_agent_id)
    if squad is None or not squad.is_lead(lead_agent_id):
        raise PermissionError("Only the squad lead may audit squad channel access")

    channel_id = str(channel_id or "").strip()
    if not channel_id:
        raise ValueError("channel_id is required")

    channel_name = channel_id
    lead_cfg = _load_discord_cfg(lead_agent_id)
    lead_entry = _channel_entry(lead_cfg, channel_id)
    if lead_entry and lead_entry.get("name"):
        channel_name = str(lead_entry["name"])

    lead_token = str((lead_cfg or {}).get("bot_token") or "").strip()
    guild_id = await fetch_channel_guild_id(lead_token, channel_id)

    faces: list[ChannelFaceStatus] = []
    in_guild_by_agent: dict[str, bool | None] = {}
    for aid in squad.all_member_ids:
        meta = _agent_meta(sm, aid, squad)
        cfg = _load_discord_cfg(aid)
        entry = _channel_entry(cfg, channel_id)
        token = str((cfg or {}).get("bot_token") or "").strip()
        api_view = await probe_bot_channel_view(token, channel_id) if cfg else None

        in_guild: bool | None = None
        if guild_id and token and meta["role"] != "lead":
            in_guild = await probe_bot_in_guild(token, guild_id)
        in_guild_by_agent[aid] = in_guild

        configured = bool(cfg and token)
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
            in_guild=in_guild,
        )
        face.issue = _issue_for_face(
            configured=face.configured,
            api_can_view=face.api_can_view,
            scoped=face.scoped,
            listening=face.listening,
            platform_access=face.platform_access,
            bot_username=face.bot_username,
            in_guild=in_guild,
        )
        faces.append(face)

    playbook = build_playbook(
        faces, channel_id, guild_id=guild_id, in_guild_by_agent=in_guild_by_agent,
    )

    lines = [f"Squad channel readiness — #{channel_name} ({channel_id}):"]
    blocked = [f for f in faces if f.issue != "OK — listening"]
    for f in faces:
        bot = f"@{f.bot_username}" if f.bot_username else "(no bot)"
        lines.append(f"  • {f.name} [{f.role}] {bot}: {f.issue}")
    summary = playbook_summary(playbook)
    if summary:
        lines.append("")
        lines.append(summary)

    return faces, "\n".join(lines), playbook


async def invite_squad_bots_to_channel(
    lead_agent_id: str,
    channel_id: str,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Grant squad member bots channel access via lead bot, then sync member scope."""
    from server.main import app
    from nls.runtime.discord_squad_playbook import (
        build_playbook,
        oauth_invite_url,
        playbook_summary,
    )

    sm = getattr(app.state, "squad_manager", None)
    if sm is None:
        raise RuntimeError("Squad manager not available")
    squad = sm.get_squad_for_agent(lead_agent_id)
    if squad is None or not squad.is_lead(lead_agent_id):
        raise PermissionError("Only the squad lead may invite squad bots to a channel")

    sl = app.state.skill_loader
    sk = sl.skills.get("discord-channel")
    adapter = getattr(sk.context, "adapter", None) if sk and sk.context else None
    if adapter is None:
        raise RuntimeError("Discord skill not loaded")

    channel_id = str(channel_id or "").strip()
    if not channel_id:
        raise ValueError("channel_id is required")

    faces, before, playbook_before = await audit_squad_discord_channel(
        lead_agent_id, channel_id,
    )
    results: list[dict[str, Any]] = []

    for face in faces:
        if face.role == "lead" or face.issue == "OK — listening":
            continue
        if not face.bot_id:
            results.append({
                "agent_id": face.agent_id,
                "name": face.name,
                "ok": False,
                "message": "No discord bot_id on this agent",
            })
            continue

        if face.in_guild is False:
            url = oauth_invite_url(face.bot_id)
            results.append({
                "agent_id": face.agent_id,
                "name": face.name,
                "bot_id": face.bot_id,
                "ok": False,
                "oauth_invite_url": url,
                "message": (
                    "Bot not in guild — owner must open oauth_invite_url, then re-run "
                    "invite_squad_bots."
                ),
            })
            continue

        ok, msg = await adapter.grant_channel_member_access(
            lead_agent_id, channel_id, face.bot_id, grant=True,
        )
        sync_note = ""
        oauth_url = ""
        if not ok and "not a member of this guild" in (msg or "").lower():
            oauth_url = oauth_invite_url(face.bot_id)
        if ok:
            from nls.runtime.skill_config_service import finalize_discord_member_channels

            sync_note = await finalize_discord_member_channels(
                face.agent_id,
                lead_agent_id=lead_agent_id,
            ) or ""
        entry: dict[str, Any] = {
            "agent_id": face.agent_id,
            "name": face.name,
            "bot_id": face.bot_id,
            "ok": ok,
            "message": msg or sync_note or "access granted",
        }
        if oauth_url:
            entry["oauth_invite_url"] = oauth_url
        results.append(entry)

    _, after, playbook_after = await audit_squad_discord_channel(
        lead_agent_id, channel_id,
    )
    lines = [f"Invite squad bots to channel {channel_id}:", before, "", "Actions taken:"]
    if not results:
        lines.append("  • (none — all squad bots already OK or not configured)")
    for r in results:
        status = "OK" if r["ok"] else "FAILED"
        line = f"  • {r['name']}: {status} — {r.get('message', '')}"
        if r.get("oauth_invite_url"):
            line += f"\n    oauth_invite_url: {r['oauth_invite_url']}"
        lines.append(line)
    lines.append("")
    lines.append("After:")
    lines.append(after.split("\n", 1)[-1] if "\n" in after else after)
    lines.append("")
    lines.append(playbook_summary(playbook_after))
    return results, "\n".join(lines), playbook_after


def _load_discord_cfg(agent_id: str) -> dict[str, Any] | None:
    data_root = resolve_data_root(agent_id)
    if data_root is None:
        return None
    return load_agent_channel_config(data_root, agent_id, "discord")


def _agent_meta(sm: Any, agent_id: str, squad: Any) -> dict[str, str]:
    name = sm._agent_display_name(agent_id) or agent_id
    discord = sm._discord_bot_meta(agent_id)
    return {
        "name": name,
        "role": "lead" if squad.is_lead(agent_id) else "member",
        "bot_username": discord.get("bot_username", ""),
        "bot_id": discord.get("bot_id", ""),
    }
