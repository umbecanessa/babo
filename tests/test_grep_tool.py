"""Tests for grep tool — Python fallback must not block the event loop."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nls.tools.agent_tools.grep import (
    GrepTool,
    _iter_search_files,
    _python_grep,
    _should_skip_dir,
)


def test_should_skip_heavy_dirs():
    assert _should_skip_dir("node_modules")
    assert _should_skip_dir("release-build")
    assert _should_skip_dir(".git")
    assert not _should_skip_dir("nls")


def test_iter_search_files_skips_node_modules(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("discord token\n", encoding="utf-8")
    nm = root / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "huge.js").write_text("discord" * 10000, encoding="utf-8")

    files, truncated = _iter_search_files(root)
    rel = {f.relative_to(root).as_posix() for f in files}
    assert "src/main.py" in rel
    assert not any("node_modules" in p for p in rel)
    assert not truncated


def test_python_grep_finds_match(tmp_path: Path):
    d = tmp_path / "nls"
    d.mkdir()
    (d / "mod.py").write_text("DISCORD_BOT_TOKEN = 'x'\n", encoding="utf-8")

    lines, count = _python_grep(
        d, "DISCORD", None, False, False, 0, 0, 50,
    )
    assert count == 1
    assert any("mod.py" in line for line in lines)


@pytest.mark.asyncio
async def test_python_fallback_runs_in_executor_with_timeout(tmp_path: Path):
    tool = GrepTool(str(tmp_path))
    tool._rg_available = False  # force Python path

    (tmp_path / "a.py").write_text("hello discord\n", encoding="utf-8")

    result = await tool.execute({"pattern": "discord", "path": str(tmp_path)})
    assert not result.is_error
    assert "discord" in result.content
    assert result.details.get("backend") == "python"


@pytest.mark.asyncio
async def test_python_grep_large_skipped_tree_completes_quickly(tmp_path: Path):
    tool = GrepTool(str(tmp_path))
    tool._rg_available = False

    rb = tmp_path / "release-build" / "nested"
    rb.mkdir(parents=True)
    for i in range(200):
        (rb / f"file_{i}.txt").write_text("noise\n", encoding="utf-8")
    (tmp_path / "hit.py").write_text("discord here\n", encoding="utf-8")

    result = await asyncio.wait_for(
        tool.execute({"pattern": "discord", "path": str(tmp_path)}),
        timeout=5,
    )
    assert not result.is_error
    assert "hit.py" in result.content
