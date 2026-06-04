"""Squad API access control — members-only reads, lead-only settings."""

from __future__ import annotations

from fastapi import HTTPException, Request

from nls.agentic.squad_registry import Squad


def caller_agent_id(request: Request) -> str | None:
    """Agent identity for squad visibility (header or query)."""
    raw = (
        request.headers.get("X-Babo-Agent-Id")
        or request.headers.get("x-babo-agent-id")
        or request.query_params.get("caller_agent_id")
    )
    return (raw or "").strip() or None


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
