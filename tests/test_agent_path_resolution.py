"""Tests for path resolution and display for agent file tools."""

from __future__ import annotations

import sys

import pytest
from pathlib import Path

from nls.tools.agent_tools.bash import _guard_bash_cwd_change
from nls.tools.agent_tools.write import _resolve_path, format_path_for_agent


def test_resolve_path_strips_duplicate_project_dir():
    cwd = "/workspace/my-app"
    resolved = _resolve_path("my-app/frontend/src/App.tsx", cwd)
    assert resolved == Path("/workspace/my-app/frontend/src/App.tsx")


def test_format_path_prefers_cwd_relative_inside_project():
    ws = Path("/workspace")
    cwd = ws / "my-app"
    file_path = cwd / "frontend" / "src" / "App.tsx"
    display = format_path_for_agent(
        file_path,
        workspace_root=str(ws),
        effective_cwd=str(cwd),
    )
    assert display == "frontend/src/App.tsx"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
def test_guard_bash_blocks_redundant_project_folder_cd():
    old = (
        r"C:\agent\workspace\icf-coaching-session-evaluation-platform\backend"
    )
    bad = (
        old + r"\icf-coaching-session-evaluation-platform"
    )
    guarded = _guard_bash_cwd_change(old, bad)
    assert Path(guarded).resolve() == Path(old).resolve()
