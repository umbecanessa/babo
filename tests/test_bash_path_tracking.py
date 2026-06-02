"""Tests for bash → FileStateCache path bootstrap."""

from __future__ import annotations

import os
from pathlib import Path

from nls.tools.agent_tools import FileStateCache
from nls.tools.agent_tools.bash_path_tracking import (
    extract_paths_from_command,
    record_bash_paths,
)


def test_extract_npm_create_vite_target():
    paths = extract_paths_from_command(
        "npm create vite@latest frontend -- --template react-ts",
        "/workspace/proj",
    )
    assert any(p.name == "frontend" for p in paths)


def test_extract_powershell_new_item():
    paths = extract_paths_from_command(
        "New-Item -ItemType Directory -Path backend/services/ -Force",
        "/workspace/proj",
    )
    assert any("services" in str(p) for p in paths)


def test_record_bash_paths_allows_subsequent_write(tmp_path: Path):
    cache = FileStateCache()
    target = tmp_path / "package.json"
    target.write_text('{"name":"x"}', encoding="utf-8")

    n = record_bash_paths(
        cache,
        f'echo hello > "{target.name}"',
        str(tmp_path),
    )
    assert n >= 1
    assert cache.check(str(target.resolve())) is None


def test_file_history_records_read_for_write_gate(tmp_path: Path):
    import asyncio

    from nls.tools.agent_tools.file_ledger import FileHistoryTool, FileLedger

    async def _run() -> None:
        cache = FileStateCache()
        f = tmp_path / "src" / "app.py"
        f.parent.mkdir(parents=True)
        f.write_text("x = 1\n", encoding="utf-8")

        ledger = FileLedger(tmp_path / "ledger.jsonl")
        tool = FileHistoryTool(ledger, file_state_cache=cache, cwd=str(tmp_path))
        await tool.execute({"path": "src/app.py"})
        assert cache.check(str(f.resolve())) is None

    asyncio.run(_run())
