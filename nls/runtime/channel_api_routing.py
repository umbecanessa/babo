"""Channel-agnostic detection of raw REST calls vs configured integrations.

Works for bundled channels (Discord, Slack, …) and custom *-channel skills.
Custom skills may declare extra hosts in agent config: ``rest_api_hosts: ["api.example.com"]``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nls.runtime.channel_agent_config import (
    CHANNEL_CREDENTIAL_KEYS,
    agent_channel_is_configured,
    data_root_from_agent_dir,
    load_agent_channel_config,
    resolve_channel_skill_dir,
)
from nls.runtime.channel_policy_profiles import CHANNEL_TO_SKILL

_BUILTIN_REST_PATTERNS: dict[str, tuple[str, ...]] = {
    "discord": (r"discord(?:app)?\.com/api",),
    "slack": (r"slack\.com/api",),
    "telegram": (r"api\.telegram\.org",),
    "whatsapp": (r"graph\.facebook\.com/.+/whatsapp",),
}

_COMPILED_BUILTIN: dict[str, tuple[re.Pattern[str], ...]] = {
    ch: tuple(re.compile(p, re.I) for p in patterns)
    for ch, patterns in _BUILTIN_REST_PATTERNS.items()
}


def channel_key_from_skill_dir(skill_dir: str) -> str:
    """Map skill package folder → channel key (``discord-channel`` → ``discord``)."""
    name = (skill_dir or "").strip().lower()
    if name in CHANNEL_TO_SKILL.values():
        for key, skill in CHANNEL_TO_SKILL.items():
            if skill == name:
                return key
    if name.endswith("-channel"):
        return name[: -len("-channel")]
    return name


def discover_agent_channel_keys(data_root: Path, agent_id: str) -> list[str]:
    """Channel keys with per-agent config on disk (bundled + custom skills)."""
    found: set[str] = set()
    for channel in CHANNEL_TO_SKILL:
        if agent_channel_is_configured(data_root, agent_id, channel):
            found.add(channel)

    agents_glob = data_root / "skills"
    if agents_glob.is_dir():
        for skill_path in sorted(agents_glob.iterdir()):
            if not skill_path.is_dir():
                continue
            cfg_path = skill_path / "agents" / f"{agent_id}.json"
            if not cfg_path.is_file():
                continue
            key = channel_key_from_skill_dir(skill_path.name)
            if key and agent_channel_is_configured(data_root, agent_id, key):
                found.add(key)
    return sorted(found)


def _rest_patterns_for_channel(
    channel: str,
    cfg: dict[str, Any] | None,
) -> tuple[re.Pattern[str], ...]:
    patterns: list[re.Pattern[str]] = list(
        _COMPILED_BUILTIN.get(channel.strip().lower(), ()),
    )
    if not cfg:
        return tuple(patterns)
    extra = cfg.get("rest_api_hosts") or cfg.get("api_hosts") or []
    if isinstance(extra, str):
        extra = [extra]
    if isinstance(extra, (list, tuple)):
        for host in extra:
            text = str(host or "").strip()
            if not text:
                continue
            if text.startswith("re:"):
                try:
                    patterns.append(re.compile(text[3:], re.I))
                except re.error:
                    continue
            else:
                patterns.append(re.compile(re.escape(text), re.I))
    return tuple(patterns)


def command_matches_channel_rest(
    command: str,
    channel: str,
    cfg: dict[str, Any] | None,
) -> bool:
    if not command:
        return False
    return any(p.search(command) for p in _rest_patterns_for_channel(channel, cfg))


def resolve_agent_data_dir(path: str | Path) -> Path | None:
    """Normalize agent data dir from agent dir or ``…/workspace`` cwd."""
    if not path:
        return None
    try:
        resolved = Path(path).resolve()
    except Exception:
        return None
    if resolved.name == "workspace" and resolved.parent.is_dir():
        return resolved.parent
    return resolved


def detect_configured_channel_rest_in_command(
    command: str,
    agent_dir: str,
) -> str | None:
    """Return configured channel key when *command* hits that channel's REST surface."""
    if not command or not agent_dir:
        return None
    try:
        agent_path = resolve_agent_data_dir(agent_dir)
        if agent_path is None:
            return None
        agent_id = agent_path.name
        data_root = data_root_from_agent_dir(agent_path)
    except Exception:
        return None

    for channel in discover_agent_channel_keys(data_root, agent_id):
        cfg = load_agent_channel_config(data_root, agent_id, channel)
        if not command_matches_channel_rest(command, channel, cfg):
            continue
        if agent_channel_is_configured(data_root, agent_id, channel):
            return channel
    return None


def format_channel_rest_bash_hint(channel: str) -> str:
    ch = (channel or "channel").strip().lower()
    return (
        f"[CHANNEL HINT] {ch} is configured — for messages since the bot joined use "
        f"channel_history(action='recent', ...). For pre-connect backfill on Discord/Slack "
        f"use channel_remote(channel='{ch}', action='read', ...). "
        f"Admin: channel_manage(channel='{ch}', action=...). "
        f"Never bash/curl with tokens. "
        f"channel_inspect(action='get', channel='{ch}') lists scoped IDs."
    )


def format_channel_rest_breadcrumb(channel: str) -> str:
    ch = (channel or "channel").strip().lower()
    return (
        f"[BREADCRUMB] Bash hit {ch} REST — prefer channel_history for ambient context "
        f"or channel_remote(action='read'|'send'|'delete', ...) on Discord/Slack. "
        f"channel_manage for admin. channel_inspect(action='get', channel='{ch}') "
        f"has scoped IDs."
    )


def bash_signature_command(sig: str) -> str:
    """Extract shell command from a ``bash:{json}`` tool signature."""
    if not sig or not sig.startswith("bash:"):
        return ""
    raw = sig[5:]
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return str(data.get("command") or "")
    except json.JSONDecodeError:
        pass
    return raw


def matched_channel_rest_in_commands(commands: list[str]) -> str | None:
    """Return bundled channel key when *commands* hit that REST surface 2+ times."""
    if len(commands) < 2:
        return None
    for channel, patterns in _COMPILED_BUILTIN.items():
        hits = sum(
            1 for cmd in commands
            if cmd and any(p.search(cmd) for p in patterns)
        )
        if hits >= 2:
            return channel
    return None


def format_channel_remote_stall_nudge(channel: str) -> str:
    ch = (channel or "channel").strip().lower()
    return (
        f"You have called bash against {ch} REST APIs multiple times. "
        f"Stop using curl/Invoke-RestMethod/python with tokens. "
        f"For messages since the bot joined: channel_history(action='recent', ...). "
        f"For pre-connect backfill (Discord/Slack): "
        f"channel_remote(channel='{ch}', action='read', channel_id=...). "
        f"Send/delete: channel_remote(action='send'|'delete', ...). "
        f"channel_inspect(action='get', channel='{ch}') lists scoped IDs."
    )
