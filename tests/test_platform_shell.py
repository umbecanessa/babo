"""Tests for cross-platform shell guidance."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nls.platform_shell import (
    build_powershell_subprocess_argv,
    extract_missing_bin_from_output,
    format_bin_install_hint,
    format_http_api_error_hints,
    format_windows_sh_guidance,
    infer_requires_bins_from_scripts,
    normalize_powershell_command_names,
    powershell_is_pwsh,
    reset_powershell_executable_cache,
    resolve_powershell_executable,
)
from nls.skills_setup_policy import format_activation_steps, instruction_skill_setup_hint


def test_infer_bins_from_sh_script(tmp_path: Path):
    script = tmp_path / "demo.sh"
    script.write_text(
        "#!/bin/bash\n"
        "check_deps() {\n"
        "  command -v curl &> /dev/null || log_error \"curl required\"\n"
        "  command -v jq &> /dev/null || log_error \"jq required\"\n"
        "}\n",
        encoding="utf-8",
    )
    assert infer_requires_bins_from_scripts(tmp_path) == ["curl", "jq"]


def test_missing_bin_from_output():
    assert extract_missing_bin_from_output("[ERROR] jq required (sudo apt install jq)") == "jq"
    assert extract_missing_bin_from_output("jq: command not found") == "jq"


def test_http_api_hint_generic():
    out = '{"message": "Unauthorized", "code": 401}'
    cmd = "Invoke-RestMethod -Uri 'https://api.example.com/v1/items'"
    hint = format_http_api_error_hints(out, cmd)
    assert hint is not None
    assert "User-Agent" in hint
    assert "SKILL.md" in hint
    assert "Discord" not in hint


def test_http_api_hint_discord_enrichment():
    out = '{"message": "internal network error", "code": 40333}'
    hint = format_http_api_error_hints(
        out,
        "Invoke-RestMethod https://discord.com/api/v10/guilds/1",
    )
    assert hint is not None
    assert "User-Agent" in hint
    assert "Bot <token>" in hint


def test_http_api_hint_discord_empty_message():
    out = '{"message": "Cannot send an empty message", "code": 50006}'
    hint = format_http_api_error_hints(
        out,
        "Invoke-RestMethod https://discord.com/api/v10/channels/1/messages",
    )
    assert hint is not None
    assert "embeds" in hint


def test_bash_powershell_curl_not_doubled():
    if sys.platform != "win32":
        pytest.skip("PowerShell curl rewrite is Windows-only")
    from nls.tools.agent_tools.bash import BashTool

    fixed = BashTool._fix_powershell('curl.exe -s https://example.com')
    assert fixed == 'curl.exe -s https://example.com'
    assert "curl.exe.exe" not in fixed
    assert BashTool._fix_powershell("curl -s https://example.com") == "curl.exe -s https://example.com"


def test_resolve_powershell_prefers_bundled_nls_pwsh_bin(tmp_path: Path):
    reset_powershell_executable_cache()
    bundled = tmp_path / "pwsh.exe"
    bundled.touch()
    with patch.dict(os.environ, {"NLS_PWSH_BIN": str(bundled)}, clear=False):
        assert resolve_powershell_executable() == str(bundled)
        assert powershell_is_pwsh()
    os.environ.pop("NLS_PWSH_BIN", None)
    reset_powershell_executable_cache()


@patch("nls.platform_shell.shutil.which")
def test_resolve_powershell_prefers_pwsh(mock_which):
    if sys.platform != "win32":
        pytest.skip("PowerShell resolution is Windows-only")
    reset_powershell_executable_cache()
    mock_which.side_effect = lambda name: {
        "pwsh.exe": r"C:\Program Files\PowerShell\7\pwsh.exe",
    }.get(name)

    assert resolve_powershell_executable() == r"C:\Program Files\PowerShell\7\pwsh.exe"
    assert powershell_is_pwsh()


@patch("nls.platform_shell.shutil.which")
def test_resolve_powershell_falls_back_to_windows_ps(mock_which):
    reset_powershell_executable_cache()
    mock_which.side_effect = lambda name: {
        "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    }.get(name)

    assert "WindowsPowerShell" in resolve_powershell_executable()
    assert not powershell_is_pwsh()


@patch("nls.platform_shell.is_windows", return_value=True)
@patch("nls.platform_shell.resolve_powershell_executable")
def test_build_powershell_argv_uses_pwsh(mock_resolve, _win):
    mock_resolve.return_value = r"C:\Program Files\PowerShell\7\pwsh.exe"
    argv = build_powershell_subprocess_argv("Write-Output hi", utf8_preamble=False)
    assert argv[0] == r"C:\Program Files\PowerShell\7\pwsh.exe"
    assert argv[-1] == "Write-Output hi"


@patch("nls.platform_shell.is_windows", return_value=True)
@patch("nls.platform_shell.powershell_is_pwsh", return_value=True)
@patch("nls.platform_shell.resolve_powershell_executable")
def test_normalize_powershell_command_names(mock_resolve, _pwsh, _win):
    mock_resolve.return_value = r"C:\Program Files\PowerShell\7\pwsh.exe"
    cmd = normalize_powershell_command_names(
        'powershell -NoProfile -File "C:\\skill\\run.ps1"'
    )
    assert "powershell" not in cmd.lower() or "pwsh.exe" in cmd
    assert "pwsh.exe" in cmd


def test_http_api_hint_github_enrichment():
    out = '{"message": "Bad credentials"}'
    hint = format_http_api_error_hints(
        out,
        "curl.exe https://api.github.com/user",
    )
    assert hint is not None
    assert "GitHub" in hint or "gh auth" in hint


def test_looks_like_http_api_failure():
    from nls.platform_shell import looks_like_http_api_shell_failure

    assert looks_like_http_api_shell_failure(
        'Invoke-RestMethod : {"error":"x"}',
        "Invoke-RestMethod https://example.com",
    )
    assert not looks_like_http_api_shell_failure("hello world", "echo hi")


@patch("nls.platform_shell.is_windows", return_value=True)
@patch("nls.platform_shell.wsl_available", return_value=False)
def test_windows_sh_guidance_no_wsl(_wsl, _win, tmp_path: Path):
    (tmp_path / "run.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    text = format_windows_sh_guidance(tmp_path, [tmp_path / "run.sh"])
    assert "PowerShell" in text
    assert "WSL is not installed" in text
    assert ".ps1" in text


@patch("nls.skills_setup_policy.is_windows", return_value=True)
@patch("nls.platform_shell.is_windows", return_value=True)
@patch("nls.platform_shell.wsl_available", return_value=True)
@patch("nls.platform_shell.detect_package_managers", return_value=["winget"])
def test_activation_steps_windows(_pm, _wsl, _plat, _win, tmp_path: Path):
    (tmp_path / "demo.sh").write_text(
        "command -v jq || log_error \"jq required\"\n", encoding="utf-8"
    )
    meta = SimpleNamespace(
        skill_type="agentskill",
        source="clawhub",
        requires_env=["DISCORD_BOT_TOKEN"],
        requires_bins=[],
        instructions="## Quick Start\n```bash\n./demo.sh\n```\n",
    )
    steps = format_activation_steps(meta, "demo-skill", tmp_path)
    assert "PowerShell" in steps
    assert "absolute skill path" in steps.lower() or str(tmp_path) in steps
    assert "DISCORD_BOT_TOKEN" in steps
    assert "jq" in steps
    assert "User-Agent" in steps
    assert "skill_configure" in steps.lower()


@patch("nls.skills_setup_policy.is_windows", return_value=False)
@patch("nls.platform_shell.is_windows", return_value=False)
def test_activation_steps_unix(_win, _plat, tmp_path: Path):
    meta = SimpleNamespace(
        skill_type="agentskill",
        source="clawhub",
        requires_env=["API_TOKEN"],
        requires_bins=["curl"],
        instructions="",
    )
    steps = format_activation_steps(meta, "demo", tmp_path)
    assert "export API_TOKEN" in steps
    assert "40333" not in steps
    assert "User-Agent" not in steps


@patch("nls.skills_setup_policy.is_windows", return_value=True)
@patch("nls.platform_shell.is_windows", return_value=True)
@patch("nls.platform_shell.wsl_available", return_value=False)
def test_instruction_hint_includes_windows_sh(_wsl, _plat, _win, tmp_path: Path):
    (tmp_path / "tool.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    hint = instruction_skill_setup_hint("demo", tmp_path)
    assert "PowerShell" in hint
    assert "tool.sh" in hint


def test_bin_install_hint_falls_back_without_pm():
    with patch("nls.platform_shell.detect_package_managers", return_value=[]):
        hint = format_bin_install_hint("jq")
        assert "jq" in hint
        if sys.platform == "win32":
            assert "winget" in hint or "PATH" in hint


def test_windows_instruction_skills_env_prompt():
    from nls.platform_shell import WINDOWS_INSTRUCTION_SKILLS_ENV_PROMPT

    assert "CLAWHUB" in WINDOWS_INSTRUCTION_SKILLS_ENV_PROMPT
    assert "skill_configure" in WINDOWS_INSTRUCTION_SKILLS_ENV_PROMPT
    assert "PowerShell" in WINDOWS_INSTRUCTION_SKILLS_ENV_PROMPT
    assert "SKILL.md" in WINDOWS_INSTRUCTION_SKILLS_ENV_PROMPT


def test_v5_supplement_includes_clawhub_block_on_windows():
    import sys
    from nls.agentic import types as agentic_types

    if sys.platform != "win32":
        return
    assert "CLAWHUB / INSTRUCTION SKILLS" in agentic_types._V5_AGENTIC_SUPPLEMENT
    assert "CLAWHUB / INSTRUCTION SKILLS" in agentic_types._SUB_AGENT_SUPPLEMENT


def test_python_runtime_crash_detected():
    from nls.platform_shell import (
        classify_bash_runtime_outcome,
        looks_like_python_runtime_crash,
        looks_like_shell_command_failure,
    )

    uvicorn_out = (
        "INFO:     Uvicorn running on http://127.0.0.1:8000\n"
        "Traceback (most recent call last):\n"
        "TypeError: non-default argument 'created_at' follows default argument\n"
    )
    cmd = "uvicorn app.main:app --reload"
    assert looks_like_python_runtime_crash(uvicorn_out, command=cmd)
    assert classify_bash_runtime_outcome(uvicorn_out, command=cmd) == "failed"
    assert looks_like_shell_command_failure(uvicorn_out, cmd)


def test_daemon_start_without_crash_is_verified():
    from nls.platform_shell import classify_bash_runtime_outcome

    out = (
        "[SERVER/DAEMON STARTED — process detached to background (pid: 1234)]\n"
        "INFO:     Uvicorn running on http://127.0.0.1:8000\n"
    )
    assert classify_bash_runtime_outcome(out, command="uvicorn app.main:app") == "verified"


def test_traceback_shell_failure_scoped_to_server_commands():
    from nls.platform_shell import looks_like_shell_command_failure

    crash = (
        "Traceback (most recent call last):\n"
        "TypeError: non-default argument 'created_at' follows default argument\n"
    )
    assert looks_like_shell_command_failure(crash, "uvicorn app.main:app --reload")
    assert not looks_like_shell_command_failure(crash, "cat error.log")
