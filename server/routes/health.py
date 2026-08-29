"""Health endpoint — inference backend, sleep queue, agent overview."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request

from server.shutdown_trace import record_initiator, request_sigint_exit

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/ready")
async def ready() -> dict:
    """Lightweight readiness probe for Electron startup (no heavy status work)."""
    return {"status": "ok"}


@router.get("/health")
async def health(request: Request) -> dict:
    """Health check with system status."""
    app = request.app

    try:
        model_status = app.state.model_manager.get_status()
    except Exception as exc:
        logger.warning("health: model status failed: %s", exc)
        model_status = {"loaded": False, "error": str(exc)}

    try:
        sleep_status = app.state.sleep_scheduler.get_status()
    except Exception as exc:
        logger.warning("health: sleep status failed: %s", exc)
        sleep_status = {"error": str(exc)}

    try:
        agent_overview = app.state.agent_manager.get_overview()
    except Exception as exc:
        logger.warning("health: agent overview failed: %s", exc)
        agent_overview = {"error": str(exc)}

    consciousness_status = None
    consciousness_scheduler = getattr(
        app.state, "consciousness_scheduler", None,
    )
    if consciousness_scheduler is not None:
        try:
            consciousness_status = consciousness_scheduler.get_status()
        except Exception as exc:
            logger.warning("health: consciousness status failed: %s", exc)

    agent_energy: dict[str, Any] = {}
    agent_mgr = getattr(app.state, "agent_manager", None)
    if agent_mgr is not None:
        try:
            for aid, runtime in agent_mgr.get_loaded_runtimes().items():
                e_level = 1.0
                a_state = "awake"
                if getattr(runtime, "temporal_self", None) is not None:
                    e_level = runtime.temporal_self.energy
                if getattr(runtime, "ans", None) is not None:
                    a_state = runtime.ans.state.value
                agent_energy[aid] = {
                    "energy_level": round(e_level, 2),
                    "agent_status": a_state,
                }
        except Exception:
            pass

    agentic_loops_ws = 0
    cm = getattr(app.state, "connection_manager", None)
    if cm is not None:
        try:
            for aid in cm.connected_agents():
                if cm.agentic_running(aid):
                    agentic_loops_ws += 1
        except Exception:
            pass

    agentic_loops_disk = 0
    settings = getattr(app.state, "settings", None)
    if settings is not None:
        try:
            from nls.agentic.active_loop_marker import count_active_agentic_loops

            agentic_loops_disk = count_active_agentic_loops(settings.agents_dir)
        except Exception:
            pass

    agentic_loops_active = max(agentic_loops_ws, agentic_loops_disk)

    return {
        "status": "healthy" if model_status.get("loaded") else "loading",
        "model": model_status,
        "sleep_queue": sleep_status,
        "consciousness": consciousness_status,
        "agents": agent_overview,
        "agent_energy": agent_energy,
        "agentic_loops_active": agentic_loops_active,
        "agentic_running": agentic_loops_active > 0,
    }


@router.get("/admin/hidden_cache/status")
async def hidden_cache_status(request: Request) -> dict:
    return {"enabled": False, "reason": "not available in product mode"}


@router.post("/admin/hidden_cache/enable")
async def hidden_cache_enable(request: Request) -> dict:
    return {"ok": False, "error": "not available in product mode"}


@router.post("/admin/hidden_cache/disable")
async def hidden_cache_disable(request: Request) -> dict:
    return {"ok": True, "disabled": True}


@router.post("/admin/shutdown")
async def graceful_shutdown(request: Request) -> dict:
    """Trigger a graceful shutdown from the desktop Electron wrapper."""
    client_host = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")
    record_initiator(
        "http:admin_shutdown",
        client=client_host,
        user_agent=user_agent[:200] if user_agent else None,
    )
    logger.info(
        "Graceful shutdown requested via /admin/shutdown (client=%s)",
        client_host,
    )

    async def _exit():
        await asyncio.sleep(0.5)
        request_sigint_exit()

    asyncio.get_running_loop().create_task(_exit())
    return {"ok": True}
