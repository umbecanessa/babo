"""Tests for generic bash shell hints."""

from __future__ import annotations

import sys

import pytest

from nls.tools.agent_tools.shell_hints import (
    format_shell_error_hints,
    preflight_bash_command,
)


def test_preflight_blocks_redundant_cd():
    msg = preflight_bash_command(
        "cd my-app && npm test",
        cwd="/agents/ws/my-app",
    )
    assert msg is not None
    assert "redundant cd" in msg.lower()


def test_module_not_found_hint():
    hint = format_shell_error_hints(
        "ModuleNotFoundError: No module named 'app'",
        "python -c \"import app\"",
        "/agents/ws/my-app",
    )
    assert hint is not None
    assert "SHELL HINT" in hint
    assert "owned_paths" in hint.lower() or "cwd" in hint.lower()


def test_double_nest_path_hint():
    if sys.platform != "win32":
        pytest.skip("Windows PowerShell cd error patterns")
    hint = format_shell_error_hints(
        "cd : Cannot find path 'C:\\ws\\my-app\\my-app\\src' because it does not exist.",
        "cd my-app",
        "C:\\ws\\my-app",
    )
    assert hint is not None
    assert "double-nest" in hint.lower() or "omit" in hint.lower()
