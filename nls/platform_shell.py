"""Cross-platform shell guidance for instruction skills and bash tool hints.

Babo runs on macOS/Linux (real bash) and Windows (PowerShell via bash()).
ClawHub skills are often ``.sh`` scripts expecting Unix tooling — this module
builds scalable, platform-aware activation steps and post-error hints without
hardcoding per-skill install commands.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_CMD_V_RE = re.compile(r"command\s+-v\s+(\w+)", re.I)
_BIN_REQUIRED_RE = re.compile(
    r"(?:log_error|die|fatal)\s+[\"']?(\w+)\s+required",
    re.I,
)
_MISSING_BIN_OUTPUT_RES = (
    re.compile(r"(\w+)\s+required(?:\s|\(|$)", re.I),
    re.compile(r"(\w+):\s*command not found", re.I),
    re.compile(r"command not found:\s*(\w+)", re.I),
    re.compile(
        r"['\"]?(\w+)['\"]?\s+is not recognized as an internal or external command",
        re.I,
    ),
    re.compile(r"(\w+):\s*line\s+\d+:\s*(\w+):\s*command not found", re.I),
)

# Common CLI tools referenced by AgentSkill shell scripts (not exhaustive).
_KNOWN_BIN_PACKAGES: dict[str, dict[str, str]] = {
    "jq": {
        "winget": "jqlang.jq",
        "scoop": "jq",
        "choco": "jq",
        "brew": "jq",
        "apt": "jq",
        "dnf": "jq",
        "pacman": "jq",
    },
    "curl": {
        "winget": "cURL.cURL",
        "scoop": "curl",
        "choco": "curl",
        "brew": "curl",
        "apt": "curl",
        "dnf": "curl",
    },
    "wget": {
        "winget": "GNU.Wget",
        "scoop": "wget",
        "brew": "wget",
        "apt": "wget",
    },
}


def is_windows() -> bool:
    return sys.platform == "win32"


_PS_UTF8_PREAMBLE = (
    "[Console]::InputEncoding = [Console]::OutputEncoding = "
    "[System.Text.UTF8Encoding]::new($false); "
    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
)

_powershell_exe_cache: str | None = None


def reset_powershell_executable_cache() -> None:
    """Clear cached PowerShell path (tests only)."""
    global _powershell_exe_cache
    _powershell_exe_cache = None


def resolve_powershell_executable(*, prefer_pwsh: bool = True) -> str:
    """Prefer PowerShell 7 (``pwsh``) over Windows PowerShell 5.1."""
    global _powershell_exe_cache
    if _powershell_exe_cache is not None:
        return _powershell_exe_cache

    bundled = os.environ.get("NLS_PWSH_BIN", "").strip()
    if bundled:
        bundled_path = Path(bundled)
        if bundled_path.is_file():
            _powershell_exe_cache = str(bundled_path)
            logger.info("Windows shell: using bundled %s for bash()", bundled_path)
            return _powershell_exe_cache

    candidates: tuple[str, ...]
    if prefer_pwsh:
        candidates = ("pwsh.exe", "pwsh", "powershell.exe", "powershell")
    else:
        candidates = ("powershell.exe", "powershell", "pwsh.exe", "pwsh")

    for name in candidates:
        path = shutil.which(name)
        if path:
            _powershell_exe_cache = path
            logger.info("Windows shell: using %s for bash()", path)
            return path

    _powershell_exe_cache = "powershell.exe"
    logger.warning(
        "Windows shell: pwsh/powershell not found on PATH; falling back to powershell.exe",
    )
    return _powershell_exe_cache


def powershell_is_pwsh(exe: str | None = None) -> bool:
    name = Path(exe or resolve_powershell_executable()).name.lower()
    return name.startswith("pwsh")


def normalize_powershell_command_names(command: str) -> str:
    """When PS7 is available, rewrite inline ``powershell`` calls to ``pwsh``."""
    if not is_windows() or not powershell_is_pwsh():
        return command
    pwsh_name = Path(resolve_powershell_executable()).name
    return re.sub(r"\bpowershell(?:\.exe)?\b", pwsh_name, command, flags=re.I)


def build_powershell_subprocess_argv(
    command: str,
    *,
    utf8_preamble: bool = True,
) -> list[str]:
    """Argv for ``asyncio.create_subprocess_exec`` on Windows."""
    if utf8_preamble and is_windows():
        command = _PS_UTF8_PREAMBLE + command
    return [
        resolve_powershell_executable(),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        command,
    ]


def format_powershell_runtime_note() -> str:
    """One-line note for agents: which PowerShell runs under bash() on Windows."""
    if not is_windows():
        return ""
    exe = resolve_powershell_executable()
    if powershell_is_pwsh(exe):
        bundled = os.environ.get("NLS_PWSH_BIN", "").strip()
        source = "system"
        if bundled:
            try:
                source = (
                    "Babo-managed"
                    if Path(bundled).resolve() == Path(exe).resolve()
                    else "system"
                )
            except OSError:
                source = "system"
        return (
            f"Windows bash() uses PowerShell 7 ({Path(exe).name}, {source}) "
            f"with UTF-8 I/O."
        )
    return (
        "Windows bash() uses Windows PowerShell 5.1 (legacy). "
        "Babo desktop setup installs PowerShell 7 automatically — "
        "re-run setup if UTF-8/shell issues persist."
    )

WINDOWS_INSTRUCTION_SKILLS_ENV_PROMPT = (
    "CLAWHUB / INSTRUCTION SKILLS: Most community skills ship .sh scripts for "
    "Mac/Linux bash. On Windows your bash() tool runs PowerShell — act accordingly.\n"
    "- Read SKILL.md first; use the absolute installed skills path (not ./data/skills/ "
    "relative to workspace).\n"
    "- Do NOT use skill_configure for ClawHub/AgentSkill packages.\n"
    "- When the skill is mostly HTTP/API: use Invoke-RestMethod or curl.exe with auth "
    "headers from SKILL.md and an explicit non-browser User-Agent.\n"
    "- Post JSON payloads via UTF-8 files + raw bytes; do not use `` `u{1F4E2} `` in "
    "PowerShell (invalid — prints literally as u{1F4E2}).\n"
    "- When running .sh: install jq/curl/etc. in the shell that actually executes the "
    "script; Windows (scoop/winget) PATH does not apply inside WSL bash.\n"
    "- Use WSL only if already on the machine; otherwise PowerShell, curl.exe, or a "
    "small .ps1 wrapper beside the skill.\n\n"
)


def is_macos() -> bool:
    return sys.platform == "darwin"


def wsl_available() -> bool:
    """True when ``wsl.exe`` is on PATH (optional; not required on Windows)."""
    if not is_windows():
        return False
    return shutil.which("wsl") is not None


def detect_package_managers() -> list[str]:
    """Return package managers likely available on this host (best-effort)."""
    found: list[str] = []
    if is_windows():
        for name in ("winget", "scoop", "choco"):
            if shutil.which(name):
                found.append(name)
        return found
    if is_macos():
        if shutil.which("brew"):
            found.append("brew")
        return found
    for name in ("apt", "apt-get", "dnf", "yum", "pacman", "apk"):
        if shutil.which(name):
            found.append(name.split("-")[0] if name == "apt-get" else name)
            break
    return found


def format_bin_install_hint(bin_name: str) -> str:
    """Dynamic install suggestion from detected package managers."""
    bin_name = bin_name.strip().lower()
    pkg_map = _KNOWN_BIN_PACKAGES.get(bin_name, {})
    managers = detect_package_managers()
    examples: list[str] = []

    for mgr in managers:
        pkg = pkg_map.get(mgr)
        if not pkg:
            continue
        if mgr == "winget":
            examples.append(f"winget install --id {pkg}")
        elif mgr == "scoop":
            examples.append(f"scoop install {pkg}")
        elif mgr == "choco":
            examples.append(f"choco install {pkg}")
        elif mgr == "brew":
            examples.append(f"brew install {pkg}")
        elif mgr in ("apt", "dnf", "pacman", "apk"):
            examples.append(f"sudo {mgr} install {pkg}")

    if examples:
        primary = examples[0]
        extra = ""
        if len(examples) > 1:
            extra = f" (alternatives: {'; '.join(examples[1:3])})"
        return (
            f"Install `{bin_name}` in the same shell that will run the command: "
            f"{primary}{extra}"
        )

    if is_windows():
        return (
            f"Install `{bin_name}` for Windows (e.g. winget/scoop/choco) and ensure "
            f"it is on PATH in the shell you use — PowerShell and WSL have separate PATHs."
        )
    return (
        f"Install `{bin_name}` with your system package manager "
        f"(brew, apt, dnf, etc.) in the shell that runs the script."
    )


def find_shell_scripts(skill_dir: Path | None) -> list[Path]:
    if skill_dir is None or not skill_dir.is_dir():
        return []
    scripts: list[Path] = []
    for p in sorted(skill_dir.iterdir()):
        if p.is_file() and p.suffix.lower() == ".sh":
            scripts.append(p)
    return scripts


def infer_requires_bins_from_scripts(skill_dir: Path | None) -> list[str]:
    """Parse ``.sh`` files for ``command -v`` / ``X required`` dependency checks."""
    if skill_dir is None:
        return []
    bins: list[str] = []
    seen: set[str] = set()
    for script in find_shell_scripts(skill_dir):
        try:
            text = script.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in (_CMD_V_RE, _BIN_REQUIRED_RE):
            for m in pattern.finditer(text):
                name = (m.group(1) if m.lastindex else "").lower()
                if name and name not in seen and len(name) < 32:
                    seen.add(name)
                    bins.append(name)
    return bins


def format_env_var_step(var: str, step_num: int) -> str:
    if is_windows():
        return (
            f"{step_num}. Set env var: $env:{var} = '...' "
            f"(or add {var}=... to workspace .env — auto-loaded before bash())"
        )
    return (
        f"{step_num}. Set env var: export {var}=... "
        f"(or add to workspace .env — auto-loaded before bash())"
    )


def format_platform_shell_note() -> str:
    if is_windows():
        wsl = " WSL is available — use it only if you install deps inside WSL." if wsl_available() else ""
        return (
            f"{format_powershell_runtime_note()} "
            f"{wsl} Prefer native PowerShell/curl.exe for HTTP API skills; "
            "for .sh scripts use absolute paths under the skills directory."
        ).strip()
    return (
        "Unix/macOS: bash() runs /bin/bash. Run scripts from the skill directory "
        "with required env vars set."
    )


def format_windows_sh_guidance(skill_dir: Path, scripts: list[Path]) -> str:
    """Guidance when a skill ships shell scripts on Windows."""
    if not is_windows() or not scripts:
        return ""
    names = ", ".join(s.name for s in scripts[:3])
    extra = f" (+{len(scripts) - 3} more)" if len(scripts) > 3 else ""
    lines: list[str] = []
    ps1 = skill_dir / "run.ps1"
    if ps1.is_file():
        ps_name = Path(resolve_powershell_executable()).name
        lines.extend([
            f"Windows entrypoint: {ps_name} -NoProfile -File \"{ps1}\" <subcommand> <args>",
            "Set env vars from SKILL.md (e.g. DISCORD_BOT_TOKEN) before running.",
        ])
        try:
            body = ps1.read_text(encoding="utf-8", errors="replace")
            if "/mnt/c" in body or "/mnt/" in body:
                lines.append(
                    "If run.ps1 references /mnt/c/... from PowerShell it will fail. "
                    "Use wsl bash -lc from the skill folder, or Invoke-RestMethod "
                    "with subcommands documented in SKILL.md."
                )
        except Exception:
            pass
    lines.extend([
        f"Skill includes shell script(s): {names}{extra}.",
        "On Windows choose one approach (in order):",
        "  (a) Use run.ps1 / discord-admin.sh subcommands from SKILL.md when available.",
        "  (b) REST/API from PowerShell: Invoke-RestMethod with Bot <token> + DiscordBot User-Agent.",
        "  (c) Optional WSL only if the skill is .sh-only — install jq/curl inside WSL, not winget on Windows.",
    ])
    if wsl_available():
        lines.append(
            "  WSL example: wsl bash -lc 'cd /mnt/c/.../skills/<slug> && "
            "export DISCORD_BOT_TOKEN=... && ./discord-admin.sh channel-list <guildId>'"
        )
    else:
        lines.append(
            "  WSL is not installed — do not assume apt/Linux paths; use run.ps1 or REST."
        )
    lines.append(f"Always use absolute skill path: {skill_dir}")
    return "\n".join(lines)


def extract_missing_bin_from_output(output: str) -> str | None:
    """Best-effort bin name from a failed command's stderr/stdout."""
    if not output:
        return None
    for pattern in _MISSING_BIN_OUTPUT_RES:
        m = pattern.search(output)
        if m:
            # Last group may be the bin name depending on pattern
            for g in reversed(m.groups()):
                if g and g.lower() not in ("line", "command"):
                    return g.lower()
    return None


def format_missing_bin_hint(bin_name: str, command: str) -> str:
    install = format_bin_install_hint(bin_name)
    lines = [
        f"[SHELL HINT] Missing CLI `{bin_name}` for this command.",
        f"  • {install}",
    ]
    if is_windows():
        if "bash" in command.lower() and (".sh" in command or "/mnt/" in command):
            lines.append(
                "  • Windows PATH (scoop/winget) does not apply inside WSL bash — "
                "install the tool in the same environment you run the script from."
            )
        elif ".sh" in command:
            lines.append(
                "  • .sh scripts on Windows: prefer PowerShell/curl.exe, a local .ps1 wrapper, "
                "or WSL with deps installed in WSL."
            )
    return "\n".join(lines)


_HTTP_SHELL_CMD_RE = re.compile(
    r"Invoke-RestMethod|Invoke-WebRequest|curl\.exe|\bcurl\b|wget\.exe|\bwget\b",
    re.I,
)
_JSON_API_ERROR_RE = re.compile(
    r'\{\s*"(?:message|error|code)"\s*:',
    re.I,
)
_URL_HOST_RE = re.compile(r"https?://([^/\s'\"<>]+)", re.I)
_CLOUDFLARE_BLOCK_RES = (
    re.compile(r'"code"\s*:\s*40333\b'),
    re.compile(r"internal network error", re.I),
    re.compile(r"cloudflare", re.I),
    re.compile(r"cf-ray", re.I),
)

# Optional enrichments when output matches a known API — base hint stays generic.
_API_ERROR_ENRICHMENTS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"discord\.com", re.I),
        "Discord bot REST: Authorization header is `Bot <token>` (not Bearer). "
        "User-Agent should identify your bot, e.g. "
        "DiscordBot (https://your-project-url, 1.0).",
    ),
    (
        re.compile(r"Cannot send an empty message|\"code\"\s*:\s*50006", re.I),
        "Discord POST /channels/{id}/messages requires `embeds` (array), not "
        "`embed`: `{\"embeds\":[{...}]}`. Or include non-empty `content`. "
        "Use Get-Content -Raw file.json | Invoke-RestMethod -Body $_ — do not "
        "double-encode JSON.",
    ),
    (
        re.compile(r"api\.github\.com", re.I),
        "GitHub REST: use Authorization: Bearer <PAT> or `gh api` with gh auth login.",
    ),
    (
        re.compile(r"api\.stripe\.com", re.I),
        "Stripe REST: use Authorization: Bearer <secret_key> — see SKILL.md for test vs live keys.",
    ),
]


def _extract_http_host(output: str, command: str) -> str | None:
    for text in (command, output):
        m = _URL_HOST_RE.search(text or "")
        if m:
            return m.group(1).lower()
    return None


def _looks_like_http_api_failure(output: str, command: str) -> bool:
    if not output:
        return False
    combined = f"{output}\n{command}"
    if not _HTTP_SHELL_CMD_RE.search(combined):
        return False
    if "Invoke-RestMethod :" in output or "Invoke-WebRequest :" in output:
        return True
    if _JSON_API_ERROR_RE.search(output):
        return True
    if any(p.search(output) for p in _CLOUDFLARE_BLOCK_RES):
        return True
    if re.search(r"\b40[0134]\b", output) and _JSON_API_ERROR_RE.search(output):
        return True
    return False


def _generic_user_agent() -> str:
    return "BaboAgent/1.0 (+https://github.com/umbecanessa/babo)"


def _format_generic_http_api_hint(host: str | None, output: str = "") -> str:
    ua = _generic_user_agent()
    host_note = f" for `{host}`" if host else ""
    lines = [
        "[API HINT] HTTP/API call failed in shell output"
        f"{host_note}. On Windows, PowerShell often sends a browser-like User-Agent "
        "that some APIs (Cloudflare-protected) reject — this is not a Babo network outage.",
        "  • Set auth headers exactly as SKILL.md specifies (Bearer, Bot, ApiKey, etc.).",
        "  • Retry with an explicit non-browser User-Agent, e.g.:",
        f'    curl.exe -H "User-Agent: {ua}" -H "Authorization: <per SKILL.md>" <url>',
        f"  • Or Invoke-RestMethod -Headers @{{ Authorization='...'; "
        f"User-Agent='{ua}' }} -Uri <url>",
        "  • If one endpoint works but another fails, compare headers/path — "
        "auth scope or missing permissions are common.",
    ]
    probe = f"{host or ''}\n{output}"
    for pattern, extra in _API_ERROR_ENRICHMENTS:
        if pattern.search(probe):
            lines.append(f"  • {extra}")
    return "\n".join(lines)


def format_http_api_error_hints(output: str, command: str) -> str | None:
    """Hints for HTTP API failures in shell output (any host, not Discord-only)."""
    if not _looks_like_http_api_failure(output, command):
        return None
    host = _extract_http_host(output, command)
    return _format_generic_http_api_hint(host, output)


def looks_like_http_api_shell_failure(output: str, command: str) -> bool:
    return _looks_like_http_api_failure(output, command)


_SHELL_FAILURE_RES = (
    re.compile(r"bash:\s*-c:\s*line\s+\d+:\s*syntax error", re.I),
    re.compile(r"is not recognized as the name of a cmdlet", re.I),
    re.compile(r"is not recognized as an internal or external command", re.I),
    re.compile(r"/bin/bash:\s*line\s+\d+:\s*\w+:\s*command not found", re.I),
    re.compile(r"RemoteException", re.I),
)


def looks_like_shell_command_failure(output: str, command: str) -> bool:
    """True when output shows failure despite exit code 0 (PowerShell/WSL quirks)."""
    if not output:
        return False
    if looks_like_http_api_shell_failure(output, command):
        return True
    if any(p.search(output) for p in _SHELL_FAILURE_RES):
        if "syntax error" in output.lower():
            return True
        if "not recognized" in output.lower():
            return True
        if "command not found" in output.lower():
            return True
        if "RemoteException" in output and (
            "Invoke-RestMethod" in command or "Invoke-WebRequest" in command
        ):
            return True
    if re.search(r"\bAt line:\d+", output, re.I):
        return True
    if "Cannot index into a null array" in output:
        return True
    if re.search(r"\bAt\s+[A-Za-z]:\\", output) and (
        "Unexpected token" in output
        or "ParserError" in output
        or "FullyQualifiedErrorId" in output
    ):
        return True
    return False
