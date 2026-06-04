"""Per-agent channel skill config — shared source of truth for runtime, tools, and UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nls.runtime.channel_policy_profiles import channel_skill_dirs

_CHANNEL_SKILL_DIRS: dict[str, str] = channel_skill_dirs()

CHANNEL_CREDENTIAL_KEYS = frozenset({
    "bot_token",
    "signing_secret",
    "app_token",
    "linked_phone",
    "connected_email",
})


def merge_global_and_agent_channel_config(
    global_cfg: dict[str, Any],
    agent_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Merge skill config without leaking global credentials to other agents."""
    base = {
        k: v for k, v in global_cfg.items()
        if k not in CHANNEL_CREDENTIAL_KEYS
    }
    merged = dict(base)
    merged.update(agent_cfg)
    return merged


def data_root_from_agent_dir(agent_dir: str | Path) -> Path:
    return Path(agent_dir).parent.parent


def load_agent_channel_config(
    data_root: Path,
    agent_id: str,
    channel: str,
) -> dict[str, Any] | None:
    """Load per-agent channel config only (never global config.json credentials)."""
    skill_dir = _CHANNEL_SKILL_DIRS.get(channel)
    if not skill_dir:
        return None
    path = data_root / "skills" / skill_dir / "agents" / f"{agent_id}.json"
    if not path.is_file():
        return None
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return cfg if isinstance(cfg, dict) else None


def agent_channel_is_configured(
    data_root: Path,
    agent_id: str,
    channel: str,
) -> bool:
    """True when this agent has its own linked channel credentials."""
    cfg = load_agent_channel_config(data_root, agent_id, channel)
    if not cfg:
        return False
    if channel == "whatsapp":
        return bool(str(cfg.get("linked_phone", "")).strip())
    if channel == "telegram":
        return bool(
            str(cfg.get("bot_token", "")).strip()
            or str(cfg.get("linked_id", "")).strip()
        )
    if channel == "email":
        return bool(str(cfg.get("connected_email", "")).strip())
    if channel in ("discord", "slack"):
        return bool(
            cfg.get("enabled")
            and str(cfg.get("bot_token", "")).strip()
        )
    return False
