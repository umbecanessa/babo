"""REST API for squad (multi-agent fleet) management."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from server.routes.squad_access import (
    caller_agent_id as resolve_caller_agent_id,
    require_lead_or_owner,
    require_owner_dashboard,
    require_squad_member,
)
from server.middleware.auth import verify_auth

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


class PendingActionResolve(BaseModel):
    approved: bool
    resolution_note: str = ""


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
async def create_squad(
    body: SquadCreate,
    request: Request,
    _auth: dict[str, Any] = Depends(require_owner_dashboard),
) -> dict[str, Any]:
    reg = _registry(request)
    try:
        squad = reg.create(
            name=body.name,
            lead_agent_id=body.lead_agent_id,
            member_agent_ids=body.member_agent_ids,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _apply_roster_sync(request, squad, set())
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
    auth: dict[str, Any] = Depends(verify_auth),
) -> dict[str, Any]:
    reg = _registry(request)
    squad = reg.get(squad_id)
    if squad is None:
        raise HTTPException(404, "Squad not found")
    caller = caller_agent_id or resolve_caller_agent_id(request)
    settings_change = any(
        x is not None
        for x in (
            body.checkback_enabled,
            body.checkback_interval_seconds,
            body.proposal_sla_seconds,
        )
    )
    membership_change = any(
        x is not None
        for x in (
            body.lead_agent_id,
            body.member_agent_ids,
            body.name,
        )
    )
    if settings_change:
        require_lead_or_owner(squad, caller, auth, action="update squad settings")
    if membership_change:
        require_lead_or_owner(squad, caller, auth, action="update squad membership")
    old_ids = set(squad.all_member_ids)
    try:
        if membership_change:
            squad = reg.update_members(
                squad_id,
                lead_agent_id=body.lead_agent_id,
                member_agent_ids=body.member_agent_ids,
                name=body.name,
            )
        if settings_change:
            squad = reg.update_settings(
                squad_id,
                checkback_enabled=body.checkback_enabled,
                checkback_interval_seconds=body.checkback_interval_seconds,
                proposal_sla_seconds=body.proposal_sla_seconds,
            )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if membership_change:
        _apply_roster_sync(request, squad, old_ids)
    return squad.to_dict()


@router.post("/{squad_id}/pending-actions/{action_id}/resolve")
async def resolve_pending_action(
    squad_id: str,
    action_id: str,
    body: PendingActionResolve,
    request: Request,
    _auth: dict[str, Any] = Depends(require_owner_dashboard),
) -> dict[str, Any]:
    reg = _registry(request)
    sm = _manager(request)
    squad = reg.get(squad_id)
    if squad is None:
        raise HTTPException(404, "Squad not found")
    try:
        result = await sm.resolve_pending_action(
            squad_id,
            action_id,
            approved=body.approved,
            resolution_note=body.resolution_note,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@router.delete("/{squad_id}")
async def delete_squad(
    squad_id: str,
    request: Request,
    caller_agent_id: str | None = Query(None),
    delete_agents: bool = Query(False),
    auth: dict[str, Any] = Depends(verify_auth),
) -> dict[str, Any]:
    reg = _registry(request)
    squad = reg.get(squad_id)
    if squad is None:
        raise HTTPException(404, "Squad not found")
    caller = caller_agent_id or resolve_caller_agent_id(request)
    require_lead_or_owner(squad, caller, auth, action="delete this squad")
    member_ids = list(squad.all_member_ids)
    for aid in member_ids:
        _sync_agent_squad(request, aid, None)
    if not reg.delete(squad_id):
        raise HTTPException(404, "Squad not found")

    agents_deleted: list[str] = []
    if delete_agents:
        am = request.app.state.agent_manager
        cm = getattr(request.app.state, "connection_manager", None)
        for aid in member_ids:
            try:
                await am.delete_agent(aid)
                agents_deleted.append(aid)
                if cm is not None:
                    await cm.stop_relay(aid)
            except Exception as exc:
                logger.error("Failed to delete agent %s during squad delete: %s", aid, exc)
                raise HTTPException(
                    500,
                    f"Squad removed but failed deleting agent {aid}: {exc}",
                ) from exc

    return {"deleted": squad_id, "agents_deleted": agents_deleted}


@router.get("/by-agent/{agent_id}")
async def get_squad_for_agent(agent_id: str, request: Request) -> dict[str, Any]:
    reg = _registry(request)
    squad = reg.get_for_agent(agent_id)
    if squad is None:
        return {
            "squad": None,
            "channel_topology": None,
            "channel_topology_guidance": "",
        }

    guidance = ""
    topology_payload: dict[str, Any] | None = None
    try:
        from nls.runtime.fleet_channel_topology import (
            build_fleet_topology_snapshot,
            render_topology_guidance,
            topology_to_dict,
        )

        settings = request.app.state.settings
        snap = build_fleet_topology_snapshot(
            agent_id=agent_id,
            agent_dir=settings.agents_dir / agent_id,
            app=request.app,
            squad=squad,
        )
        if snap.mode != "none":
            topology_payload = topology_to_dict(snap)
            guidance = render_topology_guidance(snap, compact=True)
    except Exception:
        logger.debug("channel topology for agent %s failed", agent_id, exc_info=True)

    d = squad.to_dict()
    d["job_titles"] = _member_job_titles(request, squad.all_member_ids)
    return {
        "squad": d,
        "is_lead": squad.is_lead(agent_id),
        "channel_topology": topology_payload,
        "channel_topology_guidance": guidance,
    }


def _sync_squad_members(request: Request, squad) -> None:
    for aid in squad.all_member_ids:
        _sync_agent_squad(request, aid, squad)


def _apply_roster_sync(request: Request, squad, old_ids: set[str]) -> None:
    sm = _manager(request)

    def sync_fn(agent_id: str, sq) -> None:
        _sync_agent_squad(request, agent_id, sq)

    sm.apply_roster_change(squad, old_ids, sync_fn)


def _sync_agent_squad(request: Request, agent_id: str, squad) -> None:
    runtime = request.app.state.agent_manager.get_runtime(agent_id)
    sm = _manager(request)
    sm.sync_agent_runtime(agent_id, runtime, squad=squad)
    if runtime is not None and hasattr(runtime, "sync_squad_tools"):
        runtime.sync_squad_tools()
        if hasattr(runtime, "refresh_tools"):
            runtime.refresh_tools()
