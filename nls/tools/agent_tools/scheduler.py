"""Scheduler tool -- create, list, and remove cron/interval/one-shot jobs.

Jobs are persisted to ``{data_dir}/scheduler_jobs.json`` and survive
restarts.  The scheduler runs as an asyncio background task managed by
the tool instance itself.

Skills can also register jobs via ``SkillContext.register_schedule()``,
which delegates to the same ``SchedulerManager``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Awaitable

from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)

_AGENT_MSG_RE = __import__("re").compile(
    r"^\[AGENT_MSG\|agent_id=([^\]|]+)",
)


def tag_agent_message(
    agent_id: str,
    message: str,
    *,
    owner: str = "agent",
) -> str:
    """Prefix a scheduler agent_message with routing metadata."""
    text = (message or "").strip()
    if not text or not agent_id:
        return text
    if _AGENT_MSG_RE.match(text):
        return text
    return f"[AGENT_MSG|agent_id={agent_id}|owner={owner}] {text}"


def parse_agent_message_target(message: str) -> str | None:
    """Return agent_id from a tagged scheduler message, if present."""
    m = _AGENT_MSG_RE.match((message or "").strip())
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------

@dataclass
class ScheduledJob:
    name: str
    schedule_type: str  # "interval" | "cron" | "once"
    interval_seconds: float = 0
    cron_expr: str = ""
    run_at: float = 0  # unix timestamp for "once"
    action: str = ""  # "http" | "callback" | "agent_message"
    action_url: str = ""
    action_method: str = "GET"
    action_headers: dict[str, str] = field(default_factory=dict)
    action_body: str = ""
    action_message: str = ""  # for agent_message type
    owner: str = ""  # skill name or "agent"
    owner_agent_id: str = ""  # agent that created the job (shared scheduler)
    enabled: bool = True
    created_at: float = 0
    last_run: float = 0
    run_count: int = 0


# ---------------------------------------------------------------------------
# Scheduler engine (shared singleton)
# ---------------------------------------------------------------------------

class SchedulerManager:
    """Manages all scheduled jobs for an agent."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._jobs: dict[str, ScheduledJob] = {}
        self._callbacks: dict[str, Callable[[], Awaitable[Any]]] = {}
        self._task: asyncio.Task | None = None
        self._shutting_down = False
        self._on_agent_message: Callable[[str], Awaitable[Any]] | None = None
        self._on_notify_user: Callable[[str], Awaitable[Any]] | None = None
        self._load()

    @property
    def jobs(self) -> dict[str, ScheduledJob]:
        return dict(self._jobs)

    def set_agent_message_handler(
        self,
        handler: Callable[[str], Awaitable[Any]],
    ) -> None:
        self._on_agent_message = handler

    def set_notify_user_handler(
        self,
        handler: Callable[[str], Awaitable[Any]],
    ) -> None:
        self._on_notify_user = handler

    def add_job(self, job: ScheduledJob) -> None:
        if not job.created_at:
            job.created_at = time.time()
        self._jobs[job.name] = job
        self._save()

    def remove_job(self, name: str) -> bool:
        if name in self._jobs:
            del self._jobs[name]
            self._callbacks.pop(name, None)
            self._save()
            return True
        return False

    def register_callback(
        self,
        name: str,
        callback: Callable[[], Awaitable[Any]],
    ) -> None:
        """Register an async callback for a job (used by skills)."""
        self._callbacks[name] = callback

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._shutting_down = False
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._run_loop())
            logger.info("Scheduler started with %d job(s)", len(self._jobs))
        except RuntimeError:
            logger.warning("Scheduler: no event loop, not started")

    async def stop(self) -> None:
        self._shutting_down = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    # -- persistence -------------------------------------------------------

    def _jobs_path(self) -> Path:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir / "scheduler_jobs.json"

    def _load(self) -> None:
        path = self._jobs_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for name, d in raw.items():
                self._jobs[name] = ScheduledJob(**d)
        except Exception as exc:
            logger.warning("Failed to load scheduler jobs: %s", exc)

    def _save(self) -> None:
        path = self._jobs_path()
        data = {name: asdict(job) for name, job in self._jobs.items()}
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save scheduler jobs: %s", exc)

    # -- main loop ---------------------------------------------------------

    async def _run_loop(self) -> None:
        while not self._shutting_down:
            now = time.time()

            for name, job in list(self._jobs.items()):
                if not job.enabled:
                    continue
                try:
                    if self._should_run(job, now):
                        await self._execute_job(job)
                        job.last_run = time.time()
                        job.run_count += 1
                        if job.schedule_type == "once":
                            job.enabled = False
                        self._save()
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    logger.error("Scheduler job '%s' failed: %s", name, exc)

            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return

    def _should_run(self, job: ScheduledJob, now: float) -> bool:
        if job.schedule_type == "once":
            return job.run_at <= now and job.run_count == 0

        if job.schedule_type == "interval":
            if job.last_run == 0:
                return True
            return (now - job.last_run) >= job.interval_seconds

        if job.schedule_type == "cron":
            return self._cron_matches(job.cron_expr, now) and (now - job.last_run) > 55

        return False

    @staticmethod
    def _cron_matches(expr: str, now: float) -> bool:
        """Minimal cron matching: 'minute hour day month weekday'."""
        import datetime
        parts = expr.strip().split()
        if len(parts) != 5:
            return False
        dt = datetime.datetime.fromtimestamp(now)
        fields = [dt.minute, dt.hour, dt.day, dt.month, dt.weekday()]
        for part, val in zip(parts, fields):
            if part == "*":
                continue
            try:
                if "," in part:
                    if val not in [int(x) for x in part.split(",")]:
                        return False
                elif "/" in part:
                    step = int(part.split("/")[1])
                    if val % step != 0:
                        return False
                elif int(part) != val:
                    return False
            except ValueError:
                return False
        return True

    async def _execute_job(self, job: ScheduledJob) -> None:
        if job.action == "callback":
            cb = self._callbacks.get(job.name)
            if cb:
                await cb()
            return

        if job.action == "http":
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                if job.action_method.upper() == "POST":
                    await client.post(
                        job.action_url,
                        headers=job.action_headers,
                        content=job.action_body,
                    )
                else:
                    await client.get(
                        job.action_url,
                        headers=job.action_headers,
                    )
            return

        if job.action == "agent_message":
            if self._on_agent_message and job.action_message:
                routed = tag_agent_message(
                    job.owner_agent_id,
                    job.action_message,
                    owner=job.owner or "agent",
                )
                await self._on_agent_message(routed)
            return

        if job.action == "notify_user":
            if self._on_notify_user and job.action_message:
                await self._on_notify_user(job.action_message)
            elif self._on_agent_message and job.action_message:
                routed = tag_agent_message(
                    job.owner_agent_id,
                    (
                        "[REMINDER for user — deliver via active channel] "
                        f"{job.action_message}"
                    ),
                    owner=job.owner or "agent",
                )
                await self._on_agent_message(routed)
            return


# ---------------------------------------------------------------------------
# Scheduler tool (agent-facing)
# ---------------------------------------------------------------------------

class SchedulerTool:
    """Agent tool for creating, listing, and removing scheduled jobs.

    Supports interval, cron, and one-shot schedules. Jobs persist across
    restarts.
    """

    def __init__(self, manager: SchedulerManager, *, agent_id: str = "") -> None:
        self._manager = manager
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "scheduler"

    @property
    def description(self) -> str:
        return (
            "Create, list, or remove scheduled jobs. "
            "Use command='create' to create a job, command='list' to see all jobs, "
            "command='remove' to delete a job. "
            "Supports interval (every N seconds), cron (e.g. '0 9 * * *' for daily "
            "at 9am), and one-shot (run once at a specific time). "
            "Set action='agent_message' to send yourself a reminder, or "
            "action='notify_user' to notify the user via their active channel "
            "(Telegram, WhatsApp, or NLS UI)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "enum": ["create", "list", "remove"],
                    "description": "Operation to perform: 'create' a new job, 'list' all jobs, or 'remove' a job.",
                },
                "name": {
                    "type": "string",
                    "description": "Unique job name (required for create/remove). Pass '-' for list.",
                },
                "schedule_type": {
                    "type": "string",
                    "enum": ["interval", "cron", "once"],
                    "description": "Type of schedule (required for create). Pass 'interval' for non-create actions.",
                },
                "interval_seconds": {
                    "type": "number",
                    "description": "Seconds between runs (for interval type)",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression 'min hour day month weekday' (for cron type)",
                },
                "run_at_iso": {
                    "type": "string",
                    "description": "ISO timestamp for one-shot (e.g. '2026-02-22T15:00:00')",
                },
                "action": {
                    "type": "string",
                    "enum": ["agent_message", "notify_user"],
                    "description": (
                        "Job action type (for create): "
                        "'agent_message' sends a message to yourself (internal reminder). "
                        "'notify_user' sends a notification to the user via their "
                        "active channel (Telegram/WhatsApp/NLS UI). "
                        "Defaults to 'agent_message'."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "Message content (for agent_message or notify_user action)",
                },
            },
            "required": ["command", "name"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        command = params.get("command") or params.get("action", "")

        if command == "list":
            return self._list_jobs()
        elif command == "remove":
            return self._remove_job(params.get("name", ""))
        elif command == "create":
            return self._create_job(params)
        else:
            return ToolResult(
                content=f"Unknown action: {command}. Use 'create', 'list', or 'remove'.",
                is_error=True,
            )

    def _list_jobs(self) -> ToolResult:
        jobs = self._manager.jobs
        if not jobs:
            return ToolResult(content="No scheduled jobs.")
        lines = []
        for name, job in jobs.items():
            status = "enabled" if job.enabled else "disabled"
            schedule = ""
            if job.schedule_type == "interval":
                schedule = f"every {job.interval_seconds}s"
            elif job.schedule_type == "cron":
                schedule = f"cron: {job.cron_expr}"
            elif job.schedule_type == "once":
                import datetime
                dt = datetime.datetime.fromtimestamp(job.run_at)
                schedule = f"once at {dt.isoformat()}"
            lines.append(
                f"  {name}: {schedule} [{status}] "
                f"(runs: {job.run_count}, owner: {job.owner or 'agent'})"
            )
        return ToolResult(content="Scheduled jobs:\n" + "\n".join(lines))

    def _remove_job(self, name: str) -> ToolResult:
        if not name:
            return ToolResult(content="Error: 'name' is required", is_error=True)
        if self._manager.remove_job(name):
            return ToolResult(content=f"Job '{name}' removed.")
        return ToolResult(content=f"Job '{name}' not found.", is_error=True)

    def _create_job(self, params: dict[str, Any]) -> ToolResult:
        name = params.get("name", "")
        if not name:
            return ToolResult(content="Error: 'name' is required", is_error=True)

        schedule_type = params.get("schedule_type", "")
        if schedule_type not in ("interval", "cron", "once"):
            return ToolResult(
                content="Error: schedule_type must be 'interval', 'cron', or 'once'",
                is_error=True,
            )

        job = ScheduledJob(
            name=name,
            schedule_type=schedule_type,
            owner="agent",
            owner_agent_id=self._agent_id,
            action=params.get("action", "agent_message"),
            action_message=params.get("message", ""),
        )

        if schedule_type == "interval":
            secs = params.get("interval_seconds", 0)
            if not secs or secs < 10:
                return ToolResult(
                    content="Error: interval_seconds must be >= 10",
                    is_error=True,
                )
            job.interval_seconds = secs

        elif schedule_type == "cron":
            expr = params.get("cron_expr", "")
            if not expr or len(expr.split()) != 5:
                return ToolResult(
                    content="Error: cron_expr must have 5 fields (min hour day month weekday)",
                    is_error=True,
                )
            job.cron_expr = expr

        elif schedule_type == "once":
            iso = params.get("run_at_iso", "")
            if not iso:
                return ToolResult(
                    content="Error: run_at_iso is required for one-shot jobs",
                    is_error=True,
                )
            import datetime
            try:
                dt = datetime.datetime.fromisoformat(iso)
                job.run_at = dt.timestamp()
            except ValueError:
                return ToolResult(
                    content=f"Error: invalid ISO timestamp: {iso}",
                    is_error=True,
                )

        self._manager.add_job(job)
        return ToolResult(content=f"Job '{name}' created ({schedule_type}).")


def create_scheduler_tool(
    data_dir: str,
    *,
    agent_id: str = "",
) -> tuple[SchedulerTool, SchedulerManager]:
    """Factory: create a scheduler tool and its underlying manager."""
    manager = SchedulerManager(data_dir)
    tool = SchedulerTool(manager, agent_id=agent_id)
    return tool, manager
