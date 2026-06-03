"""Tests for instruction-skill setup policy (scalable, not Discord-specific)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nls.skills_setup_policy import (
    BABO_GITHUB_REPO_URL,
    build_native_skill_setup_lines,
    babo_bundled_skill_github_ref,
    babo_github_raw_path,
    babo_github_tree_path,
    format_activation_steps,
    instruction_skill_post_read_nudge,
    instruction_skill_setup_hint,
    is_instruction_only_skill,
    infer_channel_platform,
    looks_like_active_channel_integration,
    looks_like_native_skill_authoring,
    skill_configure_absorption_content,
)


def test_instruction_only_agentskill():
    meta = SimpleNamespace(
        config_schema=[],
        skill_type="agentskill",
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


def test_activation_steps_include_skill_md_and_env():
    meta = SimpleNamespace(
        skill_type="agentskill",
        source="clawhub",
        requires_env=["API_TOKEN"],
        requires_bins=[],
        instructions=(
            "## Quick Start\n\n```bash\nexport API_TOKEN=x\n./run.sh --help\n```\n"
        ),
    )
    steps = format_activation_steps(meta, "demo-skill", Path("/data/skills/demo-skill"))
    assert "SKILL.md" in steps
    assert "API_TOKEN" in steps
    assert "skill_configure" in steps.lower()


def test_skill_configure_absorption_on_no_schema_error():
    content = skill_configure_absorption_content(
        "demo",
        "Skill 'demo' has no config_schema declared.",
        is_error=True,
    )
    assert content is not None
    assert "skill_configure" in content.lower()


def test_instruction_hint_includes_path():
    hint = instruction_skill_setup_hint("demo", Path("/data/skills/demo"))
    assert "SKILL.md" in hint
    assert "demo" in hint and "skills" in hint


@patch("nls.skills_setup_policy.is_windows", return_value=True)
def test_post_read_nudge_python_first_on_windows(_win, tmp_path: Path):
    skill_dir = tmp_path / "discord-admin"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Discord\n", encoding="utf-8")
    (skill_dir / "welcome-message.json").write_text("{}", encoding="utf-8")
    nudge = instruction_skill_post_read_nudge(str(skill_dir / "SKILL.md"))
    assert nudge is not None
    assert "deploy-*.py" in nudge
    assert "httpx" in nudge
    assert "embeds" in nudge


def test_native_skill_authoring_detection():
    assert looks_like_native_skill_authoring(
        "we could create a dedicated nls python skill maybe?",
    )
    assert not looks_like_native_skill_authoring(
        "configure the discord-admin bot token",
    )


def test_build_native_skill_setup_lines():
    lines = build_native_skill_setup_lines()
    assert any("NATIVE SKILL" in line for line in lines)
    assert any("nls/skills/bundled" in line for line in lines)
    assert any("babo.agency" in line for line in lines)


def test_active_channel_integration_detection():
    msg = (
        "I would love you to become an active moderator, always reading, "
        "always listening, interact when tagged on Discord"
    )
    assert looks_like_active_channel_integration(msg)
    assert infer_channel_platform(msg) == "discord"
    assert looks_like_native_skill_authoring(msg)


def test_build_native_skill_setup_lines_discord_channel():
    lines = build_native_skill_setup_lines(channel_platform="discord")
    assert any("discord-channel" in line for line in lines)
    assert any("add-channel-integration" in line for line in lines)
    assert any(BABO_GITHUB_REPO_URL in line for line in lines)
    assert any("telegram-channel" in line for line in lines)


def test_babo_github_reference_helpers():
    tree = babo_github_tree_path("nls/skills/bundled/telegram-channel")
    assert tree.startswith(BABO_GITHUB_REPO_URL)
    assert "telegram-channel" in tree
    raw = babo_github_raw_path("nls/skills/bundled/telegram-channel/__init__.py")
    assert raw.startswith("https://raw.githubusercontent.com/umbecanessa/babo/")
    ref = babo_bundled_skill_github_ref("telegram-channel")
    assert BABO_GITHUB_REPO_URL in ref
    assert "web_fetch" in ref
