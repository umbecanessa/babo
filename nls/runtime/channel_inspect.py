"""Factual channel configuration inspection for agents (on-demand detail)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nls.runtime.channel_agent_config import (
    _CHANNEL_SKILL_DIRS,
    agent_channel_is_configured,
    data_root_from_agent_dir,
    load_agent_channel_config,
)
from nls.runtime.interaction_policy import (
    channel_skill_name,
    summarize_interaction_mode,
)

_KNOWN_CHANNELS = tuple(_CHANNEL_SKILL_DIRS.keys())

_SECRET_KEYS = frozenset({
    "bot_token",
    "signing_secret",
    "app_token",
})


def known_channels() -> tuple[str, ...]:
    return _KNOWN_CHANNELS


def _resolve_adapter(channel: str) -> Any | None:
    skill_dir = _CHANNEL_SKILL_DIRS.get(channel)
    if not skill_dir:
        return None
    try:
        from server.main import app

        sl = getattr(app.state, "skill_loader", None)
        if sl is None:
            return None
        sk = sl.skills.get(skill_dir)
        if sk is None or not sk.context:
            return None
        return getattr(sk.context, "adapter", None)
    except Exception:
        return None


def _skill_loaded(channel: str) -> bool:
    skill_dir = _CHANNEL_SKILL_DIRS.get(channel)
    if not skill_dir:
        return False
    try:
        from server.main import app

        sl = getattr(app.state, "skill_loader", None)
        if sl is None:
            return False
        sk = sl.skills.get(skill_dir)
        return sk is not None and getattr(sk, "status", "") == "loaded"
    except Exception:
        return False


def _gateway_live(adapter: Any | None, agent_id: str) -> bool:
    if adapter is None:
        return False
    connected = getattr(adapter, "_connected_agents", None)
    return isinstance(connected, set) and agent_id in connected


def _mask_config(cfg: dict[str, Any]) -> dict[str, Any]:
    safe = dict(cfg)
    for key in _SECRET_KEYS:
        if safe.get(key):
            safe[key] = "***configured***"
    return safe


def _format_channel_line(
    channel: str,
    *,
    configured: bool,
    gateway_live: bool,
    skill_loaded: bool,
    summary: str = "",
) -> str:
    if not skill_loaded and not configured:
        return f"- {channel}: skill not loaded"
    if not configured:
        return f"- {channel}: NOT CONFIGURED on this agent"
    gate = "gateway LIVE" if gateway_live else "gateway offline (config saved)"
    extra = f" | {summary}" if summary else ""
    return f"- {channel}: CONFIGURED, {gate}{extra}"


def _discord_summary(cfg: dict[str, Any], adapter: Any | None, agent_id: str) -> str:
    parts: list[str] = []
    if adapter is not None:
        aid = agent_id or ""
        username = getattr(adapter, "_bot_usernames", {}).get(aid, "")
        if username:
            parts.append(f"bot @{username}")
    scoped = cfg.get("scoped_channels") or {}
    guilds = scoped.get("guilds") or {}
    if guilds:
        names = [
            str(g.get("name", "")).strip()
            for g in guilds.values()
            if isinstance(g, dict) and g.get("name")
        ]
        if names:
            parts.append(f"guild {names[0]}")
    try:
        from nls.skills.channel_scope import list_scoped_channels

        channels = list_scoped_channels(cfg)
        active = sum(1 for c in channels if c.get("effective_enabled"))
        if channels:
            parts.append(f"{active}/{len(channels)} channels listening")
    except Exception:
        pass
    return "; ".join(parts)


def _slack_summary(cfg: dict[str, Any], adapter: Any | None, agent_id: str) -> str:
    parts: list[str] = []
    if adapter is not None:
        team = getattr(adapter, "_team_names", {}).get(agent_id or "", "")
        if team:
            parts.append(f"workspace {team}")
    try:
        from nls.skills.channel_scope import list_scoped_channels

        channels = list_scoped_channels(cfg)
        active = sum(1 for c in channels if c.get("effective_enabled"))
        if channels:
            parts.append(f"{active}/{len(channels)} channels listening")
    except Exception:
        pass
    return "; ".join(parts)


def _interaction_summary(channel: str, cfg: dict[str, Any]) -> str:
    skill = channel_skill_name(channel)
    if not skill or not cfg:
        return ""
    try:
        return summarize_interaction_mode(skill, cfg)
    except Exception:
        return ""


def _simple_summary(channel: str, cfg: dict[str, Any], adapter: Any | None, agent_id: str) -> str:
    parts: list[str] = []
    if channel == "discord":
        parts.append(_discord_summary(cfg, adapter, agent_id))
    elif channel == "slack":
        parts.append(_slack_summary(cfg, adapter, agent_id))
    elif channel == "telegram":
        uname = ""
        if adapter is not None:
            uname = getattr(adapter, "_bot_usernames", {}).get(agent_id or "", "")
        if uname:
            parts.append(f"bot @{uname}")
    elif channel == "whatsapp":
        phone = str(cfg.get("linked_phone", "")).strip()
        if phone:
            parts.append(f"phone {phone}")
    elif channel == "email":
        alias = str(cfg.get("alias", "") or cfg.get("from_address", "")).strip()
        email = str(cfg.get("connected_email", "")).strip()
        if email:
            parts.append(f"mailbox {email}")
        elif alias:
            parts.append(f"sends from {alias}")

    mode = _interaction_summary(channel, cfg)
    if mode:
        parts.append(mode)
    return "; ".join(p for p in parts if p)


def _format_scoped_channel_rows(
    cfg: dict[str, Any],
    *,
    active_only: bool,
) -> list[str]:
    try:
        from nls.skills.channel_scope import list_scoped_channels
    except Exception:
        return []

    rows: list[str] = []
    for ch in list_scoped_channels(cfg):
        if active_only and not ch.get("effective_enabled"):
            continue
        name = str(ch.get("name") or ch.get("id") or "?")
        cid = str(ch.get("id") or "")
        flags: list[str] = []
        if ch.get("effective_enabled"):
            flags.append("listening")
        else:
            flags.append("not listening")
        if ch.get("require_mention"):
            flags.append("mention-required")
        if ch.get("platform_access") is False:
            flags.append("no-platform-access")
        guild_id = ch.get("guild_id")
        guild_suffix = f" guild={guild_id}" if guild_id else ""
        flag_text = ", ".join(flags) if flags else "scoped"
        rows.append(f"  • #{name} ({cid}){guild_suffix} — {flag_text}")
    return rows


def inspect_all_channels(data_root: Path, agent_id: str) -> str:
    """One-line status for every pre-shipped channel skill."""
    lines = ["Channel integrations (this agent — use channel_inspect get for detail):"]
    for channel in _KNOWN_CHANNELS:
        skill_loaded = _skill_loaded(channel)
        configured = agent_channel_is_configured(data_root, agent_id, channel)
        adapter = _resolve_adapter(channel)
        gateway = _gateway_live(adapter, agent_id)
        cfg = load_agent_channel_config(data_root, agent_id, channel) or {}
        summary = _simple_summary(channel, cfg, adapter, agent_id) if configured else ""
        lines.append(
            _format_channel_line(
                channel,
                configured=configured,
                gateway_live=gateway,
                skill_loaded=skill_loaded,
                summary=summary,
            ),
        )
    return "\n".join(lines)


def inspect_channel(
    data_root: Path,
    agent_id: str,
    channel: str,
    *,
    active_only: bool = False,
) -> str:
    """Detailed, non-secret configuration for one channel."""
    channel = (channel or "").strip().lower()
    if channel not in _KNOWN_CHANNELS:
        known = ", ".join(_KNOWN_CHANNELS)
        return f"Unknown channel '{channel}'. Supported: {known}"

    configured = agent_channel_is_configured(data_root, agent_id, channel)
    if not _skill_loaded(channel) and not configured:
        return f"{channel}: skill not loaded on this runtime and no per-agent config saved."
    cfg = load_agent_channel_config(data_root, agent_id, channel)
    adapter = _resolve_adapter(channel)
    gateway = _gateway_live(adapter, agent_id)

    lines: list[str] = [f"{channel.upper()} channel (this agent):"]
    lines.append(f"  configured: {'yes' if configured else 'no'}")
    lines.append(f"  gateway: {'live' if gateway else 'offline'}")

    if not configured or not cfg:
        lines.append(
            "  No per-agent credentials on this agent. "
            "Link the channel in Dashboard → Tools for this agent before expecting inbound traffic."
        )
        return "\n".join(lines)

    safe = _mask_config(cfg)
    if channel in ("discord", "slack"):
        if safe.get("bot_token"):
            lines.append("  credentials: bot token saved (masked)")
        if safe.get("enabled") is not None:
            lines.append(f"  enabled: {safe.get('enabled')}")
        owner = safe.get("owner_identity")
        if owner:
            lines.append(f"  owner_identity: {owner}")
        if safe.get("dm_policy"):
            lines.append(f"  dm_policy: {safe.get('dm_policy')}")
        mode_line = _interaction_summary(channel, cfg)
        if mode_line:
            lines.append(f"  interaction: {mode_line}")
        if safe.get("bot_username"):
            lines.append(f"  bot_username: {safe.get('bot_username')}")
        if safe.get("bot_id"):
            lines.append(f"  bot_id: {safe.get('bot_id')}")
        if adapter is not None and not safe.get("bot_username"):
            try:
                status = adapter.get_status(agent_id)
            except Exception:
                status = {}
            else:
                if status.get("bot_username"):
                    lines.append(f"  bot_username: {status.get('bot_username')}")
                if status.get("bot_id") and not safe.get("bot_id"):
                    lines.append(f"  bot_id: {status.get('bot_id')}")
                if channel == "slack" and status.get("team_name"):
                    lines.append(f"  team_name: {status.get('team_name')}")
        scoped = cfg.get("scoped_channels") or {}
        guilds = scoped.get("guilds") or {}
        if guilds:
            lines.append("  guilds / workspaces:")
            for gid, g in guilds.items():
                if isinstance(g, dict):
                    gname = g.get("name") or gid
                    lines.append(f"    • {gname} ({gid})")
        ch_rows = _format_scoped_channel_rows(cfg, active_only=active_only)
        if ch_rows:
            heading = "  active listening channels:" if active_only else "  scoped channels:"
            lines.append(heading)
            lines.extend(ch_rows)
        elif channel in ("discord", "slack"):
            lines.append("  scoped channels: none synced yet (open Tools → channel scope or call sync API)")
            if adapter is not None:
                try:
                    status = adapter.get_status(agent_id)
                    sync_err = str(status.get("sync_error") or "").strip()
                    active = int(status.get("active_channel_count") or 0)
                    if sync_err:
                        lines.append(f"  sync_error: {sync_err}")
                    if active:
                        lines.append(f"  active listening: {active} channel(s)")
                except Exception:
                    pass
    elif channel == "telegram":
        if safe.get("bot_token"):
            lines.append("  credentials: bot token saved (masked)")
        if adapter is not None:
            uname = getattr(adapter, "_bot_usernames", {}).get(agent_id, "")
            if uname:
                lines.append(f"  bot_username: @{uname}")
        if safe.get("owner_identity"):
            lines.append(f"  owner_identity: {safe.get('owner_identity')}")
        if safe.get("dm_policy"):
            lines.append(f"  dm_policy: {safe.get('dm_policy')}")
        mode_line = _interaction_summary(channel, cfg)
        if mode_line:
            lines.append(f"  interaction: {mode_line}")
    elif channel == "whatsapp":
        if safe.get("linked_phone"):
            lines.append(f"  linked_phone: {safe.get('linked_phone')}")
        if safe.get("owner_identity"):
            lines.append(f"  owner_identity: {safe.get('owner_identity')}")
        if safe.get("dm_policy"):
            lines.append(f"  dm_policy: {safe.get('dm_policy')}")
        mode_line = _interaction_summary(channel, cfg)
        if mode_line:
            lines.append(f"  interaction: {mode_line}")
    elif channel == "email":
        if safe.get("connected_email"):
            lines.append(f"  connected_email: {safe.get('connected_email')}")
        if safe.get("alias"):
            lines.append(f"  alias: {safe.get('alias')}")
        if safe.get("from_address"):
            lines.append(f"  from_address: {safe.get('from_address')}")
        if safe.get("owner_identity"):
            lines.append(f"  owner_identity: {safe.get('owner_identity')}")
        if safe.get("dm_policy"):
            lines.append(f"  dm_policy: {safe.get('dm_policy')}")
        if safe.get("thread_policy"):
            lines.append(f"  thread_policy: {safe.get('thread_policy')}")
        mode_line = _interaction_summary(channel, cfg)
        if mode_line:
            lines.append(f"  interaction: {mode_line}")

    lines.append(
        "  Use this data for squad routing and job design — do not ask the owner "
        "for bot tokens or channel names already listed here."
    )
    return "\n".join(lines)


def resolve_data_root(agent_id: str, agent_dir: Path | None = None) -> Path | None:
    if agent_dir is not None:
        return data_root_from_agent_dir(agent_dir)
    try:
        from server.main import app

        am = getattr(app.state, "agent_manager", None)
        if am is not None:
            return Path(am.agents_dir).parent
    except Exception:
        pass
    return None
