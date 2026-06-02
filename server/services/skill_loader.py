"""Skill Loader -- Discover, validate, and load agent-created skills.

Scans ``{data_dir}/skills/`` for skill packages at startup.  Supports
two formats:

* **Native** -- directory with ``__init__.py`` exporting ``meta``
  (SkillMeta) and ``register(app, ctx)``.
* **AgentSkill** -- directory with ``SKILL.md`` (YAML frontmatter +
  instructions).  No Python code; instructions are injected into the
  agent's system prompt.
* **Hybrid** -- both ``__init__.py`` and ``SKILL.md`` present.

Bad skills are sandboxed: import or register errors are logged but
never crash the server.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from nls.skills import SkillBridge, SkillContext, SkillMeta, SkillOnboarding, SkillPoller, SkillSchedule
from nls.skills.agentskill_parser import (
    AgentSkillInfo,
    ParseError,
    check_gating,
    parse_skill_md,
    validate_skill_info,
)

logger = logging.getLogger(__name__)

_DISABLED_FILE = ".disabled"


class LoadedSkill:
    """Runtime representation of a loaded (or failed) skill."""

    def __init__(
        self,
        name: str,
        path: Path,
        meta: SkillMeta | None = None,
        context: SkillContext | None = None,
        error: str | None = None,
    ) -> None:
        self.name = name
        self.path = path
        self.meta = meta
        self.context = context
        self.error = error

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        if self.meta and not self.meta.enabled:
            return "disabled"
        return "loaded"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "path": str(self.path),
        }
        if self.meta:
            d["version"] = self.meta.version
            d["description"] = self.meta.description
            d["dependencies"] = self.meta.dependencies
            d["enabled"] = self.meta.enabled
            d["created_by"] = self.meta.created_by
            d["skill_type"] = self.meta.skill_type
            d["source"] = self.meta.source
            d["license"] = self.meta.license
            d["homepage"] = self.meta.homepage
            d["clawhub_slug"] = self.meta.clawhub_slug
            d["crystallized_from"] = self.meta.crystallized_from
            if self.meta.config_schema:
                d["config_schema"] = [f.to_dict() for f in self.meta.config_schema]
        if self.error:
            d["error"] = self.error
        if self.context:
            d["tools"] = [t.name for t in self.context.tools]
            d["routes"] = [kw.get("prefix", "") for _, kw in self.context.routers]
        return d


class BridgeManager:
    """Manages sidecar (bridge) processes declared by skills."""

    def __init__(self) -> None:
        self._processes: dict[str, subprocess.Popen] = {}
        self._log_files: dict[str, Any] = {}
        self._health_task: asyncio.Task | None = None

    async def start_bridges(self, skill_name: str, skill_dir: Path, bridges: list[SkillBridge]) -> None:
        for bridge in bridges:
            key = f"{skill_name}:{bridge.name}"
            if key in self._processes:
                proc = self._processes[key]
                if proc.poll() is None:
                    continue
                logger.warning("Bridge '%s' exited (code=%s), restarting", key, proc.returncode)

            entry = skill_dir / bridge.entry
            if not entry.exists():
                logger.warning("Bridge '%s' entry point not found: %s", key, entry)
                continue

            bridge_dir = entry.parent

            if bridge.runtime == "node":
                self._ensure_node_deps(key, bridge_dir)

            env = {**self._node_env(), **bridge.env}
            env["BRIDGE_PORT"] = str(bridge.port)

            if not env.get("WEBHOOK_URL"):
                server_port = os.environ.get("PORT", "9222")
                env["WEBHOOK_URL"] = (
                    f"http://127.0.0.1:{server_port}"
                    f"/skills/{skill_name}/webhook/{{agent_id}}"
                )

            # Persist bridge data (auth state etc.) in NLS_DATA_DIR so it
            # survives app rebuilds / updates.  Falls back to skill_dir/data.
            data_dir = os.environ.get("NLS_DATA_DIR", "")
            if data_dir:
                bridge_data = Path(data_dir) / "skills" / skill_name / "bridge-data"
                bridge_data.mkdir(parents=True, exist_ok=True)
                env.setdefault("AUTH_DIR", str(bridge_data / "baileys-auth"))

            runtime_cmd = self._resolve_runtime(bridge.runtime)
            if not runtime_cmd:
                logger.warning("Bridge '%s': runtime '%s' not found", key, bridge.runtime)
                continue

            if data_dir:
                log_path = Path(data_dir) / "skills" / skill_name / f"{bridge.name}.log"
            else:
                log_path = skill_dir / "data" / f"{bridge.name}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                log_fh = open(log_path, "a", encoding="utf-8")
                self._log_files[key] = log_fh
                proc = subprocess.Popen(
                    [runtime_cmd, str(entry)],
                    cwd=str(bridge_dir),
                    env=env,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                )
                self._processes[key] = proc
                logger.info(
                    "Bridge '%s' started (pid=%d, port=%d, webhook=%s, auth=%s, log=%s)",
                    key, proc.pid, bridge.port,
                    env.get("WEBHOOK_URL", "?"),
                    env.get("AUTH_DIR", "?"),
                    log_path,
                )
            except Exception as exc:
                logger.error("Failed to start bridge '%s': %s", key, exc)

    async def wait_healthy(
        self,
        bridges: list[SkillBridge],
        timeout: float = 30.0,
        interval: float = 1.0,
    ) -> None:
        """Wait until all bridges respond on their health endpoint."""
        deadline = asyncio.get_event_loop().time() + timeout
        for bridge in bridges:
            if bridge.port <= 0 or not bridge.health_check:
                continue
            url = f"http://127.0.0.1:{bridge.port}{bridge.health_check}"
            while asyncio.get_event_loop().time() < deadline:
                try:
                    ok = await asyncio.to_thread(self._probe_health, url)
                    if ok:
                        logger.info("Bridge '%s' healthy on port %d", bridge.name, bridge.port)
                        break
                except Exception:
                    pass
                await asyncio.sleep(interval)
            else:
                logger.warning(
                    "Bridge '%s' did not become healthy within %.0fs",
                    bridge.name, timeout,
                )

    @staticmethod
    def _probe_health(url: str) -> bool:
        import urllib.request
        resp = urllib.request.urlopen(url, timeout=2)
        return resp.status == 200

    @staticmethod
    def _node_env() -> dict[str, str]:
        """Return an env dict with the standalone Node bin dir on PATH."""
        env = {**os.environ}
        node_bin = os.environ.get("NLS_NODE_BIN")
        if node_bin:
            node_dir = str(Path(node_bin).parent)
            env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
        return env

    def _ensure_node_deps(self, key: str, bridge_dir: Path) -> None:
        """Run ``npm install`` if ``package.json`` exists but ``node_modules`` does not."""
        pkg_json = bridge_dir / "package.json"
        node_modules = bridge_dir / "node_modules"

        if not pkg_json.exists():
            return
        if node_modules.is_dir():
            return

        npm_cmd = os.environ.get("NLS_NPM_BIN") or shutil.which("npm")
        if not npm_cmd:
            logger.warning(
                "Bridge '%s': npm not found, cannot install deps for %s",
                key, bridge_dir,
            )
            return

        logger.info("Bridge '%s': installing Node.js dependencies in %s", key, bridge_dir)
        try:
            subprocess.check_call(
                [npm_cmd, "install", "--production", "--no-audit", "--no-fund"],
                cwd=str(bridge_dir),
                env=self._node_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            logger.info("Bridge '%s': npm install completed", key)
        except subprocess.TimeoutExpired:
            logger.error("Bridge '%s': npm install timed out", key)
        except subprocess.CalledProcessError as exc:
            logger.error("Bridge '%s': npm install failed (exit %d)", key, exc.returncode)

    def _resolve_runtime(self, runtime: str) -> str | None:
        if runtime == "node":
            return os.environ.get("NLS_NODE_BIN") or shutil.which("node")
        if runtime == "python":
            return sys.executable
        if runtime == "binary":
            return None
        return shutil.which(runtime)

    async def stop_all(self) -> None:
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
        for key, proc in self._processes.items():
            try:
                proc.terminate()
                proc.wait(timeout=5)
                logger.info("Bridge '%s' stopped", key)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._processes.clear()
        for fh in self._log_files.values():
            try:
                fh.close()
            except Exception:
                pass
        self._log_files.clear()

    def is_running(self, skill_name: str, bridge_name: str) -> bool:
        key = f"{skill_name}:{bridge_name}"
        proc = self._processes.get(key)
        return proc is not None and proc.poll() is None


class SkillLoader:
    """Discovers and loads skills from a directory.

    Supports two scan paths:

    * **bundled_dir** -- read-only skills shipped with the codebase
      (``nls/skills/bundled/``).  Always present, version-controlled.
    * **skills_dir** -- writable, agent-created skills
      (``data/skills/``).  Agent-created skills with the same name
      as a bundled skill take precedence (override / fork pattern).
    """

    def __init__(
        self,
        skills_dir: Path,
        app: FastAPI,
        bundled_dir: Path | None = None,
    ) -> None:
        self._skills_dir = skills_dir
        self._bundled_dir = bundled_dir
        self._app = app
        self._skills: dict[str, LoadedSkill] = {}
        self._bridge_manager = BridgeManager()
        self._skills_dir.mkdir(parents=True, exist_ok=True)

    @property
    def skills(self) -> dict[str, LoadedSkill]:
        return self._skills

    @property
    def all_tools(self) -> list[Any]:
        """All agent tools registered by loaded skills."""
        tools: list[Any] = []
        for sk in self._skills.values():
            if sk.context and sk.status == "loaded":
                tools.extend(sk.context.tools)
        return tools

    def tools_for(self, skill_names: list[str]) -> list[Any]:
        """Return shared (non-agent-specific) tools from the specified skills.

        If *skill_names* is ``["*"]``, returns all tools (backward compat).

        For per-agent tools, use ``tool_factories_for()`` instead.
        """
        if skill_names == ["*"]:
            return self.all_tools
        tools: list[Any] = []
        for name in skill_names:
            sk = self._skills.get(name)
            if sk and sk.context and sk.status == "loaded":
                tools.extend(sk.context.tools)
        return tools

    def tool_factories_for(self, skill_names: list[str]) -> list[Any]:
        """Return tool factories ``(agent_id) -> AgentTool`` for the named skills.

        Each factory is called once per agent runtime so that channel
        tools get their own instance with the correct agent_id bound.
        """
        if skill_names == ["*"]:
            factories: list[Any] = []
            for sk in self._skills.values():
                if sk.context and sk.status == "loaded":
                    factories.extend(sk.context.tool_factories)
            return factories
        factories = []
        for name in skill_names:
            sk = self._skills.get(name)
            if sk and sk.context and sk.status == "loaded":
                factories.extend(sk.context.tool_factories)
        return factories

    def instructions_for(
        self, skill_names: list[str]
    ) -> list[tuple[str, str, str]]:
        """Return ``(name, description, instructions)`` for AgentSkill/hybrid skills.

        Only returns skills that have non-empty instructions.
        Appends setup/install hints when required binaries are missing,
        with platform-specific guidance based on the detected OS.
        """
        import platform
        import shutil

        os_name = platform.system().lower()  # "windows", "darwin", "linux"

        result: list[tuple[str, str, str]] = []
        for sk in self._skills.values():
            if sk.status != "loaded":
                continue
            if not sk.meta or not sk.meta.instructions:
                continue
            if skill_names != ["*"] and sk.name not in skill_names:
                continue

            instructions = sk.meta.instructions
            missing_bins = [
                b for b in (sk.meta.requires_bins or [])
                if not shutil.which(b)
            ]
            if missing_bins:
                install_lines = [
                    f"\n\n## ⚠ Setup Required — YOU MUST DO THIS (do NOT ask the user)\n",
                    f"**Platform**: {platform.system()} ({platform.machine()})",
                    f"**Missing binaries**: {', '.join(missing_bins)}",
                    "",
                    "**INSTALL THEM NOW using bash.** Do NOT ask the user to install them. "
                    "Do NOT web-search for installation guides. Follow these steps:",
                    "",
                ]

                has_platform_method = False
                for inst in (sk.meta.install_instructions or []):
                    label = inst.get("label", inst.get("id", "install"))
                    kind = inst.get("kind", "")
                    if kind == "brew" and os_name == "darwin":
                        formula = inst.get("formula", "")
                        install_lines.append(f"- `brew install {formula}`")
                        has_platform_method = True
                    elif kind == "brew" and os_name != "darwin":
                        pass
                    elif kind == "npm":
                        pkg = inst.get("package", inst.get("formula", ""))
                        install_lines.append(f"- `npm install -g {pkg}`")
                        has_platform_method = True
                    elif kind == "go":
                        pkg = inst.get("package", "")
                        install_lines.append(f"- `go install {pkg}`")
                        has_platform_method = True
                    else:
                        install_lines.append(f"- {label}")
                        has_platform_method = True

                if not has_platform_method:
                    if os_name == "windows":
                        install_lines.append(
                            "No direct installer listed for Windows. Try IN THIS ORDER:"
                        )
                        for b in missing_bins:
                            install_lines.append(f"1. `winget install {b}` (try first)")
                            install_lines.append(f"2. `scoop install {b}` (if winget fails)")
                            install_lines.append(
                                f"3. Check GitHub Releases page for a Windows .exe or .zip"
                            )
                        if sk.meta.homepage:
                            install_lines.append(
                                f"4. Visit {sk.meta.homepage} → look for Releases / Downloads"
                            )
                        install_lines.append(
                            "\nAfter downloading a binary, move it to a directory on PATH "
                            "or add its directory to PATH."
                        )
                    elif os_name == "darwin":
                        for b in missing_bins:
                            install_lines.append(f"- `brew install {b}` (try first)")
                    else:
                        for b in missing_bins:
                            install_lines.append(
                                f"- Try: `apt install {b}` or `snap install {b}` "
                                f"or check the project homepage"
                            )

                if sk.meta.homepage:
                    install_lines.append(f"\nProject homepage: {sk.meta.homepage}")

                install_lines.append(
                    "\n**After installing**: verify with `where <binary>` (Windows) "
                    "or `which <binary>` (macOS/Linux), then proceed with the skill."
                )

                instructions = instructions + "\n".join(install_lines)

            result.append((
                sk.meta.name,
                sk.meta.description,
                instructions,
            ))
        return result

    @staticmethod
    def get_skill_domain(skill_name: str, description: str = "") -> str:
        """Infer domain area from skill name/description for cryptex ring placement."""
        text = (skill_name + " " + description).lower()
        _DOMAIN_KEYWORDS: dict[str, list[str]] = {
            "frontend": ["react", "vue", "angular", "css", "tailwind", "ui", "frontend", "component", "browser"],
            "backend": ["api", "fastapi", "django", "flask", "rest", "graphql", "backend", "server", "database", "sql"],
            "devops": ["docker", "kubernetes", "ci", "cd", "deploy", "terraform", "ansible", "infrastructure", "devops"],
            "data": ["data", "pandas", "numpy", "ml", "machine learning", "analytics", "etl", "pipeline"],
            "communication": ["email", "whatsapp", "telegram", "slack", "sms", "notification", "message"],
        }
        for domain, keywords in _DOMAIN_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return domain
        return "general"

    def cli_wrappers_for(
        self,
        skill_names: list[str],
        calibrator: Any = None,
        cwd: str | Path = ".",
        on_setup_needed: Any = None,
    ) -> list[Any]:
        """Create CLI wrapper tools for enabled skills that declare ``requires_bins``.

        Returns a list of ``SkillCLIWrapperTool`` instances — one per
        primary binary declared by a matching skill.

        Parameters
        ----------
        on_setup_needed : async callable, optional
            ``(skill_name, bin_name, install_cmd, setup_notes) -> ToolResult | None``
            Called when the binary is missing to attempt auto-installation.
        """
        from nls.tools.agent_tools.skill_cli_wrapper import SkillCLIWrapperTool

        wrappers: list[Any] = []
        for sk in self._skills.values():
            if sk.status != "loaded":
                continue
            if not sk.meta or not sk.meta.requires_bins:
                continue
            if not sk.meta.instructions:
                continue
            if skill_names != ["*"] and sk.name not in skill_names:
                continue

            primary_bin = sk.meta.requires_bins[0]
            setup_notes = self._extract_setup_section(sk.meta.instructions)

            wrapper = SkillCLIWrapperTool(
                skill_name=sk.name,
                bin_name=primary_bin,
                description=(
                    sk.meta.description
                    or f"CLI tool for the '{sk.name}' skill."
                ),
                instructions=sk.meta.instructions,
                install_instructions=sk.meta.install_instructions or [],
                setup_notes=setup_notes,
                cwd=cwd,
                calibrator=calibrator,
                on_setup_needed=on_setup_needed,
            )
            wrappers.append(wrapper)
            logger.info(
                "CLI wrapper created for skill '%s' -> binary '%s'",
                sk.name, primary_bin,
            )
        return wrappers

    def cli_wrapped_skill_names(self, skill_names: list[str]) -> frozenset[str]:
        """Return the set of skill names that would get CLI wrappers."""
        names: set[str] = set()
        for sk in self._skills.values():
            if sk.status != "loaded":
                continue
            if not sk.meta or not sk.meta.requires_bins:
                continue
            if not sk.meta.instructions:
                continue
            if skill_names != ["*"] and sk.name not in skill_names:
                continue
            names.add(sk.name)
        return frozenset(names)

    def get_activation_steps(self, slug: str) -> str:
        """Generate a structured activation checklist for a skill.

        Returns human-readable steps derived from skill metadata, SKILL.md
        Quick Start, and install path (scalable for any AgentSkill/ClawHub pkg).
        """
        from nls.skills_setup_policy import format_activation_steps

        sk = self._skills.get(slug)
        if not sk or not sk.meta:
            return ""

        return format_activation_steps(sk.meta, slug, sk.path)

    @staticmethod
    def _extract_setup_section(instructions: str) -> str:
        """Pull out the Setup/Auth section from skill instructions."""
        import re as _re

        match = _re.search(
            r"(?:^|\n)##?\s*(?:Setup|Auth|Authentication|Configuration|Getting Started)"
            r"(.*?)(?=\n##?\s|\Z)",
            instructions,
            _re.DOTALL | _re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()[:600]
        return ""

    @staticmethod
    def _best_install_command(meta: Any, os_name: str) -> str:
        """Return the best install command for the current platform."""
        for inst in (meta.install_instructions or []):
            kind = inst.get("kind", "")
            if kind == "brew" and os_name == "darwin":
                formula = inst.get("formula", "")
                tap = inst.get("tap", "")
                if tap:
                    return f"brew install {tap}/{formula}" if formula else f"brew tap {tap}"
                return f"brew install {formula}"
            elif kind == "npm":
                return f"npm install -g {inst.get('package', '')}"
            elif kind == "go":
                return f"go install {inst.get('package', '')}@latest"
            elif kind == "pip":
                return f"pip install {inst.get('package', '')}"

        if meta.requires_bins:
            primary = meta.requires_bins[0]
            if os_name == "darwin":
                return f"brew install {primary}"
            elif os_name == "linux":
                return f"apt install {primary}"
        return ""

    # ── Loading ────────────────────────────────────────────────

    async def load_all(self) -> None:
        """Scan both bundled and user skill directories.

        User skills (``data/skills/``) override bundled skills
        (``nls/skills/bundled/``) with the same name.

        Supports three formats per directory:
        - ``__init__.py`` only  -> native Python skill
        - ``SKILL.md`` only    -> AgentSkill (instruction-based)
        - both present         -> hybrid
        """
        candidates: dict[str, tuple[Path, str]] = {}

        if self._bundled_dir and self._bundled_dir.is_dir():
            for entry in sorted(self._bundled_dir.iterdir()):
                if not entry.is_dir():
                    continue
                fmt = self._detect_format(entry)
                if fmt:
                    candidates[entry.name] = (entry, "bundled")

        if self._skills_dir.is_dir():
            for entry in sorted(self._skills_dir.iterdir()):
                if not entry.is_dir():
                    continue
                fmt = self._detect_format(entry)
                if fmt:
                    source = "clawhub" if (entry / ".clawhub").exists() else "local"
                    candidates[entry.name] = (entry, source)

        for _name, (skill_dir, source) in candidates.items():
            fmt = self._detect_format(skill_dir)
            if fmt == "hybrid":
                await self._load_hybrid(skill_dir, source)
            elif fmt == "agentskill":
                await self._load_agentskill(skill_dir, source)
            else:
                await self._load_skill(skill_dir, source)

        loaded = [s.name for s in self._skills.values() if s.status == "loaded"]
        failed = [s.name for s in self._skills.values() if s.status == "error"]
        disabled = [s.name for s in self._skills.values() if s.status == "disabled"]
        agentskills = [s.name for s in self._skills.values()
                       if s.meta and s.meta.skill_type == "agentskill" and s.status == "loaded"]

        logger.info(
            "Skills loaded: %d ok (%d agentskill), %d failed, %d disabled — %s",
            len(loaded), len(agentskills), len(failed), len(disabled),
            ", ".join(loaded) or "(none)",
        )

    @staticmethod
    def _detect_format(skill_dir: Path) -> str | None:
        """Detect the skill format from directory contents.

        Returns ``"native"``, ``"agentskill"``, ``"hybrid"``, or ``None``.
        """
        has_init = (skill_dir / "__init__.py").exists()
        has_skillmd = (skill_dir / "SKILL.md").exists()
        if has_init and has_skillmd:
            return "hybrid"
        if has_init:
            return "native"
        if has_skillmd:
            return "agentskill"
        return None

    async def _load_skill(self, skill_dir: Path, source: str = "local") -> None:
        """Load a native Python skill (``__init__.py`` with ``meta`` + ``register``)."""
        name = skill_dir.name

        if (skill_dir / _DISABLED_FILE).exists():
            self._skills[name] = LoadedSkill(
                name=name,
                path=skill_dir,
                meta=SkillMeta(name=name, enabled=False, source=source),
            )
            logger.info("Skill '%s' is disabled, skipping", name)
            return

        try:
            self._install_deps(skill_dir)
            module = self._import_skill(skill_dir)
            meta = getattr(module, "meta", None)
            if not isinstance(meta, SkillMeta):
                raise ValueError(
                    f"Skill '{name}' must export a SkillMeta instance as 'meta'"
                )
            meta.name = name
            meta.skill_type = "native"
            meta.source = source

            creator_file = skill_dir / ".creator"
            if creator_file.exists():
                try:
                    meta.created_by = creator_file.read_text(encoding="utf-8").strip()
                except Exception as exc:
                    logger.debug("Could not read .creator for '%s': %s", name, exc)

            register_fn = getattr(module, "register", None)
            if not callable(register_fn):
                raise ValueError(
                    f"Skill '{name}' must export a register(app, ctx) function"
                )

            ctx = SkillContext(self._app, name, self._skills_dir)
            register_fn(self._app, ctx)

            for router, kwargs in ctx.routers:
                self._app.include_router(router, **kwargs)

            self._skills[name] = LoadedSkill(
                name=name, path=skill_dir, meta=meta, context=ctx,
            )
            logger.info(
                "Skill '%s' v%s loaded (native/%s): %d tools, %d factories, %d routes",
                name, meta.version, source,
                len(ctx.tools), len(ctx.tool_factories), len(ctx.routers),
            )

        except Exception as exc:
            logger.error("Failed to load skill '%s': %s", name, exc, exc_info=True)
            fallback_meta = self._read_meta_safe(skill_dir, name)
            fallback_meta.source = source
            self._skills[name] = LoadedSkill(
                name=name, path=skill_dir,
                meta=fallback_meta, error=str(exc),
            )

    async def _load_agentskill(self, skill_dir: Path, source: str = "local") -> None:
        """Load an AgentSkill (``SKILL.md`` with YAML frontmatter, no Python code)."""
        name = skill_dir.name

        if (skill_dir / _DISABLED_FILE).exists():
            self._skills[name] = LoadedSkill(
                name=name,
                path=skill_dir,
                meta=SkillMeta(name=name, enabled=False, skill_type="agentskill", source=source),
            )
            logger.info("AgentSkill '%s' is disabled, skipping", name)
            return

        try:
            info = parse_skill_md(skill_dir / "SKILL.md")

            errors = validate_skill_info(info)
            if errors:
                raise ValueError(f"SKILL.md validation failed: {'; '.join(errors)}")

            gating = check_gating(info)
            if not gating.eligible:
                logger.info(
                    "AgentSkill '%s' has unmet requirements (soft gate): %s — "
                    "loading anyway so agent can install dependencies",
                    name, "; ".join(gating.reasons),
                )

            meta = self._agentskill_info_to_meta(info, name, source)

            if not meta.onboarding:
                meta.onboarding = self._auto_onboarding(info, name)

            self._skills[name] = LoadedSkill(name=name, path=skill_dir, meta=meta)
            logger.info(
                "AgentSkill '%s' v%s loaded (%s): %d chars of instructions, onboarding=%s",
                name, meta.version, source,
                len(meta.instructions or ""),
                meta.onboarding.setup_type if meta.onboarding else "none",
            )

        except Exception as exc:
            logger.error("Failed to load AgentSkill '%s': %s", name, exc, exc_info=True)
            self._skills[name] = LoadedSkill(
                name=name, path=skill_dir,
                meta=SkillMeta(name=name, skill_type="agentskill", source=source),
                error=str(exc),
            )

    async def _load_hybrid(self, skill_dir: Path, source: str = "local") -> None:
        """Load a hybrid skill (both ``__init__.py`` and ``SKILL.md``)."""
        await self._load_skill(skill_dir, source)

        sk = self._skills.get(skill_dir.name)
        if sk and sk.meta and sk.status == "loaded":
            try:
                info = parse_skill_md(skill_dir / "SKILL.md")
                sk.meta.skill_type = "hybrid"
                sk.meta.instructions = info.instructions or None
                if info.license:
                    sk.meta.license = info.license
                if info.homepage:
                    sk.meta.homepage = info.homepage
                logger.info(
                    "Skill '%s' enhanced with SKILL.md instructions (%d chars)",
                    sk.name, len(sk.meta.instructions or ""),
                )
            except ParseError as exc:
                logger.warning("Skill '%s' has SKILL.md but it failed to parse: %s", sk.name, exc)

    @staticmethod
    def _agentskill_info_to_meta(info: AgentSkillInfo, name: str, source: str) -> SkillMeta:
        """Convert parsed ``AgentSkillInfo`` to ``SkillMeta``."""
        return SkillMeta(
            name=name,
            version=info.version or "0.1",
            description=info.description,
            skill_type="agentskill",
            source=source,
            license=info.license,
            compatibility=info.compatibility,
            instructions=info.instructions or None,
            requires_bins=info.requires_bins,
            requires_env=info.requires_env,
            os_filter=info.os_filter,
            homepage=info.homepage,
            created_by=info.author,
            install_instructions=info.install_instructions,
        )

    @staticmethod
    def _auto_onboarding(info: AgentSkillInfo, name: str) -> SkillOnboarding | None:
        """Auto-generate onboarding for AgentSkills that need setup."""
        import platform

        needs_bins = bool(info.requires_bins)
        needs_env = bool(info.requires_env)
        needs_setup = needs_bins or needs_env

        if not needs_setup:
            return None

        os_name = platform.system()   # "Windows", "Darwin", "Linux"
        os_lower = os_name.lower()

        parts: list[str] = []
        if info.requires_bins:
            parts.append(f"Required CLI tools: {', '.join(info.requires_bins)}")
        if info.requires_env:
            parts.append(f"Required credentials/env vars: {', '.join(info.requires_env)}")

        install_hints: list[str] = []
        for inst in info.install_instructions:
            label = inst.get("label", inst.get("id", ""))
            kind = inst.get("kind", "")
            if kind == "brew" and os_lower == "darwin":
                install_hints.append(f"`brew install {inst.get('formula', '')}`")
            elif kind == "npm":
                install_hints.append(f"`npm install -g {inst.get('package', '')}`")
            elif kind == "go":
                install_hints.append(f"`go install {inst.get('package', '')}`")
            elif kind != "brew":
                install_hints.append(label)

        homepage = info.homepage or ""

        intro = (
            f"The **{info.name or name}** skill needs a few things set up before "
            f"it can work. I'll handle the installation — just sit back."
        )
        if info.description:
            intro = f"{info.description}\n\n{intro}"

        setup_prompt = (
            f"You are setting up the '{info.name or name}' community skill.\n"
            f"Platform: {os_name} ({platform.machine()})\n\n"
            f"Requirements:\n"
        )
        for p in parts:
            setup_prompt += f"- {p}\n"

        setup_prompt += (
            f"\n## CRITICAL RULES\n"
            f"- Do NOT ask the user 'would you like me to install this?'. JUST DO IT.\n"
            f"- Do NOT web-search for installation guides. The instructions are RIGHT HERE.\n"
            f"- Do NOT ask the user to install anything. YOU install it with bash.\n"
            f"- The user may be non-technical. Treat them like they've never seen a terminal.\n\n"
            f"## Step 1: Install missing binaries\n"
            f"Use bash to install. Check first with "
        )
        if os_lower == "windows":
            setup_prompt += "`where <binary>`.\n"
        else:
            setup_prompt += "`which <binary>`.\n"

        if install_hints:
            setup_prompt += f"Known install commands: {'; '.join(install_hints)}\n"

        if os_lower == "windows":
            setup_prompt += (
                "On Windows, try in order:\n"
                "1. `winget install <name>` (most common)\n"
                "2. `scoop install <name>` (if winget fails)\n"
                "3. Download from GitHub Releases (look for .exe or .zip for windows-amd64)\n"
            )
            if homepage:
                setup_prompt += f"4. Project homepage: {homepage}\n"
            setup_prompt += (
                "After downloading a binary, move it to a PATH directory or "
                "add its folder to PATH with: "
                "`$env:PATH += ';C:\\path\\to\\folder'`\n"
            )
        elif os_lower == "darwin":
            if not any("brew" in h for h in install_hints):
                for b in (info.requires_bins or []):
                    setup_prompt += f"- Try: `brew install {b}`\n"
            if homepage:
                setup_prompt += f"Project homepage: {homepage}\n"
        else:
            for b in (info.requires_bins or []):
                setup_prompt += f"- Try: `apt install {b}` or `snap install {b}`\n"
            if homepage:
                setup_prompt += f"Project homepage: {homepage}\n"

        setup_prompt += (
            f"\n## Step 2: Set up credentials (if needed)\n"
            f"If the skill needs API keys or OAuth credentials:\n"
            f"- Use your BROWSER tool to open the service's setup page.\n"
            f"- Walk the user through VISUALLY — tell them what to click, "
            f"take screenshots, guide them step by step.\n"
            f"- Do NOT just paste a URL and say 'go here'. Many users will get lost.\n"
            f"- For OAuth (Google, etc.): open the console IN THE BROWSER, "
            f"show them where to create credentials, download the JSON.\n\n"
            f"## Step 3: Verify\n"
            f"Run a quick test command to confirm the tool works.\n\n"
            f"## Step 4: Tell the user it's ready\n"
            f"Brief confirmation, then immediately proceed with their original request."
        )

        return SkillOnboarding(
            setup_type="conversational",
            intro_message=intro,
            setup_prompt=setup_prompt,
            completion_event=f"skill_{name}_ready",
        )

    def _import_skill(self, skill_dir: Path) -> Any:
        """Import a skill package by path."""
        name = skill_dir.name
        module_name = f"_nls_skill_{name}"

        if not str(self._skills_dir) in sys.path:
            sys.path.insert(0, str(self._skills_dir))

        spec = importlib.util.spec_from_file_location(
            module_name,
            skill_dir / "__init__.py",
            submodule_search_locations=[str(skill_dir)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for skill '{name}'")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _read_meta_safe(skill_dir: Path, name: str) -> SkillMeta:
        """Best-effort metadata extraction from __init__.py without importing."""
        import re

        meta = SkillMeta(name=name)
        try:
            text = (skill_dir / "__init__.py").read_text(encoding="utf-8")
            for field, attr in [("version", "version"), ("description", "description")]:
                m = re.search(rf'{field}\s*=\s*["\']([^"\']*)["\']', text)
                if m:
                    setattr(meta, attr, m.group(1))
            deps_m = re.search(r'dependencies\s*=\s*\[([^\]]*)\]', text)
            if deps_m:
                meta.dependencies = [
                    d.strip().strip("\"'")
                    for d in deps_m.group(1).split(",") if d.strip()
                ]
        except Exception as exc:
            logger.debug("Could not extract metadata for '%s': %s", name, exc)
        return meta

    def _install_deps(self, skill_dir: Path) -> None:
        """Auto-install requirements.txt if present."""
        req_file = skill_dir / "requirements.txt"
        if not req_file.exists():
            return

        logger.info("Installing deps for skill '%s'", skill_dir.name)
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            importlib.invalidate_caches()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to install dependencies for skill '{skill_dir.name}': {exc}"
            ) from exc

    # ── Lifecycle ──────────────────────────────────────────────

    async def run_startup_hooks(self) -> None:
        """Start bridges first, then run startup hooks from loaded skills.

        Also registers any pollers/schedules declared by skills with the
        SchedulerManager stored on ``app.state.scheduler_manager``.
        """
        for sk in self._skills.values():
            if sk.context and sk.status == "loaded":
                bridges: list[SkillBridge] = []
                if sk.meta and sk.meta.bridges:
                    bridges.extend(sk.meta.bridges)
                if sk.context.bridges:
                    bridges.extend(sk.context.bridges)
                if bridges:
                    await self._bridge_manager.start_bridges(sk.name, sk.path, bridges)
                    await self._bridge_manager.wait_healthy(bridges)

                for hook in sk.context.startup_hooks:
                    try:
                        await hook()
                    except Exception as exc:
                        logger.error(
                            "Startup hook failed for skill '%s': %s",
                            sk.name, exc, exc_info=True,
                        )

                self._register_skill_jobs(sk)

    def _register_skill_jobs(self, sk: LoadedSkill) -> None:
        """Register skill-declared pollers and schedules with the SchedulerManager."""
        mgr = getattr(self._app.state, "scheduler_manager", None)
        if mgr is None or not sk.context:
            return

        from nls.tools.agent_tools.scheduler import ScheduledJob

        for poller in sk.context.pollers:
            job_name = f"{sk.name}:{poller.name}"
            job = ScheduledJob(
                name=job_name,
                schedule_type="interval",
                interval_seconds=poller.interval_seconds,
                action="callback" if poller.callback else "http",
                action_url=poller.url,
                action_method=poller.method,
                action_headers=poller.headers,
                action_body=poller.body,
                owner=sk.name,
                enabled=True,
            )
            mgr.add_job(job)
            if poller.callback:
                mgr.register_callback(job_name, poller.callback)
            logger.info(
                "Skill '%s': registered poller '%s' (every %ds)",
                sk.name, poller.name, poller.interval_seconds,
            )

        for sched in sk.context.schedules:
            job_name = f"{sk.name}:{sched.name}"
            job = ScheduledJob(
                name=job_name,
                schedule_type=sched.schedule_type,
                interval_seconds=sched.interval_seconds,
                cron_expr=sched.cron_expr,
                action="callback" if sched.callback else "agent_message",
                owner=sk.name,
                enabled=True,
            )
            mgr.add_job(job)
            if sched.callback:
                mgr.register_callback(job_name, sched.callback)
            logger.info(
                "Skill '%s': registered schedule '%s' (%s)",
                sk.name, sched.name, sched.schedule_type,
            )

    async def run_shutdown_hooks(self) -> None:
        """Stop all bridges, then run shutdown hooks from loaded skills."""
        await self._bridge_manager.stop_all()
        for sk in self._skills.values():
            if sk.context and sk.status == "loaded":
                for hook in sk.context.shutdown_hooks:
                    try:
                        await hook()
                    except Exception as exc:
                        logger.error(
                            "Shutdown hook failed for skill '%s': %s",
                            sk.name, exc, exc_info=True,
                        )

    # ── Reload ─────────────────────────────────────────────────

    async def reload_skill(self, name: str) -> LoadedSkill:
        """Re-import a skill after its files have been edited.

        Purges the module (and any sub-modules) from ``sys.modules``
        so Python re-reads from disk, then calls the appropriate loader
        based on format detection.
        Returns the new ``LoadedSkill`` (check ``.status``).
        """
        skill_dir = self._skills_dir / name
        if not skill_dir.is_dir():
            raise FileNotFoundError(f"Skill directory not found: {name}")

        module_prefix = f"_nls_skill_{name}"
        stale = [k for k in sys.modules if k == module_prefix or k.startswith(module_prefix + ".")]
        for k in stale:
            del sys.modules[k]

        importlib.invalidate_caches()

        old = self._skills.pop(name, None)
        source = old.meta.source if old and old.meta else "local"
        fmt = self._detect_format(skill_dir)

        if fmt == "hybrid":
            await self._load_hybrid(skill_dir, source)
        elif fmt == "agentskill":
            await self._load_agentskill(skill_dir, source)
        else:
            await self._load_skill(skill_dir, source)

        sk = self._skills.get(name)
        if sk is None:
            raise RuntimeError(f"Skill '{name}' not found after reload")

        logger.info(
            "Skill '%s' reloaded — status=%s format=%s error=%s",
            name, sk.status, fmt or "native", sk.error or "(none)",
        )
        return sk

    # ── Management ─────────────────────────────────────────────

    def enable_skill(self, name: str) -> bool:
        skill_dir = self._skills_dir / name
        disabled_marker = skill_dir / _DISABLED_FILE
        if disabled_marker.exists():
            disabled_marker.unlink()
            return True
        return False

    def disable_skill(self, name: str) -> bool:
        skill_dir = self._skills_dir / name
        if skill_dir.is_dir():
            (skill_dir / _DISABLED_FILE).touch()
            return True
        return False

    def delete_skill(self, name: str) -> bool:
        skill_dir = self._skills_dir / name
        if skill_dir.is_dir():
            shutil.rmtree(skill_dir)
            self._skills.pop(name, None)
            return True
        return False

    def get_skill_files(self, name: str) -> list[dict[str, Any]]:
        """List all files in a skill directory with sizes."""
        skill_dir = self._skills_dir / name
        if not skill_dir.is_dir():
            return []
        files = []
        for f in sorted(skill_dir.rglob("*")):
            if f.is_file() and f.name != _DISABLED_FILE:
                files.append({
                    "path": str(f.relative_to(skill_dir)),
                    "size": f.stat().st_size,
                })
        return files

    def get_new_skills_summary(self) -> list[dict[str, Any]]:
        """Return info about skills that are new (not yet loaded by server).

        Used by request_restart to build skill review data.
        Detects both native (``__init__.py``) and AgentSkill (``SKILL.md``) formats.
        """
        summaries = []
        if not self._skills_dir.is_dir():
            return summaries

        for entry in sorted(self._skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            fmt = self._detect_format(entry)
            if not fmt:
                continue
            name = entry.name
            if name in self._skills:
                continue
            summary: dict[str, Any] = {
                "name": name,
                "skill_type": fmt,
                "files": self.get_skill_files(name),
                "file_count": len(list(entry.rglob("*"))),
            }
            req = entry / "requirements.txt"
            if req.exists():
                summary["dependencies"] = req.read_text().strip().splitlines()
            if fmt in ("agentskill", "hybrid"):
                try:
                    info = parse_skill_md(entry / "SKILL.md")
                    summary["description"] = info.description
                    summary["version"] = info.version
                except Exception as exc:
                    logger.debug("Could not parse SKILL.md for '%s': %s", entry.name, exc)
            summaries.append(summary)
        return summaries
