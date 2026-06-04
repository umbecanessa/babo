"""Channel scope reconciliation for bundled workspace channels (Discord, Slack, …).

Maintains desired vs observed channel access and compiles PolicyEnforcer ``groups``
config.  Platform changes (invite bot to channel) update observed state; Babo UI /
skill_configure updates desired state; effective routing uses the intersection.
"""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any


def default_scoped_channels() -> dict[str, Any]:
    return {"guilds": {}, "channels": {}}


def scoped_channels_from_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return normalized scoped_channels block from agent config."""
    raw = cfg.get("scoped_channels")
    if not isinstance(raw, dict):
        return default_scoped_channels()
    guilds = raw.get("guilds") if isinstance(raw.get("guilds"), dict) else {}
    channels = raw.get("channels") if isinstance(raw.get("channels"), dict) else {}
    return {"guilds": dict(guilds), "channels": dict(channels)}


def _channel_entry(
    *,
    channel_id: str,
    name: str = "",
    guild_id: str | None = None,
    enabled_desired: bool = False,
    platform_access: bool = False,
    require_mention: bool = True,
    sync_source: str = "",
) -> dict[str, Any]:
    effective = bool(enabled_desired and platform_access)
    return {
        "id": str(channel_id),
        "name": name or str(channel_id),
        "guild_id": guild_id,
        "enabled_desired": bool(enabled_desired),
        "platform_access": bool(platform_access),
        "effective_enabled": effective,
        "require_mention": bool(require_mention),
        "sync_source": sync_source,
        "last_seen_at": time.time(),
    }


def compile_groups_policy(cfg: dict[str, Any]) -> dict[str, Any]:
    """Build PolicyEnforcer ``groups`` map from scoped_channels."""
    scoped = scoped_channels_from_config(cfg)
    groups: dict[str, Any] = {}
    for cid, entry in scoped.get("channels", {}).items():
        if not isinstance(entry, dict):
            continue
        if not entry.get("effective_enabled"):
            continue
        groups[str(cid)] = {
            "name": entry.get("name", cid),
            "require_mention": entry.get("require_mention", True),
            "allow_from": ["*"],
        }
    if not groups:
        # Safe default: no guild/channel listening until explicitly scoped.
        groups = {"__none__": {"require_mention": True, "allow_from": []}}
    return groups


def effective_channel_ids(cfg: dict[str, Any]) -> set[str]:
    scoped = scoped_channels_from_config(cfg)
    out: set[str] = set()
    for cid, entry in scoped.get("channels", {}).items():
        if isinstance(entry, dict) and entry.get("effective_enabled"):
            out.add(str(cid))
    return out


def merge_observed_channels(
    cfg: dict[str, Any],
    observed: list[dict[str, Any]],
    *,
    auto_enable_on_platform_access: bool = True,
) -> dict[str, Any]:
    """Merge platform-observed channels into config (platform → Babo sync).

    *observed* items: ``{id, name, guild_id?, platform_access}``.
    When ``auto_enable_on_platform_access`` is True, newly accessible channels
    are enabled_desired automatically (two-way sync default).
    """
    scoped = scoped_channels_from_config(cfg)
    channels = scoped.setdefault("channels", {})
    guilds = scoped.setdefault("guilds", {})

    for item in observed:
        cid = str(item.get("id") or "")
        if not cid:
            continue
        name = str(item.get("name") or cid)
        guild_id = item.get("guild_id")
        if guild_id:
            guilds[str(guild_id)] = {
                "id": str(guild_id),
                "name": str(item.get("guild_name") or guild_id),
                "last_seen_at": time.time(),
            }
        platform_access = bool(item.get("platform_access", True))
        prev = channels.get(cid) if isinstance(channels.get(cid), dict) else {}
        enabled_desired = bool(prev.get("enabled_desired", False))
        if platform_access and auto_enable_on_platform_access and not prev:
            enabled_desired = True
        elif platform_access and auto_enable_on_platform_access and prev.get("sync_source") == "platform":
            enabled_desired = True
        require_mention = prev.get("require_mention", True)
        channels[cid] = _channel_entry(
            channel_id=cid,
            name=name,
            guild_id=str(guild_id) if guild_id else prev.get("guild_id"),
            enabled_desired=enabled_desired,
            platform_access=platform_access,
            require_mention=bool(require_mention),
            sync_source="platform",
        )

    scoped["channels"] = channels
    scoped["guilds"] = guilds
    return scoped


def apply_desired_channel(
    cfg: dict[str, Any],
    channel_id: str,
    *,
    enabled: bool | None = None,
    require_mention: bool | None = None,
    name: str | None = None,
    guild_id: str | None = None,
) -> dict[str, Any]:
    """Apply Babo-side desired changes (UI / skill_configure → config)."""
    scoped = scoped_channels_from_config(cfg)
    channels = scoped.setdefault("channels", {})
    cid = str(channel_id)
    prev = channels.get(cid) if isinstance(channels.get(cid), dict) else {}
    entry = _channel_entry(
        channel_id=cid,
        name=name or prev.get("name", cid),
        guild_id=guild_id if guild_id is not None else prev.get("guild_id"),
        enabled_desired=enabled if enabled is not None else bool(prev.get("enabled_desired", False)),
        platform_access=bool(prev.get("platform_access", False)),
        require_mention=(
            require_mention if require_mention is not None
            else bool(prev.get("require_mention", True))
        ),
        sync_source="babo",
    )
    channels[cid] = entry
    scoped["channels"] = channels
    return scoped


def finalize_scoped_config(cfg: dict[str, Any], scoped: dict[str, Any]) -> dict[str, Any]:
    """Persist scoped_channels and recompile groups policy."""
    out = deepcopy(cfg)
    out["scoped_channels"] = scoped
    out["groups"] = compile_groups_policy({**out, "scoped_channels": scoped})
    return out


def reconcile_config(
    cfg: dict[str, Any],
    observed: list[dict[str, Any]] | None = None,
    *,
    auto_enable_on_platform_access: bool = True,
) -> dict[str, Any]:
    scoped = scoped_channels_from_config(cfg)
    if observed:
        scoped = merge_observed_channels(
            {**cfg, "scoped_channels": scoped},
            observed,
            auto_enable_on_platform_access=auto_enable_on_platform_access,
        )
    # Recompute effective flags for all entries
    channels = scoped.get("channels", {})
    for cid, entry in list(channels.items()):
        if not isinstance(entry, dict):
            continue
        channels[cid] = _channel_entry(
            channel_id=cid,
            name=entry.get("name", cid),
            guild_id=entry.get("guild_id"),
            enabled_desired=bool(entry.get("enabled_desired", False)),
            platform_access=bool(entry.get("platform_access", False)),
            require_mention=bool(entry.get("require_mention", True)),
            sync_source=entry.get("sync_source", ""),
        )
    scoped["channels"] = channels
    return finalize_scoped_config(cfg, scoped)


def list_scoped_channels(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    scoped = scoped_channels_from_config(cfg)
    return sorted(
        (e for e in scoped.get("channels", {}).values() if isinstance(e, dict)),
        key=lambda e: (e.get("guild_id") or "", e.get("name") or e.get("id", "")),
    )
