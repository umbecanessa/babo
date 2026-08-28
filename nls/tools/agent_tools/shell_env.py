"""Isolated shell environment for agent PTY sessions and bash()."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_GH_CREDENTIAL_MARKER = '[credential "https://github.com"]'


def _read_gh_token(hosts_path: Path) -> str:
    try:
        import yaml

        data = yaml.safe_load(hosts_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ""
        gh_entry = data.get("github.com")
        if isinstance(gh_entry, dict):
            return gh_entry.get("oauth_token", "") or ""
        return ""
    except Exception:
        return ""


def _ensure_gh_credential_helper(gitconfig_path: Path) -> None:
    try:
        content = ""
        if gitconfig_path.exists():
            content = gitconfig_path.read_text(encoding="utf-8")
            if _GH_CREDENTIAL_MARKER in content:
                return
        content += (
            f"\n{_GH_CREDENTIAL_MARKER}\n"
            "\thelper = \n"
            "\thelper = !gh auth git-credential\n"
        )
        gitconfig_path.write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.debug("Failed to configure gh credential helper: %s", exc)


def build_agent_shell_env(
    workspace_root: str,
    cwd: str,
    *,
    venv_bin: str | None = None,
) -> dict[str, str]:
    """Build env for agent shell sessions (git/gh isolation, project venv PATH)."""
    env = {**os.environ}
    home_base = (workspace_root or cwd or "").strip()
    agent_home = str(Path(home_base).expanduser().resolve()) if home_base else str(Path(cwd).resolve())

    if venv_bin:
        env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(Path(venv_bin).parent)

    env["GIT_CONFIG_GLOBAL"] = str(Path(agent_home) / ".gitconfig")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"

    gh_config = Path(agent_home) / ".config" / "gh"
    env["GH_CONFIG_DIR"] = str(gh_config)
    _ensure_gh_credential_helper(Path(agent_home) / ".gitconfig")

    gh_hosts = gh_config / "hosts.yml"
    if gh_hosts.exists():
        token = _read_gh_token(gh_hosts)
        if token:
            env["GH_TOKEN"] = token
            env["GITHUB_TOKEN"] = token
        for key in ["GITHUB_ENTERPRISE_TOKEN"]:
            env.pop(key, None)
    else:
        for key in ["GITHUB_TOKEN", "GH_TOKEN", "GITHUB_ENTERPRISE_TOKEN"]:
            env.pop(key, None)

    if sys.platform == "darwin":
        extra_paths = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]
        current = env.get("PATH", "")
        for p in reversed(extra_paths):
            if os.path.isdir(p) and p not in current:
                env["PATH"] = p + os.pathsep + env["PATH"]

    env["XDG_CONFIG_HOME"] = str(Path(agent_home) / ".config")
    env["XDG_DATA_HOME"] = str(Path(agent_home) / ".local" / "share")

    if sys.platform == "win32":
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

    return env
