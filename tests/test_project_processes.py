"""Detached project process tracking on BashTool."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from nls.tools.agent_tools.bash import (
    BashTool,
    _extract_tracked_pid,
    _infer_process_label,
)


def test_infer_process_label_uvicorn():
    kind, label = _infer_process_label(
        "uvicorn backend.main:app --port 8000",
        "Uvicorn running on http://0.0.0.0:8000",
        "server",
    )
    assert kind == "backend"
    assert ":8000" in label


def test_extract_tracked_pid_node_child():
    pid = _extract_tracked_pid(
        "(node:43392) DeprecationWarning\nBackend on :5000",
        wrapper_pid=42864,
    )
    assert pid == 43392


@pytest.mark.asyncio
async def test_detached_survives_wrapper_exit(tmp_path, monkeypatch):
    """Windows/npm: wrapper returncode set while node child PID stays alive."""
    monkeypatch.setattr(
        "nls.tools.agent_tools.bash._process_is_alive",
        lambda pid: pid == 43392,
    )
    tool = BashTool(cwd=str(tmp_path))
    proc = MagicMock()
    proc.pid = 42864
    proc.returncode = 0  # npm wrapper exited

    await tool._register_detached(
        proc,
        "npm start",
        "(node:43392) server running on http://localhost:5000",
        "server",
    )

    listed = tool.list_detached_processes()
    assert len(listed) == 1
    assert listed[0]["pid"] == 43392


def test_infer_process_label_vite():
    kind, label = _infer_process_label(
        "npm run dev",
        "  ➜  Local:   http://localhost:5173/",
        "server",
    )
    assert kind == "frontend"
    assert ":5173" in label


@pytest.mark.asyncio
async def test_list_and_kill_detached(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "nls.tools.agent_tools.bash._process_is_alive", lambda _pid: True,
    )
    tool = BashTool(cwd=str(tmp_path))
    proc = MagicMock()
    proc.pid = 4242
    proc.returncode = None

    await tool._register_detached(
        proc,
        "uvicorn app:main --port 8000",
        "Uvicorn running on http://127.0.0.1:8000",
        "server",
    )

    listed = tool.list_detached_processes()
    assert len(listed) == 1
    assert listed[0]["pid"] == 4242
    assert listed[0]["kind"] == "backend"

    # kill_detached calls _kill_process_tree — mock it
    import nls.tools.agent_tools.bash as bash_mod

    bash_mod._kill_process_tree = lambda pid: None  # type: ignore[assignment]
    assert await tool.kill_detached(4242) is True
    assert tool.list_detached_processes() == []
