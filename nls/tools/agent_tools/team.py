"""Team tool — agent-facing interface for team lifecycle management.

Lets the orchestrator create, inspect, launch, advance, hint, pause,
resume, and disband teams.  Teams bridge Plans → Delegates → Kanban.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nls.tools.agent_tools.base import ToolResult

logger = logging.getLogger(__name__)


class TeamTool:
    """Per-agent team management tool (AgentTool protocol)."""

    def __init__(self, team_manager: Any) -> None:
        self._tm = team_manager

    @property
    def name(self) -> str:
        return "team"

    @property
    def description(self) -> str:
        return (
            "Manage execution teams — persistent groups of sub-agents "
            "working on plan delegation waves.\n"
            "WORKFLOW: 1) plan(action='create', title='...'), "
            "2) plan(action='add_step', plan_id=..., label='task', "
            "delegatable=true) for each task, "
            "3) team(action='create', plan_id=..., wave=0, name='...'), "
            "4) team(action='launch', team_id=...). "
            "Steps with no depends_on form wave 0. "
            "Actions: create, list, inspect, launch, advance, hint, "
            "brief, pause, resume, disband, intervene, rewake.\n"
            "REWAKE: If a member finished/failed but work is incomplete, "
            "use rewake to resume the SAME delegate with new instructions "
            "instead of spawning a brand new one."
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
                    ],
                    "description": "The team operation to perform.",
                },
                "plan_id": {
                    "type": "string",
                    "description": "Plan ID (required for 'create').",
                },
                "wave": {
                    "type": "integer",
                    "description": (
                        "Wave index (0-based) from the plan's delegation "
                        "waves (required for 'create')."
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
                        "(required for 'hint', 'intervene', and 'rewake')."
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

        wave_int = int(wave)

        # Guard: prevent duplicate teams for the same plan+wave
        existing = self._tm.list_teams(include_terminal=False)
        # Also include terminal teams (completed) for ordering check
        all_teams = self._tm.list_teams(include_terminal=True)
        for t in existing:
            if t.plan_id == plan_id and t.wave_index == wave_int:
                return ToolResult(
                    content=(
                        f"A team already exists for plan {plan_id} wave {wave_int}: "
                        f"{t.name} [{t.id}] (status: {t.status}).\n"
                        f"Use team(action='launch', team_id='{t.id}') to launch it, "
                        f"or team(action='inspect', team_id='{t.id}') to check status."
                    ),
                    is_error=True,
                )

        # Guard: prevent skipping waves — cannot create wave N if a
        # previous wave for the same plan exists but hasn't completed.
        for t in all_teams:
            if t.plan_id == plan_id and t.wave_index < wave_int:
                if t.status not in ("completed", "partial", "failed"):
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

        # Pre-flight: validate plan exists and has steps before calling create_team
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

        from nls.agentic.plan_store import get_delegation_waves
        _waves = get_delegation_waves(_plan)
        if wave_int >= len(_waves):
            return ToolResult(
                content=(
                    f"Wave {wave_int} out of range — plan '{plan_id}' has "
                    f"{len(_waves)} wave(s) (0-indexed: 0..{len(_waves)-1}).\n"
                    f"Use wave=0 for the first parallel batch."
                ),
                is_error=True,
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
            return ToolResult(
                content=(
                    f"Failed to create team — {detail}\n\n"
                    f"If the wave's steps are already done, try the next wave "
                    f"(increment the wave number)."
                ),
                is_error=True,
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

        return ToolResult(
            content=(
                f"Team created: {team.name} [{team.id}]\n"
                f"Plan: {team.plan_id} | Wave: {team.wave_index}\n"
                f"Members ({len(team.members)}):\n"
                + "\n".join(
                    f"  [{i}] {m.task} (step: {m.step_id})"
                    for i, m in enumerate(team.members)
                )
                + _batch_note
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

        return ToolResult(content=summary)

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
                _unmet = []
                for member in team.members:
                    if not member.step_id:
                        continue
                    _step = _plan.get_step(member.step_id)
                    if not _step or not getattr(_step, "depends_on", None):
                        continue
                    for dep_label in _step.depends_on:
                        _dep_step = next(
                            (s for s in _plan.steps if s.label == dep_label),
                            None,
                        )
                        if _dep_step and _dep_step.status not in ("done", "skipped"):
                            _unmet.append(
                                f"  • \"{_step.label}\" depends on "
                                f"\"{dep_label}\" (status: {_dep_step.status})"
                            )
                if _unmet:
                    return ToolResult(
                        content=(
                            f"Cannot launch team '{team_id}' — unmet "
                            f"dependencies:\n"
                            + "\n".join(_unmet)
                            + "\n\nComplete the prerequisite steps first, "
                            f"then retry launch."
                        ),
                        is_error=True,
                    )

        # Delegate spawning requires run_delegate_fn which only the
        # executor has.  Signal back via details so the executor can
        # handle the actual spawn + scheduler wiring.
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
                    f"Team {team_id} was already advanced and reported. "
                    f"No further action needed — move to the next phase."
                ),
                details={"team_id": team_id, "action": "advance", "already_reported": True},
            )

        try:
            result = await self._tm.advance_team(team_id)
        except ValueError as e:
            return ToolResult(content=str(e), is_error=True)
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
                    f"[BEFORE LAUNCHING NEXT WAVE]\n"
                    f"1. Review the output from the completed wave — read key "
                    f"files, check structure, verify quality.\n"
                    f"2. Fix any gaps or issues you spot (missing files, wrong "
                    f"structure, incomplete config).\n"
                    f"3. When satisfied, launch: "
                    f"team(action='launch', team_id='{result.id}')\n\n"
                    f"Do NOT skip the review — catching problems now prevents "
                    f"cascading failures in the next wave."
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
            "All plan steps completed — project is done!"
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
                f"Member results:\n{_member_lines}\n\n"
                f"ACTION REQUIRED: {_guidance}"
            ),
            details={
                "team_id": team_id,
                "action": "advance",
                "next_team": False,
                "outcome": _outcome,
                "wave": getattr(result, "wave_index", None),
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

        dm = self._tm._delegate_manager
        if dm is None:
            return ToolResult(content="No delegate manager available.", is_error=True)

        # "approve" maps to "terminate" on the hint_queue — tells the
        # delegate's completion-review wait that the orchestrator is
        # satisfied and it can exit cleanly.
        _dm_action = "terminate" if decision == "approve" else decision

        result = await dm.intervene(
            member.delegate_number,
            action=_dm_action,
            message=message or ("Approved by orchestrator." if decision == "approve" else ""),
            extra_iterations=extra_iters,
        )
        if result is not True:
            err_detail = result if isinstance(result, str) else (
                f"delegate #{member.delegate_number} not found"
            )
            _rewake_hint = ""
            if "already finished" in str(err_detail):
                _rewake_hint = (
                    f"\n\nTo resume this delegate with new instructions, use:\n"
                    f"  team(action='rewake', team_id='{team_id}', "
                    f"member={idx}, message='<what needs to be done>')"
                )
            return ToolResult(
                content=(
                    f"Could not intervene on member #{idx}: {err_detail}"
                    + _rewake_hint
                ),
                is_error=True,
            )

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

        return ToolResult(
            content=(
                f"Intervention sent to {team.name} member #{idx} "
                f"(delegate #{member.delegate_number}): {action_desc}."
                + (f"\nMessage: {message}" if message else "")
            ),
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
        return ToolResult(content=f"Team {team_id} disbanded — all delegates cancelled.")


def create_team_tool(team_manager: Any) -> TeamTool:
    """Factory for the team tool."""
    return TeamTool(team_manager)
