"""skill_install — Install and activate a native NLS skill from the agent workspace.

Copies a skill package from ``agents/{id}/workspace/`` into ``data/skills/{name}/``,
reloads it into ``SkillLoader``, and enables it for the current agent.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)

_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_SKIP_DIR_NAMES = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", "dist", "build",
})


def _validate_skill_name(name: str) -> str | None:
    slug = (name or "").strip().lower()
    if not slug or not _SKILL_NAME_RE.match(slug):
        return (
            "Invalid skill name — use lowercase letters, digits, hyphens "
            "(e.g. 'discord-channel')."
        )
    return None


def _detect_skill_format(skill_dir: Path) -> str | None:
    has_init = (skill_dir / "__init__.py").is_file()
    has_md = (skill_dir / "SKILL.md").is_file()
    if has_init and has_md:
        return "hybrid"
    if has_init:
        return "native"
    if has_md:
        return "agentskill"
    return None


def _resolve_workspace_path(workspace: Path, source_path: str) -> tuple[Path | None, str | None]:
    raw = (source_path or "").strip()
    if not raw:
        return None, "source_path is required (folder under workspace with __init__.py)."
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (workspace / candidate).resolve()
    else:
        candidate = candidate.resolve()
    try:
        candidate.relative_to(workspace.resolve())
    except ValueError:
        return None, (
            f"source_path must be inside the agent workspace ({workspace})."
        )
    if not candidate.is_dir():
        return None, f"Not a directory: {candidate}"
    return candidate, None


def _copy_skill_tree(src: Path, dest: Path) -> None:
    preserved: dict[str, bytes] = {}
    bridge_data = dest / "bridge-data"
    if bridge_data.is_dir():
        for item in bridge_data.rglob("*"):
            if item.is_file():
                rel = item.relative_to(dest)
                preserved[str(rel).replace("\\", "/")] = item.read_bytes()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(src)
        if any(part in _SKIP_DIR_NAMES for part in rel.parts):
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
    for rel, data in preserved.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


async def _broadcast_skill_installed(agent_id: str, skill_name: str) -> None:
    """Notify connected clients so the Tools tab refreshes."""
    if not agent_id:
        return
    try:
        from server.main import app as _app

        cm = getattr(_app.state, "connection_manager", None)
        if cm is None:
            return
        await cm.broadcast(agent_id, {
            "type": "skill_installed",
            "skill": skill_name,
            "slug": skill_name,
        })
    except Exception:
        logger.debug(
            "skill_installed broadcast failed for %s",
            skill_name,
            exc_info=True,
        )


class SkillInstallTool:
    """Install a workspace skill package into data/skills and activate it."""

    def __init__(
        self,
        *,
        workspace: str,
        data_dir: str,
        agent_id: str,
    ) -> None:
        self._workspace = Path(workspace)
        self._data_dir = Path(data_dir) if data_dir else Path("data")
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "skill_install"

    @property
    def description(self) -> str:
        return (
            "Install and activate a native NLS skill from the agent workspace into "
            "the runtime skills folder (data/skills/). Copies the package, reloads "
            "it into SkillLoader, and enables it for this agent. Use when a skill "
            "under workspace/ is ready (__init__.py with meta + register). "
            "For ClawHub packages use clawhub(action='install') instead."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source_path": {
                    "type": "string",
                    "description": (
                        "Folder under workspace containing the skill package "
                        "(must have __init__.py and/or SKILL.md). "
                        "Example: 'discord-channel' or 'babo-discord-bot/discord-channel'."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Skill slug in data/skills/ (default: last segment of source_path)."
                    ),
                },
                "enable": {
                    "type": "boolean",
                    "description": (
                        "Enable for this agent after install (default true)."
                    ),
                },
            },
            "required": ["source_path"],
        }

    def _get_runtime(self) -> Any:
        try:
            from server.main import app as _app

            am = getattr(_app.state, "agent_manager", None)
            if am is None:
                return None
            return am.get_loaded_runtimes().get(self._agent_id)
        except Exception:
            return None

    def _get_skill_loader(self) -> Any:
        try:
            from server.main import app as _app

            return getattr(_app.state, "skill_loader", None)
        except Exception:
            return None

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        source_path = (params.get("source_path") or "").strip()
        skill_name = (params.get("name") or "").strip().lower()
        enable = params.get("enable", True)

        src, src_err = _resolve_workspace_path(self._workspace, source_path)
        if src_err or src is None:
            return ToolResult(content=f"Error: {src_err}", is_error=True)

        if not skill_name:
            skill_name = src.name.lower()

        name_err = _validate_skill_name(skill_name)
        if name_err:
            return ToolResult(content=f"Error: {name_err}", is_error=True)

        fmt = _detect_skill_format(src)
        if fmt is None:
            return ToolResult(
                content=(
                    f"Error: {src} is not a skill package — need __init__.py "
                    "(native NLS plugin) and/or SKILL.md."
                ),
                is_error=True,
            )

        skills_dir = self._data_dir / "skills"
        dest = skills_dir / skill_name
        try:
            _copy_skill_tree(src, dest)
            creator = dest / ".creator"
            creator.write_text(self._agent_id, encoding="utf-8")
        except OSError as exc:
            return ToolResult(
                content=f"Error copying skill to {dest}: {exc}",
                is_error=True,
            )

        sl = self._get_skill_loader()
        if sl is None:
            return ToolResult(
                content=(
                    f"Copied skill to {dest}, but SkillLoader is unavailable. "
                    "Call request_restart(reason='Install skill') to load it."
                ),
            )

        try:
            if fmt == "agentskill":
                await sl._load_agentskill(dest, source="local")
                loaded = sl.skills.get(skill_name)
            else:
                loaded = await sl.reload_skill(skill_name)
        except Exception as exc:
            from nls.skills_setup_policy import format_skill_load_error_message

            return ToolResult(
                content=format_skill_load_error_message(
                    skill_name, str(exc), dest=dest,
                ),
                is_error=True,
            )

        if loaded is None or loaded.status != "loaded":
            err = getattr(loaded, "error", None) or "unknown load error"
            from nls.skills_setup_policy import format_skill_load_error_message

            return ToolResult(
                content=format_skill_load_error_message(
                    skill_name, str(err), dest=dest,
                ),
                is_error=True,
            )

        enabled_msg = ""
        if enable:
            runtime = self._get_runtime()
            if runtime is not None:
                runtime.enable_skill(skill_name)
                enabled_msg = f"\nEnabled for this agent ({self._agent_id})."
            else:
                enabled_msg = (
                    "\nCould not enable automatically — toggle the skill in "
                    "Tools or retry after the agent runtime is loaded."
                )

        await _broadcast_skill_installed(self._agent_id, skill_name)

        cfg_hint = ""
        if getattr(loaded.meta, "config_schema", None):
            cfg_hint = (
                f"\nNext: skill_configure(skill_name='{skill_name}', config={{...}}) "
                "for credentials."
            )

        deps_msg = ""
        if (dest / "requirements.txt").is_file():
            deps_msg = "\nDependencies installed from requirements.txt."

        return ToolResult(
            content=(
                f"Installed and loaded NLS skill '{skill_name}' from "
                f"{source_path} → {dest} (format={fmt})."
                f"{deps_msg}{enabled_msg}{cfg_hint}"
            ),
            details={
                "skill_name": skill_name,
                "source_path": source_path,
                "dest_path": str(dest),
                "format": fmt,
            },
        )


def create_skill_install_tool(
    *,
    workspace: str,
    data_dir: str,
    agent_id: str,
) -> SkillInstallTool:
    return SkillInstallTool(
        workspace=workspace,
        data_dir=data_dir,
        agent_id=agent_id,
    )
