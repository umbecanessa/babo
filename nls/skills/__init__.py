"""NLS Skills SDK -- types shared by the skill loader and individual skills."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)


@dataclass
class SkillOnboarding:
    """Declarative onboarding specification for a skill.

    Tells the frontend *how* to set up this skill when the user clicks
    "Connect" or "Enable".
    """

    setup_type: str = "manual"
    """``"auto"`` | ``"conversational"`` | ``"ui"`` | ``"qr_pair"`` | ``"manual"``"""

    intro_message: str = ""
    """Shown to the user when they initiate the setup flow."""

    setup_prompt: str = ""
    """Instructions fed to the agent in conversational mode."""

    completion_event: str = ""
    """WebSocket event name emitted when setup completes."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_type": self.setup_type,
            "intro_message": self.intro_message,
            "setup_prompt": self.setup_prompt,
            "completion_event": self.completion_event,
        }


@dataclass
class SkillBridge:
    """Declares a non-Python sidecar process managed by the skill loader."""

    name: str = ""
    runtime: str = "node"
    """``"node"`` | ``"python"`` | ``"binary"``"""

    entry: str = "bridge/index.js"
    """Path relative to the skill directory."""

    port: int = 0
    """Port the bridge HTTP server listens on (0 = auto-assign)."""

    health_check: str = "/health"
    """HTTP endpoint for health probes."""

    env: dict[str, str] = field(default_factory=dict)
    """Extra environment variables passed to the bridge process."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "runtime": self.runtime,
            "entry": self.entry,
            "port": self.port,
            "health_check": self.health_check,
            "env": dict(self.env),
        }


@dataclass
class SkillWebhook:
    """Declares an inbound webhook that this skill needs.

    The NestJS relay server registers a public endpoint at
    ``/api/channels/webhook/{channel}/{agentId}`` and forwards
    incoming payloads to the local desktop runtime.
    """

    channel: str = ""
    """Channel name (e.g. 'telegram', 'whatsapp').  Used to build
    the relay URL on the NestJS side."""

    local_path: str = ""
    """Local FastAPI route that receives relayed payloads.
    Example: ``/skills/telegram-channel/webhook/{agent_id}``"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "local_path": self.local_path,
        }


@dataclass
class ConfigField:
    """Declarative description of a single skill configuration field.

    Skills declare these in ``SkillMeta.config_schema`` so the agent
    and the frontend can discover what a skill needs without reading
    its source code.
    """

    key: str = ""
    type: str = "string"
    """``"string"`` | ``"secret"`` | ``"choice"`` | ``"boolean"``
    | ``"number"`` | ``"list"``"""

    description: str = ""
    required: bool = False
    default: Any = None
    options: list[str] | None = None
    """Valid values when ``type="choice"``."""

    scope: str = "agent"
    """``"global"`` (shared across agents) or ``"agent"`` (per-agent)."""

    category: str = ""
    """Semantic grouping: ``"connection"``, ``"identity"``, ``"policy"``, etc."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "key": self.key,
            "type": self.type,
            "description": self.description,
            "required": self.required,
            "scope": self.scope,
        }
        if self.default is not None:
            d["default"] = self.default
        if self.options:
            d["options"] = self.options
        if self.category:
            d["category"] = self.category
        return d


@dataclass
class ContactIdentityField:
    """Identity field a channel skill contributes to the contacts tool."""

    key: str = ""
    description: str = ""
    required_for_outbound: bool = False


@dataclass
class ContactChannelSpec:
    """Declares how a channel skill integrates with the contacts tool."""

    channel_key: str = ""
    display_name: str = ""
    identity_fields: list[ContactIdentityField] = field(default_factory=list)
    supports_groups: bool = True
    supports_recent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_key": self.channel_key,
            "display_name": self.display_name,
            "identity_fields": [
                {
                    "key": f.key,
                    "description": f.description,
                    "required_for_outbound": f.required_for_outbound,
                }
                for f in self.identity_fields
            ],
            "supports_groups": self.supports_groups,
            "supports_recent": self.supports_recent,
        }


@dataclass
class SkillMeta:
    """Declarative metadata for a skill package."""

    name: str = ""
    version: str = "0.1"
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    enabled: bool = True
    created_by: str | None = None
    onboarding: SkillOnboarding | None = None
    bridges: list[SkillBridge] = field(default_factory=list)
    webhooks: list[SkillWebhook] = field(default_factory=list)
    config_schema: list[ConfigField] = field(default_factory=list)
    contacts: ContactChannelSpec | None = None

    skill_type: str = "native"
    """``"native"`` | ``"agentskill"`` | ``"hybrid"``"""

    source: str = "local"
    """``"bundled"`` | ``"local"`` | ``"clawhub"``"""

    license: str | None = None
    compatibility: str | None = None

    instructions: str | None = None
    """Markdown instruction body from SKILL.md (AgentSkills format)."""

    requires_bins: list[str] = field(default_factory=list)
    requires_env: list[str] = field(default_factory=list)
    os_filter: list[str] = field(default_factory=list)
    homepage: str | None = None
    clawhub_slug: str | None = None
    crystallized_from: str | None = None
    install_instructions: list[dict[str, str]] = field(default_factory=list)


@dataclass
class SkillPoller:
    """Declares a polling job a skill wants to register at startup."""

    name: str
    """Unique name within the skill (prefixed with skill name at runtime)."""

    url: str = ""
    """URL to poll (can be set later via config)."""

    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    interval_seconds: float = 60
    callback: Callable[[], Awaitable[Any]] | None = None
    """If set, run this async function instead of HTTP polling."""


@dataclass
class SkillSchedule:
    """Declares a scheduled job a skill wants to register at startup."""

    name: str
    schedule_type: str = "interval"
    interval_seconds: float = 0
    cron_expr: str = ""
    callback: Callable[[], Awaitable[Any]] | None = None


class SkillContext:
    """Runtime context passed to a skill's ``register()`` function.

    Provides helpers for registering routes, tools, lifecycle hooks,
    and reading/writing skill-local configuration.
    """

    def __init__(self, app: FastAPI, skill_name: str, skills_dir: Path) -> None:
        self._app = app
        self._skill_name = skill_name
        self._skills_dir = skills_dir
        self.tools: list[Any] = []
        self.tool_factories: list[Callable[[str], Any]] = []
        self.routers: list[tuple[APIRouter, dict[str, Any]]] = []
        self.startup_hooks: list[Callable[[], Awaitable[None]]] = []
        self.shutdown_hooks: list[Callable[[], Awaitable[None]]] = []
        self.bridges: list[SkillBridge] = []
        self.pollers: list[SkillPoller] = []
        self.schedules: list[SkillSchedule] = []
        self.adapter: Any | None = None

    @property
    def data_dir(self) -> Path:
        """Persistent data directory for this skill."""
        d = self._skills_dir / self._skill_name / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def include_router(self, router: APIRouter, **kwargs: Any) -> None:
        self.routers.append((router, kwargs))

    def register_tool(self, tool: Any) -> None:
        self.tools.append(tool)

    def register_tool_factory(self, factory: Callable[[str], Any]) -> None:
        """Register a callable ``(agent_id: str) -> AgentTool``.

        Unlike ``register_tool``, factories are called once per agent
        runtime so each agent gets its own tool instance with the
        correct ``agent_id`` bound.  Use this for channel tools that
        need per-agent isolation (tokens, policies, etc.).
        """
        self.tool_factories.append(factory)

    def on_startup(self, hook: Callable[[], Awaitable[None]]) -> None:
        self.startup_hooks.append(hook)

    def on_shutdown(self, hook: Callable[[], Awaitable[None]]) -> None:
        self.shutdown_hooks.append(hook)

    def register_bridge(self, bridge: SkillBridge) -> None:
        """Declare a sidecar process that the skill loader will manage."""
        self.bridges.append(bridge)

    def register_poller(self, poller: SkillPoller) -> None:
        """Declare an HTTP polling job managed by the scheduler.

        If the SchedulerManager is already running (i.e. post-boot
        registration, such as when a channel skill is activated later),
        the poller is wired to the scheduler immediately.
        """
        self.pollers.append(poller)
        self._try_dynamic_register_poller(poller)

    def _try_dynamic_register_poller(self, poller: SkillPoller) -> None:
        mgr = getattr(self._app.state, "scheduler_manager", None)
        if mgr is None:
            return
        try:
            from nls.tools.agent_tools.scheduler import ScheduledJob
            job_name = f"{self._skill_name}:{poller.name}"
            if job_name in getattr(mgr, "_jobs", {}):
                return
            job = ScheduledJob(
                name=job_name,
                schedule_type="interval",
                interval_seconds=poller.interval_seconds,
                action="callback" if poller.callback else "http",
                action_url=poller.url if not poller.callback else "",
                action_method=poller.method,
                action_headers=poller.headers,
                action_body=poller.body,
                owner=self._skill_name,
                enabled=True,
            )
            mgr.add_job(job)
            if poller.callback:
                mgr.register_callback(job_name, poller.callback)
        except Exception:
            pass

    def register_schedule(self, schedule: SkillSchedule) -> None:
        """Declare a cron/interval job managed by the scheduler."""
        self.schedules.append(schedule)

    def load_config(self, defaults: dict[str, Any] | None = None, agent_id: str | None = None) -> dict[str, Any]:
        """Load skill configuration from ``config.json``, merging defaults.

        On first load (no config.json on disk), writes the defaults so
        that the admin API can discover the available config keys.

        When defaults are provided, only stored keys that also exist in
        ``defaults`` are kept.  This prevents stale fields from older
        skill versions from lingering in the config.

        If ``agent_id`` is provided, loads from
        ``agents/{agent_id}.json`` instead of the global config.
        """
        if agent_id:
            config_path = self._skills_dir / self._skill_name / "agents" / f"{agent_id}.json"
        else:
            config_path = self._skills_dir / self._skill_name / "config.json"
        config = dict(defaults or {})
        if config_path.exists():
            try:
                stored = json.loads(config_path.read_text(encoding="utf-8"))
                if defaults:
                    for key in defaults:
                        if key in stored:
                            config[key] = stored[key]
                else:
                    config.update(stored)
            except Exception as exc:
                logger.warning("Failed to read config for skill '%s': %s", self._skill_name, exc)
        if defaults and not agent_id:
            self.save_config(config)
        return config

    def save_config(self, config: dict[str, Any], agent_id: str | None = None) -> None:
        """Persist skill configuration to ``config.json``.

        If ``agent_id`` is provided, saves to ``agents/{agent_id}.json``
        for per-agent isolation (channels must be agent-siloed).
        """
        if agent_id:
            config_path = self._skills_dir / self._skill_name / "agents" / f"{agent_id}.json"
        else:
            config_path = self._skills_dir / self._skill_name / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    def load_all_agent_configs(self) -> dict[str, dict[str, Any]]:
        """Load all per-agent config files for this skill.

        Returns a dict mapping ``agent_id`` -> config.
        """
        agents_dir = self._skills_dir / self._skill_name / "agents"
        configs: dict[str, dict[str, Any]] = {}
        if agents_dir.is_dir():
            for f in agents_dir.glob("*.json"):
                try:
                    agent_id = f.stem
                    configs[agent_id] = json.loads(f.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning("Failed to read agent config %s: %s", f, exc)
        return configs

    def list_agent_ids(self) -> list[str]:
        """Agent IDs with per-agent config files for this skill."""
        return list(self.load_all_agent_configs().keys())
