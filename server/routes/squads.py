"""REST API for squad (multi-agent fleet) management."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from server.routes.squad_access import (
    caller_agent_id as resolve_caller_agent_id,
    require_squad_lead,
    require_squad_member,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/squads", tags=["squads"])


class SquadCreate(BaseModel):
    name: str
    lead_agent_id: str
    member_agent_ids: list[str] = Field(default_factory=list)


class SquadUpdate(BaseModel):
    name: str | None = None
    lead_agent_id: str | None = None
    member_agent_ids: list[str] | None = None
    checkback_enabled: bool | None = None
    checkback_interval_seconds: int | None = None
    proposal_sla_seconds: int | None = None


def _registry(request: Request):
    reg = getattr(request.app.state, "squad_registry", None)
    if reg is None:
        raise HTTPException(503, "Squad registry not available")
    return reg


def _manager(request: Request):
    sm = getattr(request.app.state, "squad_manager", None)
    if sm is None:
        raise HTTPException(503, "Squad manager not available")
    return sm


def _filter_squads_for_caller(squads: list, caller: str | None) -> list:
    if not caller:
        return squads
    return [s for s in squads if s.is_member(caller)]


@router.get("")
async def list_squads(
    request: Request,
    caller_agent_id: str | None = Query(None, description="Filter to squads this agent belongs to"),
) -> dict[str, Any]:
    reg = _registry(request)
    caller = caller_agent_id or resolve_caller_agent_id(request)
    squads = _filter_squads_for_caller(reg.list_squads(), caller)
    return {
        "squads": [
            {
                **s.to_dict(),
                "job_titles": _member_job_titles(request, s.all_member_ids),
            }
            for s in squads
        ],
    }


def _member_job_titles(request: Request, agent_ids: list[str]) -> dict[str, str]:
    from nls.runtime.job_trust import load_job, DEFAULT_JOB_TITLE

    out: dict[str, str] = {}
    agents_dir = request.app.state.settings.agents_dir
    for aid in agent_ids:
        job = load_job(agents_dir / aid)
        out[aid] = job.display_title or DEFAULT_JOB_TITLE
    return out


@router.post("")
async def create_squad(body: SquadCreate, request: Request) -> dict[str, Any]:
    reg = _registry(request)
    try:
        squad = reg.create(
            name=body.name,
            lead_agent_id=body.lead_agent_id,
            member_agent_ids=body.member_agent_ids,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _sync_squad_members(request, squad)
    return squad.to_dict()


@router.get("/{squad_id}")
async def get_squad(
    squad_id: str,
    request: Request,
    caller_agent_id: str | None = Query(None),
) -> dict[str, Any]:
    reg = _registry(request)
    squad = reg.get(squad_id)
    if squad is None:
        raise HTTPException(404, "Squad not found")
    caller = caller_agent_id or resolve_caller_agent_id(request)
    if caller:
        require_squad_member(squad, caller)
    d = squad.to_dict()
    d["job_titles"] = _member_job_titles(request, squad.all_member_ids)
    return d


@router.get("/{squad_id}/kanban")
async def get_squad_kanban(
    squad_id: str,
    request: Request,
    caller_agent_id: str | None = Query(None),
) -> dict[str, Any]:
    reg = _registry(request)
    sm = _manager(request)
    squad = reg.get(squad_id)
    if squad is None:
        raise HTTPException(404, "Squad not found")
    caller = caller_agent_id or resolve_caller_agent_id(request)
    require_squad_member(squad, caller)
    board = sm.build_kanban_view(squad)
    board["job_titles"] = _member_job_titles(request, squad.all_member_ids)
    return board


@router.patch("/{squad_id}")
async def update_squad(
    squad_id: str,
    body: SquadUpdate,
    request: Request,
    caller_agent_id: str | None = Query(None),
) -> dict[str, Any]:
    reg = _registry(request)
    squad = reg.get(squad_id)
    if squad is None:
        raise HTTPException(404, "Squad not found")
    caller = caller_agent_id or resolve_caller_agent_id(request)
    if any(
        x is not None
        for x in (
            body.checkback_enabled,
            body.checkback_interval_seconds,
            body.proposal_sla_seconds,
        )
    ):
        require_squad_lead(squad, caller or squad.lead_agent_id)
    try:
        if any(
            x is not None
            for x in (
                body.lead_agent_id,
                body.member_agent_ids,
                body.name,
            )
        ):
            if caller:
                require_squad_lead(squad, caller)
            squad = reg.update_members(
                squad_id,
                lead_agent_id=body.lead_agent_id,
                member_agent_ids=body.member_agent_ids,
                name=body.name,
            )
        if any(
            x is not None
            for x in (
                body.checkback_enabled,
                body.checkback_interval_seconds,
                body.proposal_sla_seconds,
            )
        ):
            squad = reg.update_settings(
                squad_id,
                checkback_enabled=body.checkback_enabled,
                checkback_interval_seconds=body.checkback_interval_seconds,
                proposal_sla_seconds=body.proposal_sla_seconds,
            )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _sync_squad_members(request, squad)
    return squad.to_dict()


@router.delete("/{squad_id}")
async def delete_squad(
    squad_id: str,
    request: Request,
    caller_agent_id: str | None = Query(None),
) -> dict[str, Any]:
    reg = _registry(request)
    squad = reg.get(squad_id)
    if squad is None:
        raise HTTPException(404, "Squad not found")
    caller = caller_agent_id or resolve_caller_agent_id(request)
    require_squad_lead(squad, caller or squad.lead_agent_id)
    for aid in squad.all_member_ids:
        _sync_agent_squad(request, aid, None)
    if not reg.delete(squad_id):
        raise HTTPException(404, "Squad not found")
    return {"deleted": squad_id}


@router.get("/by-agent/{agent_id}")
async def get_squad_for_agent(agent_id: str, request: Request) -> dict[str, Any]:
    reg = _registry(request)
    squad = reg.get_for_agent(agent_id)
    if squad is None:
        return {"squad": None}
    d = squad.to_dict()
    d["job_titles"] = _member_job_titles(request, squad.all_member_ids)
    return {"squad": d, "is_lead": squad.is_lead(agent_id)}


def _sync_squad_members(request: Request, squad) -> None:
    for aid in squad.all_member_ids:
        _sync_agent_squad(request, aid, squad)


def _wire_lead_hooks(sm, lead_agent_id: str, request: Request) -> None:
    """Attach WM orchestration hooks when the lead runtime already has an active loop."""
    runtime = request.app.state.agent_manager.get_runtime(lead_agent_id)
    if runtime is None:
        return
    hooks = getattr(runtime, "_agentic_hooks", None)
    if hooks is not None:
        sm.set_hooks(hooks)


def _sync_agent_squad(request: Request, agent_id: str, squad) -> None:
    runtime = request.app.state.agent_manager.get_runtime(agent_id)
    if runtime is None:
        return
    if hasattr(runtime, "sync_job_trust"):
        runtime.sync_job_trust(squad=squad)
    if hasattr(runtime, "sync_squad_tools"):
        runtime.sync_squad_tools()
    sm = getattr(request.app.state, "squad_manager", None)
    if sm is not None and squad is not None and squad.is_lead(agent_id):
        _wire_lead_hooks(sm, squad.lead_agent_id, request)
