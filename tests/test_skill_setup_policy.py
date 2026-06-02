"""Tests for instruction-skill setup policy (scalable, not Discord-specific)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nls.skills_setup_policy import (
    format_activation_steps,
    instruction_skill_post_read_nudge,
    instruction_skill_setup_hint,
    is_instruction_only_skill,
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
