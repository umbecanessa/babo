"""Read tool hints when path is a directory."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nls.tools.agent_tools.read import ReadTool


@pytest.mark.asyncio
async def test_read_directory_suggests_list_dir_and_skill_configure(tmp_path: Path):
    skill_dir = tmp_path / "discord-channel"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Discord\n", encoding="utf-8")

    tool = ReadTool(cwd=str(tmp_path))
    result = await tool.execute({"path": str(skill_dir)})
    assert result.is_error
    assert "directory" in result.content.lower()
    assert "SKILL.md" in result.content
    assert "skill_configure" in result.content.lower()
