"""Bundled skill cryptex ring guidance."""

from __future__ import annotations

from nls.skills_setup_policy import bundled_skill_ring_guidance


def test_bundled_skill_ring_not_enabled():
    headline, guidance = bundled_skill_ring_guidance(
        "telegram-channel",
        "Telegram bot integration",
        enabled=False,
        config_schema=[{"key": "bot_token"}],
    )
    assert "NOT enabled" in headline
    assert "Pre-shipped" in guidance
    assert "bot_token" in guidance


def test_pre_shipped_discord_ring_not_agent_installed():
    headline, guidance = bundled_skill_ring_guidance(
        "discord-channel",
        "Discord bot integration",
        enabled=True,
        config_schema=[{"key": "bot_token"}],
        agent_installed=True,
    )
    assert "Pre-shipped" in guidance
    assert "skill_configure" in guidance
    assert "skill_install from scratch" in guidance


def test_agent_installed_native_skill_ring():
    headline, guidance = bundled_skill_ring_guidance(
        "my-custom-channel",
        "Custom channel skill",
        enabled=True,
        config_schema=[{"key": "api_key"}],
        agent_installed=True,
    )
    assert "Agent-installed" in guidance
    assert "skill_install" in guidance
