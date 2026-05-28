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
from .delegate_manager import DELEGATE_DEFAULT_MAX_STEPS, DELEGATE_UNASSIGNED_NUMBER

logger = logging.getLogger(__name__)

TEAM_STATUSES = ("created", "active", "paused", "completed", "partial", "failed", "blocked")

# Wave-complete EM review: dedup wakes, then auto-close without model if idle.
WAVE_REVIEW_MAX_WAKES = 3
WAVE_REVIEW_WAKE_COOLDOWN_SECONDS = 90.0
WAVE_REVIEW_GRACE_SECONDS = 180.0
WAVE_EMPTY_REVIEW_AUTO_RECONCILE = 2

_INTERRUPTED_MEMBER_SUMMARY = (
    "Sub-agent interrupted (runtime stopped before completion). "
    "Re-launch the wave with team(action='launch') if work should continue."
)


@dataclass(frozen=True)
class PendingAutoLaunch:
    """Next-wave team created by auto-reconcile; awaiting policy-guarded launch."""

    team_id: str
    reason: str


def _plan_step_status_for_member(member_status: str) -> str:
    """Map a team member's terminal status to a plan step status."""
    if member_status == "done":
        return "done"
    if member_status in ("failed", "cancelled"):
        return "failed"
    return "skipped"


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
    wave_attempt: int = 1
    supersedes_team_id: str = ""
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
    checkback_suppressed: bool = False
    wave_review_wakes: int = 0
    wave_review_last_wake_at: float = 0.0
    wave_empty_reviews: int = 0

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
        return self.status in ("completed", "partial", "failed", "cancelled")

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
            "wave_attempt": self.wave_attempt,
            "supersedes_team_id": self.supersedes_team_id,
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
            "checkback_suppressed": self.checkback_suppressed,
            "wave_review_wakes": self.wave_review_wakes,
            "wave_review_last_wake_at": self.wave_review_last_wake_at,
            "wave_empty_reviews": self.wave_empty_reviews,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Team:
        members = [TeamMember.from_dict(m) for m in d.get("members", [])]
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            plan_id=d.get("plan_id", ""),
            wave_index=d.get("wave_index", 0),
            wave_attempt=d.get("wave_attempt", 1),
            supersedes_team_id=d.get("supersedes_team_id", ""),
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
            checkback_suppressed=d.get("checkback_suppressed", False),
            wave_review_wakes=d.get("wave_review_wakes", 0),
            wave_review_last_wake_at=d.get("wave_review_last_wake_at", 0.0),
            wave_empty_reviews=d.get("wave_empty_reviews", 0),
        )

    def to_summary(self, compact: bool = False) -> str:
        """Render a human-readable summary for prompt injection."""
        if compact:
            _attempt = (
                f" attempt {self.wave_attempt}" if self.wave_attempt > 1 else ""
            )
            return (
                f"[{self.id}] {self.name} (wave {self.wave_index + 1}{_attempt}, "
                f"{self.status}) — {self.progress} members done"
            )
        _tags: list[str] = []
        if self.completion_reported:
            _tags.append("WAVE ADVANCED")
        if self.checkback_suppressed:
            _tags.append("CHECKBACK SUPPRESSED")
        _tag_str = f" [{', '.join(_tags)}]" if _tags else ""
        _attempt_note = (
            f" | Attempt: {self.wave_attempt}" if self.wave_attempt > 1 else ""
        )
        if self.supersedes_team_id:
            _attempt_note += f" | Retries: {self.supersedes_team_id}"
        lines = [
            f"Team: {self.name} [{self.id}]",
            f"  Plan: {self.plan_id} | Wave: {self.wave_index}{_attempt_note} | "
            f"Status: {self.status}{_tag_str}",
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


_PEER_ASSIGNMENT_MARKERS = (
    "### MEMBER ASSIGNMENTS",
    "## MEMBER ASSIGNMENTS",
    "MEMBER ASSIGNMENTS",
)


def _wave_context_without_peer_specs(briefing: str) -> str:
    """Wave-wide context without other members' full task specs."""
    text = (briefing or "").strip()
    if not text:
        return ""
    for marker in _PEER_ASSIGNMENT_MARKERS:
        idx = text.find(marker)
        if idx >= 0:
            text = text[:idx].strip()
            break
    return text[:2000]


def _peer_awareness_block(team: Team, member_idx: int) -> str:
    """Tell a delegate what teammates own — awareness only, not their scope."""
    others: list[str] = []
    for i, member in enumerate(team.members):
        if i == member_idx:
            continue
        headline = (member.task or "").split("\n")[0].strip()
        if not headline:
            headline = member.step_id or f"member {i}"
        others.append(
            f"  - Member {i} (delegate #{member.delegate_number}): "
            f"{headline[:120]}"
        )
    if not others:
        return ""
    return (
        "[PARALLEL TEAM — peer awareness only]\n"
        "You are told what teammates are doing so you do NOT duplicate their "
        "work (same files, same layer). This is NOT an invitation to implement "
        "their tasks — only complete [YOUR TASK] below.\n"
        "Coordinate through the shared repo; do not edit paths outside your "
        "assignment unless your task explicitly requires it.\n"
        "Others in this wave:\n"
        + "\n".join(others)
    )


def _member_launch_preamble(
    team: Team,
    member_idx: int,
    *,
    credential_block: str = "",
    uploads_block: str = "",
    project_dir_block: str = "",
) -> list[str]:
    """Build per-delegate preamble: peer awareness + wave context, not full specs."""
    parts: list[str] = []
    peer = _peer_awareness_block(team, member_idx)
    if peer:
        parts.append(peer)
    wave_ctx = (team.mission or "").strip() or _wave_context_without_peer_specs(
        team.briefing,
    )
    if wave_ctx:
        parts.append(f"[WAVE CONTEXT]\n{wave_ctx[:2000]}")
    if credential_block:
        parts.append(credential_block)
    if uploads_block:
        parts.append(uploads_block)
    if project_dir_block:
        parts.append(project_dir_block)
    return parts


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
        self._dispatch_drain: Callable[[str], int] | None = None
        self._schedule_orchestration_wake: Callable[[str, str], None] | None = None
        # delegate_number → pending EM completion review (delegate blocked)
        self._pending_completion_reviews: dict[int, dict[str, Any]] = {}
        self._pending_auto_launch: PendingAutoLaunch | None = None
        self._file_ledger: Any | None = None
        self._load_all()

    def set_file_ledger(self, ledger: Any | None) -> None:
        """Shared file ledger for wave ownership enforcement."""
        self._file_ledger = ledger

    def _clear_wave_ownership(self, team: "Team") -> None:
        if self._file_ledger is None:
            return
        try:
            self._file_ledger.clear_active_wave(team.wave_index)
        except Exception:
            pass

    def set_hooks(self, hooks: Any) -> None:
        """Wire LoopHooks so orchestration events update WM."""
        self._hooks = hooks

    def set_dispatch_drain(
        self, fn: Callable[[str], int] | None,
    ) -> None:
        """Drain inner-loop pending dispatches (source_exact e.g. team_checkback:team_x)."""
        self._dispatch_drain = fn

    def set_schedule_orchestration_wake(
        self, fn: Callable[[str, str], None] | None,
    ) -> None:
        """Schedule an inner-loop autonomous dispatch (prompt, source)."""
        self._schedule_orchestration_wake = fn

    def offer_pending_auto_launch(self, team: Team, reason: str) -> None:
        """Queue a created next-wave team for policy-guarded auto-launch."""
        if team.status != "created" or team.batch_id:
            return
        self._pending_auto_launch = PendingAutoLaunch(team.id, reason)
        logger.info(
            "TeamManager: pending auto-launch for %s (reason=%s)",
            team.id, reason,
        )

    def pop_pending_auto_launch(self) -> PendingAutoLaunch | None:
        pending = self._pending_auto_launch
        self._pending_auto_launch = None
        return pending

    def clear_pending_auto_launch(self, team_id: str | None = None) -> None:
        if self._pending_auto_launch is None:
            return
        if team_id is None or self._pending_auto_launch.team_id == team_id:
            self._pending_auto_launch = None

    def discover_unlaunched_wave_teams(self) -> list[Team]:
        """Teams in ``created`` with no batch — usually stale auto-reconcile."""
        return [
            t for t in self._teams.values()
            if t.status == "created" and not t.batch_id
        ]

    def enqueue_unlaunched_for_auto_launch(self) -> int:
        """Offer the newest unlaunched wave if nothing is already pending."""
        if self._pending_auto_launch is not None:
            return 0
        candidates = self.discover_unlaunched_wave_teams()
        if not candidates:
            return 0
        team = max(candidates, key=lambda t: t.created_at)
        self.offer_pending_auto_launch(team, "recovery_unlaunched")
        return 1

    def schedule_pending_launch_wake(
        self, team_id: str, block_reason: str, *, reconcile_reason: str = "",
    ) -> bool:
        """Wake EM to launch when auto-launch was blocked by policy."""
        if self._schedule_orchestration_wake is None:
            logger.warning(
                "TeamManager: cannot schedule launch wake for %s — no scheduler",
                team_id,
            )
            return False
        team = self._teams.get(team_id)
        from nls.agentic.orchestration_policy import build_pending_wave_launch_wake

        msg = build_pending_wave_launch_wake(
            team_id,
            team_name=team.name if team else "",
            reconcile_reason=reconcile_reason,
            block_reason=block_reason,
        )
        try:
            self._schedule_orchestration_wake(
                msg, f"pending_wave_launch:{team_id}",
            )
            logger.info(
                "TeamManager: scheduled launch wake for %s (%s)",
                team_id, block_reason[:80],
            )
            return True
        except Exception:
            logger.debug("TeamManager: pending launch wake failed", exc_info=True)
            return False

    def _should_auto_reconcile_wave(self, team: Team) -> bool:
        """True when EM review is stale and the wave should close without model."""
        if not team.is_terminal or team.completion_reported:
            return False
        now = time.time()
        if team.completed_at and (now - team.completed_at) >= WAVE_REVIEW_GRACE_SECONDS:
            return True
        if team.wave_review_wakes >= WAVE_REVIEW_MAX_WAKES:
            return True
        if team.wave_empty_reviews >= WAVE_EMPTY_REVIEW_AUTO_RECONCILE:
            return True
        return False

    def _drain_wave_complete_dispatch(self, team_id: str) -> None:
        if self._dispatch_drain is None:
            return
        try:
            removed = self._dispatch_drain(f"team_wave_complete:{team_id}")
            if removed:
                logger.info(
                    "TeamManager: drained %d pending wave-complete dispatch(es) for %s",
                    removed, team_id,
                )
        except Exception:
            logger.debug("TeamManager: wave-complete dispatch drain failed", exc_info=True)

    def _drain_stale_wave_complete_dispatches_for_plan(self, plan_id: str) -> None:
        """Drop queued EM review wakes for finalized waves on this plan."""
        for team in self._teams.values():
            if team.plan_id == plan_id and team.completion_reported:
                self._drain_wave_complete_dispatch(team.id)

    def stale_wave_review_wake_reason(self, team_id: str) -> str:
        """Non-empty when a ``team_wave_complete`` wake is redundant."""
        team = self._teams.get(team_id)
        if team is None:
            return "team_missing"
        if not team.is_terminal:
            return ""
        if not team.completion_reported:
            return ""
        for other in self._teams.values():
            if other.plan_id != team.plan_id:
                continue
            if other.wave_index <= team.wave_index:
                continue
            if other.batch_id and other.status in (
                "active", "created", "paused",
            ):
                return f"successor_wave_running:{other.id}"
        return "wave_already_finalized"

    def _sync_plan_steps_from_team(self, team: Team) -> None:
        """Mirror team member outcomes onto plan steps; block plan on partial waves."""
        if not team.plan_id:
            return
        plan = self._plan_store.load(team.plan_id)
        if plan is None:
            return
        failed_step_ids: list[str] = []
        for member in team.members:
            step = plan.get_step(member.step_id)
            if step is None:
                continue
            step.status = _plan_step_status_for_member(member.status)
            if member.result_summary:
                step.notes = member.result_summary[:500]
            if member.status in ("failed", "cancelled"):
                failed_step_ids.append(member.step_id)
        if team.is_terminal and team.status in ("partial", "failed"):
            from nls.agentic.plan_work import mark_plan_blocked_for_partial_wave

            mark_plan_blocked_for_partial_wave(
                plan,
                team_id=team.id,
                failed_step_ids=failed_step_ids,
            )
        self._plan_store.save(plan)

    def _finalize_unreported_wave_sync(
        self, team_id: str, *, reason: str,
    ) -> Team | None:
        """Sync idempotent wave closure when EM never called team(advance)."""
        team = self._teams.get(team_id)
        if team is None or not team.is_terminal:
            return None
        if team.completion_reported:
            if team.status == "completed":
                _existing_next = self._try_create_next_wave(team)
                if (
                    _existing_next is not None
                    and _existing_next.status == "created"
                    and not _existing_next.batch_id
                ):
                    self.offer_pending_auto_launch(_existing_next, reason)
                return _existing_next or team
            return team

        team.completion_reported = True
        if team.checkback_job and self._scheduler_manager is not None:
            try:
                self._scheduler_manager.remove_job(team.checkback_job)
            except Exception:
                pass
        self._drain_team_checkback_dispatch(team_id)
        self._drain_wave_complete_dispatch(team_id)
        self.cleanup_plan_checkbacks(team.plan_id)

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

        self._sync_plan_steps_from_team(team)

        _kanban_parent_status = {
            "completed": "done",
            "partial": "in_progress",
            "failed": "failed",
        }.get(team.status, "failed")
        if self._todo_store is not None:
            if team.kanban_parent_id:
                self._todo_store.update(
                    team.kanban_parent_id, status=_kanban_parent_status,
                )
            for member in team.members:
                if member.kanban_task_id and member.status == "done":
                    self._todo_store.update(member.kanban_task_id, status="done")
                elif member.kanban_task_id and member.status in (
                    "failed", "cancelled",
                ):
                    self._todo_store.update(
                        member.kanban_task_id, status="failed",
                    )

        self.save(team)
        logger.info(
            "TeamManager: auto-reconciled wave %s (reason=%s, status=%s, "
            "wakes=%d, empty_reviews=%d)",
            team.id, reason, team.status,
            team.wave_review_wakes, team.wave_empty_reviews,
        )

        next_team = None
        if team.status == "completed":
            next_team = self._try_create_next_wave(team)
            if next_team is not None and next_team.status == "created":
                self.offer_pending_auto_launch(next_team, reason)
        return next_team or team

    def try_auto_reconcile_wave_sync(
        self, team_id: str, *, reason: str,
    ) -> Team | None:
        team = self._teams.get(team_id)
        if team is None or not self._should_auto_reconcile_wave(team):
            return None
        return self._finalize_unreported_wave_sync(team_id, reason=reason)

    async def handle_wave_review_loop_end(
        self, team_id: str, *, tool_calls: int,
    ) -> None:
        """Track empty EM reviews on team_wave_complete wakes; auto-close if stale."""
        team = self._teams.get(team_id)
        if team is None or team.completion_reported:
            return
        if tool_calls <= 0:
            team.wave_empty_reviews += 1
            self.save(team)
            logger.info(
                "TeamManager: empty wave review for %s (%d/%d)",
                team_id,
                team.wave_empty_reviews,
                WAVE_EMPTY_REVIEW_AUTO_RECONCILE,
            )
        if not self._should_auto_reconcile_wave(team):
            return
        result = self._finalize_unreported_wave_sync(team_id, reason="empty_em_review")
        if result is not None:
            await self._broadcast_async("team_advanced", team)

    def _wave_review_message(self, team: Team) -> str:
        _ok = sum(1 for m in team.members if m.status == "done")
        _fail = len(team.members) - _ok
        _outcome = team.compute_outcome()
        if team.completion_reported:
            return (
                f"[TEAM WAVE ALREADY FINALIZED]\n"
                f"Team: {team.name} [{team.id}] "
                f"(status={team.status})\n"
                "Do NOT call team(advance) again.\n"
                "Use switch_mode(evaluating), inspect outputs, "
                "plan(accept_partial) if needed, then launch "
                "next wave or patch gaps."
            )
        from nls.agentic.plan_work import format_recovery_wake

        _failed = [
            m.step_id for m in team.members
            if m.status in ("failed", "cancelled")
        ]
        _base = format_recovery_wake(
            plan_id=team.plan_id,
            team_id=team.id,
            failed_step_ids=_failed,
        )
        return (
            f"{_base}\n"
            f"[WAVE FINISHED — EM REVIEW REQUIRED]\n"
            f"Team: {team.name} [{team.id}]\n"
            f"Outcome: {_outcome.upper()} ({_ok} done, {_fail} failed)\n"
            f"7) team(advance, team_id='{team.id}') after steps are resolved\n"
            "8) Update Kanban; ONE stakeholder update if requested"
        )

    def _notify_wave_review_required(self, team: Team) -> bool:
        """Wake the engineering manager when a wave lands (queue + autonomous dispatch).

        Returns True if a new orchestration wake was enqueued.
        """
        if team.completion_reported:
            self._drain_wave_complete_dispatch(team.id)
            return False

        if self.stale_wave_review_wake_reason(team.id):
            self._drain_wave_complete_dispatch(team.id)
            return False

        if self.try_auto_reconcile_wave_sync(team.id, reason="pre_notify_stale"):
            return False

        now = time.time()
        if team.wave_review_wakes > 0:
            elapsed = now - team.wave_review_last_wake_at
            if elapsed < WAVE_REVIEW_WAKE_COOLDOWN_SECONDS:
                logger.info(
                    "TeamManager: wave review wake skipped (cooldown %.0fs) for %s",
                    WAVE_REVIEW_WAKE_COOLDOWN_SECONDS - elapsed,
                    team.id,
                )
                return False

        if team.wave_review_wakes >= WAVE_REVIEW_MAX_WAKES:
            self.try_auto_reconcile_wave_sync(team.id, reason="wake_cap")
            return False

        msg = self._wave_review_message(team)
        if self._copilot_queue is not None:
            try:
                self._copilot_queue.put_nowait(msg)
            except Exception:
                logger.debug(
                    "TeamManager: copilot_queue injection failed for %s",
                    team.id, exc_info=True,
                )
        if self._schedule_orchestration_wake is None:
            logger.warning(
                "TeamManager: wave %s completed but no orchestration wake "
                "scheduler wired — EM may stay idle until check-back",
                team.id,
            )
            return False
        self._drain_team_checkback_dispatch(team.id)
        routing = (
            f"[AGENT_MSG|agent_id={self._agent_id}] "
            if self._agent_id else ""
        )
        source = f"team_wave_complete:{team.id}"
        try:
            self._schedule_orchestration_wake(routing + msg, source)
            team.wave_review_wakes += 1
            team.wave_review_last_wake_at = now
            self.save(team)
            logger.info(
                "TeamManager: scheduled EM review wake for %s (source=%s, wake=%d/%d)",
                team.id, source, team.wave_review_wakes, WAVE_REVIEW_MAX_WAKES,
            )
            return True
        except Exception:
            logger.debug(
                "TeamManager: orchestration wake failed for %s",
                team.id, exc_info=True,
            )
            return False

    def register_completion_review(
        self,
        team: Team,
        member_idx: int,
        delegate_number: int,
    ) -> None:
        """Track a delegate blocked in completion review until EM intervenes."""
        self._pending_completion_reviews[delegate_number] = {
            "team_id": team.id,
            "team_name": team.name,
            "member_idx": member_idx,
        }

    def clear_completion_review(self, delegate_number: int) -> None:
        self._pending_completion_reviews.pop(delegate_number, None)
        try:
            from nls.agentic.wake_coordination import sync_wake_attention_board
            sync_wake_attention_board(self)
        except Exception:
            pass

    def has_pending_completion_reviews(self) -> bool:
        return bool(self._pending_completion_reviews)

    def completion_review_yield_block_message(self) -> str | None:
        """Return a block message when await_delegates would leave reviews orphaned."""
        if not self._pending_completion_reviews:
            return None
        lines = [
            "BLOCKED: One or more delegates are waiting for your completion "
            "review decision. You cannot exit with await_delegates yet.",
            "",
            "Pending reviews:",
        ]
        for delegate_num, info in self._pending_completion_reviews.items():
            lines.append(
                f"  - Delegate #{delegate_num} on "
                f"{info.get('team_name', '?')} [{info.get('team_id', '?')}] "
                f"member #{info.get('member_idx', '?')}"
            )
            lines.append(
                f"    APPROVE: team(action='intervene', "
                f"team_id='{info.get('team_id', '')}', "
                f"member={info.get('member_idx', 0)}, decision='approve')"
            )
        lines.append(
            "\nAfter approving, call team(advance) if the wave is complete."
        )
        return "\n".join(lines)

    def reconcile_pending_completion_reviews(self) -> int:
        """Re-enqueue one batched EM wake per team with pending reviews."""
        if not self._pending_completion_reviews or self._delegate_manager is None:
            return 0
        teams_touched: set[str] = set()
        for delegate_num, info in list(self._pending_completion_reviews.items()):
            ds = self._delegate_manager._delegates.get(delegate_num)
            if ds is None or ds.state != "running":
                self.clear_completion_review(delegate_num)
                continue
            team = self._teams.get(info.get("team_id", ""))
            if team is None or team.is_terminal:
                self.clear_completion_review(delegate_num)
                continue
            teams_touched.add(team.id)
        n = 0
        for team_id in teams_touched:
            team = self._teams.get(team_id)
            if team is None:
                continue
            if self._notify_completion_review_required(team, is_reminder=True):
                n += 1
        if n:
            logger.info(
                "TeamManager: re-queued batched completion-review wake for %d team(s)",
                n,
            )
        return n

    def _drain_completion_review_dispatches(self, team_id: str) -> int:
        """Remove queued completion-review wakes for a team (batched + legacy)."""
        removed = 0
        if self._dispatch_drain is not None:
            try:
                removed += self._dispatch_drain(
                    source_exact=f"team_completion_review:{team_id}",
                )
            except Exception:
                logger.debug("TeamManager: completion-review drain failed", exc_info=True)
        try:
            from server.main import app as _app

            cs = getattr(_app.state, "consciousness_scheduler", None)
            if cs is not None:
                il = cs.get_inner_loop(self._agent_id)
                if il is not None:
                    removed += il.drain_pending_dispatches(
                        source_prefix=f"team_completion_review:{team_id}",
                    )
        except Exception:
            pass
        return removed

    def _notify_completion_review_required(
        self,
        team: Team,
        delegate_number: int = 0,
        review_msg: str = "",
        *,
        is_reminder: bool = False,
    ) -> bool:
        """Wake the EM once per team when delegate(s) enter completion review."""
        from nls.agentic.wake_coordination import schedule_batched_completion_review_wake

        if self._schedule_orchestration_wake is None:
            logger.warning(
                "TeamManager: completion review for %s but no "
                "orchestration wake scheduler wired",
                team.id,
            )
            return False
        return schedule_batched_completion_review_wake(
            self, team, is_reminder=is_reminder,
        )

    def reconcile_unreported_terminal_teams(self) -> int:
        """Enqueue EM review for terminal teams missing team(advance), with dedup/cap."""
        n = 0
        for team in self._teams.values():
            if not team.is_terminal or team.completion_reported:
                continue
            if self.try_auto_reconcile_wave_sync(team.id, reason="loop_start_stale"):
                continue
            if self._notify_wave_review_required(team):
                n += 1
        if n:
            logger.info(
                "TeamManager: scheduled review wake for %d unreported terminal team(s)",
                n,
            )
        return n

    def _drain_team_checkback_dispatch(self, team_id: str) -> int:
        if self._dispatch_drain is None:
            return 0
        try:
            removed = self._dispatch_drain(f"team_checkback:{team_id}")
            if removed:
                logger.info(
                    "TeamManager: drained %d pending check-back dispatch(es) for %s",
                    removed, team_id,
                )
            return removed
        except Exception:
            logger.debug("TeamManager: dispatch drain failed", exc_info=True)
            return 0

    def cleanup_plan_checkbacks(self, plan_id: str) -> None:
        """Cancel scheduler jobs and drain queued check-backs for a plan."""
        if not plan_id:
            return
        for team in self._teams.values():
            if team.plan_id != plan_id:
                continue
            if team.checkback_job and self._scheduler_manager is not None:
                try:
                    self._scheduler_manager.remove_job(team.checkback_job)
                except Exception:
                    pass
            self._drain_team_checkback_dispatch(team.id)

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

        _same_wave = [
            t for t in self._teams.values()
            if t.plan_id == plan_id and t.wave_index == wave_index
        ]
        _wave_attempt = max((t.wave_attempt for t in _same_wave), default=0) + 1
        _supersedes = ""
        if _same_wave:
            _prev = max(_same_wave, key=lambda t: t.created_at)
            if _prev.is_terminal:
                _supersedes = _prev.id

        team = Team(
            name=name,
            plan_id=plan_id,
            wave_index=wave_index,
            wave_attempt=_wave_attempt,
            supersedes_team_id=_supersedes,
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
                delegate_number=DELEGATE_UNASSIGNED_NUMBER,
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

    def teams_for_wave(self, plan_id: str, wave_index: int) -> list[Team]:
        """All teams for a plan wave, newest attempt first."""
        out = [
            t for t in self._teams.values()
            if t.plan_id == plan_id and t.wave_index == wave_index
        ]
        return sorted(out, key=lambda t: t.created_at, reverse=True)

    def has_orchestrator_blocking_team(self) -> bool:
        """True when the orchestrator must not self-implement (delegates running).

        A team stuck in ``active`` with all members already terminal does NOT
        block — the orchestrator may salvage in ``evaluating`` mode.
        """
        for team in self._teams.values():
            if team.is_terminal:
                continue
            if team.status in ("created", "paused", "blocked"):
                return True
            if team.status == "active":
                if any(m.status == "running" for m in team.members):
                    return True
                if any(m.status == "pending" for m in team.members):
                    return True
        return False

    def _effective_delegate_state(self, ds: Any) -> str:
        """Map persisted/running delegate rows to a status safe for team tools."""
        state = getattr(ds, "state", "")
        if state != "running":
            return state
        num = getattr(ds, "delegate_number", -1)
        if (
            self._delegate_manager is not None
            and not self._delegate_manager.is_delegate_live(int(num))
        ):
            return "interrupted"
        return "running"

    def _apply_delegate_status_to_member(self, member: TeamMember, ds: Any) -> None:
        """Update a team member from a delegate status snapshot."""
        member.iterations = getattr(ds, "iteration", member.iterations)
        member.tool_calls = getattr(ds, "total_tool_calls", member.tool_calls)
        member.elapsed_seconds = getattr(
            ds, "elapsed_seconds", getattr(ds, "elapsed", member.elapsed_seconds),
        )
        last_actions = getattr(ds, "last_actions", None)
        if last_actions:
            member.last_actions = list(last_actions[-5:])
        hint_ack = getattr(ds, "hint_ack", "")
        if hint_ack:
            member.hint_ack = hint_ack

        state = self._effective_delegate_state(ds)
        if state == "running":
            member.status = "running"
        elif state == "done":
            member.status = "done"
            preview = getattr(ds, "summary_preview", "") or getattr(ds, "summary", "")
            if preview:
                member.result_summary = str(preview)[:500]
        elif state == "interrupted":
            member.status = "failed"
            if not member.result_summary:
                member.result_summary = _INTERRUPTED_MEMBER_SUMMARY
        elif state in ("error", "cancelled"):
            member.status = "failed" if state == "error" else "cancelled"

    def reconcile_with_delegates(
        self, *, persist: bool = True, team_id: str | None = None,
    ) -> int:
        """Sync team members with DelegateManager (e.g. after runtime restart)."""
        if self._delegate_manager is None:
            return 0
        changed_teams = 0
        teams = self._teams.values()
        if team_id is not None:
            team = self._teams.get(team_id)
            teams = [team] if team is not None else []
        for team in teams:
            if team.is_terminal:
                continue
            team_changed = False
            for member in team.members:
                if member.status not in ("running", "pending"):
                    continue
                if member.delegate_number <= 0:
                    continue
                ds = self._delegate_manager._delegates.get(member.delegate_number)
                old_status = member.status
                if ds is None:
                    if member.status == "running" and team.batch_id:
                        member.status = "failed"
                        if not member.result_summary:
                            member.result_summary = _INTERRUPTED_MEMBER_SUMMARY
                        team_changed = True
                    continue
                if team.batch_id and getattr(ds, "batch_id", "") != team.batch_id:
                    continue
                self._apply_delegate_status_to_member(member, ds)
                if member.status != old_status:
                    team_changed = True
            if team_changed:
                changed_teams += 1
                if persist:
                    self.save(team)
        if changed_teams:
            logger.info(
                "TeamManager: reconciled %d team(s) with delegate state",
                changed_teams,
            )
        return changed_teams

    def inspect_team(self, team_id: str) -> Team | None:
        """Return full team detail including live delegate status.

        Returns a deep copy enriched with ephemeral delegate data so
        that the persistent Team object is never mutated by reads.
        """
        team = self._teams.get(team_id)
        if team is None:
            return None

        if self._delegate_manager is not None and not team.is_terminal:
            self.reconcile_with_delegates(team_id=team_id, persist=True)
            team = self._teams.get(team_id)
            if team is None:
                return None

        # Work on a deep copy so live delegate data doesn't bleed
        _STALE_TERMINAL_SECONDS = 900
        if (
            team.is_terminal
            and not team.checkback_suppressed
            and team.completed_at > 0
            and (time.time() - team.completed_at) > _STALE_TERMINAL_SECONDS
        ):
            team.checkback_suppressed = True
            if team.checkback_job and self._scheduler_manager is not None:
                try:
                    self._scheduler_manager.remove_job(team.checkback_job)
                except Exception:
                    pass
            self._drain_team_checkback_dispatch(team_id)
            self.save(team)
            logger.warning(
                "TeamManager: suppressed stale check-backs for team %s "
                "(terminal for %.0fs — call team advance after review)",
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
            seen: set[int] = set()
            for ds in statuses:
                member = snapshot.member_by_delegate(ds.delegate_number)
                if member is not None:
                    seen.add(ds.delegate_number)
                    self._apply_delegate_status_to_member(member, ds)
            for member in snapshot.members:
                if member.delegate_number in seen:
                    continue
                if member.status != "running":
                    continue
                ds = self._delegate_manager._delegates.get(member.delegate_number)
                if ds is not None:
                    if (
                        snapshot.batch_id
                        and getattr(ds, "batch_id", "") != snapshot.batch_id
                    ):
                        continue
                    self._apply_delegate_status_to_member(member, ds)
                elif snapshot.batch_id:
                    member.status = "failed"
                    if not member.result_summary:
                        member.result_summary = _INTERRUPTED_MEMBER_SUMMARY

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

        _tech_stack_block = ""
        _plan_for_wave = None
        if team.plan_id and self._plan_store is not None:
            _plan_for_wave = self._plan_store.load(team.plan_id)
            if _plan_for_wave is not None:
                from .wave_coordination import build_tech_stack_block
                _tech_stack_block = build_tech_stack_block(plan=_plan_for_wave)

        from .wave_coordination import (
            build_file_ownership_block,
            build_wave_ownership_registry,
            derive_shared_paths,
            resolve_step_owned_paths,
        )

        # Assign delegate numbers before building the ownership registry.
        for i, member in enumerate(team.members):
            member.delegate_number = base_num + i

        _wave_registry, _peer_registry_lines = build_wave_ownership_registry(
            team.members,
            plan=_plan_for_wave,
        )
        _shared_paths: list[str] = []
        if _project_dir:
            _ws_path = self._agent_dir / "workspace" / _project_dir
            if _ws_path.is_dir():
                _shared_paths = derive_shared_paths(_ws_path)
        if self._file_ledger is not None:
            try:
                self._file_ledger.set_wave_ownership(
                    team.wave_index,
                    _wave_registry,
                    shared_paths=_shared_paths,
                    project_dir=_project_dir or None,
                )
            except Exception:
                pass

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
            num = member.delegate_number

            _project_dir_block = ""
            if _project_dir:
                _project_dir_block = (
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
            _step = (
                _plan_for_wave.get_step(member.step_id)
                if _plan_for_wave and member.step_id
                else None
            )
            _owned = resolve_step_owned_paths(_step, _project_dir or "")
            _peer_lines = [
                ln for ln in _peer_registry_lines
                if f"#{num}" not in ln
            ]
            _file_ownership_block = build_file_ownership_block(
                delegate_number=num,
                owned_patterns=_owned,
                peer_lines=_peer_lines,
                shared_paths=_shared_paths,
            )
            _preamble_parts = _member_launch_preamble(
                team,
                i,
                credential_block=_credential_block,
                uploads_block=_uploads_block,
                project_dir_block=_project_dir_block,
            )
            task_with_briefing = member.task
            if _preamble_parts:
                task_with_briefing = (
                    "\n\n".join(_preamble_parts)
                    + f"\n\n[YOUR TASK]\n{member.task}"
                )
            if _tech_stack_block:
                task_with_briefing = (
                    f"{_tech_stack_block}\n\n{task_with_briefing}"
                )
            if _file_ownership_block:
                task_with_briefing = (
                    f"{_file_ownership_block}\n\n{task_with_briefing}"
                )
            _member_peer_briefing = _peer_awareness_block(team, i)

            if len(specs) < available_slots:
                member.status = "running"
                try:
                    _ms = int(fn_kwargs.get("max_steps", DELEGATE_DEFAULT_MAX_STEPS))
                except (ValueError, TypeError):
                    _ms = DELEGATE_DEFAULT_MAX_STEPS
                specs.append(DelegateSpec(
                    task=task_with_briefing,
                    delegate_number=num,
                    max_steps=_ms,
                    file_manifest=_file_manifest,
                    team_briefing=_member_peer_briefing,
                    wave=team.wave_index,
                    tech_stack_block=_tech_stack_block,
                    file_ownership_block=_file_ownership_block,
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
        if team.wave_index > 0:
            self._drain_stale_wave_complete_dispatches_for_plan(team.plan_id)
        queued_count = len(team.members) - len(specs)
        await self._broadcast_async("team_launched", team)
        await self._broadcast_stakeholder_milestone(
            self._wave_launched_milestone(team, len(specs)),
            source=f"milestone:wave_launched:{team.id}",
        )

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

        if self._delegate_manager is not None and not team.is_terminal:
            self.reconcile_with_delegates(team_id=team_id, persist=True)
            team = self._teams.get(team_id)
            if team is None:
                return None

        if team.is_terminal:
            raise ValueError(
                f"Cannot advance team {team_id}: already finalized "
                f"(status={team.status}). Use team(action='inspect') on the "
                f"active wave team instead."
            )

        _sibling_active = [
            t for t in self._teams.values()
            if t.id != team_id
            and t.plan_id == team.plan_id
            and t.status in ("active", "created", "paused", "blocked")
        ]
        if _sibling_active:
            _labels = ", ".join(
                f"{t.id} ({t.name!r}, {t.status})" for t in _sibling_active[:3]
            )
            raise ValueError(
                f"Cannot advance team {team_id}: another team is still active "
                f"on plan {team.plan_id}: {_labels}. Finish or advance the "
                f"current wave first."
            )

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
        self._clear_wave_ownership(team)

        if team.checkback_job and self._scheduler_manager is not None:
            try:
                self._scheduler_manager.remove_job(team.checkback_job)
            except Exception:
                pass
        self._drain_team_checkback_dispatch(team_id)
        self._drain_wave_complete_dispatch(team_id)
        self._drain_completion_review_dispatches(team_id)
        self.cleanup_plan_checkbacks(team.plan_id)
        try:
            from nls.agentic.wake_coordination import orchestration_hygiene_after_wave_advanced
            orchestration_hygiene_after_wave_advanced(self, team_id)
        except Exception:
            logger.debug("wave advance hygiene failed", exc_info=True)

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

        self._sync_plan_steps_from_team(team)

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

        next_team = None
        if _outcome == "completed":
            next_team = self._try_create_next_wave(team)
        if next_team is not None:
            logger.info(
                "TeamManager: team %s completed → auto-created next wave team %s",
                team.id, next_team.id,
            )
            return next_team

        logger.info("TeamManager: team %s completed — no further waves", team.id)
        return team

    async def reconcile_terminal_team(self, team_id: str) -> Team | None:
        """Idempotent advance when team is already terminal but not reported."""
        team = self._teams.get(team_id)
        if team is None or not team.is_terminal:
            return None
        if team.completion_reported:
            if team.status == "completed":
                return self._try_create_next_wave(team) or team
            return team

        result = self._finalize_unreported_wave_sync(team_id, reason="em_team_advance")
        if result is None:
            return None
        await self._broadcast_async("team_advanced", team)
        return result

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
        """Cancel all running delegates and close the team."""
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

        _never_launched = (
            not team.batch_id
            and all(m.status in ("pending", "cancelled") for m in team.members)
        )
        team.status = "cancelled" if _never_launched else "failed"
        team.completed_at = time.time()
        team.completion_reported = True
        self.save(team)
        await self._broadcast_async("team_disbanded", team)
        return True

    async def grant_member_paths(
        self,
        team_id: str,
        member_idx: int,
        paths: list[str],
        *,
        message: str = "",
    ) -> tuple[bool, str]:
        """Grant a running delegate extra file paths mid-wave."""
        team = self._teams.get(team_id)
        if team is None:
            return False, f"Team '{team_id}' not found."
        if team.is_terminal:
            return False, f"Team '{team_id}' is terminal (status: {team.status})."
        if member_idx < 0 or member_idx >= len(team.members):
            return False, f"Invalid member index {member_idx}."
        member = team.members[member_idx]
        if member.delegate_number is None:
            return False, f"Member #{member_idx} has no delegate number."
        if member.status != "running":
            return False, (
                f"Member #{member_idx} (delegate #{member.delegate_number}) "
                f"is not running (status: {member.status})."
            )

        raw_paths = [str(p).strip() for p in paths if str(p).strip()]
        if not raw_paths:
            return False, "paths must be a non-empty array of path patterns."

        added_to_plan = False
        granted: list[str] = []
        if self._file_ledger is not None:
            try:
                granted = self._file_ledger.grant_delegate_paths(
                    team.wave_index,
                    member.delegate_number,
                    raw_paths,
                )
            except Exception:
                logger.debug("grant_member_paths ledger failed", exc_info=True)

        if self._plan_store is not None and team.plan_id and member.step_id:
            try:
                from .wave_coordination import resolve_step_owned_paths

                plan = self._plan_store.load(team.plan_id)
                step = plan.get_step(member.step_id) if plan else None
                if plan is not None and step is not None:
                    existing = list(step.owned_paths or [])
                    for p in raw_paths:
                        if p not in existing:
                            existing.append(p)
                            added_to_plan = True
                    step.owned_paths = existing
                    self._plan_store.save(plan)
                    if self._file_ledger is not None:
                        resolved = resolve_step_owned_paths(
                            step, project_dir=plan.project_dir,
                        )
                        self._file_ledger.set_delegate_paths(
                            team.wave_index,
                            member.delegate_number,
                            resolved,
                        )
                        granted = resolved
            except Exception:
                logger.debug("grant_member_paths plan sync failed", exc_info=True)

        if not granted and not added_to_plan:
            return False, "No new paths granted (already in scope or invalid)."

        hint = (
            f"[PATH ACCESS GRANTED] You may now write/edit: "
            + ", ".join(granted[:8])
            + (f" (+{len(granted) - 8} more)" if len(granted) > 8 else "")
        )
        if message:
            hint += f"\n\n{message.strip()}"
        await self.hint_member_async(team_id, member_idx, hint)
        return True, (
            f"Granted paths to member #{member_idx} "
            f"(delegate #{member.delegate_number}): "
            + ", ".join(granted[:6])
            + (f" (+{len(granted) - 6} more)" if len(granted) > 6 else "")
        )

    def sync_step_owned_paths_to_wave(
        self,
        plan_id: str,
        step_id: str,
    ) -> bool:
        """Push plan step owned_paths into the active wave file ledger."""
        if self._plan_store is None or self._file_ledger is None:
            return False
        plan = self._plan_store.load(plan_id)
        if plan is None:
            return False
        step = plan.get_step(step_id)
        if step is None:
            return False
        from .wave_coordination import resolve_step_owned_paths

        paths = resolve_step_owned_paths(step, project_dir=plan.project_dir)
        synced = False
        for team in self._teams.values():
            if team.plan_id != plan_id or team.is_terminal:
                continue
            if team.status != "active":
                continue
            for member in team.members:
                if (
                    member.step_id == step_id
                    and member.delegate_number is not None
                    and member.status == "running"
                ):
                    self._file_ledger.set_delegate_paths(
                        team.wave_index,
                        member.delegate_number,
                        paths,
                    )
                    synced = True
        return synced

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
        """Called when a team member hits a limit or requests help.

        Passive limit/stall (first time): auto-extend immediately.
        Proactive escalate()/ask_user()/repeated_write: always notify
        orchestrator — no silent auto-extend.
        Subsequent passive escalations: full orchestrator decision.
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
            if not _is_reminder:
                self.register_completion_review(
                    team, member_idx, delegate_number,
                )
            self._notify_completion_review_required(
                team, delegate_number, review_msg, is_reminder=_is_reminder,
            )
            logger.info(
                "TeamManager: completion %s for delegate #%d "
                "(writes=%d) — notified orchestrator",
                "reminder" if _is_reminder else "review",
                delegate_number, _writes,
            )
            return

        # --- First escalation: auto-extend immediately (passive limits only) ---
        # Proactive escalate()/ask_user() from the member always reach the
        # orchestrator — they are asking for help, not hitting a guard rail.
        _PROACTIVE_PREFIXES = ("escalate:", "ask_user:", "repeated_write:")
        _is_proactive = any(reason.startswith(p) for p in _PROACTIVE_PREFIXES)

        # Scale extension with original budget: at least 10, up to 1/2 of
        # max_steps, so complex tasks get proportionally more room.
        _base_budget = DELEGATE_DEFAULT_MAX_STEPS
        if self._delegate_manager is not None:
            _ds = self._delegate_manager._delegates.get(delegate_number)
            if _ds is not None:
                _base_budget = _ds.max_iterations
        _ext_iters = max(10, _base_budget // 2)

        if (
            not _is_proactive
            and esc_count == 0
            and self._delegate_manager is not None
        ):
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

        # --- Orchestrator escalation (proactive or 2nd+ passive) ---
        if _is_proactive:
            _writes_n = 0
            for line in (context_summary or "").splitlines():
                if line.strip().lower().startswith("writes:"):
                    try:
                        _writes_n = int(line.split(":", 1)[1].strip())
                    except (ValueError, IndexError):
                        pass
            _finish_hint = (
                " They listed a bounded finish list — prefer extend (+15 iters) "
                "with a one-paragraph hint naming exact files/edits, NOT terminate."
                if "escalate:" in reason and _writes_n > 0
                else ""
            )
            _file_access_hint = ""
            if "file_access" in reason or "paths_requested:" in (context_summary or ""):
                _file_access_hint = (
                    "\n\nFILE ACCESS REQUEST — prefer: team(action='grant_paths', "
                    f"team_id='{team.id}', member={member_idx}, "
                    "paths=['.gitignore', ...], message='approved — proceed'). "
                    "Deny with a hint naming an alternative path if you refuse."
                )
            help_msg = (
                f"[TEAM MEMBER HELP REQUEST — PROACTIVE]\n"
                f"Team: {team.name} [{team.id}]\n"
                f"Member #{member_idx} (delegate #{member.delegate_number}): "
                f"{member.task[:100]}\n"
                f"Reason: {reason}\n"
                f"Context: {context_summary}\n\n"
                f"This member is PAUSED waiting for you"
                + (
                    " (3rd full rewrite of same file)."
                    if reason.startswith("repeated_write:")
                    else "."
                )
                + _finish_hint
                + _file_access_hint
                + "\n\nRespond with: team(action='intervene', "
                f"team_id='{team.id}', member={member_idx}, "
                f"decision='extend' (default) or 'hint', "
                f"message='...'). Avoid terminate unless zero useful files on disk."
            )
        else:
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

        try:
            from nls.agentic.wake_coordination import schedule_member_escalation_wake
            schedule_member_escalation_wake(
                self, team, delegate_number, help_msg,
            )
        except Exception:
            logger.debug("TeamManager: escalation wake schedule failed", exc_info=True)

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
        self.clear_completion_review(delegate_number)
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
            elif _state == "interrupted":
                member.status = "failed"
                if not member.result_summary:
                    member.result_summary = _INTERRUPTED_MEMBER_SUMMARY
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
                        if _step:
                            _notes = (_step.notes or "")
                            if (
                                "[accept_partial]" in _notes
                                and member.status in ("failed", "cancelled")
                            ):
                                if member.result_summary:
                                    _step.notes = (
                                        f"{_notes}\n[delegate exit] "
                                        f"{member.result_summary[:400]}"
                                    )[:2000]
                            elif _step.status == "done":
                                pass
                            elif _step.status != "done":
                                _prev_step_status = _step.status
                                _step.status = _plan_step_status_for_member(
                                    member.status,
                                )
                                if member.result_summary:
                                    _step.notes = member.result_summary[:500]
                                if (
                                    _prev_step_status != _step.status
                                    and self._connection_manager is not None
                                ):
                                    _step_idx = next(
                                        (
                                            i
                                            for i, st in enumerate(_plan.steps)
                                            if st.id == _step.id
                                        ),
                                        -1,
                                    )
                                    try:
                                        await self._connection_manager.broadcast(
                                            self._agent_id,
                                            {
                                                "type": "plan_step_update",
                                                "step_index": _step_idx,
                                                "step_id": _step.id,
                                                "status": _step.status,
                                                "label": _step.label,
                                                "plan_id": _plan.id,
                                                "todo_id": _plan.todo_id or "",
                                            },
                                        )
                                    except Exception:
                                        logger.debug(
                                            "plan_step_update broadcast failed",
                                            exc_info=True,
                                        )
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

            if self._file_ledger is not None:
                try:
                    self._file_ledger.release_delegate_ownership(
                        team.wave_index,
                        member.delegate_number,
                    )
                except Exception:
                    pass

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
                try:
                    from nls.agentic.wake_coordination import (
                        orchestration_hygiene_after_member_done,
                    )
                    orchestration_hygiene_after_member_done(
                        self, team.id, member_idx, delegate_number,
                    )
                except Exception:
                    pass

            # --- Auto-spawn next queued member if slot available ---
            await self._spawn_next_pending(team)

            if team.all_members_done():
                _outcome = team.compute_outcome()
                team.status = _outcome
                team.completed_at = __import__("time").time()
                self._clear_wave_ownership(team)
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
                        self._sync_plan_steps_from_team(team)
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
                await self._broadcast_stakeholder_milestone(
                    self._wave_complete_milestone(team),
                    source=f"milestone:wave_complete:{team.id}",
                )

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

                self._notify_wave_review_required(team)
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

        _credential_block = ""
        if self._hooks is not None:
            _get_creds = getattr(self._hooks, "wm_get_credentials", None)
            if _get_creds is not None:
                try:
                    _creds = _get_creds()
                    if _creds:
                        _cred_lines = [f"  - {d}: {c}" for d, c in _creds]
                        _credential_block = (
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
        _project_dir_block = ""
        if _project_dir:
            _project_dir_block = (
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

        _tech_stack_block = ""
        _pw = None
        if team.plan_id and self._plan_store is not None:
            _pw = self._plan_store.load(team.plan_id)
            if _pw is not None:
                from .wave_coordination import build_tech_stack_block
                _tech_stack_block = build_tech_stack_block(plan=_pw)

        from .wave_coordination import (
            build_file_ownership_block,
            resolve_step_owned_paths,
        )
        _pending_step = (
            _pw.get_step(next_pending.step_id)
            if _pw and next_pending.step_id
            else None
        )
        _owned = resolve_step_owned_paths(_pending_step, _project_dir or "")
        _file_ownership_block = build_file_ownership_block(
            delegate_number=next_pending.delegate_number,
            owned_patterns=_owned,
            peer_lines=[],
        )

        _member_idx = team.members.index(next_pending)
        _preamble_parts = _member_launch_preamble(
            team,
            _member_idx,
            credential_block=_credential_block,
            uploads_block=_uploads_block,
            project_dir_block=_project_dir_block,
        )
        task_with_briefing = next_pending.task
        if _preamble_parts:
            task_with_briefing = (
                "\n\n".join(_preamble_parts)
                + f"\n\n[YOUR TASK]\n{next_pending.task}"
            )
        if _tech_stack_block:
            task_with_briefing = f"{_tech_stack_block}\n\n{task_with_briefing}"
        if _file_ownership_block:
            task_with_briefing = f"{_file_ownership_block}\n\n{task_with_briefing}"
        _member_peer_briefing = _peer_awareness_block(team, _member_idx)

        try:
            _ms = int(fn_kwargs.get("max_steps", DELEGATE_DEFAULT_MAX_STEPS))
        except (ValueError, TypeError):
            _ms = DELEGATE_DEFAULT_MAX_STEPS
        spec = DelegateSpec(
            task=task_with_briefing,
            delegate_number=next_pending.delegate_number,
            max_steps=_ms,
            file_manifest=_file_manifest,
            team_briefing=_member_peer_briefing,
            wave=team.wave_index,
            tech_stack_block=_tech_stack_block,
            file_ownership_block=_file_ownership_block,
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

        _existing = [
            t for t in self._teams.values()
            if t.plan_id == completed_team.plan_id and t.wave_index == next_wave
        ]
        if _existing:
            _active = [t for t in _existing if not t.is_terminal]
            if _active:
                _pick = max(_active, key=lambda t: t.created_at)
                logger.info(
                    "TeamManager: wave %d already active (%s) — skip auto-create",
                    next_wave, _pick.id,
                )
                return _pick
            logger.info(
                "TeamManager: wave %d already has %d terminal attempt(s) — "
                "skip auto-create (use team(create) to retry)",
                next_wave, len(_existing),
            )
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

    async def _broadcast_stakeholder_milestone(
        self, message: str, *, source: str,
    ) -> None:
        """Curated wave/plan update for the main chat thread."""
        text = (message or "").strip()
        if not text or self._connection_manager is None:
            return
        try:
            await self._connection_manager.broadcast(self._agent_id, {
                "type": "communicate",
                "message": text,
                "iteration": 0,
                "autonomous": True,
                "user_facing": True,
                "source": source,
            })
        except Exception as exc:
            logger.debug("TeamManager milestone broadcast failed: %s", exc)

    def _wave_complete_milestone(self, team: Team) -> str:
        wave_num = team.wave_index + 1
        _ok = sum(1 for m in team.members if m.status == "done")
        _total = len(team.members)
        name = team.name or f"Wave {wave_num}"
        return (
            f"Wave {wave_num} complete — {name}. "
            f"{_ok}/{_total} task(s) finished; reviewing before the next wave."
        )

    def _wave_launched_milestone(self, team: Team, launched_count: int) -> str:
        wave_num = team.wave_index + 1
        name = team.name or f"Wave {wave_num}"
        tasks = [
            m.task.split("\n")[0].strip()[:48]
            for m in team.members[:3]
            if m.status in ("running", "pending", "done")
        ]
        task_hint = ", ".join(tasks)
        if len(team.members) > 3:
            task_hint += f" (+{len(team.members) - 3} more)"
        suffix = f": {task_hint}" if task_hint else ""
        return (
            f"Wave {wave_num} started — {name}. "
            f"{launched_count} sub-agent(s) running{suffix}."
        )
