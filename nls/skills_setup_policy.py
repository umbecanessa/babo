"""Shared policy for NLS bundled skills vs ClawHub/AgentSkill instruction packages.

Bundled Python skills use ``config_schema`` + ``skill_configure``.
AgentSkill / ClawHub packages use ``SKILL.md`` + bash/read/write — never
``skill_configure``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from nls.platform_shell import (
    find_shell_scripts,
    format_bin_install_hint,
    format_env_var_step,
    format_platform_shell_note,
    format_windows_sh_guidance,
    infer_requires_bins_from_scripts,
    is_windows,
)

_QUICK_START_RE = re.compile(
    r"(?:^|\n)##?\s*Quick\s+Start\b(.*?)(?=\n##?\s|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_SETUP_HEADING_RE = re.compile(
    r"(?:^|\n)##?\s*(?:Setup|Auth|Authentication|Configuration|Getting Started)"
    r"(.*?)(?=\n##?\s|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_BASH_FENCE_RE = re.compile(
    r"```(?:bash|sh|shell)?\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_EXPORT_ENV_RE = re.compile(
    r"export\s+([A-Z][A-Z0-9_]*)=",
    re.IGNORECASE,
)


def resolve_data_skills_dir() -> Path | None:
    """Writable skills directory (``{NLS_DATA_DIR}/skills``)."""
    data_dir = os.environ.get("NLS_DATA_DIR", "").strip()
    if data_dir:
        return Path(data_dir) / "skills"
    try:
        from server.main import app

        settings = getattr(app.state, "settings", None)
        if settings is not None:
            return Path(settings.data_dir) / "skills"
    except Exception:
        pass
    return None


_SKIP_SKILL_SCAN_DIR_NAMES = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", "dist", "build", "browser_profile",
})


def resolve_runtime_data_dir(*, fallback: str | Path | None = None) -> Path:
    """Canonical writable Babo data root (``NLS_DATA_DIR`` or server settings)."""
    env_dir = os.environ.get("NLS_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    try:
        from server.main import app

        settings = getattr(app.state, "settings", None)
        if settings is not None:
            return Path(settings.data_dir)
    except Exception:
        pass
    if fallback:
        return Path(fallback)
    return Path("data")


def is_skill_package_dir(path: Path) -> bool:
    """True when ``path`` looks like an installable skill package."""
    if not path.is_dir():
        return False
    return (path / "__init__.py").is_file() or (path / "SKILL.md").is_file()


def iter_skill_package_dirs(root: Path) -> list[Path]:
    """Find skill packages under a scan root (``data/skills/`` or agent workspace)."""
    if not root.is_dir():
        return []
    found: list[Path] = []

    def _maybe_add(entry: Path) -> None:
        if not entry.is_dir() or entry.name in _SKIP_SKILL_SCAN_DIR_NAMES:
            return
        if is_skill_package_dir(entry):
            found.append(entry)

    nested = root / "skills"
    if nested.is_dir():
        for entry in sorted(nested.iterdir()):
            _maybe_add(entry)

    for entry in sorted(root.iterdir()):
        if entry.name == "skills":
            continue
        _maybe_add(entry)
    return found


def resolve_skill_scan_roots(
    *,
    data_dir: str | Path | None = None,
    workspace: str | Path | None = None,
) -> list[tuple[Path, str]]:
    """Return ``(directory, source_label)`` pairs for restart/skill discovery."""
    roots: list[tuple[Path, str]] = []
    seen: set[str] = set()

    def _add(path: Path | None, label: str) -> None:
        if path is None or not path.is_dir():
            return
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            return
        roots.append((path, label))
        seen.add(key)

    _add(resolve_data_skills_dir(), "data")
    runtime_data = resolve_runtime_data_dir(fallback=data_dir)
    _add(runtime_data / "skills", "data")
    if workspace:
        _add(Path(workspace), "workspace")
    return roots


def skill_package_mtime(path: Path) -> float:
    """Newest mtime of the skill package marker files."""
    best = 0.0
    for name in ("__init__.py", "SKILL.md"):
        f = path / name
        if f.is_file():
            try:
                best = max(best, f.stat().st_mtime)
            except OSError:
                pass
    return best


def pick_skill_package_candidate(
    name: str,
    candidates: list[tuple[Path, str]],
) -> tuple[Path, str] | None:
    """Choose the best on-disk copy when the same slug appears in multiple roots."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    by_source: dict[str, Path] = {}
    for path, source in candidates:
        by_source[source] = path

    data_path = by_source.get("data")
    workspace_path = by_source.get("workspace")
    if data_path is not None and workspace_path is not None:
        if skill_package_mtime(workspace_path) > skill_package_mtime(data_path):
            return workspace_path, "workspace_newer"
        return data_path, "data"
    return candidates[0]


def pre_shipped_channel_skills() -> frozenset[str]:
    """Channel plugins shipped with Babo and configured via skill_configure."""
    from nls.tools.skill_manager import PRE_SHIPPED_CHANNEL_SKILLS

    return PRE_SHIPPED_CHANNEL_SKILLS


def is_pre_shipped_channel_skill(skill_name: str) -> bool:
    return skill_name.strip().lower() in pre_shipped_channel_skills()


def infer_pre_shipped_channel_skill(text: str) -> str | None:
    """Map user text to a pre-shipped channel skill, or None."""
    low = (text or "").lower()
    if "whatsapp" in low:
        return "whatsapp-channel"
    if "telegram" in low:
        return "telegram-channel"
    if "discord" in low:
        return "discord-channel"
    if "slack" in low:
        return "slack-channel"
    if "email" in low and "gmail" not in low:
        return "email-channel"
    platform = infer_channel_platform(text)
    if platform in ("telegram", "whatsapp", "discord", "slack"):
        return f"{platform}-channel"
    return None


def format_skill_load_error_message(
    skill_name: str,
    err: str,
    *,
    dest: Path | None = None,
) -> str:
    """Actionable skill_install reload failure text for the agent."""
    lines = [
        f"Skill '{skill_name}' did not load.",
        f"Error: {err}",
    ]
    if dest is not None:
        lines.insert(1, f"Install path: {dest}")
    low = (err or "").lower()
    if "cannot import name 'router'" in low or (
        "router" in low and "import" in low
    ):
        lines.extend([
            "",
            "Loader contract: webhook.py MUST export module-level "
            "`router = APIRouter(...)` because register() imports "
            "`from .webhook import router` or uses webhook.router.",
            "Fix webhook.py — do not rewrite the whole skill in a loop.",
        ])
    if "nonetype" in low and "await" in low:
        lines.append(
            "Startup hook must be `async def startup()` (or return nothing "
            "from sync startup) — the loader invokes lifecycle hooks safely "
            "but sync hooks that spawn tasks should use async def."
        )
    if "register" in low and "import" in low:
        lines.append(
            "Check __init__.py: export SkillMeta as `meta` and "
            "`def register(app, ctx)` with no import errors in submodules."
        )
    if "_load_known_senders" in low:
        lines.append(
            "Adapter calls _load_known_senders() but the method is missing — "
            "copy it from nls/skills/bundled/telegram-channel/adapter.py "
            "(or iterate self._agent_configs in startup like Telegram)."
        )
    if "list_agent_ids" in low:
        lines.append(
            "Use ctx.load_all_agent_configs().keys() or self._agent_configs "
            "in startup — not ctx.list_agent_ids() unless you know SkillContext "
            "provides it."
        )
    if "startup hook failed" in low or "startup" in low and "hook" in low:
        lines.append(
            "Startup hook failed — fix adapter.startup() before request_restart; "
            "the skill will show loaded in UI but will not connect to Discord."
        )
    lines.extend([
        "",
        "Fix the skill under workspace/, then retry skill_install. "
        "Do not call task_complete until status=loaded.",
    ])
    return "\n".join(lines)


def skill_dir(slug: str) -> Path | None:
    """Installed skill directory, or None if not on disk."""
    base = resolve_data_skills_dir()
    if base is None:
        return None
    path = base / slug
    return path if path.is_dir() else None


def skill_md_path(slug: str) -> Path | None:
    """Path to ``SKILL.md`` when the skill is installed."""
    sd = skill_dir(slug)
    if sd is None:
        return None
    md = sd / "SKILL.md"
    return md if md.is_file() else None


def is_instruction_only_skill(meta: Any) -> bool:
    """True for ClawHub / AgentSkill packages (SKILL.md), not bundled NLS plugins."""
    if meta is None:
        return False
    schema = getattr(meta, "config_schema", None) or []
    if schema:
        return False
    skill_type = getattr(meta, "skill_type", "") or ""
    source = getattr(meta, "source", "") or ""
    if skill_type == "agentskill":
        return True
    if source == "clawhub":
        return True
    instructions = getattr(meta, "instructions", None)
    return bool(instructions and skill_type in ("agentskill", "hybrid"))


def looks_like_instruction_skill(skill_name: str, meta: Any | None = None) -> bool:
    """True when skill is instruction-based (meta and/or SKILL.md on disk)."""
    if is_instruction_only_skill(meta):
        return True
    if not skill_name:
        return False
    if meta is not None and getattr(meta, "config_schema", None):
        return False
    return skill_md_path(skill_name) is not None


def instruction_skill_setup_hint(skill_name: str, skill_path: Path | None = None) -> str:
    """Actionable setup guidance for any instruction-only skill."""
    sd = skill_path or skill_dir(skill_name)
    lines = [
        f"Skill '{skill_name}' is an instruction package (ClawHub/AgentSkill) — "
        f"not a bundled NLS skill with config_schema.",
        "Do NOT use skill_configure for this skill.",
        "Setup: read() its SKILL.md, set required env vars, execute via bash().",
        format_platform_shell_note(),
    ]
    if sd is not None:
        lines.append(f"Start with: read(path='{sd / 'SKILL.md'}')")
        lines.append(f"Skill directory (use absolute paths): {sd}")
    else:
        md = skill_md_path(skill_name)
        if md is not None:
            lines.append(f"Start with: read(path='{md}')")
    base = resolve_data_skills_dir()
    if base is not None and sd is None:
        lines.append(f"Installed skills directory: {base}")
    sh_scripts = find_shell_scripts(sd)
    if sh_scripts and sd is not None:
        sh_note = format_windows_sh_guidance(sd, sh_scripts)
        if sh_note:
            lines.append(sh_note)
    return "\n".join(lines)


def format_activation_steps(meta: Any, slug: str, skill_path: Path | None = None) -> str:
    """Build a scalable post-install checklist from skill metadata + SKILL.md."""
    steps: list[str] = []
    step_num = 1

    sd = skill_path or skill_dir(slug)
    md = (sd / "SKILL.md") if sd is not None else None
    if md is not None and md.is_file():
        steps.append(f"{step_num}. Read instructions: read(path='{md}')")
        step_num += 1
    elif base := resolve_data_skills_dir():
        steps.append(
            f"{step_num}. Skill files live under {base / slug}/ — read SKILL.md first"
        )
        step_num += 1

    if sd is not None:
        steps.append(f"{step_num}. Platform: {format_platform_shell_note()}")
        step_num += 1
        if is_windows() and sd.is_dir():
            steps.append(
                f"{step_num}. Use absolute skill path {sd} — "
                "never ./data/skills/ relative to agent workspace"
            )
            step_num += 1

    sh_scripts = find_shell_scripts(sd)
    if sh_scripts and sd is not None:
        for line in format_windows_sh_guidance(sd, sh_scripts).split("\n"):
            steps.append(f"{step_num}. {line}")
            step_num += 1

    requires_env = list(getattr(meta, "requires_env", None) or [])
    for var in requires_env[:5]:
        steps.append(format_env_var_step(var, step_num))
        step_num += 1

    instructions = getattr(meta, "instructions", None) or ""
    if instructions and not requires_env:
        for var in _EXPORT_ENV_RE.findall(instructions)[:4]:
            if var not in requires_env:
                steps.append(format_env_var_step(var, step_num))
                step_num += 1

    requires_bins = list(getattr(meta, "requires_bins", None) or [])
    inferred = infer_requires_bins_from_scripts(sd)
    for b in inferred:
        if b not in requires_bins:
            requires_bins.append(b)

    for bin_name in requires_bins[:5]:
        steps.append(
            f"{step_num}. Verify CLI `{bin_name}`: bash `{bin_name} --version` — "
            f"{format_bin_install_hint(bin_name)}"
        )
        step_num += 1

    quick = _extract_section(instructions, _QUICK_START_RE)
    if quick:
        bash_blocks = _BASH_FENCE_RE.findall(quick)
        if bash_blocks:
            first_cmd = bash_blocks[0].strip().splitlines()[0][:100]
            if is_windows() and first_cmd.strip().startswith("./"):
                script_name = first_cmd.strip().lstrip("./").split()[0]
                if sd is not None:
                    first_cmd = (
                        f"# Windows: read {script_name} and use PowerShell/curl.exe, "
                        f"a .ps1 wrapper, or WSL — see SKILL.md"
                    )
            steps.append(f"{step_num}. Follow Quick Start (example): {first_cmd}")
            step_num += 1

    if step_num == 1:
        setup = _extract_section(instructions, _SETUP_HEADING_RE)
        if setup:
            first_line = setup.strip().split("\n")[0][:120]
            steps.append(f"{step_num}. Setup: {first_line}")
            step_num += 1

    smoke = (
        "Verify with a smoke test from SKILL.md (do NOT use skill_configure)"
    )
    if is_windows():
        smoke += (
            ". REST/API smoke tests: use curl.exe or Invoke-RestMethod with auth headers "
            "from SKILL.md and an explicit non-browser User-Agent (PowerShell defaults "
            "to a browser UA that some APIs block)."
        )
    steps.append(f"{step_num}. {smoke}")
    return "\n".join(steps)


def _extract_section(instructions: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(instructions or "")
    return match.group(1).strip()[:800] if match else ""


def _instruction_skill_post_read_extra(
    slug: str,
    skill_path: Path | None = None,
) -> list[str]:
    """Skill-specific next steps after SKILL.md — generic first, API-specific second."""
    lines = [
        "",
        "[NEXT STEPS — do not re-read SKILL.md]",
    ]
    if is_windows():
        json_note = ""
        if skill_path is not None and any(skill_path.glob("*.json")):
            json_note = (
                " Payloads are already in *.json under the skill dir — load with "
                "json.loads(Path(...).read_text(encoding='utf-8')); do not rebuild embeds in PowerShell."
            )
        lines.extend([
            "1. Windows API/REST setup: write deploy-*.py beside the skill (httpx + JSON files on disk), "
            f"then bash('python <absolute-skill-path>/deploy-*.py').{json_note} "
            "Prefer this over inline PowerShell JSON, jq, or upstream .sh on Windows.",
            "2. PowerShell (bash()): thin shell only — env checks, curl.exe one-liners, run.ps1 if shipped.",
            "3. ASCII in scripts; use $env:VAR — never hardcode tokens; never curl.exe.exe.",
            "4. Do not loop on WSL/jq diagnostics — execute or verify with Python + read() of results.",
        ])
        step = 5
    else:
        lines.extend([
            "1. Use skill CLI subcommands from SKILL.md (run.ps1 / .sh) — "
            "not ad-hoc REST unless SKILL.md says so.",
            "2. Post JSON via a file; match the API schema exactly.",
            "3. Prefer $env:VAR over hardcoded secrets.",
        ])
        step = 4
    if "discord" in slug.lower():
        lines.extend([
            f"{step}. Discord: Authorization `Bot <token>`; User-Agent DiscordBot (...).",
            f"{step + 1}. channel-create: POST /guilds/{{id}}/channels. "
            "Messages: `embeds` (array), not singular `embed`.",
        ])
    return lines


def instruction_skill_post_read_nudge(skill_md_path: str) -> str | None:
    """Action nudge after SKILL.md was read — steer off raw REST/WSL jq loops."""
    if not skill_md_path:
        return None
    p = Path(skill_md_path)
    if p.name.lower() != "skill.md":
        return None
    slug = p.parent.name
    if not slug:
        return None
    base = instruction_skill_setup_hint(slug, p.parent)
    return base + "\n" + "\n".join(_instruction_skill_post_read_extra(slug, p.parent))


def build_configure_bundled_setup_lines(
    *,
    skill_name: str,
    channel_platform: str | None = None,
    enabled: bool | None = None,
) -> list[str]:
    """System lines injected at loop start for setup:configure_bundled turns."""
    platform = (channel_platform or infer_channel_platform(skill_name) or "").strip()
    lines = [
        "[CONFIGURE BUNDLED SKILL] The server already ships this plugin — "
        "use skill_configure, NOT native skill authoring or skill_install from scratch.",
        f"First: skill_configure(skill_name='{skill_name}') to inspect missing fields.",
        "Then: skill_configure(skill_name='...', config={{bot_token: '...', ...}}) "
        "with values the user provided in chat.",
        "Do NOT scaffold nls/skills/bundled/, rebuild __init__.py, or read cleanup-backup "
        "folders under data/ as the active install path.",
    ]
    if enabled is False:
        lines.append(
            f"'{skill_name}' is loaded on the server but NOT enabled for this agent — "
            "configure it, then enable via skill manager / enabled_skills.json."
        )
    if platform:
        lines.append(
            f"Active {platform} channel reference: {babo_bundled_skill_github_ref(skill_name)} · "
            f"{CHANNEL_INTEGRATION_DOCS_URL}"
        )
    return lines


def bundled_skill_ring_guidance(
    skill_name: str,
    description: str,
    *,
    enabled: bool,
    config_schema: list[Any] | None = None,
    agent_installed: bool = False,
    configured: bool = False,
) -> tuple[str, str]:
    """Cryptex skills-ring slot content for a configurable channel/native skill."""
    if is_pre_shipped_channel_skill(skill_name) and configured:
        kind = "Pre-shipped channel skill"
        action = (
            "already configured and connected for this agent — use channel send "
            "tools and squad tools; do not skill_configure or ask for bot token"
        )
    elif is_pre_shipped_channel_skill(skill_name):
        kind = "Pre-shipped channel skill"
        action = f"skill_configure(skill_name='{skill_name}')"
    elif agent_installed:
        kind = "Agent-installed native skill"
        action = (
            f"skill_configure(skill_name='{skill_name}') after skill_install loads; "
            "webhook.py must export module-level router"
        )
    else:
        kind = "Configurable native skill"
        action = f"skill_configure(skill_name='{skill_name}')"
    if configured:
        status = "connected"
    elif enabled:
        status = "enabled"
    else:
        status = "NOT enabled for this agent"
    headline = f"{skill_name}: {description} [{status}]"
    schema_keys = [
        getattr(f, "key", None) or (f.get("key") if isinstance(f, dict) else None)
        for f in (config_schema or [])
    ]
    schema_keys = [k for k in schema_keys if k]
    detail_lines = [
        f"{kind} — {action}.",
    ]
    if agent_installed and not is_pre_shipped_channel_skill(skill_name):
        detail_lines.append(
            "Built via workspace + skill_install — not a pre-shipped Babo channel."
        )
    elif is_pre_shipped_channel_skill(skill_name):
        detail_lines.append(
            "Pre-shipped Babo channel (telegram/whatsapp/email/discord/slack) "
            "— do not skill_install from scratch."
        )
    if schema_keys:
        detail_lines.append(f"Schema fields: {', '.join(schema_keys[:8])}")
    if not enabled:
        detail_lines.append(
            "Enable this skill for the agent after configuration succeeds."
        )
    return headline, "\n".join(detail_lines)


def build_instruction_skill_setup_lines(
    skills_base: Path,
    *,
    read_index: Any | None = None,
) -> list[str]:
    """System lines injected at loop start for setup:instruction_skill turns."""
    lines = [
        "[INSTRUCTION SKILL SETUP] ClawHub/AgentSkill packages live at "
        f"{skills_base}. Run setup via bash/read/write after reading "
        "SKILL.md once. Do NOT use skill_configure (bundled NLS channel "
        "skills with config_schema only). Do NOT use contacts() for "
        "Discord/CLI instruction skills.",
        "When you need a bot token, API key, or password from the user: "
        "call ask_user() and stop — do NOT ask in prose-only text during "
        "the loop. Never invent or assume credentials the user has not "
        "provided in this conversation.",
        "ClawHub/OpenClaw/AgentSkill packages are OK to search, read, and use "
        "for one-shot API or setup docs. For always-on inbound messages "
        "(live moderator, listen/respond when tagged), build a native bundled "
        f"channel plugin per {CHANNEL_INTEGRATION_DOCS_URL} (reference "
        f"{babo_bundled_skill_github_ref('telegram-channel')}) — not a standalone "
        "discord.py project or OpenClaw gateway.",
        "After reading SKILL.md: execute skill subcommands (run.ps1 / .sh) — "
        "do not loop on jq/WSL/curl.exe.exe diagnostics.",
    ]
    if is_windows():
        lines.append(
            "Windows: for API/REST skills (Discord, GitHub, etc.), prefer a deploy-*.py "
            "script (httpx + JSON files in the skill dir) over inline PowerShell or .sh."
        )
    if read_index is not None:
        _already: list[str] = []
        _skills_prefix = str(skills_base).replace("\\", "/").lower()
        for ent in read_index.list_entries():
            ep = (ent.path or "").replace("\\", "/").lower()
            if _skills_prefix in ep and ep.endswith("/skill.md"):
                _already.append(
                    f"  - {ent.path} (read @ {ent.ts[:19]}) — proceed with bash/setup"
                )
        if _already:
            lines.append(
                "Already loaded SKILL.md (use read(force=true) only if changed):"
            )
            lines.extend(_already[:8])
    if is_windows() and skills_base.is_dir():
        for sd in sorted(skills_base.iterdir()):
            if not sd.is_dir() or not (sd / "SKILL.md").is_file():
                continue
            sh = find_shell_scripts(sd)
            if not sh:
                continue
            note = format_windows_sh_guidance(sd, sh)
            if note:
                lines.append(f"[{sd.name}] {note.split(chr(10))[0]}")
    return lines


def lookup_skill_meta(skill_name: str) -> tuple[Any | None, Path | None]:
    """Resolve loaded skill metadata and install path from the skill loader."""
    try:
        from server.main import app

        sl = getattr(app.state, "skill_loader", None)
        if sl is None:
            return None, skill_dir(skill_name)
        sk = sl.skills.get(skill_name)
        if sk is None:
            return None, skill_dir(skill_name)
        return sk.meta, sk.path
    except Exception:
        return None, skill_dir(skill_name)


def skill_configure_absorption_content(
    skill_name: str,
    result_str: str,
    *,
    is_error: bool,
) -> str | None:
    """Return skills-ring content for skill_configure, or None to skip absorption."""
    if not skill_name:
        return None
    meta, path = lookup_skill_meta(skill_name)
    if looks_like_instruction_skill(skill_name, meta):
        return instruction_skill_setup_hint(skill_name, path)
    if is_error and "no config_schema" in (result_str or "").lower():
        return (
            f"skill_configure is for bundled NLS channel skills only. "
            f"'{skill_name}' has no config_schema — check if it is an instruction skill."
        )
    if is_error:
        return None
    return None


PLATFORM_DOCS_URL = "https://babo.agency/"
PLATFORM_DOCS_GETTING_STARTED = "https://babo.agency/getting-started/"
NATIVE_SKILL_DOCS_SLUG = "extension/add-bundled-skill"
NATIVE_SKILL_DOCS_URL = f"{PLATFORM_DOCS_URL}{NATIVE_SKILL_DOCS_SLUG}/"
CHANNEL_INTEGRATION_DOCS_SLUG = "extension/add-channel-integration"
CHANNEL_INTEGRATION_DOCS_URL = f"{PLATFORM_DOCS_URL}{CHANNEL_INTEGRATION_DOCS_SLUG}/"
BABO_GITHUB_REPO_URL = "https://github.com/umbecanessa/babo"
BABO_GITHUB_DEFAULT_BRANCH = "main"
BABO_BUNDLED_SKILLS_GITHUB_TREE = (
    f"{BABO_GITHUB_REPO_URL}/tree/{BABO_GITHUB_DEFAULT_BRANCH}/nls/skills/bundled"
)


def babo_github_tree_path(repo_relative_path: str) -> str:
    """Browse URL for a path inside the public Babo repo."""
    slug = (repo_relative_path or "").strip().strip("/")
    if not slug:
        return BABO_GITHUB_REPO_URL
    return f"{BABO_GITHUB_REPO_URL}/tree/{BABO_GITHUB_DEFAULT_BRANCH}/{slug}"


def babo_github_raw_path(repo_relative_path: str) -> str:
    """Raw file URL for web_fetch/read when the repo is not on disk."""
    slug = (repo_relative_path or "").strip().strip("/")
    return (
        f"https://raw.githubusercontent.com/umbecanessa/babo/"
        f"{BABO_GITHUB_DEFAULT_BRANCH}/{slug}"
    )


def babo_bundled_skill_github_ref(skill_name: str) -> str:
    """Human + fetchable reference for a bundled skill example."""
    slug = (skill_name or "").strip().strip("/")
    tree = babo_github_tree_path(f"nls/skills/bundled/{slug}")
    init_py = babo_github_raw_path(f"nls/skills/bundled/{slug}/__init__.py")
    return f"{tree} (e.g. web_fetch {init_py})"


def platform_doc_url(path: str = "") -> str:
    """Absolute URL under the published Babo documentation site."""
    slug = (path or "").strip().strip("/")
    if not slug:
        return PLATFORM_DOCS_URL
    return f"{PLATFORM_DOCS_URL}{slug}/"

_NATIVE_SKILL_AUTHORING_RE = re.compile(
    r"\b(?:nls|native|bundled)\s+(?:python\s+)?skill\b"
    r"|\b(?:build|create|author|write|implement)\s+(?:a|an)\s+"
    r"(?:native|bundled|nls|live|discord|telegram|whatsapp)\s+"
    r"(?:python\s+)?(?:channel\s+)?skill\b"
    r"|\b(?:discord|telegram|whatsapp)-channel\b"
    r"|\blive\s+discord\b"
    r"|\bdiscord\s+gateway\b"
    r"|\bskill\s+(?:with|using)\s+(?:register|config\.schema|config_schema)\b"
    r"|\bnls/skills/bundled\b"
    r"|\bregister\s*\(\s*app\s*,\s*ctx\b",
    re.IGNORECASE,
)

_ACTIVE_CHANNEL_INTEGRATION_RE = re.compile(
    r"\b(?:always|active|live)\s+(?:reading|listening|monitor|moderat)"
    r"|\b(?:discord|telegram|whatsapp)\s+(?:moderator|mod\b)"
    r"|\bbecome\s+(?:an?\s+)?(?:active|live)\b"
    r"|\bmoderate\s+when\b"
    r"|\binteract\s+when\s+tagged\b"
    r"|\brespond\s+when\s+(?:tagged|mentioned)\b"
    r"|\bin\s+every\s+channel\b"
    r"|\bmessage\s+channel\b.*\b(?:listen|moderat)",
    re.IGNORECASE,
)


def looks_like_active_channel_integration(text: str) -> bool:
    """True when the user wants always-on inbound channel connectivity."""
    return bool(_ACTIVE_CHANNEL_INTEGRATION_RE.search(text or ""))


def infer_channel_platform(text: str) -> str | None:
    """Best-effort channel name from user text (discord, telegram, whatsapp)."""
    low = (text or "").lower()
    for name in ("discord", "telegram", "whatsapp", "slack"):
        if name in low:
            return name
    return None


def looks_like_native_skill_authoring(text: str) -> bool:
    """True when the user wants a native Python NLS skill, not ClawHub/SKILL.md only."""
    if looks_like_active_channel_integration(text):
        return True
    return bool(_NATIVE_SKILL_AUTHORING_RE.search(text or ""))


def native_skill_authoring_summary() -> str:
    """Compact native-skill contract for Cryptex / loop system lines."""
    return (
        "NATIVE NLS SKILL (Python plugin — NOT instruction-only SKILL.md):\n"
        "- Ship in repo: nls/skills/bundled/{skill-name}/\n"
        "- Per-agent override: data/skills/{skill-name}/ (same layout)\n"
        "- Required: __init__.py with SkillMeta + register(app, ctx)\n"
        "- Optional: SKILL.md (AgentSkill instructions), config.schema.json (Tools UI)\n"
        "- register(): ctx.register_tool_factory(...), ctx.include_router(...), "
        "ctx.on_startup(...)\n"
        "- Channel admin: implement adapter.manage_channel(agent_id, action, params) "
        "-> (ok, message), and/or ctx.register_channel_manage(channel_key, handler). "
        "Agents use channel_manage(channel=..., action=...) — never raw tokens.\n"
        "- Loader discovers by directory name on server start — no manual import list\n"
        f"- Platform docs: {PLATFORM_DOCS_GETTING_STARTED}\n"
        f"- Native skill guide: {NATIVE_SKILL_DOCS_URL}\n"
        f"- Public Babo source repo: {BABO_GITHUB_REPO_URL}\n"
        f"- Bundled skill examples (GitHub): {BABO_BUNDLED_SKILLS_GITHUB_TREE}\n"
        "Use web_fetch on docs URLs and GitHub raw URLs when unsure — bundled "
        "examples are usually NOT in the agent workspace (only if the user "
        "cloned Babo). Do not search the user's GitHub for Babo source.\n"
        "- Do NOT use skill_configure for greenfield authoring — that configures "
        "existing bundled channel skills. Use write/edit to scaffold files.\n"
        f"- Reference patterns from {babo_bundled_skill_github_ref('telegram-channel')} "
        "and sibling folders under nls/skills/bundled/.\n"
        "- ACTIVE CHANNEL (always-on listen/respond/moderate): build or extend a "
        "native bundled channel plugin (discord-channel, telegram-channel, etc.) — see "
        f"{CHANNEL_INTEGRATION_DOCS_URL}. ClawHub/OpenClaw skills are fine for "
        "research and one-shot API calls; do NOT use a standalone discord.py "
        "bot repo or OpenClaw gateway as the primary Babo integration path."
    )


def build_native_skill_setup_lines(
    *,
    channel_platform: str | None = None,
) -> list[str]:
    """System lines injected at loop start for setup:native_skill turns."""
    lines = [
        "[NATIVE SKILL AUTHORING] " + native_skill_authoring_summary().replace(
            "\n", " ",
        ),
        (
            "Workflow: web_fetch docs/GitHub examples, scaffold in workspace, "
            "verify imports, then skill_install(source_path='<folder>') to copy "
            f"into data/skills/, reload, and enable — no manual cp/restart. "
            f"Docs: {NATIVE_SKILL_DOCS_URL}"
        ),
    ]
    platform = (channel_platform or "").strip().lower()
    if platform:
        lines.append(
            f"[ACTIVE {platform.upper()} CHANNEL] Scaffold workspace/"
            f"{platform}-channel/ (adapter.py + __init__.py with register), "
            f"then skill_install(source_path='{platform}-channel'). "
            f"Channel guide: {CHANNEL_INTEGRATION_DOCS_URL}. "
            f"Reference {babo_bundled_skill_github_ref('telegram-channel')} "
            f"from {BABO_GITHUB_REPO_URL}."
        )
    return lines
