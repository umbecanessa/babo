"""Channel management API routes.

Provides endpoints for the frontend to query channel status,
list active conversation threads, and manage channel configuration.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/channels", tags=["channels"])


@router.get("/{agent_id}/status")
async def channel_status(agent_id: str, request: Request):
    """Get connection status for all channels of an agent."""
    runtime = _get_runtime(request, agent_id)
    registry = getattr(runtime, "channel_registry", None)
    if registry is None:
        return {"channels": []}

    connected = await registry.list_connected()
    return {"channels": connected}


@router.get("/{agent_id}/threads")
async def list_threads(agent_id: str, request: Request):
    """List all active conversation threads across channels."""
    runtime = _get_runtime(request, agent_id)
    registry = getattr(runtime, "channel_registry", None)
    if registry is None:
        return {"threads": []}

    sessions = registry.session_router.list_sessions()

    threads = []
    for key, meta in sessions.items():
        parts = key.split(":")
        channel = parts[0] if parts else "unknown"
        thread_type = parts[1] if len(parts) > 1 else "main"
        identifier = parts[2] if len(parts) > 2 else ""

        label = _build_thread_label(channel, thread_type, identifier)

        threads.append({
            "key": key,
            "channel": channel,
            "type": thread_type,
            "identifier": identifier,
            "label": label,
            "last_updated": meta.get("last_updated"),
            "turn_count": meta.get("turn_count", 0),
        })

    threads.sort(key=lambda t: t.get("last_updated", 0), reverse=True)
    return {"threads": threads}


@router.post("/{agent_id}/send")
async def send_to_thread(agent_id: str, request: Request):
    """Operator outbound send on a surface session (Discord, Telegram, …)."""
    body = await request.json()
    session_key = body.get("session_key", "")
    content = body.get("content", "")
    attachments = body.get("attachments") or []

    if not session_key or not (content or attachments):
        raise HTTPException(status_code=400, detail="session_key and content required")

    runtime = _get_runtime(request, agent_id)
    from nls.skills.surface_send import send_surface_message

    result = await send_surface_message(
        request.app,
        runtime,
        agent_id,
        session_key,
        content,
        attachments=attachments,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "send failed"))
    return result


@router.get("/{agent_id}/threads/{session_key:path}/history")
async def thread_history(agent_id: str, session_key: str, request: Request):
    """Load conversation history for a specific thread."""
    runtime = _get_runtime(request, agent_id)
    history = runtime.load_session_history(session_key)
    return {"session_key": session_key, "history": history}


def _get_runtime(request: Request, agent_id: str) -> Any:
    agent_manager = getattr(request.app.state, "agent_manager", None)
    if agent_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager not ready")
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    return runtime


def _build_thread_label(channel: str, thread_type: str, identifier: str) -> str:
    if channel == "websocket":
        return "Main Chat"
    if channel == "telegram":
        prefix = "TG"
        if thread_type == "dm":
            return f"{prefix}: DM {identifier}"
        if thread_type == "group":
            return f"{prefix}: Group {identifier}"
        return f"{prefix}: {identifier}"
    if channel == "whatsapp":
        return f"WA: {identifier}" if identifier else "WhatsApp"
    if channel == "email":
        return f"Email: {identifier}" if identifier else "Email Thread"
    return f"{channel}: {identifier or thread_type}"
