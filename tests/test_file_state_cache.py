"""Read-before-write cache scoped by agent loop, not asyncio task."""

from __future__ import annotations

import asyncio

import pytest

from nls.tools.agent_tools import (
    FileStateCache,
    enter_file_cache_scope,
    exit_file_cache_scope,
)


@pytest.mark.asyncio
async def test_loop_scope_allows_write_after_read_in_later_iteration(tmp_path):
    """Reads in iter N must satisfy write checks in iter N+1 (different tasks)."""
    cache = FileStateCache()
    target = tmp_path / "session.py"
    target.write_text("from sqlalchemy.orm import Mapped\n", encoding="utf-8")
    abs_path = str(target.resolve())

    token = enter_file_cache_scope("delegate_loop_7956")
    try:

        async def iter_read():
            cache.record(abs_path)

        async def iter_write_check():
            return cache.check(abs_path)

        await asyncio.create_task(iter_read())
        err = await asyncio.create_task(iter_write_check())
        assert err is None
    finally:
        exit_file_cache_scope(token)


@pytest.mark.asyncio
async def test_loop_scope_allows_parallel_reads_then_write(tmp_path):
    cache = FileStateCache()
    files = []
    for name in ("user.py", "session.py", "evaluation.py"):
        p = tmp_path / name
        p.write_text(f"# {name}\n", encoding="utf-8")
        files.append(str(p.resolve()))

    token = enter_file_cache_scope("delegate_loop_parallel")
    try:

        async def read_one(path: str) -> None:
            cache.record(path)

        await asyncio.gather(*(read_one(p) for p in files))

        async def check_one(path: str) -> str | None:
            return cache.check(path)

        results = await asyncio.gather(*(check_one(p) for p in files))
        assert results == [None, None, None]
    finally:
        exit_file_cache_scope(token)


@pytest.mark.asyncio
async def test_without_loop_scope_tasks_are_isolated(tmp_path):
    """Fallback: no loop scope → per-task isolation (legacy behavior)."""
    cache = FileStateCache()
    target = tmp_path / "a.py"
    target.write_text("x", encoding="utf-8")
    abs_path = str(target.resolve())

    async def reader():
        cache.record(abs_path)

    async def writer():
        return cache.check(abs_path)

    await asyncio.create_task(reader())
    err = await asyncio.create_task(writer())
    assert err is not None
    assert "MUST READ FIRST" in err


@pytest.mark.asyncio
async def test_different_loop_scopes_do_not_share_reads(tmp_path):
    cache = FileStateCache()
    target = tmp_path / "b.py"
    target.write_text("y", encoding="utf-8")
    abs_path = str(target.resolve())

    t1 = enter_file_cache_scope("loop_a")
    try:
        cache.record(abs_path)
    finally:
        exit_file_cache_scope(t1)

    t2 = enter_file_cache_scope("loop_b")
    try:
        err = cache.check(abs_path)
        assert err is not None
        assert "MUST READ FIRST" in err
    finally:
        exit_file_cache_scope(t2)
