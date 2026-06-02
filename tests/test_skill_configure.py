"""Tests for skill_configure vs ClawHub instruction skill distinction."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from nls.skills_setup_policy import instruction_skill_setup_hint, is_instruction_only_skill


def test_instruction_only_agentskill():
    meta = SimpleNamespace(
        config_schema=[],
        skill_type="agentskill",
        source="clawhub",
        instructions=None,
    )
    assert is_instruction_only_skill(meta) is True


def test_instruction_only_clawhub_source():
    meta = SimpleNamespace(
        config_schema=[],
        skill_type="native",
        source="clawhub",
        instructions=None,
    )
    assert is_instruction_only_skill(meta) is True


def test_bundled_channel_skill_is_configurable():
    meta = SimpleNamespace(
        config_schema=[{"key": "bot_token"}],
        skill_type="native",
        source="bundled",
        instructions=None,
    )
    assert is_instruction_only_skill(meta) is False


def test_instruction_hint_points_to_skill_md():
    hint = instruction_skill_setup_hint(
        "discord-admin",
        Path("/data/skills/discord-admin"),
    )
    assert "skill_configure" in hint.lower()
    assert "SKILL.md" in hint
    assert "discord-admin" in hint
