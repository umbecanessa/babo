"""Platform-aware GitHub CLI auth hints."""

from __future__ import annotations

import pytest

from nls.tools.agent_tools.gh_auth_hints import (
    detect_shell_syntax_issue,
    format_gh_auth_required_hint,
    gh_auth_pipe_command,
    shell_is_windows,
)


def test_gh_auth_pipe_command_universal():
    cmd = gh_auth_pipe_command()
    assert "echo TOKEN | gh auth login --with-token" == cmd


def test_format_gh_auth_required_hint_mentions_platform():
    hint = format_gh_auth_required_hint()
    assert "[GITHUB AUTH REQUIRED]" in hint
    assert gh_auth_pipe_command() in hint
    if shell_is_windows():
        assert "PowerShell" in hint
        assert "<<<" in hint
    else:
        assert "Unix" in hint or "bash" in hint


@pytest.mark.parametrize(
    "command",
    [
        'gh auth login --with-token <<< "ghp_test"',
        "cmd << EOF",
    ],
)
def test_detect_shell_syntax_issue_windows_heredoc(command: str):
    if not shell_is_windows():
        pytest.skip("Windows-only preflight")
    err = detect_shell_syntax_issue(command)
    assert err is not None
    assert "heredoc" in err.lower()
    assert gh_auth_pipe_command() in err


def test_detect_shell_syntax_issue_unix_allows_heredoc():
    if shell_is_windows():
        pytest.skip("Unix-only")
    assert detect_shell_syntax_issue('gh auth login --with-token <<< "tok"') is None


def test_detect_shell_syntax_issue_pipe_ok_everywhere():
    assert detect_shell_syntax_issue("echo TOKEN | gh auth login --with-token") is None
