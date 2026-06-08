"""Generic shell verification hints — any stack, any project layout."""

from __future__ import annotations

import re
from pathlib import Path

from nls.platform_shell import (
    extract_missing_bin_from_output,
    format_http_api_error_hints,
    format_missing_bin_hint,
    is_windows,
)

# Post-error patterns (language/runtime agnostic where possible)
_MODULE_NOT_FOUND_RES = (
    re.compile(r"ModuleNotFoundError", re.I),
    re.compile(r"No module named", re.I),
    re.compile(r"cannot find module", re.I),
    re.compile(r"Cannot find module", re.I),
    re.compile(r"package .+ is not installed", re.I),
    re.compile(r"command not found", re.I),
    re.compile(r"is not recognized as an internal or external command", re.I),
)

_DOUBLE_NEST_CD_RES = (
    re.compile(r"Cannot find path .+ because it does not exist", re.I),
    re.compile(r"No such file or directory", re.I),
    re.compile(r"cd : Cannot find path", re.I),
)

# Preflight: cd into folder name we are already inside
_CD_INTO_CWD_NAME = re.compile(
    r"\bcd\s+(?:\./)?([^\s;&|]+)",
    re.I,
)


def configured_channel_api_bash_hint(
    command: str,
    agent_dir: str,
) -> str | None:
    """Soft nudge when bash hits a configured channel REST API (any channel)."""
    if not command or not agent_dir:
        return None
    try:
        from nls.runtime.channel_api_routing import (
            detect_configured_channel_rest_in_command,
            format_channel_rest_bash_hint,
        )

        channel = detect_configured_channel_rest_in_command(command, agent_dir)
        if channel:
            return format_channel_rest_bash_hint(channel)
    except Exception:
        return None
    return None


def preflight_bash_command(command: str, cwd: str) -> str | None:
    """Return a pre-flight block message for known-bad command patterns."""
    if not command or not cwd:
        return None
    try:
        cwd_name = Path(cwd).name
    except Exception:
        return None
    if not cwd_name or len(cwd_name) < 2:
        return None
    for m in _CD_INTO_CWD_NAME.finditer(command):
        target = m.group(1).strip().strip("'\"")
        if target.rstrip("/\\") == cwd_name:
            return (
                f"Error: redundant cd — you are already inside {cwd_name}/.\n"
                f"Run commands directly (see bash [CWD: ...] footer). "
                f"Do not `cd {cwd_name}` again."
            )
    return None


def format_shell_error_hints(
    output: str,
    command: str,
    cwd: str,
) -> str | None:
    """Appendable hints after a failed bash command (stack-neutral)."""
    if not output:
        return None
    hints: list[str] = []

    if any(p.search(output) for p in _MODULE_NOT_FOUND_RES):
        hints.append(
            "[SHELL HINT] Run/import failed — cwd may not match where the code lives.\n"
            f"  • Your shell cwd is shown in [CWD: ...] above.\n"
            "  • cd into the directory where you wrote the files (your owned_paths), "
            "then re-run — do not cd into the project folder name again if already inside it.\n"
            "  • For monorepos: run builds/tests from the package root "
            "(where that package's manifest lives: package.json, pyproject.toml, go.mod, etc.)."
        )

    if any(p.search(output) for p in _DOUBLE_NEST_CD_RES):
        try:
            cwd_name = Path(cwd).name
        except Exception:
            cwd_name = ""
        dup = (
            f"{cwd_name}/{cwd_name}" if cwd_name else ""
        )
        if dup and dup.replace("/", "\\") in output.replace("/", "\\"):
            hints.append(
                "[SHELL HINT] Path double-nested — you prefixed the project folder "
                "while already inside it. Drop the leading folder segment and retry."
            )
        elif cwd_name and cwd_name in command:
            hints.append(
                f"[SHELL HINT] cd/path failed — if you are already in {cwd_name}/, "
                "omit `cd {0}` and use paths relative to [CWD].".format(cwd_name)
            )

    missing_bin = extract_missing_bin_from_output(output)
    if missing_bin:
        hints.append(format_missing_bin_hint(missing_bin, command))

    if is_windows() and ".sh" in command and "jq required" in output.lower():
        hints.append(
            "[SHELL HINT] .sh script failed a jq check on Windows — install jq in the "
            "same shell environment, or skip the script and use PowerShell/curl.exe "
            "per SKILL.md (or add a .ps1 wrapper)."
        )

    api_hint = format_http_api_error_hints(output, command)
    if api_hint:
        hints.append(api_hint)

    if not hints:
        return None
    return "\n".join(hints)
