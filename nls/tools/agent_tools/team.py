"""Team tool — agent-facing interface for team lifecycle management.

Lets the orchestrator create, inspect, launch, advance, hint, pause,
resume, and disband teams.  Teams bridge Plans → Delegates → Kanban.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nls.agentic.breadcrumbs import tool_description_supplement
from nls.tools.agent_tools.base import ToolResult

logger = logging.getLogger(__name__)

# Common model mistakes → supported actions
_TEAM_ACTION_ALIASES: dict[str, str] = {
    "wrap_up": "advance",
    "wrapup": "advance",
    "complete": "advance",
    "finish": "advance",
    "status": "inspect",
    "check": "inspect",
    "monitor": "inspect",
}


class TeamTool:
    """Per-agent team management tool (AgentTool protocol)."""

    def __init__(self, team_manager: Any) -> None:
        self._tm = team_manager

    def _approve_followup(
        self,
        team_id: str,
        *,
        team: Any,
        approved_delegate_number: int | None = None,
    ) -> tuple[str, str]:
        """Tool-result suffix and breadcrumb text after approve (wave-aware)."""
        self._tm.reconcile_with_delegates(team_id=team_id, persist=True)
        team = self._tm._teams.get(team_id) or team
        from nls.agentic.verification_hints import post_approve_advance_nudge

        extra = post_approve_advance_nudge(
            team_id=team_id,
            team=team,
            team_manager=self._tm,
            approved_delegate_number=approved_delegate_number,
        )
        breadcrumb = extra.strip()
        return extra, breadcrumb

    @property
    def name(self) -> str:
        return "team"

    @property
    def description(self) -> str:
        return (
            "Manage execution teams — persistent groups of sub-agents "
            "working on plan delegation waves.\n"
            "NOT for Discord/community squads with permanent roles — use squad_setup + "
            "squad() tools for that.\n"
            "WORKFLOW: 1) plan(action='create', title='...'), "
            "2) plan(action='add_step', plan_id=..., label='task', "
            "delegatable=true) for each task, "
            "3) team(action='create', plan_id=..., wave=0, name='...'), "
            "4) plan(action='update', step_id=..., owned_paths=['frontend/']) "
            "for each member — directory patterns cover all nested files, "
            "5) team(action='launch', team_id=...). "
            "Steps with no depends_on form wave 0. "
            "Actions: create, list, inspect, launch, advance, hint, "
            "brief, pause, resume, disband, intervene, rewake, grant_paths.\n"
            "GRANT_PATHS: When a delegate escalates for file_access (e.g. "
            ".gitignore), use grant_paths to rent them write scope mid-wave.\n"
            "REWAKE: If a member finished/failed but work is incomplete, "
            "use rewake to resume the SAME delegate with new instructions "
            "instead of spawning a brand new one."
            + tool_description_supplement("team")
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["action", "team_id"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "create", "list", "inspect", "launch",
                        "advance", "brief", "hint", "intervene",
                        "pause", "resume", "disband", "rewake",
                        "grant_paths",
                    ],
                    "description": "The team operation to perform.",
                },
                "plan_id": {
                    "type": "string",
                    "description": "Plan ID (required for 'create').",
                },
                "wave": {
                    "oneOf": [
                        {
                            "type": "integer",
                            "description": (
                                "Wave index (0-based) from the plan's "
                                "delegation waves."
                            ),
                        },
                        {
                            "type": "string",
                            "enum": ["auto"],
                            "description": (
                                "Pick the next wave with pending delegatable "
                                "steps automatically."
                            ),
                        },
                    ],
                    "description": (
                        "Delegation wave for 'create': 0-based index or "
                        "'auto' for the next pending wave."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Team name (required for 'create').",
                },
                "team_id": {
                    "type": "string",
                    "description": (
                        "Team ID. Required for all actions except 'create' "
                        "and 'list'. Pass '-' for create/list."
                    ),
                },
                "member": {
                    "type": "integer",
                    "description": (
                        "Member index (0-based) within the team "
                        "(required for 'hint', 'intervene', 'rewake', "
                        "and 'grant_paths')."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": (
                        "Hint message or briefing content "
                        "(required for 'hint', 'brief', optional for 'intervene' and 'rewake')."
                    ),
                },
                "decision": {
                    "type": "string",
                    "enum": ["extend", "hint", "terminate", "approve"],
                    "description": (
                        "Orchestrator decision for 'intervene': "
                        "'extend' = give more iterations, "
                        "'hint' = send guidance + more iterations, "
                        "'terminate' = stop the member, "
                        "'approve' = approve a completion review "
                        "(member exits cleanly)."
                    ),
                },
                "extra_iterations": {
                    "type": "integer",
                    "description": (
                        "Extra iterations to grant. For 'intervene': default 10, "
                        "used with decision='extend'|'hint'. For 'rewake': default 15."
                    ),
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Path patterns to grant (required for 'grant_paths'). "
                        "Examples: '.gitignore', 'backend/', 'README.md'."
                    ),
                },
                "mission": {
                    "type": "string",
                    "description": "Team mission statement (optional for 'create').",
                },
                "briefing": {
                    "type": "string",
                    "description": (
                        "Initial briefing shared with all members "
                        "(optional for 'create')."
                    ),
                },
                "include_completed": {
                    "type": "boolean",
                    "description": "Include completed/failed teams in 'list'.",
                },
            },
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        action = (params.get("action") or "").strip().lower()
        if not action:
            return ToolResult(
                content=(
                    "action is required. Use one of: create, list, inspect, "
                    "launch, advance, brief, hint, intervene, pause, resume, "
                    "disband, rewake, grant_paths."
                ),
                is_error=True,
            )
        action = _TEAM_ACTION_ALIASES.get(action, action)
        try:
            handler = {
                "create": self._create,
                "list": self._list,
                "inspect": self._inspect,
                "launch": self._launch,
                "advance": self._advance,
                "brief": self._brief,
                "hint": self._hint,
                "intervene": self._intervene,
                "pause": self._pause,
                "resume": self._resume,
                "disband": self._disband,
                "rewake": self._rewake,
                "grant_paths": self._grant_paths,
                "update": self._update_redirect,
            }.get(action)
            if handler is None:
                return ToolResult(
                    content=f"Unknown team action: '{action}'",
                    is_error=True,
                )
            return await handler(params)
        except Exception as exc:
            logger.exception("Team tool error (action=%s)", action)
            return ToolResult(content=f"Error: {exc}", is_error=True)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _update_redirect(self, params: dict[str, Any]) -> ToolResult:
        step_id = (params.get("step_id") or "").strip() or "step-N"
        return ToolResult(
            content=(
                "owned_paths, output_files, and step status belong on the plan, "
                "not the team tool.\n"
                f"Use plan(action='update', step_id='{step_id}', "
                "owned_paths=['backend/app/models/'], status='in_progress')."
            ),
            is_error=True,
            details={"redirect": "plan", "action": "update"},
        )

    async def _create(self, params: dict[str, Any]) -> ToolResult:
        plan_id = (params.get("plan_id") or "").strip()
        if not plan_id:
            return ToolResult(content="plan_id is required.", is_error=True)

        wave = params.get("wave")
        if wave is None:
            return ToolResult(content="wave index is required.", is_error=True)

        name = (params.get("name") or "").strip()
        if not name:
            return ToolResult(content="Team name is required.", is_error=True)

        _plan = self._tm._plan_store.load(plan_id)
        if _plan is None:
            return ToolResult(
                content=(
                    f"Plan '{plan_id}' not found. "
                    f"Create a plan with delegatable steps first:\n"
                    f"  plan(action='create', title='Project Name')\n"
                    f"  plan(action='add_step', plan_id='<id>', "
                    f"label='Task 1', delegatable=true)\n"
                    f"  plan(action='add_step', plan_id='<id>', "
                    f"label='Task 2', delegatable=true)\n"
                    f"Then: team(action='create', plan_id=<plan_id>, "
                    f"wave=0, name='...')"
                ),
                is_error=True,
            )
        if not _plan.steps:
            return ToolResult(
                content=(
                    f"Plan '{plan_id}' has 0 steps — cannot create a team "
                    f"from an empty plan.\n\n"
                    f"Add delegatable steps first:\n"
                    f"  plan(action='add_step', plan_id='{plan_id}', "
                    f"label='Build FastAPI backend', delegatable=true)\n"
                    f"  plan(action='add_step', plan_id='{plan_id}', "
                    f"label='Create React frontend', delegatable=true)\n"
                    f"  plan(action='add_step', plan_id='{plan_id}', "
                    f"label='Deploy to Railway', delegatable=true, "
                    f"depends_on=['Build FastAPI backend'])\n\n"
                    f"Each step with delegatable=true becomes a team member. "
                    f"Steps with no depends_on form wave 0 (parallel). "
                    f"Steps with depends_on form later waves.\n"
                    f"Then: team(action='create', plan_id='{plan_id}', "
                    f"wave=0, name='...')"
                ),
                is_error=True,
            )

        from nls.agentic.plan_store import (
            get_delegation_waves,
            is_deploy_step,
            next_pending_wave_index,
            pending_steps_in_wave,
        )

        _waves = get_delegation_waves(_plan)
        if str(wave).strip().lower() == "auto":
            _auto = next_pending_wave_index(_plan, _waves)
            if _auto is None:
                return ToolResult(
                    content=(
                        f"Plan '{plan_id}' has no pending steps — "
                        f"all waves are complete."
                    ),
                    is_error=True,
                    details={"action": "create", "plan_id": plan_id},
                )
            wave_int = _auto
        else:
            try:
                wave_int = int(wave)
            except (TypeError, ValueError):
                return ToolResult(
                    content=(
                        f"Invalid wave {wave!r} — use a 0-based integer or "
                        f"'auto' for the next pending wave."
                    ),
                    is_error=True,
                    details={"action": "create", "plan_id": plan_id},
                )

        if wave_int >= len(_waves):
            return ToolResult(
                content=(
                    f"Wave {wave_int} out of range — plan '{plan_id}' has "
                    f"{len(_waves)} wave(s) (0-indexed: 0..{len(_waves)-1}).\n"
                    f"Use wave=0 for the first parallel batch, or wave='auto' "
                    f"for the next pending wave."
                ),
                is_error=True,
            )

        _planned_ids = frozenset(
            s.id for s in pending_steps_in_wave(_plan, wave_int, _waves)
        )

        # Guard: only one non-terminal team per plan+wave (retries append attempts)
        existing = self._tm.list_teams(include_terminal=False)
        all_teams = self._tm.list_teams(include_terminal=True)
        for t in existing:
            if t.plan_id == plan_id and t.wave_index == wave_int:
                return ToolResult(
                    content=(
                        f"A team already exists for plan {plan_id} wave {wave_int}: "
                        f"{t.name} [{t.id}] (status: {t.status}).\n"
                        f"Use team(action='launch', team_id='{t.id}') to launch it, "
                        f"or team(action='inspect', team_id='{t.id}') to check status.\n"
                        f"If that wave failed and you need a retry, wait until it is "
                        f"terminal (failed/completed) then team(create) again — "
                        f"attempt number increments and prior attempts stay visible."
                    ),
                    is_error=True,
                    details={
                        "action": "create",
                        "duplicate_team": True,
                        "team_id": t.id,
                        "plan_id": plan_id,
                    },
                )

        # Guard: block rapid recreate of the same wrong/deploy wave+steps
        # without ever launching (allows legitimate retries on the correct wave).
        if _planned_ids and not params.get("force_retry"):
            _same_shape = [
                t for t in all_teams
                if t.plan_id == plan_id
                and t.wave_index == wave_int
                and frozenset(m.step_id for m in t.members if m.step_id) == _planned_ids
                and not t.batch_id
                and t.status in ("cancelled", "created", "failed")
            ]
            _nw = next_pending_wave_index(_plan, _waves)
            _wrong_wave = _nw is not None and wave_int != _nw
            _wave_pending = pending_steps_in_wave(_plan, wave_int, _waves)
            _deploy_only = bool(_wave_pending) and all(
                is_deploy_step(s) for s in _wave_pending
            )
            if len(_same_shape) >= 2 and (_wrong_wave or _deploy_only):
                _hint = (
                    f"Use team(action='create', plan_id='{plan_id}', "
                    f"wave={_nw}, name='...') for the next pending work."
                    if _nw is not None else ""
                )
                return ToolResult(
                    content=(
                        f"Blocked duplicate team(create) for plan {plan_id} wave "
                        f"{wave_int} — {len(_same_shape)} prior attempt(s) with the "
                        f"same step(s) never launched.\n"
                        f"Do NOT disband and recreate the same deploy-only wave. "
                        f"{_hint}\n"
                        f"Inspect plan(read) and pending earlier waves first."
                    ),
                    is_error=True,
                    details={
                        "action": "create",
                        "duplicate_wave_recreate": True,
                        "plan_id": plan_id,
                        "wave_index": wave_int,
                        "recommended_wave": _nw,
                        "prior_attempts": len(_same_shape),
                    },
                )

        # Guard: prevent skipping waves — cannot create wave N if a
        # previous wave for the same plan exists but hasn't completed.
        for t in all_teams:
            if t.plan_id == plan_id and t.wave_index < wave_int:
                if t.status not in ("completed", "partial", "failed", "cancelled"):
                    return ToolResult(
                        content=(
                            f"Cannot create wave {wave_int} — wave {t.wave_index} "
                            f"({t.name} [{t.id}]) is still '{t.status}'.\n"
                            f"You must launch and complete earlier waves first.\n"
                            f"Use team(action='launch', team_id='{t.id}') to "
                            f"start it."
                        ),
                        is_error=True,
                    )
                if not t.completion_reported:
                    return ToolResult(
                        content=(
                            f"Cannot create wave {wave_int} — wave {t.wave_index} "
                            f"({t.name} [{t.id}]) finished but was never advanced.\n"
                            f"Call team(action='advance', team_id='{t.id}') first, "
                            f"then team(action='launch') on the next wave team "
                            f"(or inspect the auto-created team)."
                        ),
                        is_error=True,
                        details={
                            "action": "create",
                            "wave_needs_advance": True,
                            "prior_team_id": t.id,
                            "plan_id": plan_id,
                        },
                    )

        team = self._tm.create_team(
            plan_id=plan_id,
            wave_index=wave_int,
            name=name,
            mission=params.get("mission", ""),
            briefing=params.get("briefing", ""),
        )
        if team is None:
            detail = getattr(self._tm, "_last_create_error", "") or "Unknown error"
            _err_code = getattr(self._tm, "_last_create_error_code", "") or ""
            _err_details: dict[str, Any] = {
                "action": "create",
                "plan_id": plan_id,
                "wave_index": wave_int,
            }
            _nw = next_pending_wave_index(_plan, _waves)
            if _nw is not None:
                _err_details["recommended_wave"] = _nw
            if _err_code == "skipped_pending_wave":
                _err_details["skipped_pending_wave"] = True
            elif _err_code == "deploy_blocked":
                _err_details["deploy_blocked"] = True
            elif "earlier pending step" in detail:
                _err_details["skipped_pending_wave"] = True
            elif "deploy-only wave" in detail:
                _err_details["deploy_blocked"] = True
            return ToolResult(
                content=(
                    f"Failed to create team — {detail}\n\n"
                    f"If the wave's steps are already done, use wave='auto' or "
                    f"the recommended wave from plan(fix_dependencies)."
                ),
                is_error=True,
                details=_err_details,
            )

        _max_concurrent = 5
        if self._tm._delegate_manager is not None:
            _max_concurrent = getattr(
                self._tm._delegate_manager, "_max_concurrent",
                self._tm._delegate_manager.MAX_CONCURRENT_DELEGATES,
            )

        _batch_note = ""
        if len(team.members) > _max_concurrent:
            _batch_note = (
                f"\n\nNOTE: Team has {len(team.members)} members but max "
                f"concurrent delegates is {_max_concurrent}. "
                f"Launch will auto-batch: first {_max_concurrent} start "
                f"immediately, remaining {len(team.members) - _max_concurrent} "
                f"spawn as slots free up."
            )

        _attempt_note = ""
        if team.wave_attempt > 1:
            _attempt_note = f" | Attempt: {team.wave_attempt}"
            if team.supersedes_team_id:
                _attempt_note += f" (after {team.supersedes_team_id})"

        _paths_note = ""
        _step_ids = [m.step_id for m in team.members if getattr(m, "step_id", "")]
        if _step_ids:
            from nls.agentic.wave_coordination import (
                format_owned_paths_assignment_reminder,
            )

            _paths_note = format_owned_paths_assignment_reminder(
                _plan, _step_ids, plan_id=plan_id,
            )
            if _paths_note:
                _paths_note = "\n\n" + _paths_note

        return ToolResult(
            content=(
                f"Team created: {team.name} [{team.id}]\n"
                f"Plan: {team.plan_id} | Wave: {team.wave_index}{_attempt_note}\n"
                f"Members ({len(team.members)}):\n"
                + "\n".join(
                    f"  [{i}] {m.task} (step: {m.step_id})"
                    for i, m in enumerate(team.members)
                )
                + _batch_note
                + _paths_note
            ),
            details={"team_id": team.id, "action": "create"},
        )

    async def _list(self, params: dict[str, Any]) -> ToolResult:
        include_completed = params.get("include_completed", False)
        teams = self._tm.list_teams(include_terminal=include_completed)
        if not teams:
            return ToolResult(content="No active teams.")

        lines: list[str] = []
        for team in teams:
            lines.append(team.to_summary(compact=True))
        return ToolResult(content="\n".join(lines))

    async def _inspect(self, params: dict[str, Any]) -> ToolResult:
        team_id = (params.get("team_id") or "").strip()
        if not team_id:
            return ToolResult(content="team_id is required.", is_error=True)

        team = self._tm.inspect_team(team_id)
        if team is None:
            return ToolResult(
                content=f"Team '{team_id}' not found.", is_error=True,
            )

        summary = team.to_summary(compact=False)

        # Auto-cancel lingering check-back for terminal teams
        if team.is_terminal and team.checkback_job:
            _sm = getattr(self._tm, "_scheduler_manager", None)
            if _sm is not None:
                try:
                    if _sm.remove_job(team.checkback_job):
                        logger.info(
                            "Auto-cancelled stale check-back '%s' for terminal team %s",
                            team.checkback_job, team.id,
                        )
                except Exception:
                    pass

        if team.is_terminal and team.status in ("partial", "failed"):
            _hint = (
                "\n\nNEXT STEP: This team has failures. "
                "Call team(action='advance') to finalize, then "
                "handle the failed items (retry or fix manually)."
            )
            summary += _hint

        _pending_cr = getattr(self._tm, "_pending_completion_reviews", {}) or {}
        _has_cr = (
            not team.completion_reported
            and any(
                info.get("team_id") == team_id
                for info in _pending_cr.values()
            )
        )
        if _has_cr:
            from nls.agentic.verification_hints import completion_review_verify_breadcrumb

            summary += "\n\n" + completion_review_verify_breadcrumb(team_id=team_id)

        _details: dict[str, Any] = {
            "team_id": team.id,
            "action": "inspect",
            "status": team.status,
        }
        if _has_cr:
            _details["pending_completion_review"] = True
        if team.is_terminal and not team.completion_reported:
            _details["needs_advance"] = True
        if team.completion_reported:
            _details["wave_advanced"] = True

        return ToolResult(content=summary, details=_details)

    async def _launch(self, params: dict[str, Any]) -> ToolResult:
        team_id = (params.get("team_id") or "").strip()
        if not team_id:
            return ToolResult(content="team_id is required.", is_error=True)

        team = self._tm.load(team_id)
        if team is None:
            return ToolResult(
                content=f"Team '{team_id}' not found.", is_error=True,
            )
        if team.status not in ("created", "paused"):
            return ToolResult(
                content=(
                    f"Team '{team_id}' is {team.status} — "
                    f"can only launch teams in 'created' or 'paused' state."
                ),
                is_error=True,
            )

        # Guard: verify plan step dependencies are satisfied before launch
        if team.plan_id and self._tm._plan_store is not None:
            _plan = self._tm._plan_store.load(team.plan_id)
            if _plan:
                from nls.agentic.plan_store import (
                    _resolve_dep_id,
                    format_unmet_dependency_hints,
                )

                _step_map = {s.id: s for s in _plan.steps}
                _label_map = {
                    s.label.lower().strip(): s.id for s in _plan.steps
                }
                _unmet_lines: list[str] = []
                _unmet_pairs: list[tuple[Any, Any]] = []
                for member in team.members:
                    if not member.step_id:
                        continue
                    _step = _plan.get_step(member.step_id)
                    if not _step or not getattr(_step, "depends_on", None):
                        continue
                    for dep_ref in _step.depends_on:
                        dep_id = _resolve_dep_id(
                            dep_ref, _step_map, _label_map,
                        )
                        _dep_step = _step_map.get(dep_id)
                        if _dep_step is None:
                            _dep_step = next(
                                (
                                    s for s in _plan.steps
                                    if s.label == dep_ref
                                    or s.id == dep_ref
                                ),
                                None,
                            )
                        if _dep_step and _dep_step.status not in (
                            "done", "skipped",
                        ):
                            _unmet_lines.append(
                                f"  • \"{_step.label}\" depends on "
                                f"\"{_dep_step.label}\" "
                                f"(status: {_dep_step.status})"
                            )
                            _unmet_pairs.append((_step, _dep_step))
                if _unmet_lines:
                    _fix_hints = format_unmet_dependency_hints(
                        _plan, _unmet_pairs,
                    )
                    return ToolResult(
                        content=(
                            f"Cannot launch team '{team_id}' — unmet "
                            f"dependencies:\n"
                            + "\n".join(_unmet_lines)
                            + "\n\n"
                            + _fix_hints
                        ),
                        is_error=True,
                    )

        # Delegate spawning requires run_delegate_fn which only the
        # executor has.  Signal back via details so the executor can
        # handle the actual spawn + scheduler wiring.
        _paths_note = ""
        if team.plan_id and self._tm._plan_store is not None:
            _plan = self._tm._plan_store.load(team.plan_id)
            if _plan is not None:
                from nls.agentic.wave_coordination import (
                    format_owned_paths_assignment_reminder,
                )

                _step_ids = [
                    m.step_id for m in team.members if getattr(m, "step_id", "")
                ]
                _paths_note = format_owned_paths_assignment_reminder(
                    _plan, _step_ids, plan_id=team.plan_id,
                )
                if _paths_note:
                    _paths_note = "\n\n" + _paths_note

        return ToolResult(
            content=(
                f"Team {team.name} [{team.id}] queued for launch.\n"
                f"Members: {len(team.members)}\n"
                f"The system will now spawn {len(team.members)} "
                f"sub-agent(s) and schedule a check-back.\n\n"
                f"[SUPERVISOR MODE ACTIVE]\n"
                f"Your team is now running. Your role is SUPERVISOR:\n"
                f"- Use team(action='inspect') to monitor progress\n"
                f"- Use team(action='intervene', team_id=..., member=N, decision='extend|hint|terminate', message='...') if a member struggles\n"
                f"- Use wait(seconds=120) to let them work\n"
                f"- Do NOT write code, create files, or run bash yourself\n"
                f"- Do NOT create new plans — add steps to the existing one\n"
                f"- When all members finish, use team(action='advance') "
                f"to check results and launch the next wave"
                + _paths_note
            ),
            details={
                "team_id": team.id,
                "action": "launch",
                "needs_delegate_spawn": True,
            },
        )

    async def _advance(self, params: dict[str, Any]) -> ToolResult:
        team_id = (params.get("team_id") or "").strip()
        if not team_id:
            return ToolResult(content="team_id is required.", is_error=True)

        self._tm.reconcile_with_delegates(team_id=team_id, persist=True)

        team_before = self._tm.load(team_id)
        if team_before and team_before.completion_reported:
            # Cancel any lingering check-back scheduler for this team
            if team_before.checkback_job:
                _sm = getattr(self._tm, "_scheduler_manager", None)
                if _sm is not None:
                    try:
                        _sm.remove_job(team_before.checkback_job)
                    except Exception:
                        pass
            return ToolResult(
                content=(
                    f"Team {team_id} was already advanced (wave closed). "
                    f"No further advance needed — inspect plan for next work."
                ),
                details={"team_id": team_id, "action": "advance", "already_reported": True},
            )

        try:
            result = await self._tm.advance_team(team_id)
        except ValueError as e:
            team = self._tm.load(team_id)
            if team is not None and team.is_terminal:
                try:
                    result = await self._tm.reconcile_terminal_team(team_id)
                except Exception:
                    result = None
                if result is not None:
                    if result.id != team_id:
                        return ToolResult(
                            content=(
                                f"Team {team_id} reconciled (was terminal). "
                                f"Next wave: {result.name} [{result.id}]"
                            ),
                            details={
                                "team_id": result.id,
                                "action": "advance",
                                "next_team": True,
                                "reconciled": True,
                            },
                        )
                    return ToolResult(
                        content=(
                            f"Team {team_id} reconciled (status={team.status}). "
                            "Use switch_mode(evaluating) to review outputs."
                        ),
                        details={
                            "team_id": team_id,
                            "action": "advance",
                            "reconciled": True,
                        },
                    )
            from nls.agentic.team_advance_hints import format_advance_blocked_message

            _team = self._tm.load(team_id)
            msg = format_advance_blocked_message(
                team_id,
                reason=str(e),
                team=_team,
                team_manager=self._tm,
            )
            return ToolResult(content=msg, is_error=True)
        if result is None:
            _t = self._tm._teams.get(team_id)
            if _t is None:
                _reason = f"Team '{team_id}' not found."
            else:
                _reason = (
                    f"Team '{team_id}' cannot advance "
                    f"(status: {_t.status}, "
                    f"members: {len(_t.members)})."
                )
            return ToolResult(content=_reason, is_error=True)

        if result.id != team_id:
            _member_summaries = "\n".join(
                f"  #{m.delegate_number}: {m.task.split(chr(10))[0][:60]}"
                for m in result.members
            )
            return ToolResult(
                content=(
                    f"Team {team_id} completed and advanced!\n"
                    f"Next wave team ready: {result.name} [{result.id}]\n"
                    f"Members ({len(result.members)}):\n{_member_summaries}\n\n"
                    f"[NEXT WAVE]\n"
                    f"If no other wave is active, the system may auto-launch "
                    f"this team when you advance from the tool executor.\n"
                    f"Otherwise: review completed outputs, then "
                    f"team(action='launch', team_id='{result.id}').\n"
                    f"Do NOT advance or launch terminal/old wave teams."
                ),
                details={
                    "team_id": result.id,
                    "action": "advance",
                    "next_team": True,
                    "wave": getattr(team_before, "wave_index", None),
                },
            )

        _outcome = result.status
        _member_lines = "\n".join(
            f"  {'[OK]' if m.status == 'done' else '[FAIL]'} {m.task}: "
            f"{m.result_summary[:80] if m.result_summary else m.status}"
            for m in result.members
        )

        # Check if the plan still has pending steps (even though
        # no auto-wave was created — e.g. remaining steps need
        # manual attention or a retry).
        _plan = self._tm._plan_store.load(result.plan_id) if result.plan_id else None
        _remaining = []
        if _plan:
            _remaining = [
                s for s in _plan.steps
                if s.status not in ("done", "skipped")
            ]

        from nls.agentic.plan_work import format_plan_closure_nudge

        _plan_ready = (
            not _remaining
            and _outcome == "completed"
            and _plan is not None
        )
        _closure_block = ""
        if _plan_ready:
            _closure_block = (
                "\n\n" + format_plan_closure_nudge(_plan.id)
            )

        _guidance = {
            "completed": (
                "ALL members succeeded."
                if not _remaining
                else (
                    f"ALL members succeeded, but {len(_remaining)} plan "
                    f"step(s) remain: "
                    + ", ".join(f'"{s.label}"' for s in _remaining[:4])
                    + ". Check plan(action='verify') and launch the next "
                    "wave or delegate remaining work."
                )
            ),
            "partial": (
                "MIXED results — some members succeeded, some failed. "
                "Review what failed and WHY (check result summaries). "
                "You MUST either: (a) retry the failed tasks yourself or "
                "via a new team, or (b) ask the user if they want to proceed "
                "with partial results. Do NOT silently ignore failures."
            ),
            "failed": (
                "ALL or MOST members FAILED. Do NOT report success. "
                "Investigate the root cause from the result summaries. "
                "Common causes: wrong paths, missing dependencies, timeout. "
                "You MUST either: (a) fix the root cause and retry with a "
                "new team, (b) attempt the tasks yourself directly, or "
                "(c) tell the user what went wrong and ask for guidance. "
                "Do NOT just say 'complete' — the work is NOT done."
            ),
        }.get(_outcome, "")

        _wave_note = (
            "All steps in this plan are done (this plan only)."
            if not _remaining
            else (
                f"No auto-wave was created, but {len(_remaining)} plan "
                f"step(s) are still pending. Review plan and launch next wave."
            )
        )

        return ToolResult(
            content=(
                f"Team {team_id} outcome: {_outcome.upper()}\n"
                f"{_wave_note}\n\n"
                f"[WAVE CLOSED] Review outputs before launching another team. "
                f"If the user asked for updates, send ONE concise message now.\n\n"
                f"Member results:\n{_member_lines}\n\n"
                f"ACTION REQUIRED: {_guidance}"
                f"{_closure_block}"
            ),
            details={
                "team_id": team_id,
                "action": "advance",
                "next_team": False,
                "outcome": _outcome,
                "wave": getattr(result, "wave_index", None),
                "plan_id": getattr(result, "plan_id", "") or "",
                "plan_ready_to_close": _plan_ready,
            },
        )

    async def _brief(self, params: dict[str, Any]) -> ToolResult:
        team_id = (params.get("team_id") or "").strip()
        message = (params.get("message") or "").strip()
        if not team_id:
            return ToolResult(content="team_id is required.", is_error=True)
        if not message:
            return ToolResult(content="message is required.", is_error=True)

        ok = self._tm.update_briefing(team_id, message)
        if not ok:
            return ToolResult(
                content=f"Team '{team_id}' not found.", is_error=True,
            )
        return ToolResult(content=f"Briefing updated for team {team_id}.")

    def _auto_resolve_member(
        self, team_id: str, member_idx: int | None,
    ) -> int | None:
        """Return member_idx, auto-resolving to 0 for single-member teams."""
        if member_idx is not None:
            return member_idx
        team = self._tm._teams.get(team_id)
        if team is not None and len(team.members) == 1:
            return 0
        return None

    async def _hint(self, params: dict[str, Any]) -> ToolResult:
        team_id = (params.get("team_id") or "").strip()
        member_idx = params.get("member")
        message = (params.get("message") or "").strip()

        if not team_id:
            return ToolResult(content="team_id is required.", is_error=True)
        member_idx = self._auto_resolve_member(team_id, member_idx)
        if member_idx is None:
            return ToolResult(content="member index is required.", is_error=True)
        if not message:
            return ToolResult(content="message is required.", is_error=True)

        team = self._tm._teams.get(team_id)
        if team is None:
            return ToolResult(content=f"Team '{team_id}' not found.", is_error=True)
        if int(member_idx) < 0 or int(member_idx) >= len(team.members):
            return ToolResult(
                content=f"Invalid member index {member_idx}.",
                is_error=True,
            )
        self._tm.reconcile_with_delegates(team_id=team_id, persist=True)
        team = self._tm._teams.get(team_id)
        if team is None:
            return ToolResult(content=f"Team '{team_id}' not found.", is_error=True)
        member = team.members[int(member_idx)]
        if member.status in ("done", "failed", "cancelled"):
            from nls.agentic.team_advance_hints import format_intervene_terminal_member_block

            return ToolResult(
                content=format_intervene_terminal_member_block(
                    team_id,
                    int(member_idx),
                    member,
                    team=team,
                    decision="hint",
                ),
                is_error=True,
            )

        ok = await self._tm.hint_member_async(
            team_id, int(member_idx), message,
        )
        if not ok:
            return ToolResult(
                content=(
                    f"Could not send hint — team '{team_id}' member "
                    f"#{member_idx} may not be running."
                ),
                is_error=True,
            )
        return ToolResult(
            content=f"Hint sent to team {team_id} member #{member_idx}.",
        )

    async def _intervene(self, params: dict[str, Any]) -> ToolResult:
        team_id = (params.get("team_id") or "").strip()
        member_idx = params.get("member")
        decision = (params.get("decision") or "").strip().lower()
        message = (params.get("message") or "").strip()
        extra_iters = params.get("extra_iterations", 10)

        if not team_id:
            active = [
                t for t in self._tm._teams.values()
                if not t.is_terminal
            ]
            if len(active) == 1:
                team_id = active[0].id
            else:
                return ToolResult(
                    content=(
                        f"team_id is required for intervene "
                        f"({len(active)} non-terminal teams found)."
                    ),
                    is_error=True,
                )
        member_idx = self._auto_resolve_member(team_id, member_idx)
        if member_idx is None:
            return ToolResult(content="member index is required.", is_error=True)
        if decision not in ("extend", "hint", "terminate", "approve"):
            return ToolResult(
                content="decision must be 'extend', 'hint', 'terminate', or 'approve'.",
                is_error=True,
            )
        if decision == "hint" and not message:
            return ToolResult(
                content="message is required when decision is 'hint'.",
                is_error=True,
            )

        try:
            extra_iters = max(1, int(extra_iters))
        except (TypeError, ValueError):
            extra_iters = 10

        team = self._tm._teams.get(team_id)
        if team is None:
            return ToolResult(content=f"Team '{team_id}' not found.", is_error=True)

        try:
            idx = int(member_idx)
        except (TypeError, ValueError):
            return ToolResult(
                content=f"member must be an integer index, got: {member_idx!r}",
                is_error=True,
            )
        if idx < 0 or idx >= len(team.members):
            return ToolResult(
                content=f"Invalid member index {idx} (team has {len(team.members)} members).",
                is_error=True,
            )

        member = team.members[idx]
        if member.delegate_number is None:
            return ToolResult(
                content=f"Member #{idx} has no delegate number — may not have launched.",
                is_error=True,
            )

        self._tm.reconcile_with_delegates(team_id=team_id, persist=True)
        team = self._tm._teams.get(team_id)
        if team is None:
            return ToolResult(content=f"Team '{team_id}' not found.", is_error=True)
        member = team.members[idx]

        if member.status in ("done", "failed", "cancelled"):
            if decision == "approve":
                if team.completion_reported:
                    return ToolResult(
                        content=(
                            f"Member #{idx} (delegate #{member.delegate_number}) "
                            f"already completed and wave {team_id} is closed. "
                            "Inspect the active plan for next work — do not "
                            "team(advance) this wave again."
                        ),
                    )
                _extra, _bc = self._approve_followup(
                    team_id,
                    team=team,
                    approved_delegate_number=member.delegate_number,
                )
                return ToolResult(
                    content=(
                        f"Member #{idx} (delegate #{member.delegate_number}) "
                        f"already completed.{_extra}"
                    ),
                    details={
                        "team_id": team_id,
                        "action": "intervene",
                        "decision": "approve",
                        "member_idx": idx,
                        "approve_breadcrumb": _bc,
                    },
                )
            if decision in ("hint", "extend", "terminate"):
                from nls.agentic.team_advance_hints import format_intervene_terminal_member_block

                return ToolResult(
                    content=format_intervene_terminal_member_block(
                        team_id,
                        idx,
                        member,
                        team=team,
                        decision=decision,
                    ),
                    is_error=True,
                )

        dm = self._tm._delegate_manager
        if dm is None:
            return ToolResult(content="No delegate manager available.", is_error=True)

        # "approve" maps to "terminate" on the hint_queue — tells the
        # delegate's completion-review wait that the orchestrator is
        # satisfied and it can exit cleanly.
        _dm_action = "terminate" if decision == "approve" else decision

        if decision == "terminate":
            # Cancel the asyncio task first so inspect reflects reality quickly.
            # intervene(terminate) only helps delegates blocked on escalation wait.
            cancelled = False
            try:
                cancelled = await dm.cancel(member.delegate_number)
            except Exception:
                pass
            if not cancelled:
                result = await dm.intervene(
                    member.delegate_number,
                    action="terminate",
                    message=message or "Terminated by orchestrator.",
                    extra_iterations=extra_iters,
                )
            else:
                result = True
            if result is True:
                member.status = "cancelled"
                member.result_summary = (
                    (message or "Terminated by orchestrator.")[:500]
                )
                self._tm.save(team)
        else:
            result = await dm.intervene(
                member.delegate_number,
                action=_dm_action,
                message=message or (
                    "Approved by orchestrator." if decision == "approve" else ""
                ),
                extra_iterations=extra_iters,
            )
        if result is not True:
            err_detail = result if isinstance(result, str) else (
                f"delegate #{member.delegate_number} not found"
            )
            _rewake_hint = ""
            if "already finished" in str(err_detail):
                self._tm.reconcile_with_delegates(team_id=team_id, persist=True)
                team = self._tm._teams.get(team_id)
                member = team.members[idx] if team else member
                if member.status == "done":
                    if decision == "approve":
                        _extra, _bc = self._approve_followup(
                            team_id,
                            team=team,
                            approved_delegate_number=member.delegate_number,
                        )
                        return ToolResult(
                            content=(
                                f"Member #{idx} (delegate #{member.delegate_number}) "
                                f"already finished — synced to done.{_extra}"
                            ),
                            details={
                                "team_id": team_id,
                                "action": "intervene",
                                "decision": "approve",
                                "member_idx": idx,
                                "approve_breadcrumb": _bc,
                            },
                        )
                    return ToolResult(
                        content=(
                            f"Member #{idx} already finished (delegate "
                            f"#{member.delegate_number}, status={member.status}). "
                            f"Use team(action='advance', team_id='{team_id}') "
                            "or team(action='rewake', ...) to continue work."
                        ),
                        is_error=True,
                    )
                _rewake_hint = (
                    f"\n\nTo resume this delegate with new instructions, use:\n"
                    f"  team(action='rewake', team_id='{team_id}', "
                    f"member={idx}, message='<what needs to be done>')"
                )
            else:
                _rewake_hint = ""
            return ToolResult(
                content=(
                    f"Could not intervene on member #{idx}: {err_detail}"
                    + _rewake_hint
                ),
                is_error=True,
            )

        if decision in ("approve", "hint", "terminate"):
            self._tm.clear_completion_review(member.delegate_number)

        action_desc = {
            "extend": f"Extended by {extra_iters} iterations",
            "hint": f"Sent hint + extended by {extra_iters} iterations",
            "terminate": "Terminating member",
            "approve": "Approved completion — member will exit cleanly",
        }[decision]

        # Record intervention in WM orchestration state
        _hooks = getattr(self._tm, "_hooks", None)
        if _hooks is not None:
            if getattr(_hooks, "wm_orch_resolve_escalation", None):
                _hooks.wm_orch_resolve_escalation(
                    team_id, idx,
                    f"intervened_{decision}: {action_desc}",
                )
            if getattr(_hooks, "wm_orch_record_decision", None):
                _hooks.wm_orch_record_decision(
                    action=f"intervened_{decision}",
                    context=message[:200] if message else action_desc,
                    outcome="",
                    team_id=team_id,
                    member_idx=idx,
                )

        _extra = ""
        _approve_bc = ""
        if decision == "approve":
            _extra, _approve_bc = self._approve_followup(
                team_id,
                team=team,
                approved_delegate_number=member.delegate_number,
            )
        elif decision == "hint":
            _extra = (
                "\n[BREADCRUMB] Sent work back to delegate — "
                "wait for completion review again before approve."
            )

        return ToolResult(
            content=(
                f"Intervention sent to {team.name} member #{idx} "
                f"(delegate #{member.delegate_number}): {action_desc}."
                + (f"\nMessage: {message}" if message else "")
                + _extra
            ),
            details={
                "team_id": team_id,
                "action": "intervene",
                "decision": decision,
                "member_idx": idx,
                **(
                    {"approve_breadcrumb": _approve_bc}
                    if decision == "approve" and _approve_bc
                    else {}
                ),
            },
        )

    async def _rewake(self, params: dict[str, Any]) -> ToolResult:
        team_id = (params.get("team_id") or "").strip()
        member_idx = params.get("member")
        message = (params.get("message") or "").strip()
        extra_iters = params.get("extra_iterations", 15)

        if not team_id:
            return ToolResult(content="team_id is required.", is_error=True)
        member_idx = self._auto_resolve_member(team_id, member_idx)
        if member_idx is None:
            return ToolResult(content="member index is required.", is_error=True)

        try:
            extra_iters = max(5, int(extra_iters))
        except (TypeError, ValueError):
            extra_iters = 15

        team = self._tm._teams.get(team_id)
        if team is None:
            return ToolResult(content=f"Team '{team_id}' not found.", is_error=True)

        try:
            idx = int(member_idx)
        except (TypeError, ValueError):
            return ToolResult(
                content=f"member must be an integer index, got: {member_idx!r}",
                is_error=True,
            )
        if idx < 0 or idx >= len(team.members):
            return ToolResult(
                content=f"Invalid member index {idx} (team has {len(team.members)} members).",
                is_error=True,
            )

        member = team.members[idx]
        if member.delegate_number is None:
            return ToolResult(
                content=f"Member #{idx} has no delegate — it was never launched.",
                is_error=True,
            )

        dm = self._tm._delegate_manager
        if dm is None:
            return ToolResult(content="No delegate manager available.", is_error=True)

        result = await dm.rewake(
            member.delegate_number,
            message=message,
            extra_iterations=extra_iters,
        )
        if result is not True:
            err_detail = result if isinstance(result, str) else "Unknown error"
            return ToolResult(content=f"Cannot rewake member #{idx}: {err_detail}", is_error=True)

        member.status = "running"
        member.result_summary = ""
        if team.is_terminal:
            team.status = "active"
            team.completion_reported = False

        return ToolResult(
            content=(
                f"Member #{idx} (delegate #{member.delegate_number}) rewoken "
                f"with {extra_iters} iterations.\n"
                + (f"Instructions: {message}\n" if message else "")
                + "The delegate will resume from where it left off, with "
                "awareness of its previous work and your feedback.\n"
                "Monitor with team(action='inspect')."
            ),
        )

    async def _pause(self, params: dict[str, Any]) -> ToolResult:
        team_id = (params.get("team_id") or "").strip()
        if not team_id:
            return ToolResult(content="team_id is required.", is_error=True)
        ok = await self._tm.pause_team(team_id)
        if not ok:
            return ToolResult(
                content=f"Cannot pause team '{team_id}' — not active.",
                is_error=True,
            )
        return ToolResult(content=f"Team {team_id} paused.")

    async def _resume(self, params: dict[str, Any]) -> ToolResult:
        team_id = (params.get("team_id") or "").strip()
        if not team_id:
            return ToolResult(content="team_id is required.", is_error=True)
        ok = await self._tm.resume_team(team_id)
        if not ok:
            return ToolResult(
                content=f"Cannot resume team '{team_id}' — not paused.",
                is_error=True,
            )
        return ToolResult(content=f"Team {team_id} resumed.")

    async def _disband(self, params: dict[str, Any]) -> ToolResult:
        team_id = (params.get("team_id") or "").strip()
        if not team_id:
            return ToolResult(content="team_id is required.", is_error=True)
        ok = await self._tm.disband_team(team_id)
        if not ok:
            _t = self._tm._teams.get(team_id)
            if _t is None:
                _reason = f"Team '{team_id}' not found."
            elif _t.is_terminal:
                _reason = f"Team '{team_id}' is already terminal (status: {_t.status})."
            else:
                _reason = f"Cannot disband team '{team_id}' (status: {_t.status})."
            return ToolResult(content=_reason, is_error=True)
        return ToolResult(
            content=f"Team {team_id} disbanded — all delegates cancelled.",
            details={
                "team_id": team_id,
                "action": "disband",
                "orchestrator_recovery": True,
            },
        )

    async def _grant_paths(self, params: dict[str, Any]) -> ToolResult:
        team_id = (params.get("team_id") or "").strip()
        member_idx = params.get("member")
        paths = params.get("paths") or []
        message = (params.get("message") or "").strip()

        if not team_id:
            active = [
                t for t in self._tm._teams.values()
                if not t.is_terminal
            ]
            if len(active) == 1:
                team_id = active[0].id
            else:
                return ToolResult(
                    content=(
                        f"team_id is required for grant_paths "
                        f"({len(active)} non-terminal teams found)."
                    ),
                    is_error=True,
                )
        member_idx = self._auto_resolve_member(team_id, member_idx)
        if member_idx is None:
            return ToolResult(content="member is required.", is_error=True)
        if not isinstance(paths, list) or not paths:
            return ToolResult(
                content=(
                    "paths is required — array of path patterns "
                    "(e.g. ['.gitignore'] or ['backend/config.py'])."
                ),
                is_error=True,
            )

        ok, detail = await self._tm.grant_member_paths(
            team_id,
            int(member_idx),
            [str(p) for p in paths],
            message=message,
        )
        if not ok:
            return ToolResult(content=detail, is_error=True)
        return ToolResult(
            content=detail,
            details={
                "team_id": team_id,
                "member": int(member_idx),
                "action": "grant_paths",
                "paths": paths,
                "orchestrator_recovery": False,
            },
        )


def create_team_tool(team_manager: Any) -> TeamTool:
    """Factory for the team tool."""
    return TeamTool(team_manager)
