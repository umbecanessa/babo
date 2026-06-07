"""Delegate write/delete cycle policy — counts, not heuristics."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nls.tools.agent_tools.delete_file import DeleteFileTool
from nls.tools.agent_tools.write import WriteTool


@pytest.mark.asyncio
async def test_delegate_gets_initial_write_plus_one_rewrite(tmp_path: Path):
    cwd = str(tmp_path)
    write_counts: dict[str, int] = {}
    delete_counts: dict[str, int] = {}
    write = WriteTool(cwd, block_full_rewrite_after_first=True)
    write._write_counts = write_counts

    first = await write.execute({"path": "a.py", "content": "v1\n"})
    second = await write.execute({"path": "a.py", "content": "v2\n"})
    assert not first.is_error
    assert not second.is_error
    assert write_counts[str((tmp_path / "a.py").resolve())] == 2


@pytest.mark.asyncio
async def test_third_write_blocked_suggests_edit_or_delete(tmp_path: Path):
    cwd = str(tmp_path)
    write_counts: dict[str, int] = {}
    write = WriteTool(cwd, block_full_rewrite_after_first=True)
    write._write_counts = write_counts

    await write.execute({"path": "a.py", "content": "v1\n"})
    await write.execute({"path": "a.py", "content": "v2\n"})
    third = await write.execute({"path": "a.py", "content": "v3\n"})
    assert third.is_error
    assert third.details.get("rewrite_blocked") is True
    assert "delete_file" in third.content.lower()


@pytest.mark.asyncio
async def test_delete_resets_write_cycle_then_allows_fresh_write(tmp_path: Path):
    cwd = str(tmp_path)
    write_counts: dict[str, int] = {}
    delete_counts: dict[str, int] = {}
    write = WriteTool(cwd, block_full_rewrite_after_first=True)
    write._write_counts = write_counts
    delete = DeleteFileTool(
        cwd,
        write_counts_ref=write_counts,
        delete_counts_ref=delete_counts,
    )

    await write.execute({"path": "a.py", "content": "v1\n"})
    await write.execute({"path": "a.py", "content": "v2\n"})
    blocked = await write.execute({"path": "a.py", "content": "v3\n"})
    assert blocked.is_error

    deleted = await delete.execute({"path": "a.py"})
    assert not deleted.is_error
    key = str((tmp_path / "a.py").resolve())
    assert key not in write_counts
    assert delete_counts[key] == 1

    again = await write.execute({"path": "a.py", "content": "fresh\n"})
    assert not again.is_error


@pytest.mark.asyncio
async def test_third_delete_on_same_path_blocked(tmp_path: Path):
    cwd = str(tmp_path)
    write_counts: dict[str, int] = {}
    delete_counts: dict[str, int] = {}
    write = WriteTool(cwd, block_full_rewrite_after_first=True)
    write._write_counts = write_counts
    delete = DeleteFileTool(
        cwd,
        write_counts_ref=write_counts,
        delete_counts_ref=delete_counts,
    )
    key = str((tmp_path / "a.py").resolve())

    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    await delete.execute({"path": "a.py"})
    (tmp_path / "a.py").write_text("y", encoding="utf-8")
    await delete.execute({"path": "a.py"})
    (tmp_path / "a.py").write_text("z", encoding="utf-8")

    third = await delete.execute({"path": "a.py"})
    assert third.is_error
    assert third.details.get("delete_blocked") is True
    assert delete_counts[key] == 2
