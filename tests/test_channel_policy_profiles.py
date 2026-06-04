"""Channel policy profile registry."""

from nls.runtime.channel_agent_config import _CHANNEL_SKILL_DIRS
from nls.runtime.channel_policy_profiles import (
    CHANNEL_POLICY_PROFILES,
    CHANNEL_TO_SKILL,
    profile_for_skill,
)


def test_channel_skill_dirs_matches_registry():
    assert _CHANNEL_SKILL_DIRS == CHANNEL_TO_SKILL


def test_all_bundled_channels_have_profiles():
    expected = {"discord", "slack", "telegram", "whatsapp", "email"}
    assert set(CHANNEL_TO_SKILL.keys()) == expected
    assert len(CHANNEL_POLICY_PROFILES) == len(expected)


def test_profile_for_skill_discord():
    p = profile_for_skill("discord-channel")
    assert p is not None
    assert p.surface == "workspace"
    assert "scoped_channels" in p.runtime_keys
