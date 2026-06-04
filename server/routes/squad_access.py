"""Squad API access control — members-only reads, lead-only settings, owner dashboard."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request

from nls.agentic.squad_registry import Squad
from server.middleware.auth import verify_auth

_OWNER_AUTH_TYPES = frozenset({"local_trust", "shared_secret"})


def caller_agent_id(request: Request) -> str | None:
    """Agent identity for squad visibility (header or query)."""
    raw = (
        request.headers.get("X-Babo-Agent-Id")
        or request.headers.get("x-babo-agent-id")
        or request.query_params.get("caller_agent_id")
    )
    return (raw or "").strip() or None


async def require_owner_dashboard(
    auth: dict[str, Any] = Depends(verify_auth),
) -> dict[str, Any]:
    """Owner UI or trusted backend (desktop / NestJS proxy). Blocks agent API keys."""
    if auth.get("auth_type") not in _OWNER_AUTH_TYPES:
        raise HTTPException(
            403,
            "Owner dashboard or trusted backend required (not agent API key)",
        )
    return auth


def require_squad_member(squad: Squad, agent_id: str) -> None:
    if not agent_id:
        raise HTTPException(
            400,
            "caller_agent_id required (query or X-Babo-Agent-Id header)",
        )
    if not squad.is_member(agent_id):
        raise HTTPException(403, "Agent is not a member of this squad")


def require_squad_lead(squad: Squad, agent_id: str) -> None:
    require_squad_member(squad, agent_id)
    if not squad.is_lead(agent_id):
        raise HTTPException(403, "Only the squad lead may perform this action")


def require_lead_or_owner(
    squad: Squad,
    caller: str | None,
    auth: dict[str, Any],
    *,
    action: str = "modify this squad",
) -> None:
    """Lead via caller_agent_id, or owner dashboard / backend without caller."""
    if caller:
        require_squad_lead(squad, caller)
        return
    if auth.get("auth_type") in _OWNER_AUTH_TYPES:
        return
    raise HTTPException(
        403,
        f"caller_agent_id (squad lead) or owner dashboard required to {action}",
    )
