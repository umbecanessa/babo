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


def detect_configured_channel_rest_in_command(
    command: str,
    agent_dir: str,
) -> str | None:
    """Return configured channel key when *command* hits that channel's REST surface."""
    if not command or not agent_dir:
        return None
    try:
        agent_path = Path(agent_dir).resolve()
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
        f"[CHANNEL HINT] {ch} is configured on this agent — prefer "
        f"channel_manage(channel='{ch}', action=...) for server admin "
        f"instead of raw curl/API scripts with tokens. "
        f"channel_inspect(action='get', channel='{ch}') lists scoped IDs. "
        f"Raw REST is fine for one-off probes when channel_manage has no action."
    )


def format_channel_rest_breadcrumb(channel: str) -> str:
    ch = (channel or "channel").strip().lower()
    return (
        f"[BREADCRUMB] You used bash against {ch} REST — prefer "
        f"channel_manage(channel='{ch}', action=...) for admin work. "
        f"channel_inspect(action='get', channel='{ch}') has scoped IDs."
    )
