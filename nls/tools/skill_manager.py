"""Skill Manager — per-agent skill enablement (M-018).

Standalone functions for managing ``enabled_skills.json`` in an
agent's directory.  No runtime dependency — any agent runtime and
ServerRuntime can delegate here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BUNDLED_SKILLS: list[str] = [
    "email-channel", "google-workspace", "telegram-channel",
    "whatsapp-channel", "todo-list", "mcp-client",
]

# Pre-shipped channel plugins configured via skill_configure (not agent-authored).
PRE_SHIPPED_CHANNEL_SKILLS: frozenset[str] = frozenset({
    "telegram-channel", "whatsapp-channel", "email-channel",
})


def _enabled_path(agent_dir: Path) -> Path:
    return agent_dir / "enabled_skills.json"


def get_enabled_skills(agent_dir: Path) -> list[str]:
    """Return enabled skill names. Defaults to bundled skills for new agents."""
    path = _enabled_path(agent_dir)
    if not path.exists():
        return list(BUNDLED_SKILLS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            enabled = json.load(f).get("enabled", [])
            if enabled == ["*"]:
                return list(BUNDLED_SKILLS)
            return enabled
    except Exception:
        return list(BUNDLED_SKILLS)


def set_enabled_skills(agent_dir: Path, names: list[str]) -> None:
    """Persist the enabled-skills list."""
    path = _enabled_path(agent_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"enabled": names}, f, indent=2)


def enable_skill(
    agent_dir: Path,
    name: str,
    refresh_fn: Any | None = None,
) -> None:
    """Add a skill and optionally refresh tools."""
    current = get_enabled_skills(agent_dir)
    if name not in current:
        current.append(name)
    set_enabled_skills(agent_dir, current)
    if refresh_fn is not None:
        try:
            refresh_fn()
        except Exception as exc:
            logger.warning("refresh after enable_skill failed: %s", exc)


def disable_skill(
    agent_dir: Path,
    name: str,
    refresh_fn: Any | None = None,
) -> None:
    """Remove a skill and optionally refresh tools."""
    current = get_enabled_skills(agent_dir)
    current = [s for s in current if s != name]
    set_enabled_skills(agent_dir, current)
    if refresh_fn is not None:
        try:
            refresh_fn()
        except Exception as exc:
            logger.warning("refresh after disable_skill failed: %s", exc)
