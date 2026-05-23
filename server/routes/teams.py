"""REST API routes for the team management system.

Provides CRUD for teams, team member interaction (hints), and a
command endpoint for natural-language orchestrator instructions.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["teams"])


# -------------------------------------------------------------------
# Request models
# -------------------------------------------------------------------

class TeamCreate(BaseModel):
    plan_id: str
    wave: int
    name: str
    mission: str = ""
    briefing: str = ""


class TeamHint(BaseModel):
    message: str


class TeamBrief(BaseModel):
    content: str


class CommandRequest(BaseModel):
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _get_team_manager(request: Request, agent_id: str) -> Any:
    """Resolve TeamManager from the agent's runtime."""
    am = getattr(request.app.state, "agent_manager", None)
    if am is None:
        raise HTTPException(503, "Agent manager not available")
    runtime = am.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(404, f"Agent '{agent_id}' not loaded")

    # TeamManager is stored directly on the runtime by _initialize_tools
    tm = getattr(runtime, "_team_manager", None)
    if tm is not None:
        return tm

    # Fallback: look through the agent tools list
    tools = getattr(runtime, "_agent_tools", []) or []
    team_tool = next((t for t in tools if getattr(t, "name", "") == "team"), None)
    if team_tool is None:
        raise HTTPException(503, "Team tool not available for this agent")

    tm = getattr(team_tool, "_tm", None)
    if tm is None:
        raise HTTPException(503, "TeamManager not initialized")
    return tm


def _get_runtime(request: Request, agent_id: str) -> Any:
    am = getattr(request.app.state, "agent_manager", None)
    if am is None:
        raise HTTPException(503, "Agent manager not available")
    runtime = am.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(404, f"Agent '{agent_id}' not loaded")
    return runtime


# -------------------------------------------------------------------
# Team CRUD
# -------------------------------------------------------------------

@router.get("/{agent_id}/teams")
async def list_teams(
    agent_id: str,
    request: Request,
    include_completed: bool = False,
) -> dict[str, Any]:
    tm = _get_team_manager(request, agent_id)
    teams = tm.list_teams(include_terminal=include_completed)
    return {"teams": [t.to_dict() for t in teams]}


@router.post("/{agent_id}/teams")
async def create_team(
    agent_id: str, body: TeamCreate, request: Request,
) -> dict[str, Any]:
    tm = _get_team_manager(request, agent_id)
    team = tm.create_team(
        plan_id=body.plan_id,
        wave_index=body.wave,
        name=body.name,
        mission=body.mission,
        briefing=body.briefing,
    )
    if team is None:
        raise HTTPException(400, "Failed to create team — check plan_id and wave index")
    return {"team": team.to_dict()}


@router.get("/{agent_id}/teams/{team_id}")
async def get_team(
    agent_id: str, team_id: str, request: Request,
) -> dict[str, Any]:
    tm = _get_team_manager(request, agent_id)
    team = tm.inspect_team(team_id)
    if team is None:
        raise HTTPException(404, f"Team '{team_id}' not found")
    return {"team": team.to_dict()}


@router.post("/{agent_id}/teams/{team_id}/advance")
async def advance_team(
    agent_id: str, team_id: str, request: Request,
) -> dict[str, Any]:
    tm = _get_team_manager(request, agent_id)
    result = await tm.advance_team(team_id)
    if result is None:
        raise HTTPException(400, f"Cannot advance team '{team_id}'")
    next_team = result.id != team_id
    return {
        "team": result.to_dict(),
        "next_team_created": next_team,
    }


@router.post("/{agent_id}/teams/{team_id}/pause")
async def pause_team(
    agent_id: str, team_id: str, request: Request,
) -> dict[str, Any]:
    tm = _get_team_manager(request, agent_id)
    ok = await tm.pause_team(team_id)
    if not ok:
        raise HTTPException(400, f"Cannot pause team '{team_id}'")
    return {"status": "paused", "team_id": team_id}


@router.post("/{agent_id}/teams/{team_id}/resume")
async def resume_team(
    agent_id: str, team_id: str, request: Request,
) -> dict[str, Any]:
    tm = _get_team_manager(request, agent_id)
    ok = await tm.resume_team(team_id)
    if not ok:
        raise HTTPException(400, f"Cannot resume team '{team_id}'")
    return {"status": "resumed", "team_id": team_id}


@router.post("/{agent_id}/teams/{team_id}/disband")
async def disband_team(
    agent_id: str, team_id: str, request: Request,
) -> dict[str, Any]:
    tm = _get_team_manager(request, agent_id)
    ok = await tm.disband_team(team_id)
    if not ok:
        raise HTTPException(400, f"Cannot disband team '{team_id}'")
    return {"status": "disbanded", "team_id": team_id}


@router.post("/{agent_id}/teams/{team_id}/brief")
async def brief_team(
    agent_id: str, team_id: str, body: TeamBrief, request: Request,
) -> dict[str, Any]:
    tm = _get_team_manager(request, agent_id)
    ok = tm.update_briefing(team_id, body.content)
    if not ok:
        raise HTTPException(404, f"Team '{team_id}' not found")
    return {"status": "updated"}


# -------------------------------------------------------------------
# Team member interactions
# -------------------------------------------------------------------

@router.post("/{agent_id}/teams/{team_id}/members/{member_idx}/hint")
async def hint_member(
    agent_id: str,
    team_id: str,
    member_idx: int,
    body: TeamHint,
    request: Request,
) -> dict[str, Any]:
    tm = _get_team_manager(request, agent_id)
    ok = await tm.hint_member_async(team_id, member_idx, body.message)
    if not ok:
        raise HTTPException(
            400,
            f"Cannot hint member #{member_idx} of team '{team_id}' — "
            "member may not be running",
        )
    return {"status": "hint_sent", "member": member_idx}


# -------------------------------------------------------------------
# Timeline interactions: skip wave / force-start next wave
# -------------------------------------------------------------------

@router.post("/{agent_id}/teams/{team_id}/skip")
async def skip_wave(
    agent_id: str, team_id: str, request: Request,
) -> dict[str, Any]:
    """Skip the current wave — cancel running delegates and mark as completed."""
    tm = _get_team_manager(request, agent_id)
    team = tm.load(team_id)
    if team is None:
        raise HTTPException(404, f"Team '{team_id}' not found")

    dm = getattr(tm, "_delegate_manager", None)
    for member in team.members:
        if member.status == "running" and dm is not None:
            try:
                await dm.cancel(member.delegate_number)
            except Exception:
                pass
        if member.status in ("pending", "running"):
            member.status = "cancelled"

    team.status = "completed"
    import time as _time
    team.completed_at = _time.time()
    tm.save(team)
    return {"status": "skipped", "team": team.to_dict()}


@router.post("/{agent_id}/projects/{plan_id}/force-start/{wave_index}")
async def force_start_wave(
    agent_id: str, plan_id: str, wave_index: int, request: Request,
) -> dict[str, Any]:
    """Force-create a team for a specific wave, bypassing dependency checks."""
    tm = _get_team_manager(request, agent_id)
    existing = [
        t for t in tm.list_teams(include_terminal=True)
        if t.plan_id == plan_id and t.wave_index == wave_index
    ]
    if existing:
        raise HTTPException(
            400, f"Wave {wave_index} already has team '{existing[0].id}'",
        )
    team = tm.create_team(
        plan_id=plan_id,
        wave_index=wave_index,
        name=f"Force-started Wave {wave_index + 1}",
    )
    if team is None:
        raise HTTPException(400, "Failed to create team — check plan_id and wave index")
    return {"team": team.to_dict()}


# -------------------------------------------------------------------
# Command bar — natural language orchestrator instructions
# -------------------------------------------------------------------

@router.post("/{agent_id}/command")
async def send_command(
    agent_id: str, body: CommandRequest, request: Request,
) -> dict[str, Any]:
    """Send a natural-language instruction to the orchestrator.

    The message is enriched with project context (view, focused team,
    focused member) and routed through the agentic loop.
    """
    runtime = _get_runtime(request, agent_id)

    ctx_prefix = ""
    if body.context:
        parts = []
        if body.context.get("view"):
            parts.append(f"view={body.context['view']}")
        if body.context.get("focused_team_id"):
            parts.append(f"team={body.context['focused_team_id']}")
        if body.context.get("focused_member") is not None:
            parts.append(f"member={body.context['focused_member']}")
        if body.context.get("plan_id"):
            parts.append(f"plan={body.context['plan_id']}")
        if parts:
            ctx_prefix = f"[Projects Dashboard | {' | '.join(parts)}] "

    enriched = ctx_prefix + body.message

    try:
        result = await runtime.process_message_agentic_async(enriched)
        response = getattr(result, "final_response", "") or ""
        return {
            "status": "ok",
            "response": response[:2000],
            "iterations": getattr(result, "iterations", 0),
        }
    except Exception as exc:
        logger.exception("Command failed for agent %s", agent_id)
        raise HTTPException(500, f"Command failed: {exc}")


# -------------------------------------------------------------------
# Project timeline (read-only, computed from plan + teams)
# -------------------------------------------------------------------

@router.get("/{agent_id}/projects/{plan_id}/timeline")
async def get_timeline(
    agent_id: str, plan_id: str, request: Request,
) -> dict[str, Any]:
    """Return the plan's delegation waves with team status overlaid."""
    tm = _get_team_manager(request, agent_id)
    runtime = _get_runtime(request, agent_id)

    agent_dir = getattr(runtime, "agent_dir", None)
    workspace = str(agent_dir / "workspace") if agent_dir else ""
    if not workspace:
        raise HTTPException(500, "No workspace for agent")

    from nls.agentic.plan_store import PlanStore, get_delegation_waves
    plan_store = PlanStore(workspace)
    plan = plan_store.load(plan_id)
    if plan is None:
        raise HTTPException(404, f"Plan '{plan_id}' not found")

    waves = get_delegation_waves(plan)
    all_teams = tm.list_teams(include_terminal=True)

    timeline: list[dict[str, Any]] = []
    for w_idx, wave_steps in enumerate(waves):
        matching_team = next(
            (t for t in all_teams if t.plan_id == plan_id and t.wave_index == w_idx),
            None,
        )
        wave_data: dict[str, Any] = {
            "wave_index": w_idx,
            "steps": [
                {
                    "id": s.id,
                    "label": s.label,
                    "status": s.status,
                    "depends_on": s.depends_on,
                    "delegatable": s.delegatable,
                }
                for s in wave_steps
            ],
        }
        if matching_team is not None:
            wave_data["team"] = {
                "id": matching_team.id,
                "name": matching_team.name,
                "status": matching_team.status,
                "progress": matching_team.progress,
                "created_at": matching_team.created_at,
                "completed_at": matching_team.completed_at,
            }
        else:
            wave_data["team"] = None

        timeline.append(wave_data)

    return {
        "plan_id": plan.id,
        "title": plan.title,
        "status": plan.status,
        "waves": timeline,
    }
