"""ClawHub agent tool -- autonomous skill discovery and installation.

Allows the agent to search, list, and install skills from the ClawHub
registry.  Calls go to the local Python server's ClawHub proxy endpoints
(``/api/clawhub/*``), which handle authentication and forward to the
external ClawHub API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any
from urllib.parse import quote

from .base import ToolResult

logger = logging.getLogger(__name__)


class ClawHubTool:
    """Agent tool for interacting with the ClawHub skill registry."""

    @property
    def name(self) -> str:
        return "clawhub"

    @property
    def description(self) -> str:
        return (
            "Search and install community skills from the ClawHub registry. "
            "Actions: search (find skills by query), install (install a skill "
            "by slug), list (show installed ClawHub skills)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "install", "list"],
                    "description": "The action to perform.",
                },
                "query": {
                    "type": "string",
                    "description": "Search query (required for 'search' action).",
                },
                "slug": {
                    "type": "string",
                    "description": "Skill slug (required for 'install' action).",
                },
            },
            "required": ["action"],
        }

    def __init__(self, agent_id: str = "", server_url: str = "") -> None:
        self._agent_id = agent_id
        self._skill_name = "clawhub"
        self._server_url = server_url

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        action = params.get("action", "")

        if action == "search":
            return await self._search(params.get("query", ""))
        elif action == "install":
            return await self._install(params.get("slug", ""))
        elif action == "list":
            return await self._list_installed()
        else:
            return ToolResult(
                content=f"Unknown action '{action}'. Use: search, install, list",
                is_error=True,
            )

    async def _search(self, query: str) -> ToolResult:
        if not query:
            return ToolResult(content="'query' is required for search action.", is_error=True)

        try:
            url = self._api_url(f"/api/clawhub/search?q={quote(query)}&limit=10")
            data = await self._http_get(url)
            if not data:
                return ToolResult(
                    content=(
                        f"No skills found matching '{query}'. "
                        f"Try different keywords (e.g. the tool name or category)."
                    ),
                )

            lines = ["ClawHub search results:\n"]
            slugs = []
            for skill in data[:10]:
                name = skill.get("displayName", skill.get("name", skill.get("slug", "?")))
                slug = skill.get("slug", "?")
                desc = skill.get("summary", skill.get("description", ""))[:120]
                ver = skill.get("version", "?")
                lines.append(f"- **{name}** (`{slug}`, v{ver}): {desc}")
                if slug != "?":
                    slugs.append(slug)
            if slugs:
                lines.append(
                    f"\n→ To get step-by-step instructions, INSTALL a "
                    f"skill: clawhub(action='install', slug='{slugs[0]}')"
                )
            return ToolResult(content="\n".join(lines))
        except Exception as exc:
            return ToolResult(
                content=f"ClawHub is unreachable: {exc}",
                is_error=True,
            )

    async def _install(self, slug: str) -> ToolResult:
        if not slug:
            return ToolResult(content="'slug' is required for install action.", is_error=True)

        try:
            url = self._api_url("/api/clawhub/install")
            await self._http_post(url, {"slug": slug})

            hot_loaded = await self._try_hot_load(slug)

            if hot_loaded:
                activation = self._get_activation_steps(slug)
                msg = f"Skill '{slug}' installed and activated (no restart needed).\n\n"
                if activation:
                    msg += f"SETUP REQUIRED:\n{activation}\n"
                else:
                    msg += (
                        f"This is an INSTRUCTION skill. Its instructions "
                        f"are now in your system prompt under <available_skills>. "
                        f"Follow them using bash(), read(), write() — "
                        f"do NOT call '{slug}' as a tool.\n"
                    )
                self._upsert_skill_wm(slug, activation or msg)
                return ToolResult(content=msg)
            else:
                return ToolResult(
                    content=(
                        f"Skill '{slug}' installed successfully. "
                        f"This skill contains Python code that requires a restart. "
                        f"Call request_restart(reason='Installed {slug} from ClawHub') "
                        f"to activate it."
                    ),
                )
        except RuntimeError as exc:
            if "409" in str(exc):
                msg = f"Skill '{slug}' is already installed and available.\n"
                instructions = self._get_skill_instructions(slug)
                if instructions:
                    msg += f"\n**Instructions for {slug}:**\n{instructions}\n"
                else:
                    msg += (
                        f"This is an INSTRUCTION skill — do NOT call "
                        f"'{slug}' as a tool. Check <available_skills> "
                        f"in your system prompt and follow the instructions "
                        f"using bash(), read(), write()."
                    )
                return ToolResult(content=msg)
            return ToolResult(content=f"ClawHub install failed: {exc}", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"ClawHub install failed: {exc}", is_error=True)

    async def _try_hot_load(self, slug: str) -> bool:
        """Attempt to hot-load a SKILL.md-only skill without restart.

        Returns True if the skill was hot-loaded, False if it needs a
        full restart (has Python code).
        """
        try:
            from server.main import app as _app
            _sl = getattr(_app.state, "skill_loader", None)
            if _sl is None:
                return False

            settings = getattr(_app.state, "settings", None)
            if settings is None:
                return False
            skill_dir = settings.data_dir / "skills" / slug
            if not skill_dir.is_dir():
                return False

            has_python = (skill_dir / "__init__.py").exists()
            has_skill_md = (skill_dir / "SKILL.md").exists()

            if has_python:
                return False
            if not has_skill_md:
                return False

            await _sl._load_agentskill(skill_dir, source="clawhub")

            runtime = self._get_runtime()
            if runtime:
                runtime.enable_skill(slug)

            return True
        except Exception as exc:
            logger.warning("Hot-load failed for '%s': %s", slug, exc)
            return False

    def _get_runtime(self) -> Any:
        """Get the ServerRuntime for this agent."""
        try:
            from server.main import app as _app
            am = getattr(_app.state, "agent_manager", None)
            if am is None:
                return None
            runtimes = am.get_loaded_runtimes()
            return runtimes.get(self._agent_id)
        except Exception:
            return None

    def _upsert_skill_wm(self, slug: str, description: str) -> None:
        """Store a brief skill summary in working memory."""
        try:
            runtime = self._get_runtime()
            if runtime and hasattr(runtime, "dual_wm") and runtime.dual_wm:
                summary = description[:300].strip()
                runtime.dual_wm.upsert_fact(
                    domain=f"Skill.{slug}",
                    content=(
                        f"Installed skill '{slug}'. {summary} "
                        f"This is an INSTRUCTION skill — follow instructions "
                        f"using bash/read/write, do NOT call as a tool."
                    ),
                    source="clawhub",
                    salience=0.8,
                )
        except Exception:
            logger.debug("Failed to upsert skill WM fact for %s", slug)

    def _get_activation_steps(self, slug: str) -> str:
        """Get activation steps from the skill loader."""
        try:
            from server.main import app as _app
            _sl = getattr(_app.state, "skill_loader", None)
            if _sl and hasattr(_sl, "get_activation_steps"):
                return _sl.get_activation_steps(slug)
        except Exception:
            pass
        return ""

    def _get_skill_instructions(self, slug: str) -> str:
        """Fetch instructions for a loaded skill via the skill loader."""
        try:
            from server.main import app as _app
            _sl = getattr(_app.state, "skill_loader", None)
            if _sl is None:
                return ""
            results = _sl.instructions_for([slug])
            for _name, _desc, _instr in results:
                if _name == slug and _instr:
                    return _instr
        except Exception as exc:
            logger.warning("Failed to fetch instructions for '%s': %s", slug, exc)
        return ""

    async def _list_installed(self) -> ToolResult:
        try:
            url = self._api_url("/api/clawhub/installed")
            data = await self._http_get(url)
            if not data:
                return ToolResult(content="No ClawHub skills installed.")

            lines = ["Installed ClawHub skills:\n"]
            for skill in data:
                lines.append(f"- **{skill.get('name', skill.get('slug', '?'))}** ({skill.get('slug', '?')})")
            return ToolResult(content="\n".join(lines))
        except Exception as exc:
            return ToolResult(content=f"Failed to list installed skills: {exc}", is_error=True)

    def _api_url(self, path: str) -> str:
        base = self._server_url or os.environ.get(
            "NLS_SERVER_URL", "http://127.0.0.1:9222"
        )
        return f"{base.rstrip('/')}{path}"

    _RETRYABLE_CODES = {429, 502, 503, 504}
    _MAX_RETRIES = 3
    _BACKOFF_BASE = 2.0

    async def _http_get(self, url: str) -> Any:
        return await self._request_with_retry("GET", url)

    async def _http_post(self, url: str, body: dict) -> Any:
        return await self._request_with_retry("POST", url, body=body)

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        body: dict | None = None,
    ) -> Any:
        import httpx

        headers = self._auth_headers()
        timeout = 30.0 if method == "POST" else 15.0
        last_exc: Exception | None = None

        for attempt in range(self._MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    if method == "POST":
                        resp = await client.post(url, json=body, headers=headers)
                    else:
                        resp = await client.get(url, headers=headers)

                    if resp.status_code < 400:
                        return resp.json()

                    is_rate_limit = (
                        resp.status_code in self._RETRYABLE_CODES
                        or "429" in resp.text[:300]
                    )
                    if is_rate_limit and attempt < self._MAX_RETRIES:
                        wait = self._BACKOFF_BASE * (2 ** attempt)
                        logger.info(
                            "ClawHub %s %s: HTTP %d, retrying in %.1fs "
                            "(attempt %d/%d)",
                            method, url.split("?")[0].split("/")[-1],
                            resp.status_code, wait,
                            attempt + 1, self._MAX_RETRIES,
                        )
                        await asyncio.sleep(wait)
                        continue

                    raise RuntimeError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )
            except RuntimeError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < self._MAX_RETRIES:
                    wait = self._BACKOFF_BASE * (2 ** attempt)
                    logger.info(
                        "ClawHub %s failed (%s), retrying in %.1fs",
                        method, exc, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise

        raise last_exc or RuntimeError("ClawHub request failed after retries")

    def _auth_headers(self) -> dict[str, str]:
        secret = os.environ.get("RUNTIME_SHARED_SECRET", "") or os.environ.get("NLS_SHARED_SECRET", "")
        if secret:
            return {"X-Runtime-Secret": secret}
        api_key = os.environ.get("NLS_API_KEY", "")
        if api_key:
            return {"Authorization": f"Bearer {api_key}"}
        return {}


def create_clawhub_tool(agent_id: str, server_url: str = "") -> ClawHubTool:
    """Factory: returns a ClawHubTool bound to the given agent."""
    return ClawHubTool(agent_id=agent_id, server_url=server_url)
