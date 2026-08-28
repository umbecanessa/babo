"""REST endpoints for the chat module (non-WebSocket)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .helpers import _build_nls_metadata

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


@router.get("/sessions/{agent_id}")
async def list_sessions(agent_id: str, request: Request):
    """List all session threads for an agent.

    Always includes the main conversation (websocket:main) even though
    it's stored separately from the session-router index.
    """
    app = request.app
    agent_manager = app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        return {"sessions": {}, "default_home_session_key": "websocket:main", "primary_reachability_session_key": "websocket:main"}

    sessions: dict[str, Any] = {}

    # Always include the main conversation (thread offload — transcript IO is sync).
    main_history = await asyncio.to_thread(
        runtime.load_chat_transcript, max_turns=200,
    )
    sessions["websocket:main"] = {
        "channel": "websocket",
        "label": "Main Chat",
        "message_count": len(main_history),
    }

    # Merge channel-specific sessions from the router index
    router_obj = runtime.channel_registry.session_router
    for key, meta in router_obj.list_sessions().items():
        if key != "websocket:main":
            enriched = dict(meta)
            channel = enriched.get("channel") or key.split(":")[0]
            registry = runtime.channel_registry
            adapter = registry.get(channel) if registry is not None else None
            if adapter is not None:
                try:
                    cfg_fn = getattr(adapter, "_agent_cfg", None)
                    if cfg_fn is not None:
                        from nls.skills.channel_scope import enrich_session_index_entry
                        workspace_name = ""
                        if channel == "slack":
                            team_names = getattr(adapter, "_team_names", None) or {}
                            workspace_name = str(team_names.get(agent_id) or "")
                        enriched = enrich_session_index_entry(
                            cfg_fn(agent_id), key, enriched,
                            workspace_name=workspace_name,
                        )
                except Exception:
                    logger.debug("session label enrich failed for %s", key, exc_info=True)
            sessions[key] = enriched

    # Team/delegate pseudo-threads
    _tm = getattr(runtime, "_team_manager", None)
    if _tm is not None:
        try:
            for team in _tm.list_teams(include_terminal=True):
                tkey = f"team:{team.id}"
                sessions[tkey] = {
                    "channel": "team",
                    "label": f"Team: {team.name}",
                    "message_count": len(team.results_log),
                    "team_id": team.id,
                    "status": team.status,
                }
        except Exception:
            pass

    _dm = getattr(runtime, "delegate_manager", None)
    if _dm is not None:
        try:
            for ds in _dm.list_all():
                dkey = f"delegate:{ds.delegate_number}"
                sessions[dkey] = {
                    "channel": "delegate",
                    "label": f"Delegate #{ds.delegate_number}: {ds.task[:50]}",
                    "message_count": 1 if ds.summary_preview else 0,
                    "delegate_number": ds.delegate_number,
                    "state": ds.state,
                }
        except Exception:
            pass

    return {"sessions": sessions, "default_home_session_key": runtime.get_default_home_session_key(), "primary_reachability_session_key": runtime.get_primary_reachability_session_key()}


@router.get("/sessions/{agent_id}/{session_key:path}")
async def get_session_history(agent_id: str, session_key: str, request: Request):
    """Load conversation history for a specific session/thread."""
    app = request.app
    agent_manager = app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        return {"messages": []}

    if session_key == "websocket:main":
        history = await asyncio.to_thread(
            runtime.load_chat_transcript, max_turns=200,
        )
    elif session_key.startswith("team:"):
        team_id = session_key.split(":", 1)[1]
        return _build_team_thread(runtime, team_id)
    elif session_key.startswith("delegate:"):
        try:
            delegate_num = int(session_key.split(":", 1)[1])
        except (ValueError, IndexError):
            return {"messages": []}
        return _build_delegate_thread(runtime, delegate_num)
    else:
        ui_history = await asyncio.to_thread(
            runtime.load_session_transcript,
            session_key,
            max_turns=200,
        )
        if ui_history:
            history = ui_history
        else:
            history = await asyncio.to_thread(
                runtime.load_session_history,
                session_key=session_key,
                max_turns=200,
            )

    chat_msgs = [m for m in history if m.get("role") in ("user", "assistant")]

    ambient_timeline: list[dict[str, Any]] = []
    try:
        from nls.runtime.channel_ambient import ambient_timeline_for_session

        agent_dir = getattr(runtime, "agent_dir", None)
        if agent_dir is not None:
            ambient_timeline = ambient_timeline_for_session(
                agent_dir, session_key,
            )
    except Exception:
        logger.debug("ambient timeline load failed for %s", session_key, exc_info=True)

    return {"messages": chat_msgs, "ambient_timeline": ambient_timeline}


@router.patch("/sessions/{agent_id}/{session_key:path}")
async def patch_session(agent_id: str, session_key: str, request: Request):
    """Update branch metadata (e.g. label)."""
    if session_key == "websocket:main":
        return JSONResponse({"ok": False, "error": "cannot rename main session"}, status_code=400)

    body = await request.json()
    label = str(body.get("label") or "").strip()
    if not label:
        return JSONResponse({"ok": False, "error": "label required"}, status_code=400)

    runtime = request.app.state.agent_manager.get_runtime(agent_id)
    if runtime is None:
        return JSONResponse({"ok": False, "error": "agent not loaded"}, status_code=404)

    ok = await asyncio.to_thread(runtime.update_session_label, session_key, label)
    return {"ok": ok, "session_key": session_key, "label": label}


@router.delete("/sessions/{agent_id}/{session_key:path}")
async def delete_session(agent_id: str, session_key: str, request: Request):
    """Delete a branch or channel session history."""
    if session_key == "websocket:main":
        return JSONResponse({"ok": False, "error": "cannot delete main session"}, status_code=400)

    runtime = request.app.state.agent_manager.get_runtime(agent_id)
    if runtime is None:
        return JSONResponse({"ok": False, "error": "agent not loaded"}, status_code=404)

    if runtime.get_default_home_session_key() == session_key:
        return JSONResponse(
            {"ok": False, "error": "cannot delete the current Home session"},
            status_code=400,
        )

    ok = await asyncio.to_thread(runtime.delete_session_thread, session_key)
    return {"ok": ok, "session_key": session_key}


@router.post("/sessions/{agent_id}/default-home")
async def set_default_home_session(agent_id: str, request: Request):
    """Point default Home at an existing websocket session (no data moves)."""
    body = await request.json()
    session_key = str(body.get("session_key") or "").strip()
    if not session_key:
        return JSONResponse({"ok": False, "error": "session_key required"}, status_code=400)

    from nls.runtime.agent_runtime import is_valid_home_session_key

    if not is_valid_home_session_key(session_key):
        return JSONResponse(
            {"ok": False, "error": "session_key must be websocket:main or a websocket branch"},
            status_code=400,
        )

    runtime = request.app.state.agent_manager.get_runtime(agent_id)
    if runtime is None:
        return JSONResponse({"ok": False, "error": "agent not loaded"}, status_code=404)

    if session_key != "websocket:main":
        registry = getattr(runtime, "channel_registry", None)
        if registry is None:
            return JSONResponse({"ok": False, "error": "session router unavailable"}, status_code=503)
        router = registry.session_router
        if session_key not in router.list_sessions():
            return JSONResponse({"ok": False, "error": "unknown session"}, status_code=404)

    ok = await asyncio.to_thread(runtime.set_default_home_session_key, session_key)
    if not ok:
        return JSONResponse({"ok": False, "error": "failed to set default home"}, status_code=500)
    return {
        "ok": True,
        "default_home_session_key": runtime.get_default_home_session_key(),
    }


@router.post("/sessions/{agent_id}/primary-reachability")
async def set_primary_reachability(agent_id: str, request: Request):
    """Set where Babo should reach you by default (star on channel thread)."""
    body = await request.json()
    session_key = str(body.get("session_key") or "").strip()
    if not session_key:
        return JSONResponse({"ok": False, "error": "session_key required"}, status_code=400)

    runtime = request.app.state.agent_manager.get_runtime(agent_id)
    if runtime is None:
        return JSONResponse({"ok": False, "error": "agent not loaded"}, status_code=404)

    from nls.runtime.session_routing.config import is_valid_reachability_session_key

    if not is_valid_reachability_session_key(session_key, runtime):
        return JSONResponse(
            {"ok": False, "error": "session_key must be a Home branch or connected channel thread"},
            status_code=400,
        )

    if not session_key.startswith("websocket:"):
        registry = getattr(runtime, "channel_registry", None)
        if registry is None:
            return JSONResponse({"ok": False, "error": "session router unavailable"}, status_code=503)
        router = registry.session_router
        if session_key not in router.list_sessions():
            return JSONResponse({"ok": False, "error": "unknown session"}, status_code=404)

    ok = await asyncio.to_thread(runtime.set_primary_reachability_session_key, session_key)
    if not ok:
        return JSONResponse({"ok": False, "error": "failed to set primary reachability"}, status_code=500)
    return {
        "ok": True,
        "primary_reachability_session_key": runtime.get_primary_reachability_session_key(),
    }


@router.delete("/sessions/{agent_id}/primary-reachability")
async def clear_primary_reachability(agent_id: str, request: Request):
    """Revert primary reachability to the current default Home session."""
    runtime = request.app.state.agent_manager.get_runtime(agent_id)
    if runtime is None:
        return JSONResponse({"ok": False, "error": "agent not loaded"}, status_code=404)

    ok = await asyncio.to_thread(runtime.clear_primary_reachability_session_key)
    if not ok:
        return JSONResponse({"ok": False, "error": "failed to clear primary reachability"}, status_code=500)
    return {
        "ok": True,
        "primary_reachability_session_key": runtime.get_primary_reachability_session_key(),
    }


def _build_team_thread(runtime, team_id: str) -> dict:
    """Synthesize a chat-like thread from team lifecycle events."""
    _tm = getattr(runtime, "_team_manager", None)
    if _tm is None:
        return {"messages": []}
    team = _tm.inspect_team(team_id)
    if team is None:
        return {"messages": []}

    msgs = []
    msgs.append({
        "role": "assistant",
        "content": (
            f"Team '{team.name}' created for plan {team.plan_id}, "
            f"wave {team.wave_index + 1}. "
            f"Mission: {team.mission or 'N/A'}\n\n"
            f"Members:\n"
            + "\n".join(
                f"  #{m.delegate_number}: {m.task}"
                for m in team.members
            )
        ),
        "timestamp": team.created_at,
    })

    for member in team.members:
        if member.status == "running":
            msgs.append({
                "role": "assistant",
                "content": (
                    f"#{member.delegate_number} working on: {member.task} "
                    f"(iter {member.iterations}, {member.tool_calls} tool calls)"
                ),
            })
        elif member.status == "done":
            msgs.append({
                "role": "assistant",
                "content": (
                    f"#{member.delegate_number} completed: {member.task}\n"
                    f"Summary: {member.result_summary or 'N/A'}\n"
                    f"({member.iterations} iters, {member.tool_calls} tool calls, "
                    f"{round(member.elapsed_seconds, 1)}s)"
                ),
            })
        elif member.status == "failed":
            msgs.append({
                "role": "assistant",
                "content": (
                    f"#{member.delegate_number} FAILED: {member.task}\n"
                    f"Summary: {member.result_summary or 'N/A'}"
                ),
            })

    for entry in team.results_log:
        msgs.append({
            "role": "assistant",
            "content": (
                f"[Result] {entry.get('task', 'Unknown')}: {entry.get('status', 'N/A')}\n"
                f"{entry.get('summary', '')}"
            ),
        })

    return {"messages": msgs}


def _build_delegate_thread(runtime, delegate_number: int) -> dict:
    """Synthesize a chat-like thread from delegate state."""
    _dm = getattr(runtime, "delegate_manager", None)
    if _dm is None:
        return {"messages": []}

    all_status = _dm.list_all()
    ds = next((s for s in all_status if s.delegate_number == delegate_number), None)
    if ds is None:
        return {"messages": []}

    msgs = [{
        "role": "assistant",
        "content": (
            f"Delegate #{ds.delegate_number}: {ds.task}\n"
            f"Status: {ds.state} | Iterations: {ds.iteration}/{ds.max_iterations}\n"
            f"Tool calls: {ds.total_tool_calls} | Elapsed: {round(ds.elapsed_seconds, 1)}s"
        ),
    }]
    if ds.last_actions:
        msgs.append({
            "role": "assistant",
            "content": "Recent actions:\n" + "\n".join(f"  - {a}" for a in ds.last_actions),
        })
    if ds.summary_preview:
        msgs.append({
            "role": "assistant",
            "content": f"Summary:\n{ds.summary_preview}",
        })
    if ds.exit_reason:
        msgs.append({
            "role": "assistant",
            "content": f"Exit reason: {ds.exit_reason}",
        })

    return {"messages": msgs}


@router.post("/chat/relay")
async def chat_relay(request: Request):
    """Process a chat message forwarded through the ChannelRelayClient.

    Used by the remote dashboard: phone/browser -> NestJS -> relay WS ->
    desktop runtime -> this endpoint -> process_message -> response back.
    """
    body = await request.json()
    agent_id = body.get("agent_id", "")
    content = body.get("content", "")
    session_key = body.get("session_key", "web:remote:default")

    if not agent_id or not content:
        return JSONResponse({"error": "agent_id and content required"}, status_code=400)

    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        return JSONResponse({"error": "Agent not loaded"}, status_code=404)

    try:
        from nls.skills.surface_send import is_surface_session_key, send_surface_message

        if is_surface_session_key(session_key, runtime):
            result = await send_surface_message(
                request.app,
                runtime,
                agent_id,
                session_key,
                content,
            )
            if not result.get("ok"):
                return JSONResponse(
                    {"error": result.get("error", "send failed")},
                    status_code=400,
                )
            status = runtime.get_status()
            nls = _build_nls_metadata(
                status, signals=status.get("recent_signals", []),
            )
            return {
                "response": content,
                "agent_id": agent_id,
                "session_key": session_key,
                "nls": nls,
                "channel_send": True,
            }

        history = runtime.load_session_history(session_key=session_key, max_turns=20)

        result = await runtime.process_message_async(content, history=history)
        response_text = result.response

        updated_history = list(history or [])
        updated_history.append({"role": "user", "content": content})
        updated_history.append({"role": "assistant", "content": response_text})
        runtime.save_session_history(updated_history, session_key=session_key)

        status = runtime.get_status()
        nls = _build_nls_metadata(
            status, signals=status.get("recent_signals", []),
        )

        return {
            "response": response_text,
            "agent_id": agent_id,
            "session_key": session_key,
            "nls": nls,
        }
    except Exception as exc:
        logger.error("Chat relay failed for %s: %s", agent_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
