"""Registry of bundled channel skills for interaction policy and config paths.

Single source of truth — add a ChannelPolicyProfile here when shipping a new channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SurfaceKind = Literal["workspace", "messaging", "email"]


@dataclass(frozen=True)
class ChannelPolicyProfile:
    channel_id: str
    skill_name: str
    surface: SurfaceKind
    runtime_keys: frozenset[str]


CHANNEL_POLICY_PROFILES: tuple[ChannelPolicyProfile, ...] = (
    ChannelPolicyProfile(
        "discord", "discord-channel", "workspace",
        frozenset({"groups", "scoped_channels"}),
    ),
    ChannelPolicyProfile(
        "slack", "slack-channel", "workspace",
        frozenset({"groups", "scoped_channels"}),
    ),
    ChannelPolicyProfile(
        "telegram", "telegram-channel", "messaging",
        frozenset({"groups"}),
    ),
    ChannelPolicyProfile(
        "whatsapp", "whatsapp-channel", "messaging",
        frozenset({"groups"}),
    ),
    ChannelPolicyProfile(
        "email", "email-channel", "email",
        frozenset(),
    ),
)

CHANNEL_TO_SKILL: dict[str, str] = {
    p.channel_id: p.skill_name for p in CHANNEL_POLICY_PROFILES
}

SKILL_TO_CHANNEL: dict[str, str] = {
    p.skill_name: p.channel_id for p in CHANNEL_POLICY_PROFILES
}

RUNTIME_CONFIG_KEYS: dict[str, frozenset[str]] = {
    p.skill_name: p.runtime_keys for p in CHANNEL_POLICY_PROFILES
}

SURFACE_BY_SKILL: dict[str, SurfaceKind] = {
    p.skill_name: p.surface for p in CHANNEL_POLICY_PROFILES
}


def channel_skill_dirs() -> dict[str, str]:
    """Map channel id → skill package dir (for per-agent config paths)."""
    return dict(CHANNEL_TO_SKILL)


def runtime_config_keys(skill_name: str) -> frozenset[str]:
    return RUNTIME_CONFIG_KEYS.get(skill_name, frozenset())


def profile_for_skill(skill_name: str) -> ChannelPolicyProfile | None:
    for p in CHANNEL_POLICY_PROFILES:
        if p.skill_name == skill_name:
            return p
    return None
