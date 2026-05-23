"""Persistent team lifecycle manager.

Bridges Plans, Delegates, and Kanban into a single orchestration layer.
Teams are execution contexts wrapping one delegation wave from a plan.
They persist to disk, carry accumulated results, and survive sleep cycles.

Storage: ``{agent_dir}/teams/team_{id}.json``
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .plan_store import Plan, PlanStore, get_delegation_waves

logger = logging.getLogger(__name__)

TEAM_STATUSES = ("created", "active", "paused", "completed", "partial", "failed", "blocked")


def _build_uploads_block(workspace_root: Path) -> str:
    """Scan workspace/uploads/ and return a disambiguation block for delegates.

    Uploaded files are stored as ``uploads/{timestamp}_{original_name}``.
    Without this block, delegates with Google Workspace connected may try to
    fetch files from Google Drive when they see a bare filename in project
    facts, causing a Drive 404.
    """
    uploads_dir = workspace_root / "uploads"
    if not uploads_dir.is_dir():
        return ""
    try:
        lines: list[str] = []
        for uf in sorted(uploads_dir.iterdir()):
            if not uf.is_file():
                continue
            # Strip the leading millisecond-timestamp prefix: "{13digits}_{name}"
            parts = uf.name.split("_", 1)
            orig = (
                parts[1]
                if (len(parts) == 2 and parts[0].isdigit() and len(parts[0]) >= 10)
                else uf.name
            )
            lines.append(f"  - {orig} → read(path='uploads/{uf.name}')")
        if not lines:
            return ""
        return (
            "[UPLOADED DOCUMENTS — these are LOCAL files in the workspace.\n"
            " Use read(path='uploads/...') shown below. Do NOT use Google Drive for these.]\n"
            + "\n".join(lines)
        )
    except Exception:
        return ""

# Credential detection patterns for team briefing sanitization
import re as _re

_SECRET_PATTERNS: list[tuple[_re.Pattern[str], str]] = [
    (_re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "sk-ant-***"),
    (_re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"), "sk-proj-***"),
    (_re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk-***"),
    (_re.compile(r"ghp_[A-Za-z0-9]{20,}"), "ghp_***"),
    (_re.compile(r"gho_[A-Za-z0-9]{20,}"), "gho_***"),
    (_re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "github_pat_***"),
    (_re.compile(r"xoxb-[A-Za-z0-9\-]{20,}"), "xoxb-***"),
    (_re.compile(r"xoxp-[A-Za-z0-9\-]{20,}"), "xoxp-***"),
    (_re.compile(r"postgres://[^\s]{10,}"), "postgres://***"),
    (_re.compile(r"mongodb\+srv://[^\s]{10,}"), "mongodb+srv://***"),
    (_re.compile(r"AKIA[A-Z0-9]{16}"), "AKIA***"),
]


def _sanitize_secrets(text: str) -> tuple[str, int]:
    """Replace inline secrets with placeholders. Returns (sanitized, count)."""
    count = 0
    for pattern, replacement in _SECRET_PATTERNS:
        text, n = pattern.subn(replacement + " (use credentials from your .env file)", text)
        count += n
    return text, count


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


# -------------------------------------------------------------------
# Data model
# -------------------------------------------------------------------


@dataclass
class TeamMember:
    delegate_number: int = 0
    step_id: str = ""
    task: str = ""
    status: str = "pending"         # pending | running | done | failed | cancelled
    result_summary: str = ""
    kanban_task_id: str = ""
    iterations: int = 0
    tool_calls: int = 0
    elapsed_seconds: float = 0.0
    last_actions: list[str] = field(default_factory=list)
    hint_ack: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamMember:
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class Team:
    id: str = ""
    name: str = ""
    plan_id: str = ""
    wave_index: int = 0
    status: str = "created"
    mission: str = ""
    briefing: str = ""

    members: list[TeamMember] = field(default_factory=list)
    batch_id: str = ""
    checkback_job: str = ""
    kanban_parent_id: str = ""

    results_log: list[dict] = field(default_factory=list)
    created_at: float = 0.0
    completed_at: float = 0.0
    completion_reported: bool = False

    def __post_init__(self):
        if not self.id:
            self.id = f"team_{_short_id()}"
        if not self.created_at:
            self.created_at = time.time()

    @property
    def progress(self) -> str:
        done = sum(1 for m in self.members if m.status in ("done",))
        return f"{done}/{len(self.members)}"

    @property
    def is_terminal(self) -> bool:
        return self.status in ("completed", "partial", "failed")

    def member_by_delegate(self, delegate_number: int) -> TeamMember | None:
        for m in self.members:
            if m.delegate_number == delegate_number:
                return m
        return None

    def member_by_step(self, step_id: str) -> TeamMember | None:
        for m in self.members:
            if m.step_id == step_id:
                return m
        return None

    def all_members_done(self) -> bool:
        return all(m.status in ("done", "failed", "cancelled") for m in self.members)

    def compute_outcome(self) -> str:
        """Determine team outcome: completed / partial / failed.

        - completed: every member succeeded
        - partial: some succeeded, some failed (mixed results)
        - failed: majority or all failed

        Only counts terminal members (done/failed/cancelled).
        Running/pending members are excluded from the ratio.
        """
        terminal = [m for m in self.members if m.status in ("done", "failed", "cancelled")]
        if not terminal:
            return "failed"
        succeeded = sum(1 for m in terminal if m.status == "done")
        failed = len(terminal) - succeeded
        if succeeded == len(self.members):
            return "completed"
        if failed == 0 and succeeded > 0:
            return "partial"
        if failed > succeeded:
            return "failed"
        return "partial"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "plan_id": self.plan_id,
            "wave_index": self.wave_index,
            "status": self.status,
            "mission": self.mission,
            "briefing": self.briefing,
            "members": [m.to_dict() for m in self.members],
            "batch_id": self.batch_id,
            "checkback_job": self.checkback_job,
            "kanban_parent_id": self.kanban_parent_id,
            "results_log": self.results_log,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "completion_reported": self.completion_reported,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Team:
        members = [TeamMember.from_dict(m) for m in d.get("members", [])]
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            plan_id=d.get("plan_id", ""),
            wave_index=d.get("wave_index", 0),
            status=d.get("status", "created"),
            mission=d.get("mission", ""),
            briefing=d.get("briefing", ""),
            members=members,
            batch_id=d.get("batch_id", ""),
            checkback_job=d.get("checkback_job", ""),
            kanban_parent_id=d.get("kanban_parent_id", ""),
            results_log=d.get("results_log", []),
            created_at=d.get("created_at", 0.0),
            completed_at=d.get("completed_at", 0.0),
            completion_reported=d.get("completion_reported", False),
        )

    def to_summary(self, compact: bool = False) -> str:
        """Render a human-readable summary for prompt injection."""
        if compact:
            return (
                f"[{self.id}] {self.name} ({self.status}) "
                f"— {self.progress} members done"
            )
        _reported = " [ALREADY REPORTED TO USER]" if self.completion_reported else ""
        lines = [
            f"Team: {self.name} [{self.id}]",
            f"  Plan: {self.plan_id} | Wave: {self.wave_index} | Status: {self.status}{_reported}",
            f"  Progress: {self.progress}",
        ]
        for idx, m in enumerate(self.members):
            marker = {
                "done": "[x]", "running": "[>]", "failed": "[!]",
                "cancelled": "[-]", "pending": "[~]",
            }.get(m.status, "[ ]")
            extra = ""
            if m.status == "running":
                parts = []
                if m.iterations:
                    parts.append(f"iter {m.iterations}")
                if m.tool_calls:
                    parts.append(f"{m.tool_calls} tools")
                if m.elapsed_seconds:
                    parts.append(f"{m.elapsed_seconds:.0f}s")
                extra = f" {' | '.join(parts)}" if parts else ""
                if m.last_actions:
                    extra += f"\n       Recent: {', '.join(m.last_actions[-3:])}"
                if m.hint_ack:
                    extra += f"\n       Response: \"{m.hint_ack}\""
            elif m.result_summary:
                extra = f" — {m.result_summary[:80]}"
            lines.append(
                f"  {marker} member={idx} (delegate #{m.delegate_number}): {m.task[:60]}{extra}"
            )
        return "\n".join(lines)


# -------------------------------------------------------------------
# TeamManager
# -------------------------------------------------------------------


class TeamManager:
    """Persistent team lifecycle — bridges Plans, Delegates, and Kanban."""

    TEAMS_DIR = "teams"

    def __init__(
        self,
        agent_dir: Path,
        plan_store: PlanStore,
        *,
        todo_store: Any | None = None,
        delegate_manager: Any | None = None,
        connection_manager: Any | None = None,
        scheduler_manager: Any | None = None,
        agent_id: str = "",
        context_id: str = "primary",
    ) -> None:
        self._agent_dir = Path(agent_dir)
        self._teams_dir = self._agent_dir / self.TEAMS_DIR
        self._teams_dir.mkdir(parents=True, exist_ok=True)
        self._plan_store = plan_store
        self._todo_store = todo_store
        self._delegate_manager = delegate_manager
        self._connection_manager = connection_manager
        self._scheduler_manager = scheduler_manager
        self._agent_id = agent_id
        self._context_id = context_id
        self._copilot_queue: asyncio.Queue | None = None
        self._hooks: Any | None = None
        self._teams: dict[str, Team] = {}
        self._escalation_counts: dict[int, int] = {}
        self._last_create_error: str = ""
        self._load_all()

    def set_hooks(self, hooks: Any) -> None:
        """Wire LoopHooks so orchestration events update WM."""
        self._hooks = hooks

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _team_path(self, team_id: str) -> Path:
        return self._teams_dir / f"{team_id}.json"

    def _load_all(self) -> None:
        for path in self._teams_dir.glob("team_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                team = Team.from_dict(data)
                self._teams[team.id] = team
            except (json.JSONDecodeError, KeyError, OSError) as exc:
                logger.warning("TeamManager: failed to load %s: %s", path, exc)

    def save(self, team: Team) -> None:
        self._teams[team.id] = team
        path = self._team_path(team.id)
        path.write_text(
            json.dumps(team.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load(self, team_id: str) -> Team | None:
        return self._teams.get(team_id)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def create_team(
        self,
        plan_id: str,
        wave_index: int,
        name: str,
        mission: str = "",
        briefing: str = "",
    ) -> Team | None:
        """Create a team from a plan's delegation wave.

        Reads the plan, extracts the specified wave, creates TeamMembers
        for each step, and optionally creates linked Kanban items.
        """
        plan = self._plan_store.load(plan_id)
        if plan is None:
            logger.error("TeamManager: plan %s not found", plan_id)
            return None

        waves = get_delegation_waves(plan)
        if wave_index >= len(waves):
            logger.error(
                "TeamManager: wave %d out of range (plan has %d waves)",
                wave_index, len(waves),
            )
            return None

        all_wave_steps = waves[wave_index]
        wave_steps = [
            s for s in all_wave_steps
            if plan.get_step(s.id) is None
            or plan.get_step(s.id).status not in ("done", "skipped")
        ]
        if not wave_steps:
            done_labels = [s.label for s in all_wave_steps]
            next_waves = [
                (i, [s for s in w if (plan.get_step(s.id) or s).status not in ("done", "skipped")])
                for i, w in enumerate(waves) if i > wave_index
            ]
            next_pending = [(i, w) for i, w in next_waves if w]
            hint = ""
            if next_pending:
                nw_idx, nw_steps = next_pending[0]
                hint = (
                    f" Next wave with pending steps is wave {nw_idx} "
                    f"({len(nw_steps)} step(s): "
                    f"{', '.join(s.label[:50] for s in nw_steps[:3])})."
                )
            self._last_create_error = (
                f"Wave {wave_index} has no pending steps — "
                f"{len(all_wave_steps)} step(s) already done: "
                f"{', '.join(done_labels[:3])}."
                f"{hint}"
            )
            logger.info(
                "TeamManager: wave %d has no pending steps — %d done (%s)%s",
                wave_index, len(all_wave_steps),
                ", ".join(done_labels[:3]), hint,
            )
            return None

        # Sanitize secrets from briefing text
        if briefing:
            briefing, _n_secrets = _sanitize_secrets(briefing)
            if _n_secrets:
                logger.warning(
                    "TeamManager: sanitized %d secret(s) from team briefing",
                    _n_secrets,
                )

        team = Team(
            name=name,
            plan_id=plan_id,
            wave_index=wave_index,
            mission=mission or f"{plan.title} — Wave {wave_index + 1}",
            briefing=briefing,
        )

        for i, step in enumerate(wave_steps):
            _task = step.label
            if step.description:
                _task += f"\n\nDescription: {step.description}"
            if step.notes:
                _task += f"\n\nNotes: {step.notes}"
            member = TeamMember(
                delegate_number=i + 1,
                step_id=step.id,
                task=_task,
            )
            team.members.append(member)

        # Create Kanban items if todo store is available
        if self._todo_store is not None:
            self._create_kanban_items(team, plan)

        self.save(team)
        self._broadcast_sync("team_created", team)
        logger.info(
            "TeamManager: created team %s (%s) with %d members from plan %s wave %d",
            team.id, team.name, len(team.members), plan_id, wave_index,
        )
        return team

    def list_teams(self, include_terminal: bool = False) -> list[Team]:
        """Return all teams, optionally filtering out completed/failed."""
        teams = list(self._teams.values())
        if not include_terminal:
            teams = [t for t in teams if not t.is_terminal]
        return sorted(teams, key=lambda t: t.created_at, reverse=True)

    def inspect_team(self, team_id: str) -> Team | None:
        """Return full team detail including live delegate status.

        Returns a deep copy enriched with ephemeral delegate data so
        that the persistent Team object is never mutated by reads.
        """
        team = self._teams.get(team_id)
        if team is None:
            return None

        # Last-resort safety: if the orchestrator still hasn't reported
        # a terminal team after 15 min (despite receiving a priority
        # interrupt on completion), auto-mark it to break any stale
        # check-back loops.  The primary mechanism is the copilot_queue
        # interrupt in on_delegate_complete(); this is only a backstop.
        _STALE_TERMINAL_SECONDS = 900
        if (
            team.is_terminal
            and not team.completion_reported
            and team.completed_at > 0
            and (time.time() - team.completed_at) > _STALE_TERMINAL_SECONDS
        ):
            team.completion_reported = True
            logger.warning(
                "Auto-marked team %s as reported (stale terminal for %.0fs)",
                team_id, time.time() - team.completed_at,
            )

        # Work on a deep copy so live delegate data doesn't bleed
        # into the persisted team state.  Strip transient callables
        # first — they hold tool refs with thread locks that can't
        # be pickled by deepcopy.
        import copy
        _saved_fn = getattr(team, "_launch_fn", None)
        _saved_kw = getattr(team, "_launch_kwargs", None)
        team._launch_fn = None
        team._launch_kwargs = None
        try:
            snapshot = copy.deepcopy(team)
        finally:
            team._launch_fn = _saved_fn
            team._launch_kwargs = _saved_kw

        if self._delegate_manager is not None and snapshot.batch_id:
            statuses = self._delegate_manager.get_batch_status(snapshot.batch_id)
            for ds in statuses:
                member = snapshot.member_by_delegate(ds.delegate_number)
                if member is not None:
                    member.iterations = ds.iteration
                    member.tool_calls = ds.total_tool_calls
                    member.elapsed_seconds = ds.elapsed_seconds
                    member.last_actions = list(ds.last_actions[-5:])
                    if ds.hint_ack:
                        member.hint_ack = ds.hint_ack
                    if ds.state == "running":
                        member.status = "running"
                    elif ds.state == "done":
                        member.status = "done"
                        member.result_summary = ds.summary_preview
                    elif ds.state in ("error", "cancelled"):
                        member.status = ds.state

        return snapshot

    async def launch_team_async(
        self,
        team_id: str,
        run_delegate_fn: Callable[..., Any],
        fn_kwargs: dict[str, Any],
    ) -> Team | None:
        """Async version of launch_team for use inside event loops.

        Auto-batches members: if the team has more members than the
        delegate concurrency limit, only the first batch is spawned
        immediately.  Remaining members stay ``pending`` and are
        spawned by ``on_delegate_complete`` as slots free up.
        """
        team = self._teams.get(team_id)
        if team is None or team.status not in ("created", "paused"):
            return None

        if self._delegate_manager is None:
            logger.error("TeamManager: no delegate manager — cannot launch")
            return None

        # Wire escalation so team members call for help instead of hard-exiting.
        # The actual per-delegate callback is created in run_delegate_detached
        # (which knows the delegate number). We pass our method reference.
        fn_kwargs = {**fn_kwargs, "on_escalation": self.on_member_escalation}

        # Stash run_delegate_fn + kwargs so on_delegate_complete can
        # spawn queued members later.
        team._launch_fn = run_delegate_fn
        team._launch_kwargs = fn_kwargs

        max_concurrent = getattr(
            self._delegate_manager, "_max_concurrent",
            self._delegate_manager.MAX_CONCURRENT_DELEGATES,
        )
        active_count = sum(
            1 for ds in self._delegate_manager._delegates.values()
            if ds.state == "running"
        )
        available_slots = max(0, max_concurrent - active_count)

        from .delegate_manager import DelegateSpec

        base_num = getattr(self._delegate_manager, "_next_delegate_number", 1)
        specs: list[DelegateSpec] = []
        launched_members: list[TeamMember] = []

        # Resolve project directory from the plan for workspace scoping.
        # Falls back to any prior plan's project_dir to avoid duplicate folders.
        _project_dir = ""
        if team.plan_id and self._plan_store is not None:
            _plan = self._plan_store.load(team.plan_id)
            if _plan and _plan.project_dir:
                _project_dir = _plan.project_dir
        if not _project_dir and self._plan_store is not None:
            _project_dir = self._plan_store.find_any_project_dir()

        # Snapshot the project directory — passed as structured manifest
        # to SubCryptex instead of being inlined in the task string.
        _file_manifest: list[str] = []
        if _project_dir:
            _ws = self._agent_dir / "workspace" / _project_dir
            if _ws.is_dir():
                try:
                    for f in sorted(_ws.rglob("*")):
                        if f.is_file() and ".git" not in f.parts:
                            rel = f.relative_to(_ws)
                            _file_manifest.append(str(rel))
                except Exception:
                    pass

        # Scan workspace/uploads/ for user-uploaded files and build a
        # disambiguation block so delegates use read(path='uploads/...')
        # instead of accidentally routing to Google Drive.
        _uploads_block = _build_uploads_block(self._agent_dir / "workspace")

        # Collect credentials from the orchestrator's Cryptex to share
        # with delegates so they have API keys, connection strings, etc.
        _credential_block = ""
        if self._hooks is not None:
            _get_creds = getattr(self._hooks, "wm_get_credentials", None)
            if _get_creds is not None:
                try:
                    _creds = _get_creds()
                    if _creds:
                        _cred_lines = [f"  - {domain}: {content}" for domain, content in _creds]
                        _credential_block = (
                            "[CREDENTIALS — use these for authentication/connections]\n"
                            + "\n".join(_cred_lines)
                        )
                except Exception:
                    pass

        for i, member in enumerate(team.members):
            num = base_num + i
            member.delegate_number = num

            _preamble_parts: list[str] = []
            if team.briefing:
                _preamble_parts.append(f"[TEAM BRIEFING]\n{team.briefing}")
            if _credential_block:
                _preamble_parts.append(_credential_block)
            if _uploads_block:
                _preamble_parts.append(_uploads_block)
            if _project_dir:
                _preamble_parts.append(
                    f"[PROJECT DIRECTORY — CRITICAL]\n"
                    f"Your CWD (for bash AND file tools) is ALREADY set to {_project_dir}/.\n"
                    f"Do NOT `cd {_project_dir}` — you are already inside it.\n"
                    f"- bash: run commands directly (e.g. `mkdir -p backend/models`). "
                    f"Do NOT prefix with `cd {_project_dir} &&`.\n"
                    f"- read/write/glob: use paths relative to {_project_dir}/, "
                    f"e.g. write(path=\"backend/main.py\").\n"
                    f"Do NOT prepend '{_project_dir}/' to paths — it will double-nest.\n"
                    f"NEVER create new top-level project directories."
                )
            task_with_briefing = member.task
            if _preamble_parts:
                task_with_briefing = (
                    "\n\n".join(_preamble_parts)
                    + f"\n\n[YOUR TASK]\n{member.task}"
                )

            if len(specs) < available_slots:
                member.status = "running"
                try:
                    _ms = int(fn_kwargs.get("max_steps", 15))
                except (ValueError, TypeError):
                    _ms = 15
                specs.append(DelegateSpec(
                    task=task_with_briefing,
                    delegate_number=num,
                    max_steps=_ms,
                    file_manifest=_file_manifest,
                    team_briefing=team.briefing or "",
                    wave=team.wave_index,
                ))
                launched_members.append(member)
            else:
                member.status = "pending"

        self._delegate_manager._next_delegate_number = base_num + len(team.members)

        batch_handle = await self._delegate_manager.spawn_batch(
            specs,
            run_delegate_fn=run_delegate_fn,
            fn_kwargs=fn_kwargs,
            skip_dedup=True,
        )

        team.batch_id = batch_handle.batch_id
        team.status = "active"
        team.checkback_job = f"team_checkback_{team.id}"

        if self._todo_store is not None:
            for member in launched_members:
                if member.kanban_task_id:
                    self._todo_store.update(
                        member.kanban_task_id,
                        status="in_progress",
                    )

        self.save(team)
        queued_count = len(team.members) - len(specs)
        await self._broadcast_async("team_launched", team)

        # Update WM orchestration state
        if self._hooks is not None:
            _h = self._hooks
            if getattr(_h, "wm_orch_update_team", None):
                _members_data = [
                    {
                        "index": i,
                        "task_summary": m.task[:80],
                        "status": m.status,
                        "delegate_number": m.delegate_number,
                    }
                    for i, m in enumerate(team.members)
                ]
                _h.wm_orch_update_team(
                    team.id, team.plan_id, "running", _members_data,
                )
            if getattr(_h, "wm_orch_record_decision", None):
                _step_titles = ", ".join(
                    m.task.split("\n")[0][:40] for m in team.members[:4]
                )
                _h.wm_orch_record_decision(
                    action="launched_team",
                    context=f"Plan {team.plan_id}: {_step_titles}",
                    team_id=team.id,
                )

        logger.info(
            "TeamManager: launched team %s — batch %s, %d delegates "
            "(%d queued for next slot)",
            team.id, batch_handle.batch_id, len(specs), queued_count,
        )
        return team

    async def advance_team(self, team_id: str) -> Team | None:
        """Mark team as completed, update plan steps, check next wave.

        Returns the newly created next-wave team if one was auto-created,
        or the completed team if no further waves exist.
        Raises ValueError if members are still running.
        """
        team = self._teams.get(team_id)
        if team is None:
            return None

        running = [m for m in team.members if m.status in ("running", "pending")]
        if running:
            names = ", ".join(f"#{m.delegate_number}" for m in running)
            raise ValueError(
                f"Cannot advance team {team_id}: {len(running)} member(s) still "
                f"active ({names}). Wait for them to finish or intervene first."
            )

        _outcome = team.compute_outcome()
        team.status = _outcome
        team.completed_at = time.time()
        team.completion_reported = True

        if team.checkback_job and self._scheduler_manager is not None:
            try:
                self._scheduler_manager.remove_job(team.checkback_job)
            except Exception:
                pass

        for member in team.members:
            team.results_log.append({
                "step_id": member.step_id,
                "task": member.task,
                "status": member.status,
                "summary": member.result_summary,
                "iterations": member.iterations,
                "tool_calls": member.tool_calls,
                "elapsed": member.elapsed_seconds,
            })

        plan = self._plan_store.load(team.plan_id)
        if plan is not None:
            for member in team.members:
                step = plan.get_step(member.step_id)
                if step is not None:
                    step.status = "done" if member.status == "done" else "skipped"
                    if member.result_summary:
                        step.notes = member.result_summary[:500]
            self._plan_store.save(plan)

        _kanban_parent_status = {"completed": "done", "partial": "in_progress", "failed": "failed"}[_outcome]
        if self._todo_store is not None:
            if team.kanban_parent_id:
                self._todo_store.update(team.kanban_parent_id, status=_kanban_parent_status)
            for member in team.members:
                if member.kanban_task_id and member.status == "done":
                    self._todo_store.update(member.kanban_task_id, status="done")
                elif member.kanban_task_id and member.status in ("failed", "cancelled"):
                    self._todo_store.update(member.kanban_task_id, status="failed")

        self.save(team)
        await self._broadcast_async("team_advanced", team)

        next_team = self._try_create_next_wave(team)
        if next_team is not None:
            logger.info(
                "TeamManager: team %s completed → auto-created next wave team %s",
                team.id, next_team.id,
            )
            return next_team

        logger.info("TeamManager: team %s completed — no further waves", team.id)
        return team

    async def pause_team(self, team_id: str) -> bool:
        team = self._teams.get(team_id)
        if team is None or team.status != "active":
            return False
        team.status = "paused"
        self.save(team)
        await self._broadcast_async("team_paused", team)
        return True

    async def resume_team(self, team_id: str) -> bool:
        team = self._teams.get(team_id)
        if team is None or team.status != "paused":
            return False
        team.status = "active"
        self.save(team)
        await self._broadcast_async("team_resumed", team)
        return True

    async def disband_team(self, team_id: str) -> bool:
        """Cancel all running delegates and mark team as failed."""
        team = self._teams.get(team_id)
        if team is None or team.is_terminal:
            return False

        if self._delegate_manager is not None:
            for member in team.members:
                if member.status == "running":
                    try:
                        await self._delegate_manager.cancel(member.delegate_number)
                    except Exception:
                        pass
                    member.status = "cancelled"

        team.status = "failed"
        team.completed_at = time.time()
        self.save(team)
        await self._broadcast_async("team_disbanded", team)
        return True

    async def hint_member_async(
        self, team_id: str, member_idx: int, message: str,
    ) -> bool:
        """Async version of hint_member."""
        team = self._teams.get(team_id)
        if team is None or team.status != "active":
            return False
        if member_idx < 0 or member_idx >= len(team.members):
            return False
        member = team.members[member_idx]
        if member.status != "running" or self._delegate_manager is None:
            return False
        return await self._delegate_manager.hint(member.delegate_number, message)

    def update_briefing(self, team_id: str, content: str) -> bool:
        team = self._teams.get(team_id)
        if team is None:
            return False
        team.briefing = (team.briefing + "\n\n" + content).strip()
        self.save(team)
        return True

    async def on_member_escalation(
        self,
        delegate_number: int,
        reason: str,
        context_summary: str,
    ) -> None:
        """Called when a team member hits a limit and requests help.

        Priority order: auto-extend first (instant), then inform orchestrator.
        First escalation: auto-extend +10 iterations immediately via
        DelegateManager — no orchestrator round-trip needed.
        Subsequent escalations: full escalation to orchestrator via copilot_queue.
        """
        team = None
        member = None
        for t in self._teams.values():
            if t.is_terminal:
                continue
            m = t.member_by_delegate(delegate_number)
            if m:
                team = t
                member = m
                break

        if team is None or member is None:
            logger.warning(
                "TeamManager: escalation for unknown delegate #%d",
                delegate_number,
            )
            return

        member_idx = team.members.index(member)

        # Completion reviews don't count toward the regular escalation counter
        # so the first real stall/limit escalation still gets auto-extended.
        if reason != "completion_review":
            esc_count = self._escalation_counts.get(delegate_number, 0)
            self._escalation_counts[delegate_number] = esc_count + 1
        else:
            esc_count = self._escalation_counts.get(delegate_number, 0)

        # Record escalation in WM
        if self._hooks is not None:
            _h = self._hooks
            if getattr(_h, "wm_orch_add_escalation", None):
                _h.wm_orch_add_escalation(
                    team.id, member_idx,
                    f"{reason}: {context_summary[:150]}",
                )

        # Forward escalation to LearningAccumulator
        _acc = getattr(self._hooks, "_accumulator", None) if self._hooks else None
        if _acc is not None:
            try:
                _decision = "auto-extend" if esc_count == 0 else "pending"
                _acc.on_member_escalation(
                    member_idx=member_idx,
                    delegate_number=delegate_number,
                    task=member.task,
                    reason=reason,
                    decision=_decision,
                )
            except Exception:
                logger.debug("TeamManager: accumulator on_member_escalation failed", exc_info=True)

        # Broadcast the event for frontend display
        await self._broadcast_async(
            "team_member_escalation", team, member=member,
        )

        # --- Completion review / reminder ---
        if reason in ("completion_review", "completion_review_reminder"):
            _writes = 0
            if context_summary:
                for line in context_summary.splitlines():
                    if line.startswith("writes:"):
                        try:
                            _writes = int(line.split(":", 1)[1].strip())
                        except (ValueError, IndexError):
                            pass

            _is_reminder = reason == "completion_review_reminder"
            _tag = "REMINDER — " if _is_reminder else ""

            # Extract files_written from the summary
            _files_section = ""
            if context_summary and "files_written:" in context_summary:
                _in_files = False
                _file_lines: list[str] = []
                for _ln in context_summary.splitlines():
                    if _ln.strip() == "files_written:":
                        _in_files = True
                        continue
                    if _in_files and _ln.strip().startswith("- "):
                        _file_lines.append(_ln.strip())
                    elif _in_files:
                        break
                if _file_lines:
                    _files_section = (
                        "Files created/modified:\n"
                        + "\n".join(f"  {f}" for f in _file_lines)
                        + "\n\n"
                    )

            review_msg = (
                f"[{_tag}COMPLETION REVIEW — DELEGATE #{member.delegate_number}]\n"
                f"Team: {team.name} [{team.id}]\n"
                f"Member #{member_idx}: {member.task[:100]}\n"
                f"Stats: {context_summary}\n\n"
            )
            if _files_section:
                review_msg += _files_section
            if _writes == 0:
                review_msg += (
                    "⚠ WARNING: This delegate wrote ZERO files. It likely "
                    "saw existing code and assumed the work was done.\n"
                    "Review the actual deliverables and reject if incomplete.\n\n"
                )
            elif _writes <= 3:
                review_msg += (
                    f"⚠ WARNING: This delegate only wrote {_writes} files. "
                    "For a complex task this may be incomplete.\n"
                    "VERIFY: list_dir or read the key files before approving.\n\n"
                )
            review_msg += (
                "BEFORE deciding, you MUST verify the delegate's work:\n"
                "  1. list_dir the relevant directory to check file structure\n"
                "  2. Compare deliverables against the original task requirements\n"
                "  3. Only APPROVE if the core deliverables exist (not just config files)\n\n"
                "The delegate is WAITING for your decision. You MUST respond:\n"
                f"  APPROVE: team(action='intervene', team_id='{team.id}', "
                f"member={member_idx}, decision='approve')\n"
                f"  REJECT:  team(action='intervene', team_id='{team.id}', "
                f"member={member_idx}, decision='hint', "
                f"message='<what needs to be done>')"
            )
            if self._copilot_queue is not None:
                try:
                    self._copilot_queue.put_nowait(review_msg)
                except Exception:
                    pass
            logger.info(
                "TeamManager: completion %s for delegate #%d "
                "(writes=%d) — notified orchestrator",
                "reminder" if _is_reminder else "review",
                delegate_number, _writes,
            )
            return

        # --- First escalation: auto-extend immediately ---
        # Scale extension with original budget: at least 10, up to 1/2 of
        # max_steps, so complex tasks get proportionally more room.
        _base_budget = 15
        if self._delegate_manager is not None:
            _ds = self._delegate_manager._delegates.get(delegate_number)
            if _ds is not None:
                _base_budget = _ds.max_iterations
        _ext_iters = max(10, _base_budget // 2)

        if esc_count == 0 and self._delegate_manager is not None:
            try:
                result = await self._delegate_manager.intervene(
                    delegate_number,
                    action="extend",
                    message=(
                        f"[AUTO-EXTEND] You hit {reason} but this is your first "
                        f"escalation. You have been granted {_ext_iters} more "
                        f"iterations. Focus on completing or wrapping up your task."
                    ),
                    extra_iterations=_ext_iters,
                )
                if result is True:
                    logger.info(
                        "TeamManager: auto-extended delegate #%d by %d on first "
                        "escalation (reason=%s, base_budget=%d)",
                        delegate_number, _ext_iters, reason, _base_budget,
                    )
                    info_msg = (
                        f"[TEAM INFO — AUTO-EXTENDED]\n"
                        f"Team: {team.name} [{team.id}]\n"
                        f"Member #{member_idx} (delegate #{delegate_number}): "
                        f"{member.task[:80]}\n"
                        f"Reason: {reason} (1st escalation — auto-extended "
                        f"+{_ext_iters} iters, base budget was {_base_budget})\n"
                        f"No action needed unless the member escalates again."
                    )
                    if self._copilot_queue is not None:
                        try:
                            self._copilot_queue.put_nowait(info_msg)
                        except Exception:
                            pass
                    return
                else:
                    logger.warning(
                        "TeamManager: auto-extend failed for delegate #%d: %s",
                        delegate_number, result,
                    )
            except Exception:
                logger.warning(
                    "TeamManager: auto-extend exception for delegate #%d",
                    delegate_number, exc_info=True,
                )

        # --- Subsequent escalation: full orchestrator escalation ---
        help_msg = (
            f"[TEAM MEMBER HELP REQUEST — ESCALATION #{esc_count + 1}]\n"
            f"Team: {team.name} [{team.id}]\n"
            f"Member #{member_idx} (delegate #{member.delegate_number}): "
            f"{member.task[:100]}\n"
            f"Reason: {reason}\n"
            f"Context: {context_summary}\n\n"
            f"⚠ This member was already auto-extended once (+{_ext_iters} "
            f"iters) and is escalating AGAIN. Decide: extend further "
            f"(+{_ext_iters} recommended), give a targeted hint, or "
            f"terminate.\n\n"
            f"Respond with: team(action='intervene', team_id='{team.id}', "
            f"member={member_idx}, "
            f"decision='extend' or 'hint' or 'terminate', "
            f"message='optional guidance')"
        )

        if self._connection_manager is not None:
            try:
                await self._connection_manager.broadcast(
                    self._agent_id,
                    {
                        "type": "team_help_request",
                        "team_id": team.id,
                        "member_index": member_idx,
                        "delegate_number": delegate_number,
                        "reason": reason,
                        "message": help_msg,
                    },
                )
            except Exception:
                logger.debug(
                    "TeamManager: broadcast escalation failed", exc_info=True,
                )

        if self._copilot_queue is not None:
            try:
                self._copilot_queue.put_nowait(help_msg)
            except Exception:
                logger.debug("TeamManager: copilot_queue injection failed", exc_info=True)
        else:
            logger.warning(
                "TeamManager: no copilot_queue — escalation won't reach orchestrator loop"
            )

        logger.info(
            "TeamManager: member #%d of team %s escalated #%d (reason=%s)",
            delegate_number, team.id, esc_count + 1, reason,
        )

    def get_active_summary(self) -> str:
        """Compact summary of all active teams for WM/prompt injection."""
        active = [t for t in self._teams.values() if t.status in ("active", "created", "paused", "blocked")]
        if not active:
            return ""
        lines = ["[ACTIVE TEAMS]"]
        for team in sorted(active, key=lambda t: t.created_at):
            lines.append(team.to_summary(compact=True))
        return "\n".join(lines)

    async def on_delegate_progress(self, delegate_number: int, status: Any) -> None:
        """Called periodically by the DelegateManager progress monitor."""
        for team in self._teams.values():
            if team.status != "active":
                continue
            member = team.member_by_delegate(delegate_number)
            if member is None:
                continue
            member.iterations = getattr(status, "iteration", member.iterations)
            member.tool_calls = getattr(status, "total_tool_calls", member.tool_calls)
            member.elapsed_seconds = getattr(status, "elapsed_seconds", member.elapsed_seconds)
            await self._broadcast_async("team_member_progress", team, member=member)
            break

    async def on_delegate_complete(self, delegate_number: int, result: Any) -> None:
        """Called when a delegate finishes — updates the matching team member.

        If there are pending (queued) members waiting for a slot, the
        next one is auto-spawned to fill the freed slot.
        """
        for team in self._teams.values():
            if team.status != "active":
                continue
            member = team.member_by_delegate(delegate_number)
            if member is None:
                continue

            _state = getattr(result, "state", "done")
            _exit = getattr(result, "exit_reason", "")
            if _state == "done" and _exit == "task_complete":
                member.status = "done"
            elif _state == "done" and _exit == "orchestrator_terminated":
                member.status = "done"
            elif _state in ("error", "cancelled"):
                member.status = "failed"
            else:
                member.status = "failed"

            member.result_summary = getattr(result, "summary", "")[:500]
            member.iterations = getattr(result, "iteration", 0) or getattr(result, "iterations", 0)
            member.tool_calls = getattr(result, "total_tool_calls", 0)
            member.elapsed_seconds = getattr(result, "elapsed", 0.0) or getattr(result, "elapsed_seconds", 0.0)

            _kanban_map = {"done": "done", "failed": "failed", "cancelled": "cancelled"}
            _kanban_status = _kanban_map.get(member.status, "blocked")
            if self._todo_store is not None and member.kanban_task_id:
                self._todo_store.update(member.kanban_task_id, status=_kanban_status)

            # Immediately sync the plan step so it reflects reality
            # without waiting for the orchestrator to call advance_team().
            if self._plan_store is not None and team.plan_id and member.step_id:
                try:
                    _plan = self._plan_store.load(team.plan_id)
                    if _plan:
                        _step = _plan.get_step(member.step_id)
                        if _step and _step.status != "done":
                            _step.status = "done" if member.status == "done" else "skipped"
                            if member.result_summary:
                                _step.notes = member.result_summary[:500]
                            self._plan_store.save(_plan)
                            logger.info(
                                "TeamManager: synced plan step %s → %s "
                                "on member #%d completion",
                                member.step_id, _step.status,
                                member.delegate_number,
                            )
                            _ps_acc = getattr(self._hooks, "_accumulator", None) if self._hooks else None
                            if _ps_acc is not None:
                                _ps_acc.ingest("PLAN_STEP_CHANGE", {
                                    "label": _step.label,
                                    "old_status": "in_progress",
                                    "new_status": _step.status,
                                })
                except Exception as _exc:
                    logger.debug(
                        "TeamManager: plan step sync failed for %s: %s",
                        member.step_id, _exc,
                    )

            self.save(team)
            await self._broadcast_async("team_member_complete", team, member=member)

            # Forward trajectory to LearningAccumulator
            _acc = getattr(self._hooks, "_accumulator", None) if self._hooks else None
            if _acc is not None:
                try:
                    _files: list[str] = []
                    _tool_s: dict[str, int] = {}
                    _tool_e: dict[str, int] = {}
                    _sh = getattr(result, "state_holder", None)
                    if _sh and isinstance(_sh, list) and _sh:
                        _ls = _sh[0]
                        _files = list(getattr(_ls, "files_written", []))
                        _tool_s = dict(getattr(_ls, "tool_successes", {}))
                        _tool_e = dict(getattr(_ls, "tool_errors", {}))
                    _esc = self._escalation_counts.get(delegate_number, 0)
                    _acc.on_member_complete(
                        member_idx=team.members.index(member),
                        delegate_number=member.delegate_number,
                        task=member.task,
                        status=member.status,
                        result_summary=member.result_summary,
                        iterations=member.iterations,
                        tool_calls=member.tool_calls,
                        elapsed_seconds=member.elapsed_seconds,
                        files_written=_files,
                        tool_successes=_tool_s,
                        tool_errors=_tool_e,
                        escalation_count=_esc,
                    )
                except Exception:
                    logger.debug("TeamManager: accumulator on_member_complete failed", exc_info=True)

            # Update WM orchestration state for member completion
            if self._hooks is not None:
                _h = self._hooks
                member_idx = team.members.index(member)
                if getattr(_h, "wm_orch_update_team", None):
                    _h.wm_orch_update_team(
                        team.id, team.plan_id, team.status,
                        [
                            {
                                "index": i,
                                "task_summary": m.task[:80],
                                "status": m.status,
                                "delegate_number": m.delegate_number,
                                "iterations_used": m.iterations,
                            }
                            for i, m in enumerate(team.members)
                        ],
                    )
                if getattr(_h, "wm_orch_record_decision", None):
                    _status_label = "completed" if member.status == "done" else "failed"
                    _h.wm_orch_record_decision(
                        action=f"member_{_status_label}",
                        context=f"Member #{member_idx}: {member.task[:60]}",
                        outcome=member.result_summary[:200] if member.result_summary else _status_label,
                        team_id=team.id,
                        member_idx=member_idx,
                    )

            # --- Auto-spawn next queued member if slot available ---
            await self._spawn_next_pending(team)

            if team.all_members_done():
                _outcome = team.compute_outcome()
                team.status = _outcome
                team.completed_at = __import__("time").time()
                _kanban_status = {"completed": "done", "partial": "in_progress", "failed": "blocked"}[_outcome]
                if self._todo_store is not None and team.kanban_parent_id:
                    self._todo_store.update(team.kanban_parent_id, status=_kanban_status)

                if team.checkback_job and self._scheduler_manager is not None:
                    try:
                        if self._scheduler_manager.remove_job(team.checkback_job):
                            logger.info(
                                "TeamManager: cancelled check-back '%s' for completed team %s",
                                team.checkback_job, team.id,
                            )
                    except Exception:
                        pass

                if team.plan_id:
                    try:
                        plan = self._plan_store.load(team.plan_id)
                        if plan:
                            for m in team.members:
                                step = next((s for s in plan.steps if s.id == m.step_id), None)
                                if step:
                                    step.status = "done" if m.status == "done" else "skipped"
                                    if m.result_summary:
                                        step.notes = m.result_summary[:500]
                            self._plan_store.save(plan)
                    except Exception:
                        logger.debug("TeamManager: plan update on team complete failed", exc_info=True)

                # Record team completion in WM orchestration state
                if self._hooks is not None:
                    _h = self._hooks
                    if getattr(_h, "wm_orch_update_team", None):
                        _h.wm_orch_update_team(
                            team.id, team.plan_id, _outcome,
                            [
                                {
                                    "index": i,
                                    "task_summary": m.task[:80],
                                    "status": m.status,
                                    "delegate_number": m.delegate_number,
                                    "iterations_used": m.iterations,
                                }
                                for i, m in enumerate(team.members)
                            ],
                        )
                    if getattr(_h, "wm_orch_record_decision", None):
                        _ok = sum(1 for m in team.members if m.status == "done")
                        _fail = len(team.members) - _ok
                        _h.wm_orch_record_decision(
                            action="team_completed",
                            context=f"Team {team.name}: {_ok}/{len(team.members)} succeeded",
                            outcome=f"{_outcome}: {_ok} done, {_fail} failed",
                            team_id=team.id,
                        )

                self.save(team)
                logger.info(
                    "TeamManager: all members of team %s done — team completed",
                    team.id,
                )
                await self._broadcast_async("team_complete", team)

                # Forward wave-complete to LearningAccumulator
                _acc2 = getattr(self._hooks, "_accumulator", None) if self._hooks else None
                if _acc2 is not None:
                    try:
                        _ok = sum(1 for m in team.members if m.status == "done")
                        _fail = len(team.members) - _ok
                        _acc2.on_wave_complete(
                            wave_num=team.wave_index,
                            team_name=team.name,
                            member_count=len(team.members),
                            success_count=_ok,
                            fail_count=_fail,
                            outcome=_outcome,
                        )
                    except Exception:
                        logger.debug("TeamManager: accumulator on_wave_complete failed", exc_info=True)

                # Push a priority interrupt to the orchestrator's loop
                # so it handles the completion on the very next iteration
                # instead of waiting for a check-back or manual inspect.
                if self._copilot_queue is not None:
                    _ok = sum(1 for m in team.members if m.status == "done")
                    _fail = len(team.members) - _ok
                    _completion_msg = (
                        f"[TEAM COMPLETED — ACTION REQUIRED]\n"
                        f"Team: {team.name} [{team.id}]\n"
                        f"Outcome: {_outcome.upper()} "
                        f"({_ok} done, {_fail} failed)\n"
                        f"You MUST call team(action='advance', "
                        f"team_id='{team.id}') NOW to finalize this "
                        f"wave and proceed to the next one.\n"
                        f"Finish your current action, then handle "
                        f"this immediately."
                    )
                    try:
                        self._copilot_queue.put_nowait(_completion_msg)
                    except Exception:
                        logger.debug(
                            "TeamManager: copilot_queue injection "
                            "failed for team completion",
                        )
            break

    async def _spawn_next_pending(self, team: "Team") -> None:
        """Spawn the next pending member if a delegate slot is free."""
        if self._delegate_manager is None:
            return

        next_pending = next(
            (m for m in team.members if m.status == "pending"), None,
        )
        if next_pending is None:
            return

        max_concurrent = getattr(
            self._delegate_manager, "_max_concurrent",
            self._delegate_manager.MAX_CONCURRENT_DELEGATES,
        )
        active_count = sum(
            1 for ds in self._delegate_manager._delegates.values()
            if ds.state == "running"
        )
        if active_count >= max_concurrent:
            return

        run_fn = getattr(team, "_launch_fn", None)
        fn_kwargs = getattr(team, "_launch_kwargs", None)
        if run_fn is None or fn_kwargs is None:
            logger.warning(
                "TeamManager: cannot auto-spawn pending member %d "
                "— no launch_fn cached on team %s",
                next_pending.delegate_number, team.id,
            )
            return

        from .delegate_manager import DelegateSpec

        _preamble_parts: list[str] = []
        if team.briefing:
            _preamble_parts.append(f"[TEAM BRIEFING]\n{team.briefing}")

        # Inject credentials for the new wave member
        if self._hooks is not None:
            _get_creds = getattr(self._hooks, "wm_get_credentials", None)
            if _get_creds is not None:
                try:
                    _creds = _get_creds()
                    if _creds:
                        _cred_lines = [f"  - {d}: {c}" for d, c in _creds]
                        _preamble_parts.append(
                            "[CREDENTIALS — use these for authentication/connections]\n"
                            + "\n".join(_cred_lines)
                        )
                except Exception:
                    pass

        # Inject project_dir from the plan (falls back to any prior plan)
        _project_dir = ""
        if team.plan_id and self._plan_store is not None:
            _plan = self._plan_store.load(team.plan_id)
            if _plan and _plan.project_dir:
                _project_dir = _plan.project_dir
        if not _project_dir and self._plan_store is not None:
            _project_dir = self._plan_store.find_any_project_dir()
        _file_manifest: list[str] = []
        if _project_dir:
            _preamble_parts.append(
                f"[PROJECT DIRECTORY — CRITICAL]\n"
                f"Your CWD (for bash AND file tools) is ALREADY set to {_project_dir}/.\n"
                f"Do NOT `cd {_project_dir}` — you are already inside it.\n"
                f"- bash: run commands directly (e.g. `mkdir -p backend/models`). "
                f"Do NOT prefix with `cd {_project_dir} &&`.\n"
                f"- read/write/glob: use paths relative to {_project_dir}/, "
                f"e.g. write(path=\"backend/main.py\").\n"
                f"Do NOT prepend '{_project_dir}/' to paths — it will double-nest.\n"
                f"NEVER create new top-level project directories."
            )
            # Snapshot existing files for later SubCryptex manifest.
            _ws = self._agent_dir / "workspace" / _project_dir
            if _ws.is_dir():
                try:
                    for f in sorted(_ws.rglob("*")):
                        if f.is_file() and ".git" not in f.parts:
                            _file_manifest.append(str(f.relative_to(_ws)))
                except Exception:
                    pass

        # Surface workspace uploads so delegates use read() not Google Drive.
        _uploads_block = _build_uploads_block(self._agent_dir / "workspace")
        if _uploads_block:
            _preamble_parts.append(_uploads_block)

        task_with_briefing = next_pending.task
        if _preamble_parts:
            task_with_briefing = (
                "\n\n".join(_preamble_parts)
                + f"\n\n[YOUR TASK]\n{next_pending.task}"
            )

        try:
            _ms = int(fn_kwargs.get("max_steps", 15))
        except (ValueError, TypeError):
            _ms = 15
        spec = DelegateSpec(
            task=task_with_briefing,
            delegate_number=next_pending.delegate_number,
            max_steps=_ms,
            file_manifest=_file_manifest,
            team_briefing=team.briefing or "",
            wave=team.wave_index,
        )

        try:
            await self._delegate_manager.spawn_batch(
                [spec],
                run_delegate_fn=run_fn,
                fn_kwargs=fn_kwargs,
                skip_dedup=True,
            )
            next_pending.status = "running"
            if self._todo_store is not None and next_pending.kanban_task_id:
                self._todo_store.update(next_pending.kanban_task_id, status="in_progress")
            self.save(team)
            logger.info(
                "TeamManager: auto-spawned queued member #%d (%s) for team %s",
                next_pending.delegate_number,
                next_pending.task[:60],
                team.id,
            )
            await self._broadcast_async("team_member_spawned", team, member=next_pending)
        except Exception as exc:
            logger.warning(
                "TeamManager: auto-spawn failed for member #%d: %s",
                next_pending.delegate_number, exc,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_kanban_items(self, team: Team, plan: Plan) -> None:
        """Create parent + child todo items linked to the team."""
        if self._todo_store is None:
            return

        parent = self._todo_store.add(
            title=f"{team.name} — Wave {team.wave_index + 1}",
            description=team.mission,
            list_id="projects",
            status="queued",
            source="agent",
            tags=["team"],
            team_id=team.id,
            plan_id=plan.id,
        )
        team.kanban_parent_id = parent.id

        # Build a step-id → step map for enriching Kanban descriptions
        _step_map: dict[str, Any] = {}
        if plan:
            for _s in plan.steps:
                _step_map[_s.id] = _s

        for member in team.members:
            # Build a rich description from plan step metadata
            _desc_parts = [f"Team: {team.name} | Step: {member.step_id}"]
            _step = _step_map.get(member.step_id)
            if _step:
                if _step.description:
                    _desc_parts.append(_step.description)
                if _step.notes:
                    _desc_parts.append(f"Notes: {_step.notes}")
                if _step.output_files:
                    _desc_parts.append(
                        f"Expected output: {', '.join(_step.output_files)}"
                    )

            child = self._todo_store.add(
                title=member.task.split("\n")[0],
                description="\n".join(_desc_parts),
                list_id="projects",
                status="queued",
                source="agent",
                tags=["team-member"],
                team_id=team.id,
                plan_step_id=member.step_id,
                parent_id=parent.id,
                delegate_number=member.delegate_number,
            )
            member.kanban_task_id = child.id

    def _try_create_next_wave(self, completed_team: Team) -> Team | None:
        """Check if the next delegation wave is unblocked and auto-create."""
        plan = self._plan_store.load(completed_team.plan_id)
        if plan is None:
            return None

        waves = get_delegation_waves(plan)
        next_wave = completed_team.wave_index + 1
        if next_wave >= len(waves):
            return None

        # Check that all previous waves' steps are done/skipped
        for w_idx in range(next_wave):
            for step in waves[w_idx]:
                plan_step = plan.get_step(step.id)
                if plan_step and plan_step.status not in ("done", "skipped"):
                    logger.info(
                        "TeamManager: cannot advance to wave %d — "
                        "step '%s' in wave %d still %s",
                        next_wave, step.label, w_idx,
                        plan_step.status if plan_step else "?",
                    )
                    return None

        # Skip waves where all steps are already done (e.g. manually
        # completed out-of-band).  Find the first wave with pending work.
        while next_wave < len(waves):
            has_pending = any(
                plan.get_step(s.id) is not None
                and plan.get_step(s.id).status not in ("done", "skipped")
                for s in waves[next_wave]
            )
            if has_pending:
                break
            logger.info(
                "TeamManager: skipping wave %d — all steps already done",
                next_wave,
            )
            next_wave += 1

        if next_wave >= len(waves):
            return None

        return self.create_team(
            plan_id=completed_team.plan_id,
            wave_index=next_wave,
            name=f"{plan.title} — Wave {next_wave + 1}",
            briefing=self._compile_briefing(completed_team),
        )

    # ───────────────────────────────────────────────────────────────
    # Orchestration context snapshot (for micro-inference / thalamic router)
    # ───────────────────────────────────────────────────────────────

    def get_orchestration_context(self, compact: bool = True) -> str:
        """Return a compact text summary of active orchestration state.

        Designed for injection into micro-inference prompts so the LLM
        can answer status queries without a full agentic loop.

        Parameters
        ----------
        compact : bool
            If True (default), limits output to ~600 tokens.  False gives
            the full detail.
        """
        active_teams = [t for t in self._teams.values() if not t.is_terminal]
        terminal_recent = sorted(
            (t for t in self._teams.values() if t.is_terminal),
            key=lambda t: t.completed_at, reverse=True,
        )[:2]

        if not active_teams and not terminal_recent:
            return "No active or recent teams."

        lines: list[str] = []

        for team in active_teams:
            lines.append(f"ACTIVE TEAM: {team.name} [{team.id}]")
            lines.append(f"  Plan: {team.plan_id}  Wave: {team.wave_index + 1}")
            lines.append(f"  Progress: {team.progress}")
            for m in team.members:
                task_short = m.task[:120] if compact else m.task
                elapsed = f"{m.elapsed_seconds:.0f}s" if m.elapsed_seconds else "—"
                lines.append(
                    f"  - #{m.delegate_number} [{m.status}] "
                    f"{task_short} (iters={m.iterations}, elapsed={elapsed})"
                )
            lines.append("")

        for team in terminal_recent:
            outcome = team.status
            lines.append(
                f"RECENT TEAM: {team.name} [{team.id}] — {outcome}"
            )
            if compact:
                done = sum(1 for m in team.members if m.status == "done")
                failed = sum(1 for m in team.members if m.status == "failed")
                lines.append(f"  {done} succeeded, {failed} failed")
            else:
                for m in team.members:
                    lines.append(
                        f"  - #{m.delegate_number} [{m.status}] {m.task[:120]}"
                    )
            lines.append("")

        dm = self._delegate_manager
        if dm is not None:
            try:
                statuses = dm.get_status()
                running = [s for s in statuses if s.state == "running"]
                if running:
                    lines.append(f"Running delegates: {len(running)}")
                    for s in running[:5]:
                        lines.append(
                            f"  #{s.delegate_number}: iter {s.iteration} — "
                            f"{(s.task or '')[:80]}"
                        )
            except Exception:
                pass

        result = "\n".join(lines)
        if compact and len(result) > 2400:
            result = result[:2400] + "\n[...truncated]"
        return result

    def has_active_orchestration(self) -> bool:
        """True if any team is non-terminal or delegates are running."""
        if any(not t.is_terminal for t in self._teams.values()):
            return True
        dm = self._delegate_manager
        if dm is not None:
            try:
                return dm.has_active_delegates()
            except Exception:
                pass
        return False

    def _compile_briefing(self, previous_team: Team) -> str:
        """Build briefing from previous team results for the next wave."""
        parts = [
            f"Previous wave ({previous_team.name}) completed.",
            "Results from previous agents:",
        ]
        for member in previous_team.members:
            summary = member.result_summary or "(no summary)"
            parts.append(f"  - {member.task}: {summary}")
        if previous_team.briefing:
            parts.append(f"\nAccumulated context:\n{previous_team.briefing}")
        result = "\n".join(parts)
        result, n = _sanitize_secrets(result)
        if n:
            logger.warning(
                "TeamManager: sanitized %d secret(s) from compiled briefing for team %s",
                n, previous_team.id,
            )
        return result

    def _broadcast_sync(
        self, event_type: str, team: Team, *, member: TeamMember | None = None,
    ) -> None:
        if self._connection_manager is None:
            return
        payload: dict[str, Any] = {
            "type": event_type,
            "team": team.to_dict(),
        }
        if member is not None:
            payload["member"] = member.to_dict()
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(
                    self._connection_manager.broadcast(self._agent_id, payload)
                )
            else:
                loop.run_until_complete(
                    self._connection_manager.broadcast(self._agent_id, payload)
                )
        except Exception as exc:
            logger.debug("TeamManager broadcast failed: %s", exc)

    async def _broadcast_async(
        self, event_type: str, team: Team, *, member: TeamMember | None = None,
    ) -> None:
        if self._connection_manager is None:
            return
        payload: dict[str, Any] = {
            "type": event_type,
            "team": team.to_dict(),
        }
        if member is not None:
            payload["member"] = member.to_dict()
        try:
            await self._connection_manager.broadcast(self._agent_id, payload)
        except Exception as exc:
            logger.debug("TeamManager broadcast failed: %s", exc)
