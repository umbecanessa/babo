"""Platform-aware GitHub CLI auth guidance for bash and orchestration hints."""

from __future__ import annotations

import sys

_IS_WINDOWS = sys.platform == "win32"

def shell_is_windows() -> bool:
    return _IS_WINDOWS


def gh_auth_pipe_command(*, token_placeholder: str = "TOKEN") -> str:
    """Single-line gh auth that works on Windows (PowerShell) and Unix shells."""
    tok = token_placeholder
    return f"echo {tok} | gh auth login --with-token"


def gh_auth_status_command() -> str:
    return "gh auth status"


def format_gh_auth_required_hint() -> str:
    """Append to bash tool output when gh is not authenticated."""
    pipe_cmd = gh_auth_pipe_command()
    verify = gh_auth_status_command()
    if _IS_WINDOWS:
        shell_note = (
            "Shell: PowerShell on Windows — do NOT use bash heredoc (<<<) or "
            "cmd.exe stdin redirect (< file)."
        )
        alt = (
            "Or write token to a temp file, then:\n"
            "  bash('Get-Content $env:TEMP\\gh-token | gh auth login --with-token')"
        )
    else:
        shell_note = "Shell: bash/sh on Unix — pipe or here-string is OK."
        alt = (
            "Or: bash('gh auth login --with-token <<< \"$TOKEN\"') "
            "(bash only, not PowerShell)."
        )
    return (
        "\n\n[GITHUB AUTH REQUIRED]\n"
        "gh is not authenticated for this agent workspace.\n"
        f"{shell_note}\n"
        "Fix (in order):\n"
        f"1. Token from task/user: bash('{pipe_cmd}')\n"
        f"2. WM credential: wm(action='borrow', "
        "domain='Project.Credential.GitHub')\n"
        "3. Search skills: clawhub(action='search', query='github') or "
        "discover_tools(query='github')\n"
        f"{alt}\n"
        f"Verify with bash('{verify}') before gh repo create/push."
    )


def format_gh_auth_recipe_hint() -> str:
    """Short hint for recipe / wake / escalation copy."""
    return (
        "GitHub auth: if gh says 'auth login', use "
        f"bash('{gh_auth_pipe_command()}') with the user's token "
        "(works on Windows PowerShell and Unix bash), or "
        "wm(action='borrow', domain='Project.Credential.GitHub'). "
        "If stuck, clawhub(action='search', query='github') or "
        "discover_tools(query='github')."
    )


def detect_shell_syntax_issue(command: str) -> str | None:
    """Return a pre-flight error message for known cross-shell mistakes."""
    if not command:
        return None
    if _IS_WINDOWS:
        if "<<" in command:
            return (
                "Error: bash heredoc (<< or <<<) is not supported in PowerShell.\n"
                f"Use: bash('{gh_auth_pipe_command()}')"
            )
    return None
