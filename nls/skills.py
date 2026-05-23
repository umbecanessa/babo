"""NLS Skill SDK -- API for agent-created runtime extensions.

Skills are self-contained Python packages that live in ``data/skills/``
and are discovered + loaded by the server at startup.  Each skill
exports a ``meta`` object and a ``register(app, ctx)`` function.

Example skill ``__init__.py``::

    from nls.skills import SkillMeta

    meta = SkillMeta(
        name="scheduler",
        version="1.0",
        description="Run tasks on a cron schedule",
        dependencies=["apscheduler"],
    )

    def register(app, ctx):
        from .service import Scheduler
        svc = Scheduler()
        ctx.on_startup(svc.start)
        ctx.on_shutdown(svc.stop)
        ctx.include_router(router, prefix="/skills/scheduler")
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine

from fastapi import APIRouter, FastAPI


@dataclass
class SkillMeta:
    """Metadata every skill must export as ``meta``."""

    name: str
    version: str = "0.1"
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    enabled: bool = True
    created_by: str = ""


class SkillContext:
    """API surface passed to a skill's ``register(app, ctx)`` function.

    Provides controlled hooks for adding routes, lifecycle callbacks,
    agent tools, and accessing a writable data directory.
    """

    def __init__(
        self,
        app: FastAPI,
        skill_name: str,
        skills_dir: Path,
    ) -> None:
        self._app = app
        self._skill_name = skill_name
        self._skills_dir = skills_dir
        self._startup_hooks: list[Callable[[], Coroutine]] = []
        self._shutdown_hooks: list[Callable[[], Coroutine]] = []
        self._tools: list[Any] = []
        self._routers: list[tuple[APIRouter, dict[str, Any]]] = []
        self.logger = logging.getLogger(f"nls.skill.{skill_name}")

    # ── Lifecycle hooks ────────────────────────────────────────

    def on_startup(self, coro: Callable[[], Coroutine]) -> None:
        """Register an async function to run when the server starts."""
        self._startup_hooks.append(coro)

    def on_shutdown(self, coro: Callable[[], Coroutine]) -> None:
        """Register an async function to run when the server shuts down."""
        self._shutdown_hooks.append(coro)

    # ── Routes ─────────────────────────────────────────────────

    def include_router(
        self,
        router: APIRouter,
        prefix: str = "",
        **kwargs: Any,
    ) -> None:
        """Mount a FastAPI router.  Prefix is auto-namespaced if not given."""
        if not prefix:
            prefix = f"/skills/{self._skill_name}"
        self._routers.append((router, {"prefix": prefix, **kwargs}))

    # ── Agent tools ────────────────────────────────────────────

    def register_tool(self, tool: Any) -> None:
        """Register an agent tool instance (must follow the AgentTool protocol)."""
        self._tools.append(tool)

    # ── Data directory ─────────────────────────────────────────

    def get_data_dir(self) -> Path:
        """Return a writable directory for this skill's persistent data."""
        d = self._skills_dir / self._skill_name / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Configuration ──────────────────────────────────────────

    @property
    def _config_path(self) -> Path:
        return self._skills_dir / self._skill_name / "config.json"

    def load_config(self, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
        """Load config.json, merging with *defaults* for missing keys."""
        cfg: dict[str, Any] = dict(defaults) if defaults else {}
        if self._config_path.exists():
            try:
                saved = json.loads(
                    self._config_path.read_text(encoding="utf-8"),
                )
                cfg.update(saved)
            except Exception as exc:
                self.logger.warning("Failed to read config.json: %s", exc)
        return cfg

    def save_config(self, data: dict[str, Any]) -> None:
        """Persist *data* to config.json."""
        self._config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Internal accessors (used by SkillLoader) ───────────────

    @property
    def startup_hooks(self) -> list[Callable[[], Coroutine]]:
        return self._startup_hooks

    @property
    def shutdown_hooks(self) -> list[Callable[[], Coroutine]]:
        return self._shutdown_hooks

    @property
    def tools(self) -> list[Any]:
        return self._tools

    @property
    def routers(self) -> list[tuple[APIRouter, dict[str, Any]]]:
        return self._routers
