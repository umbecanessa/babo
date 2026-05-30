"""Bash tool -- Execute shell commands with timeout, truncation, and cleanup.

This is the power tool that replaces dozens of specialized tools.  The
agent uses bash to:

    - Run git operations (``git add``, ``git commit``, ``git push``)
    - Call APIs (``curl``)
    - Install packages (``pip install``, ``npm install``)
    - Manage processes (``ps``, ``kill``)
    - Run scripts (``python script.py``)
    - Do anything a human developer would do in a terminal

Output handling:
    - Tail-truncated to last 500 lines / 30KB (whichever hits first)
    - When truncated, full output saved to a temp file
    - Non-zero exit codes reported as errors
    - curl commands get ``-f -sS`` by default (HTTP errors fail; no progress
      meter). Pass ``-v``, ``-#``, or ``--progress-bar`` for verbose output.

Ported from pi-mono's bash tool with cross-platform adaptations for NLS
(supports both Linux/macOS and Windows via subprocess).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal as _signal_mod
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from .base import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    ToolResult,
    format_size,
    truncate_tail,
)

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# Patterns that indicate the command is blocking on user/browser interaction.
# When detected, we return partial output early so the agent can react.
_INTERACTIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"one-time code",
        r"device code",
        r"Open this URL",
        r"Enter the code",
        r"Press ENTER",
        r"press any key",
        r"Waiting for authentication",
        r"authorize this device",
        r"login/device",
        r"oauth/authorize",
        r"verification code.*\b[A-Z0-9]{4,}",
        r"enter.*passphrase",
    ]
]

# Patterns that indicate a long-running daemon/server has started and the
# command will never exit on its own.  When detected, we detach the process
# and return partial output so the agent can continue working.
_DAEMON_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"Nest application successfully started",
        r"Application is running on",
        r"listening on (?:port\s+)?\d+",
        r"server (?:is )?(?:running|started|listening)",
        r"ready on (?:https?://)?localhost[:\d]*",
        r"started server on",
        r"Accepting connections",
        r"webpack compiled",
        r"compiled successfully",
        r"Compiled in \d+",
        r"Local:\s+https?://localhost",
        r"Network:\s+https?://",
        r"ready in \d+\s*ms",
        r"VITE\s+v[\d.]+\s+ready",
        r"Next\.js\s+.*ready",
        r"Uvicorn running on",
        r"Serving Flask app",
        r"Django.*Starting development server",
        r"Rails.*Listening",
    ]
]


_BLOCKED_ALTERNATIVES: dict[str, str] = {
    "git reset --hard": (
        "Instead of git reset --hard (which destroys uncommitted work), use: "
        "git stash, git checkout -- <file>, or git restore <file>. "
        "To remove secrets from history, use git filter-branch or BFG Repo-Cleaner."
    ),
    "git clean -fdx": (
        "Instead of git clean -fdx (which deletes all untracked files), "
        "selectively remove files with Remove-Item or rm."
    ),
}

# Detect git init (with optional flags) at workspace root.
_GIT_INIT_RE = re.compile(r"\bgit\s+init\b", re.IGNORECASE)
# Detect `gh repo create ... --source=.` (source is the current dir).
_GH_REPO_CREATE_SOURCE_DOT_RE = re.compile(
    r"\bgh\s+repo\s+create\b.+--source\s*=\s*\.",
    re.IGNORECASE,
)
# Detect `pip install` / `pip3 install` — redirected to project_install.
_PIP_INSTALL_RE = re.compile(r"\bpip3?\s+install\b", re.IGNORECASE)
# Detect `python -m pip install` / `py -m pip install`.
_PY_PIP_INSTALL_RE = re.compile(
    r"(?:^|[\s;&|])(?:python|python3|py)\s+-m\s+pip\s+install\b",
    re.IGNORECASE,
)


def _extract_pip_package_spec(command: str) -> str:
    """Best-effort package spec from a pip install command."""
    text = command.strip()
    for pattern in (
        r"\bpip3?\s+install\s+(.+)$",
        r"(?:python|python3|py)\s+-m\s+pip\s+install\s+(.+)$",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            pkg = match.group(1).strip()
            pkg = re.sub(r"\s*(--quiet|--user|-q|-U|--upgrade)\b", " ", pkg)
            pkg = re.sub(r"\s+", " ", pkg).strip()
            return pkg
    return ""


def _extract_pip_requirements_file(command: str) -> str | None:
    """Return requirements file path when pip install uses -r / --requirement."""
    spec = _extract_pip_package_spec(command)
    if not spec:
        return None
    for pattern in (
        r"(?:^|\s)-r\s+([^\s]+)",
        r"(?:^|\s)--requirement\s+([^\s]+)",
    ):
        match = re.search(pattern, spec, re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("'\"")
    return None


def _project_install_redirect_hint(command: str) -> str:
    """Suggest project_install() args for a blocked pip install command."""
    req_file = _extract_pip_requirements_file(command)
    if req_file:
        return f"  project_install(requirements_file={repr(req_file)})"
    pkg = _extract_pip_package_spec(command)
    if pkg:
        return f"  project_install(package={repr(pkg)})"
    return "  project_install(ecosystem='python')"

# curl: inject -f (fail on HTTP 4xx/5xx) and -sS (silent + show errors) unless
# the agent explicitly requests verbose/progress output.
_CURL_BIN_RE = re.compile(r"(?<![\w.-])(curl(?:\.exe)?)(?=\s|$)", re.IGNORECASE)
_GH_BIN_RE = re.compile(r"(?<![\w.-])(gh(?:\.exe)?)(?=\s|$)", re.IGNORECASE)
_CURL_VERBOSE_RE = re.compile(
    r"(?:^|\s)(?:-[#v]|--(?:verbose|progress-bar|trace(?:-ascii|-time)?))(?:\s|$|=)",
    re.IGNORECASE,
)
# Windows curl progress meter lines (fallback strip when -s was not applied).
_CURL_PROGRESS_HEADER_RE = re.compile(r"^\s*% Total\b", re.MULTILINE)
_CURL_PROGRESS_STATS_RE = re.compile(
    r"^\s*\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+.*$",
    re.MULTILINE,
)

_WORKSPACE_NUKE_RE: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"Remove-Item\s+.*-Recurse\s+.*-Force",
        r"Remove-Item\s+.*-Force\s+.*-Recurse",
        r"rm\s+-r[f ]*\s+(?:\.\s|\.\/|\*)",
    ]
]

_SAFE_RECURSIVE_DELETE = frozenset({
    "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", ".cache", ".parcel-cache", "coverage", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", "htmlcov",
})


def _kill_process_tree(pid: int) -> None:
    """Kill a process and all its children."""
    try:
        if _IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
            )
        else:
            os.killpg(os.getpgid(pid), _signal_mod.SIGTERM)
            import time
            time.sleep(0.5)
            try:
                os.killpg(os.getpgid(pid), _signal_mod.SIGKILL)
            except ProcessLookupError:
                pass
    except Exception as e:
        logger.debug("Process tree kill for pid %d: %s", pid, e)


def _read_gh_token(hosts_path: Path) -> str:
    """Extract the GitHub OAuth token from a gh CLI hosts.yml file.

    Returns the token string, or "" if parsing fails.
    """
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


_GH_CREDENTIAL_MARKER = '[credential "https://github.com"]'


def _ensure_gh_credential_helper(gitconfig_path: Path) -> None:
    """Add a git credential helper for github.com that delegates to gh CLI.

    Idempotent — does nothing if the marker is already present.
    """
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


def _guard_bash_cwd_change(old_cwd: str, new_cwd: str) -> str:
    """Prevent ``cd project-dir`` from nesting when already inside it."""
    try:
        old_p = Path(old_cwd).resolve()
        new_p = Path(new_cwd).resolve()
    except Exception:
        return new_cwd

    # cd into a child directory with the same name as the current folder
    if new_p.parent == old_p and new_p.name == old_p.name:
        return str(old_p)

    # cd that prepends the project folder again (coach-sight/coach-sight)
    if len(new_p.parts) >= len(old_p.parts) + 1:
        tail = new_p.parts[len(old_p.parts):]
        if tail and tail[0] == old_p.name:
            return str(old_p)

    # cd into a child that repeats an ancestor folder (e.g. backend/ →
    # backend/icf-coaching-session-evaluation-platform when already under that project)
    try:
        if new_p.is_relative_to(old_p):
            added = new_p.relative_to(old_p)
            if added.parts and any(part in old_p.parts for part in added.parts):
                return str(old_p)
    except ValueError:
        pass

    return new_cwd


class BashTool:
    """Execute shell commands in the agent's working directory.

    Parameters
    ----------
    cwd : str
        Working directory for command execution.
    default_timeout : int | None
        Default timeout in seconds (None = no default timeout).
    max_lines : int
        Maximum output lines before truncation.
    max_bytes : int
        Maximum output bytes before truncation.
    blocked_patterns : list[str] | None
        Command patterns to block (e.g., ``["rm -rf /"]``).
    on_output : callable | None
        Async callback ``(chunk: str) -> None`` invoked for each line of
        live stdout/stderr, enabling real-time streaming to the frontend.
    """

    _PROJECT_MARKERS = (
        ".git", "package.json", "requirements.txt", "pyproject.toml",
        "Cargo.toml", "go.mod", "Makefile", "setup.py", "pom.xml",
    )

    def __init__(
        self,
        cwd: str,
        default_timeout: int | None = None,
        max_lines: int = DEFAULT_MAX_LINES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        blocked_patterns: list[str] | None = None,
        on_output: Any | None = None,
        shared_cwd: Any | None = None,
    ) -> None:
        self._cwd = cwd
        self._workspace_root = cwd
        self._shared_cwd = shared_cwd
        self._default_timeout = default_timeout
        self._max_lines = max_lines
        self._max_bytes = max_bytes
        self._blocked = blocked_patterns or []
        self._on_output = on_output
        # Processes detached after interactive-prompt detection.  Kept here
        # so they aren't garbage-collected (which could close pipes / send
        # signals) before they finish their work (e.g. OAuth token exchange).
        self._detached_procs: list[asyncio.subprocess.Process] = []
        # Per-project venv cache: None = not resolved yet, "" = failed
        self._project_venv_bin: str | None = None
        self._plan_project_dir_fn: Callable[[], str] | None = None
        self._isolated_env = self._build_isolated_env(cwd)

    def set_plan_project_dir_fn(self, fn: Callable[[], str] | None) -> None:
        self._plan_project_dir_fn = fn

    def _plan_project_dir(self) -> str:
        if self._plan_project_dir_fn is None:
            return ""
        try:
            return (self._plan_project_dir_fn() or "").strip()
        except Exception:
            return ""

    def _reap_finished_procs(self) -> None:
        """Remove already-exited processes from _detached_procs."""
        self._detached_procs = [
            p for p in self._detached_procs if p.returncode is None
        ]

    def cleanup(self) -> None:
        """Force-kill all tracked detached processes.

        Called during agent shutdown/deletion to release file locks held
        by long-running child processes (dev servers, bundlers, etc.).
        """
        self._reap_finished_procs()
        for proc in self._detached_procs:
            _kill_process_tree(proc.pid)
        self._detached_procs.clear()

    def _resolve_project_root(self) -> str | None:
        from .project_runtime import resolve_project_root

        return resolve_project_root(
            self._cwd,
            self._workspace_root,
            plan_project_dir=self._plan_project_dir() or None,
        )

    def _ensure_project_venv(self) -> str | None:
        """Lazily create a ``.venv`` in the project directory.

        Returns the venv bin path (``Scripts/`` on Windows, ``bin/``
        elsewhere), or ``None`` on failure.  Result is cached so
        subsequent calls are instant.
        """
        if self._project_venv_bin is not None:
            return self._project_venv_bin or None  # "" means failed

        project_root = self._resolve_project_root()
        if not project_root:
            self._project_venv_bin = ""
            return None

        from .project_runtime import ensure_project_venv

        bin_dir, _python_exe = ensure_project_venv(project_root)
        if bin_dir:
            self._project_venv_bin = bin_dir
            logger.info("[BASH] Project venv ready: %s", bin_dir)
            return bin_dir

        self._project_venv_bin = ""
        return None

    def _build_isolated_env(self, cwd: str) -> dict[str, str]:
        """Build an environment that isolates the agent from the host user.

        Overrides HOME, git config, and gh CLI config so the agent
        operates with its own identity and credentials without
        polluting or reading the host user's configuration.

        The agent's project venv (if available) is prepended to PATH
        so ``python`` and ``pip`` resolve to the project's isolated
        interpreter — never the NLS server's runtime.
        """
        env = {**os.environ}
        agent_home = str(Path(cwd).resolve())

        _proj_venv = self._ensure_project_venv()
        if _proj_venv:
            env["PATH"] = _proj_venv + os.pathsep + env.get("PATH", "")
            env["VIRTUAL_ENV"] = str(Path(_proj_venv).parent)

        # Git: use agent-local config instead of the host user's
        env["GIT_CONFIG_GLOBAL"] = str(Path(agent_home) / ".gitconfig")
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_TERMINAL_PROMPT"] = "0"

        # GitHub CLI: use agent-local config
        gh_config = Path(agent_home) / ".config" / "gh"
        env["GH_CONFIG_DIR"] = str(gh_config)

        # Always configure the git credential helper so git clone/push
        # can authenticate via gh CLI token once the agent logs in.
        _ensure_gh_credential_helper(Path(agent_home) / ".gitconfig")

        # If the agent has already authenticated with `gh auth login`,
        # read the stored token and expose it as GH_TOKEN / GITHUB_TOKEN
        # so that both `gh` CLI and `git` HTTPS operations work.
        # Otherwise, clear host tokens to maintain isolation.
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

        # macOS: ensure common package manager paths are available.
        # Non-interactive shells (subprocess) don't source .zshrc/.zprofile,
        # so Homebrew and MacPorts paths may be missing.
        if sys.platform == "darwin":
            extra_paths = [
                "/opt/homebrew/bin",
                "/opt/homebrew/sbin",
                "/usr/local/bin",
            ]
            current = env.get("PATH", "")
            for p in reversed(extra_paths):
                if os.path.isdir(p) and p not in current:
                    env["PATH"] = p + os.pathsep + env["PATH"]

        # XDG dirs: redirect config/data to agent workspace
        env["XDG_CONFIG_HOME"] = str(Path(agent_home) / ".config")
        env["XDG_DATA_HOME"] = str(Path(agent_home) / ".local" / "share")

        return env

    def _friendly_cwd(self) -> str:
        """Return a short CWD display relative to the workspace root."""
        try:
            rel = os.path.relpath(self._cwd, self._workspace_root)
            if rel == ".":
                return "." if _IS_WINDOWS else "~/workspace"
            return f"./{rel}" if _IS_WINDOWS else f"~/workspace/{rel}"
        except ValueError:
            return self._cwd

    _NO_SUCH_RE = re.compile(
        r"(?:No such file or directory|cannot access)[:\s]*['\"]?"
        r"([^\s'\"]+)",
        re.IGNORECASE,
    )

    def _suggest_path_fix(self, output: str, command: str) -> str | None:
        """If the error is about a missing path, check if dropping the first
        component yields a real path (common workspace/ prefix mistake)."""
        m = self._NO_SUCH_RE.search(output)
        if not m:
            return None
        bad_path = m.group(1).rstrip("/")
        parts = Path(bad_path).parts
        if len(parts) < 2:
            return None
        stripped = str(Path(*parts[1:]))
        candidate = Path(self._cwd) / stripped
        if candidate.exists():
            return (
                f"Hint: `{bad_path}` not found, but `{stripped}` exists. "
                f"You are already in the workspace root — "
                f"drop the `{parts[0]}/` prefix."
            )
        return None

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        shell_note = (
            " Shell is PowerShell on Windows — use PowerShell syntax "
            "(e.g. $env:VAR=\"val\" not export VAR=val). "
            "Common bash-isms are auto-converted but native PS syntax is safer."
            if _IS_WINDOWS else ""
        )
        return (
            f"Execute a shell command in the working directory. Returns "
            f"stdout and stderr. Output is truncated to last "
            f"{self._max_lines} lines or {format_size(self._max_bytes)}. "
            f"If truncated, full output is saved to a temp file. "
            f"Use for git, API calls, package management, builds, and "
            f"running scripts. curl calls automatically get -f -sS "
            f"(HTTP 4xx/5xx fail; no progress bar) unless you pass -v, "
            f"-#, or --progress-bar for verbose output. For reading file "
            f"contents, prefer the read tool instead.{shell_note}"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (optional)",
                },
            },
            "required": ["command"],
        }

    @staticmethod
    def _fix_quotes(cmd: str) -> str:
        """Replace curly/smart quotes with straight ASCII quotes.

        LLMs frequently emit Unicode curly quotes (\u201c \u201d \u2018 \u2019)
        which break shell commands like git commit -m "message".
        """
        return (
            cmd.replace("\u201c", '"').replace("\u201d", '"')
               .replace("\u2018", "'").replace("\u2019", "'")
        )

    @staticmethod
    def _curl_invocation_end(cmd: str, curl_start: int) -> int:
        """Return the index after the curl invocation starting at *curl_start*."""
        i = curl_start
        while i < len(cmd) and not cmd[i].isspace():
            i += 1
        in_quote = False
        quote: str | None = None
        while i < len(cmd):
            c = cmd[i]
            if in_quote:
                if c == quote:
                    in_quote = False
                i += 1
                continue
            if c in ('"', "'"):
                in_quote = True
                quote = c
                i += 1
                continue
            if c in (";", "|"):
                return i
            if c == "&" and i + 1 < len(cmd) and cmd[i + 1] == "&":
                return i
            i += 1
        return i

    @staticmethod
    def _curl_has_short_flag(rest: str, letter: str) -> bool:
        """True if *letter* appears in a combined curl short-flag cluster (e.g. ``-sS``)."""
        for m in re.finditer(r"(?:^|\s)-([A-Za-z]+)(?=\s|$|=|\d|/)", rest):
            if letter in m.group(1):
                return True
        return False

    @staticmethod
    def _curl_has_long_flag(rest: str, name: str) -> bool:
        return bool(
            re.search(rf"(?:^|\s)--{re.escape(name)}(?:\s|$|=)", rest, re.IGNORECASE)
        )

    @staticmethod
    def _inject_curl_defaults(invocation: str) -> str:
        """Add ``-f`` / ``-sS`` to a single curl invocation when missing."""
        m = re.match(r"(\S+)(\s*)(.*)", invocation, re.DOTALL)
        if not m:
            return invocation
        binary, ws, rest = m.group(1), m.group(2), m.group(3)
        flags: list[str] = []
        has_fail = (
            BashTool._curl_has_short_flag(rest, "f")
            or BashTool._curl_has_long_flag(rest, "fail")
            or BashTool._curl_has_long_flag(rest, "fail-with-body")
        )
        if not has_fail:
            flags.append("-f")
        verbose = bool(_CURL_VERBOSE_RE.search(rest))
        has_silent = (
            BashTool._curl_has_short_flag(rest, "s")
            or BashTool._curl_has_long_flag(rest, "silent")
        )
        has_show_error = (
            BashTool._curl_has_short_flag(rest, "S")
            or BashTool._curl_has_long_flag(rest, "show-error")
        )
        if not verbose and not has_silent:
            flags.append("-s")
        if not has_show_error and not verbose:
            flags.append("-S")
        if not flags:
            return invocation
        return f"{binary}{ws}{' '.join(flags)}{ws}{rest}"

    @staticmethod
    def _normalize_curl(cmd: str) -> str:
        """Inject curl defaults so HTTP errors fail and progress meters are off."""
        matches = list(_CURL_BIN_RE.finditer(cmd))
        if not matches:
            return cmd
        result = cmd
        for m in reversed(matches):
            start = m.start()
            end = BashTool._curl_invocation_end(result, start)
            invocation = result[start:end]
            result = (
                result[:start]
                + BashTool._inject_curl_defaults(invocation)
                + result[end:]
            )
        return result

    @staticmethod
    def _strip_curl_progress(text: str) -> str:
        """Remove curl progress-meter lines (mainly Windows) from output."""
        if "% Total" not in text or "Xferd" not in text:
            return text
        kept: list[str] = []
        for line in text.splitlines():
            stripped = line.strip("\r")
            if _CURL_PROGRESS_HEADER_RE.match(stripped):
                continue
            if (
                "Dload" in stripped
                and "Upload" in stripped
                and "Spent" in stripped
                and "Speed" in stripped
            ):
                continue
            if _CURL_PROGRESS_STATS_RE.match(stripped):
                continue
            kept.append(line)
        if not kept:
            return text
        out = "\n".join(kept)
        if text.endswith("\n"):
            out += "\n"
        return out

    @staticmethod
    def _fix_powershell(cmd: str) -> str:
        """Adapt common bash-isms to PowerShell equivalents.

        LLMs trained on Linux data frequently emit bash syntax that
        breaks on PowerShell (the default shell on Windows).
        """
        if not _IS_WINDOWS:
            return cmd

        import re

        # curl → curl.exe (PowerShell aliases curl to Invoke-WebRequest)
        cmd = re.sub(r'\bcurl\b', 'curl.exe', cmd)

        # wget → Invoke-WebRequest or wget.exe if available
        cmd = re.sub(r'\bwget\b', 'curl.exe', cmd)

        # head -n N file → Get-Content file -Head N
        cmd = re.sub(
            r'\bhead\s+(?:-n\s*)?(\d+)\s+(.+)',
            r'Get-Content \2 -Head \1',
            cmd,
        )
        cmd = re.sub(r'\bhead\b', 'Get-Content', cmd)

        # tail -n N file → Get-Content file -Tail N
        cmd = re.sub(
            r'\btail\s+(?:-n\s*)?(\d+)\s+(.+)',
            r'Get-Content \2 -Tail \1',
            cmd,
        )

        # ls -la / ls -l / ls -a → Get-ChildItem (strip flags)
        cmd = re.sub(r'\bls\s+-[lLaAhR]+\b', 'Get-ChildItem', cmd)

        # cat file → Get-Content file
        cmd = re.sub(r'\bcat\s+', 'Get-Content ', cmd)

        # ~/workspace → . (CWD is the workspace; ~ resolves wrong on PS)
        cmd = re.sub(r'~/workspace/?', './', cmd)
        cmd = re.sub(r'\$HOME/workspace/?', './', cmd)

        # Line continuation: backslash at end of line → backtick (PS continuation)
        cmd = re.sub(r'\\\s*\n', '`\n', cmd)

        # export VAR=value → $env:VAR = "value"
        def _export_to_env(m):
            var = m.group(1)
            val = m.group(2).strip().strip('"').strip("'")
            return f'$env:{var} = "{val}"'
        cmd = re.sub(
            r'\bexport\s+([A-Za-z_][A-Za-z0-9_]*)=([^\s;|&]+|"[^"]*"|\'[^\']*\')',
            _export_to_env,
            cmd,
        )

        # source / . → (no-op on PS, just skip)
        cmd = re.sub(r'^\s*(?:source|\.)\s+[^\s;]+\s*;?\s*', '', cmd)

        # Replace && with ; (PowerShell statement separator)
        # Careful: don't replace inside quoted strings
        cmd = re.sub(r'(?<=["\s])\s*&&\s*', ' ; ', cmd)
        cmd = re.sub(r'^\s*&&\s*', '; ', cmd)
        # Catch remaining && not inside quotes
        if '&&' in cmd:
            parts = []
            in_quote = False
            quote_char = None
            i = 0
            result = []
            while i < len(cmd):
                c = cmd[i]
                if c in ('"', "'") and not in_quote:
                    in_quote = True
                    quote_char = c
                    result.append(c)
                elif c == quote_char and in_quote:
                    in_quote = False
                    quote_char = None
                    result.append(c)
                elif c == '&' and i + 1 < len(cmd) and cmd[i + 1] == '&' and not in_quote:
                    result.append(' ; ')
                    i += 2
                    continue
                else:
                    result.append(c)
                i += 1
            cmd = ''.join(result)

        # /dev/null → $null (PowerShell equivalent)
        cmd = cmd.replace('2>/dev/null', '2>$null')
        cmd = cmd.replace('2> /dev/null', '2> $null')
        cmd = re.sub(r'>\s*/dev/null', '> $null', cmd)

        # || (OR operator) → ; if (-not $?) { cmd2 }
        # PowerShell doesn't have || as a statement separator.
        # Use a quote-aware replacement similar to && handling.
        if '||' in cmd:
            _parts: list[str] = []
            in_q = False
            qc = None
            j = 0
            while j < len(cmd):
                c = cmd[j]
                if c in ('"', "'") and not in_q:
                    in_q = True; qc = c; _parts.append(c)
                elif c == qc and in_q:
                    in_q = False; qc = None; _parts.append(c)
                elif c == '|' and j + 1 < len(cmd) and cmd[j + 1] == '|' and not in_q:
                    _parts.append(' ; ')
                    j += 2
                    continue
                else:
                    _parts.append(c)
                j += 1
            cmd = ''.join(_parts)

        # Stdin redirect: `cmd < file_or_string` → `Get-Content file | cmd`
        # or `"string" | cmd` for inline tokens (common with `gh auth login`)
        stdin_m = re.match(
            r'^(.+?)\s+<\s+(.+)$', cmd,
        )
        if stdin_m:
            before = stdin_m.group(1).strip()
            operand = stdin_m.group(2).strip().strip('"').strip("'")
            if os.path.exists(operand):
                cmd = f'Get-Content "{operand}" | {before}'
            else:
                cmd = f'"{operand}" | {before}'

        # .ps1 script invocation: on many Windows machines .ps1 files are
        # associated with Notepad (or another editor) instead of PowerShell.
        # Rewrite direct .ps1 invocations to use explicit powershell call.
        _ps1_m = re.match(
            r'^(?:&\s*)?(\.[\\/][^\s]+\.ps1)(.*)', cmd, re.IGNORECASE,
        )
        if _ps1_m:
            _script = _ps1_m.group(1)
            _rest = _ps1_m.group(2)
            cmd = (
                f'powershell -ExecutionPolicy Bypass '
                f'-File "{_script}"{_rest}'
            )

        return cmd

    _ENV_PROTECTED_KEYS = frozenset({
        "PATH", "HOME", "USERPROFILE", "VIRTUAL_ENV", "SYSTEMROOT",
        "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM",
        "GH_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    })

    _SELF_KILL_PATTERNS: list[re.Pattern[str]] = [
        re.compile(p, re.IGNORECASE)
        for p in [
            r"Stop-Process.*-(?:Name|ProcessName)\s+['\"]?(?:python|uvicorn|nls)",
            r"Stop-Process\s+-Force.*python|python.*Stop-Process",
            r"Get-Process.*(?:python|uvicorn|nls).*\|\s*Stop-Process",
            r"taskkill\s+/(?:IM|FI).*(?:python|uvicorn|nls)",
            r"kill\s+.*(?:python|uvicorn|nls)",
            r"pkill\s+.*(?:python|uvicorn|nls)",
            r"killall\s+(?:python|uvicorn|nls)",
        ]
    ]

    _PID_KILL_RE = re.compile(
        r"(?:Stop-Process\s+.*-Id\s+|taskkill\s+.*/PID\s+|kill\s+(?:-\d+\s+)?)"
        r"(\d[\d,\s]*)",
        re.IGNORECASE,
    )

    @staticmethod
    def _nls_pids() -> frozenset[int]:
        """PIDs that belong to the NLS server process tree (must not be killed)."""
        _own = os.getpid()
        _parent = os.getppid()
        return frozenset({_own, _parent})

    _SERVER_LAUNCH_PATTERNS: list[re.Pattern[str]] = [
        re.compile(p, re.IGNORECASE)
        for p in [
            r"uvicorn\s+\S+\s*.*--reload",
            r"python\s+(?:-m\s+)?(?:uvicorn|flask|django|http\.server|gunicorn)",
            r"python\s+\S*(?:manage\.py|app\.py|server\.py|main\.py)\s+(?:runserver|run)",
            r"flask\s+run",
            r"(?:npm|yarn|pnpm)\s+(?:start|run\s+(?:dev|serve))\b",
            r"ng\s+serve",
            r"next\s+dev",
            r"vite\s+(?:dev|preview)",
            r"php\s+-S",
            r"rails\s+server",
            r"cargo\s+run.*--release",
        ]
    ]

    def _effective_cwd_for_git_command(self, command: str) -> Path:
        """Resolve CWD after leading ``cd`` segments in a compound command."""
        cwd = Path(self._cwd).resolve()
        for segment in re.split(r"&&|;|\|", command):
            segment = segment.strip()
            if not segment:
                continue
            cd_match = re.match(
                r"^cd\s+(?P<target>(?:\"[^\"]+\")|'[^']+'|[^\s&;|]+)",
                segment,
                re.IGNORECASE,
            )
            if cd_match:
                target = cd_match.group("target").strip().strip("'\"")
                target_path = Path(target)
                if not target_path.is_absolute():
                    target_path = cwd / target_path
                cwd = target_path.resolve()
                continue
            if (
                _GIT_INIT_RE.search(segment)
                or _GH_REPO_CREATE_SOURCE_DOT_RE.search(segment)
            ):
                return cwd
        return cwd

    def _is_git_init_at_workspace_root(self, command: str) -> bool:
        """Return True when the command would run `git init` at the workspace root.

        Initialising a git repo at the workspace root pollutes the entire
        agent sandbox with a single `.git` folder — every project file
        becomes part of one giant repo instead of having its own clean repo
        inside its project directory.  The correct pattern is:

            mkdir my-project && cd my-project && git init

        or:

            gh repo create my-project --public --clone

        We only block when the effective CWD for ``git init`` resolves to the
        workspace root so that ``cd my-project && git init`` is allowed.
        """
        if not _GIT_INIT_RE.search(command):
            if not _GH_REPO_CREATE_SOURCE_DOT_RE.search(command):
                return False
        try:
            effective = self._effective_cwd_for_git_command(command)
            workspace = Path(self._workspace_root).resolve()
            return effective == workspace
        except Exception:
            return False

    def _is_workspace_destructive(self, command: str) -> str:
        """Return a reason string if the command would recursively nuke the workspace."""
        for pat in _WORKSPACE_NUKE_RE:
            if pat.search(command):
                _ws = getattr(self, "_workspace_root", self._cwd)
                _low = command.lower()
                # Fast reject: bare glob (rm -rf *)
                if " *" in _low:
                    return f"recursive delete targets workspace ({Path(_ws).name})"
                # Extract the target from the command (last non-flag token).
                _tokens = command.split()
                _target_dir = ""
                for _tok in reversed(_tokens):
                    _cleaned = _tok.strip("'\"")
                    if _cleaned and not _cleaned.startswith("-"):
                        _target_dir = _cleaned
                        break
                if not _target_dir:
                    return "recursive delete with no identifiable target"
                # Resolve and check: block workspace root or outside;
                # allow any subdirectory inside the workspace.
                _target_path = Path(_target_dir)
                if not _target_path.is_absolute():
                    _target_path = Path(self._cwd) / _target_path
                try:
                    _resolved = _target_path.resolve()
                    _ws_resolved = Path(_ws).resolve()
                    if _resolved == _ws_resolved:
                        return f"recursive delete targets workspace root ({Path(_ws).name})"
                    _resolved.relative_to(_ws_resolved)
                except ValueError:
                    return (
                        f"recursive delete on '{_target_dir}' — "
                        f"path is outside the workspace"
                    )
        return ""

    def _is_self_destructive(self, command: str) -> bool:
        """Return True if the command would kill the NLS host process."""
        for pat in self._SELF_KILL_PATTERNS:
            if pat.search(command):
                return True
        _protected = self._nls_pids()
        for m in self._PID_KILL_RE.finditer(command):
            for tok in re.split(r"[,\s]+", m.group(1)):
                tok = tok.strip()
                if tok.isdigit() and int(tok) in _protected:
                    return True
        return False

    def _is_server_launch(self, command: str) -> str | None:
        """Return a soft warning if the command launches a persistent
        server, or None if no warning needed.

        With per-project venv isolation the server runs in a separate
        Python runtime, so it cannot corrupt the NLS host.  The
        warning is informational only (the command still executes).
        The existing daemon detection (``_DAEMON_PATTERNS``) will
        auto-detach it after a few seconds of output.
        """
        for pat in self._SERVER_LAUNCH_PATTERNS:
            if pat.search(command):
                return (
                    "[INFO] This command starts a long-running server. "
                    "It will be auto-detached to background after startup. "
                    "Use the returned PID to stop it later if needed.\n"
                )
        return None

    def _refresh_env(self) -> None:
        """Re-read agent env files before each command.

        The agent has an isolated home directory with its own config,
        credentials, and env files.  This refresh picks up changes
        made by previous commands (e.g. ``gh auth login`` writing
        hosts.yml, or the agent writing a ``.env`` file).

        Sources (in order, later overrides earlier):
          1. ``<agent_home>/.env``  — standard KEY=VALUE dotenv
          2. ``<agent_home>/.config/gh/hosts.yml`` — GitHub CLI token
        """
        agent_home = Path(self._workspace_root)

        # 1. Standard .env file (agent can write here to persist vars)
        env_file = agent_home / ".env"
        if env_file.exists():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    if value and value[0] in ('"', "'") and value[-1] == value[0]:
                        value = value[1:-1]
                    if key and key not in self._ENV_PROTECTED_KEYS:
                        self._isolated_env[key] = value
            except Exception:
                pass

        # 2. GitHub CLI token (gh auth login writes hosts.yml)
        gh_hosts = agent_home / ".config" / "gh" / "hosts.yml"
        if gh_hosts.exists():
            try:
                token = _read_gh_token(gh_hosts)
                if token and self._isolated_env.get("GH_TOKEN") != token:
                    self._isolated_env["GH_TOKEN"] = token
                    self._isolated_env["GITHUB_TOKEN"] = token
                    _ensure_gh_credential_helper(agent_home / ".gitconfig")
            except Exception:
                pass

        # 3. Project venv: if _cwd changed (delegate cloning), the venv
        #    may not have been resolved yet.  Refresh PATH accordingly.
        _proj_venv = self._ensure_project_venv()
        if _proj_venv and _proj_venv not in self._isolated_env.get("PATH", ""):
            self._isolated_env["PATH"] = (
                _proj_venv + os.pathsep
                + self._isolated_env.get("PATH", "")
            )
            self._isolated_env["VIRTUAL_ENV"] = str(
                Path(_proj_venv).parent
            )

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        command = self._fix_quotes(params.get("command", "").strip())
        command = self._fix_powershell(command)
        command = self._normalize_curl(command)
        self._refresh_env()
        timeout = params.get("timeout", self._default_timeout)
        if timeout is not None:
            try:
                timeout = int(timeout)
            except (ValueError, TypeError):
                timeout = self._default_timeout

        if not command:
            return ToolResult(content="Error: 'command' is required.", is_error=True)

        # Block dangerous commands
        for pattern in self._blocked:
            if pattern in command:
                _alt = _BLOCKED_ALTERNATIVES.get(pattern, "")
                return ToolResult(
                    content=(
                        f"Error: Command blocked by safety policy (matched: {pattern!r})."
                        + (f" {_alt}" if _alt else "")
                    ),
                    is_error=True,
                )

        _ws_danger = self._is_workspace_destructive(command)
        if _ws_danger:
            return ToolResult(
                content=(
                    f"Error: Command blocked — it would recursively delete workspace files ({_ws_danger}). "
                    "Use targeted file operations instead. Never delete your own project directory."
                ),
                is_error=True,
            )

        if self._is_git_init_at_workspace_root(command):
            return ToolResult(
                content=(
                    "Error: Cannot initialise a git repository at the workspace root.\n\n"
                    "Git repos must live inside a dedicated project subfolder, not at the "
                    "workspace root — otherwise every file in your workspace ends up inside "
                    "one giant repo.\n\n"
                    "Correct approach:\n"
                    "  1. Create the project folder first:\n"
                    "       mkdir my-project\n"
                    "  2. cd into it, then init:\n"
                    "       cd my-project && git init\n"
                    "  OR use gh CLI to create + clone in one step:\n"
                    "       gh repo create my-project --public --clone\n"
                    "       cd my-project\n\n"
                    "Wave 0 delegates are the right place for repo setup — "
                    "the orchestrator should not run git init directly."
                ),
                is_error=True,
            )

        # Redirect pip install to project_install (project .venv) or server_install.
        if _PIP_INSTALL_RE.search(command) or _PY_PIP_INSTALL_RE.search(command):
            _install_hint = _project_install_redirect_hint(command)
            _proj = self._resolve_project_root()
            if _proj:
                return ToolResult(
                    content=(
                        "pip is not available in bash. For PROJECT dependencies "
                        "use project_install (installs into project/.venv — "
                        "the same python bash uses here):\n\n"
                        f"{_install_hint}\n\n"
                        "Use server_install ONLY when YOU need a package in "
                        "Babo's agent runtime (not the app being built)."
                    ),
                    is_error=True,
                )
            _pip_pkg = _extract_pip_package_spec(command)
            return ToolResult(
                content=(
                    "pip is not available in bash. Use server_install for "
                    "agent-runtime Python packages:\n\n"
                    f"  server_install(package={repr(_pip_pkg)})"
                ),
                is_error=True,
            )

        if self._is_self_destructive(command):
            daemon_pids = [
                str(p.pid) for p in self._detached_procs
                if p.returncode is None
            ]
            hint = ""
            if daemon_pids:
                hint = (
                    f" To stop servers YOU started, kill by PID instead: "
                    f"Stop-Process -Id {','.join(daemon_pids)} -Force"
                    if _IS_WINDOWS else
                    f" To stop servers YOU started, kill by PID: "
                    f"kill {' '.join(daemon_pids)}"
                )
            return ToolResult(
                content=(
                    "Error: This command would kill the NLS host process "
                    "(python/uvicorn). Killing by process name is blocked "
                    "because it would terminate the server running this "
                    "agent." + hint
                ),
                is_error=True,
            )

        _server_warn = self._is_server_launch(command)

        # Validate cwd exists
        if not Path(self._cwd).exists():
            return ToolResult(
                content=f"Error: Working directory does not exist: {self._cwd}",
                is_error=True,
            )

        try:
            _result = await self._run_command(command, timeout, signal)
            if _server_warn and _result.content:
                _result = ToolResult(
                    content=_server_warn + _result.content,
                    is_error=_result.is_error,
                    details=_result.details,
                )
            return _result
        except Exception as e:
            return ToolResult(
                content=f"Error executing command: {e}",
                is_error=True,
            )

    async def _read_stream(
        self,
        proc,
        output_chunks: list[bytes],
        interactive_event: asyncio.Event | None = None,
        daemon_event: asyncio.Event | None = None,
    ) -> None:
        """Read stdout line by line, invoking on_output callback for each.

        If *interactive_event* is provided, sets it when a line matches
        an interactive-prompt pattern so the caller can return early.
        If *daemon_event* is provided, sets it when a line matches a
        daemon/server-started pattern (listening on port, etc.).
        """
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            output_chunks.append(line)
            decoded = line.decode("utf-8", errors="replace")
            if self._on_output:
                try:
                    await self._on_output(decoded)
                except Exception:
                    pass
            if interactive_event and not interactive_event.is_set():
                for pat in _INTERACTIVE_PATTERNS:
                    if pat.search(decoded):
                        logger.info(
                            "Interactive prompt detected: %s",
                            decoded.strip()[:120],
                        )
                        interactive_event.set()
                        break
            if daemon_event and not daemon_event.is_set():
                for pat in _DAEMON_PATTERNS:
                    if pat.search(decoded):
                        logger.info(
                            "Daemon/server started detected: %s",
                            decoded.strip()[:120],
                        )
                        daemon_event.set()
                        break

    _CWD_SENTINEL = "__NLS_CWD__"

    async def _run_command(
        self,
        command: str,
        timeout: int | None,
        signal_event: asyncio.Event | None,
    ) -> ToolResult:
        """Run a command via asyncio subprocess with line-by-line streaming."""

        # Append a hidden pwd sentinel so we can track CWD changes.
        # The sentinel line is stripped from the output before returning.
        if _IS_WINDOWS:
            command = (
                f'{command}\n'
                f'Write-Output "{self._CWD_SENTINEL}$(Get-Location){self._CWD_SENTINEL}"'
            )
        else:
            command = (
                f'{command}\n'
                f'echo "{self._CWD_SENTINEL}$(pwd){self._CWD_SENTINEL}"'
            )

        if _IS_WINDOWS:
            shell_cmd = [
                "powershell", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-Command", command,
            ]
        elif sys.platform == "darwin" and os.path.isdir("/opt/homebrew"):
            shell_cmd = ["/usr/bin/arch", "-arm64", "/bin/bash", "-c", command]
        else:
            shell_cmd = ["/bin/bash", "-c", command]

        if _IS_WINDOWS:
            _platform_kw: dict = {
                "creationflags": (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                ),
            }
        else:
            _platform_kw = {"start_new_session": True}

        proc = await asyncio.create_subprocess_exec(
            *shell_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._cwd,
            env=self._isolated_env,
            **_platform_kw,
        )

        output_chunks: list[bytes] = []
        timed_out = False
        aborted = False
        interactive_event = asyncio.Event()
        daemon_event = asyncio.Event()

        try:
            abort_task = None
            if signal_event is not None:
                abort_task = asyncio.create_task(signal_event.wait())

            interactive_task = asyncio.create_task(interactive_event.wait())
            daemon_task = asyncio.create_task(daemon_event.wait())

            read_task = asyncio.create_task(
                self._read_stream(
                    proc, output_chunks, interactive_event, daemon_event,
                ),
            )

            wait_tasks: list[asyncio.Task] = [
                read_task, interactive_task, daemon_task,
            ]
            if abort_task:
                wait_tasks.append(abort_task)

            done, pending = await asyncio.wait(
                wait_tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if read_task in done:
                # Command finished normally.
                pass
            elif interactive_task in done:
                # Interactive prompt detected.  Give the process a short
                # grace period to emit remaining related output, then
                # DETACH (do NOT kill) so background work like OAuth token
                # exchange can finish while we return partial output.
                await asyncio.sleep(3)
                read_task.cancel()
                try:
                    await read_task
                except (asyncio.CancelledError, Exception):
                    pass
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
                self._detached_procs.append(proc)
                self._reap_finished_procs()

                partial = b"".join(output_chunks).decode("utf-8", errors="replace")
                return ToolResult(
                    content=(
                        "[INTERACTIVE PROMPT DETECTED — command is waiting "
                        "for user action]\n\n"
                        f"The command printed:\n{partial}\n\n"
                        "The command is still running in the background "
                        "and will complete once the external action is "
                        "done (e.g. OAuth token exchange). You should:\n"
                        "1. Use ask_user() to tell the user what action "
                        "is needed (include the URL and code shown above)\n"
                        "2. After the user confirms completion, VERIFY the "
                        "result with a follow-up command (e.g. "
                        "bash('gh auth status'))\n"
                        "3. Alternatively, use the browser tool to complete "
                        "the interaction yourself"
                    ),
                    is_error=False,
                    details={
                        "exit_code": None,
                        "interactive": True,
                    },
                )
            elif daemon_task in done:
                # Server/daemon started.  Give it a moment to settle and
                # emit any startup warnings, then DETACH so the agent can
                # continue with follow-up work (e.g. curl the endpoint).
                await asyncio.sleep(3)
                read_task.cancel()
                try:
                    await read_task
                except (asyncio.CancelledError, Exception):
                    pass
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
                self._detached_procs.append(proc)
                self._reap_finished_procs()

                partial = b"".join(output_chunks).decode("utf-8", errors="replace")
                return ToolResult(
                    content=(
                        "[SERVER/DAEMON STARTED — process detached to "
                        "background (pid: " + str(proc.pid) + ")]\n\n"
                        f"The command printed:\n{partial}\n\n"
                        "The server is now running in the background. "
                        "You should:\n"
                        "1. Verify it is working with a quick health "
                        "check (e.g. bash('curl -s http://localhost:"
                        "<port>/health || curl -s http://localhost:"
                        "<port>'))\n"
                        "2. Proceed with the next step of your plan\n"
                        "3. Do NOT run the same start command again — "
                        "the server is already running"
                    ),
                    is_error=False,
                    details={
                        "exit_code": None,
                        "daemon": True,
                        "pid": proc.pid,
                    },
                )
            elif abort_task and abort_task in done:
                aborted = True
                _kill_process_tree(proc.pid)
                read_task.cancel()
                try:
                    await read_task
                except (asyncio.CancelledError, Exception):
                    pass
            else:
                timed_out = True
                _kill_process_tree(proc.pid)
                read_task.cancel()
                try:
                    await read_task
                except (asyncio.CancelledError, Exception):
                    pass

            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()

        except asyncio.CancelledError:
            _kill_process_tree(proc.pid)
            raise

        output = b"".join(output_chunks)
        text = output.decode("utf-8", errors="replace")

        # Parse and strip the CWD sentinel, then update shared CWD.
        _sentinel_re = re.compile(
            rf'{re.escape(self._CWD_SENTINEL)}'
            rf'(.+?)'
            rf'{re.escape(self._CWD_SENTINEL)}'
        )
        _cwd_m = _sentinel_re.search(text)
        if _cwd_m:
            new_cwd = _cwd_m.group(1).strip()
            if new_cwd and Path(new_cwd).is_dir():
                old_cwd = self._cwd
                guarded_cwd = _guard_bash_cwd_change(old_cwd, new_cwd)
                self._cwd = guarded_cwd
                if self._shared_cwd is not None:
                    self._shared_cwd.path = guarded_cwd
                if guarded_cwd != old_cwd:
                    self._project_venv_bin = None
                    logger.info(
                        "Bash CWD changed: %s -> %s", old_cwd, guarded_cwd,
                    )
                elif new_cwd != old_cwd:
                    logger.warning(
                        "Bash CWD change blocked (double-nest): %s -> %s",
                        old_cwd, new_cwd,
                    )
            text = _sentinel_re.sub("", text).rstrip("\n")

        if _CURL_BIN_RE.search(command):
            text = self._strip_curl_progress(text)

        truncated_text, was_truncated, trunc_details = truncate_tail(
            text, self._max_lines, self._max_bytes,
        )

        temp_path = None
        if was_truncated:
            try:
                fd, temp_path = tempfile.mkstemp(
                    prefix="nls-bash-", suffix=".log",
                )
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception:
                temp_path = None

        result_text = truncated_text if truncated_text else "(no output)"

        if was_truncated and temp_path:
            start_line = trunc_details.get(
                "total_lines", 0,
            ) - trunc_details.get("output_lines", 0) + 1
            end_line = trunc_details.get("total_lines", 0)
            result_text += (
                f"\n\n[Showing lines {start_line}-{end_line} of "
                f"{trunc_details.get('total_lines', '?')}. "
                f"Full output: {temp_path}]"
            )

        exit_code = proc.returncode

        if aborted:
            result_text += "\n\nCommand aborted."
            return ToolResult(
                content=result_text,
                is_error=True,
                details={
                    "exit_code": exit_code,
                    "aborted": True,
                    "truncation": trunc_details if was_truncated else None,
                    "full_output_path": temp_path,
                },
            )

        if timed_out:
            result_text += f"\n\nCommand timed out after {timeout} seconds."
            _has_meaningful_output = len(text.strip()) > 20
            _is_hard_error = not _has_meaningful_output
            if _has_meaningful_output:
                result_text += (
                    " The command produced output before the timeout — "
                    "it may be a long-running process. Consider running "
                    "it without piping so daemon detection can work, or "
                    "use a background launch method."
                )
            return ToolResult(
                content=result_text,
                is_error=_is_hard_error,
                details={
                    "exit_code": exit_code,
                    "timed_out": True,
                    "timeout": timeout,
                    "partial_output": _has_meaningful_output,
                    "truncation": trunc_details if was_truncated else None,
                    "full_output_path": temp_path,
                },
            )

        is_error = exit_code is not None and exit_code != 0
        if is_error:
            result_text += f"\n\nCommand exited with code {exit_code}."
            if exit_code == 22 and _CURL_BIN_RE.search(command):
                result_text += (
                    "\n(curl: HTTP 4xx/5xx response — fix auth, URL, or "
                    "payload before retrying the same request.)"
                )
            _path_hint = self._suggest_path_fix(truncated_text, command)
            if _path_hint:
                result_text += f"\n{_path_hint}"

        # gh often exits 0 while printing "run gh auth login" — treat as failure.
        if not is_error and truncated_text and _GH_BIN_RE.search(command):
            _gh_lower = truncated_text.lower()
            if any(p in _gh_lower for p in (
                "gh auth login",
                "to get started with github cli",
                "not logged in",
                "authentication failed",
            )):
                is_error = True
                result_text += (
                    "\n\n[GITHUB AUTH REQUIRED]\n"
                    "gh is not authenticated for this agent workspace.\n"
                    "Fix (in order):\n"
                    "1. Token from task/user: "
                    "bash('echo TOKEN | gh auth login --with-token')\n"
                    "2. WM credential: wm(action='borrow', "
                    "domain='Project.Credential.GitHub')\n"
                    "3. Search skills: clawhub(action='search', "
                    "query='github') or discover_tools(query='github')\n"
                    "Do NOT repeat gh repo create until auth succeeds "
                    "(verify with bash('gh auth status'))."
                )

        # Annotate successful commands that produce deprecation/warning
        # output — prevents the agent from misinterpreting noisy-but-ok
        # commands (e.g. `gh repo delete --confirm` deprecation warning).
        if not is_error and truncated_text:
            _lower = truncated_text.lower()
            if any(kw in _lower for kw in (
                "deprecat", "warning:", "warn:", "deprecated",
                "flag --", "has been deprecated",
            )):
                result_text += (
                    "\n\n(Note: command succeeded (exit code 0) despite "
                    "the deprecation/warning message above.)"
                )

        result_text += f"\n\n[CWD: {self._friendly_cwd()}]"

        return ToolResult(
            content=result_text,
            is_error=is_error,
            details={
                "exit_code": exit_code,
                "truncation": trunc_details if was_truncated else None,
                "full_output_path": temp_path,
            },
        )


def create_bash_tool(
    cwd: str,
    default_timeout: int | None = None,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    blocked_patterns: list[str] | None = None,
    on_output: Any | None = None,
    shared_cwd: Any | None = None,
) -> BashTool:
    """Factory: create a bash tool configured for a working directory."""
    return BashTool(
        cwd,
        default_timeout=default_timeout,
        max_lines=max_lines,
        max_bytes=max_bytes,
        blocked_patterns=blocked_patterns,
        on_output=on_output,
        shared_cwd=shared_cwd,
    )
