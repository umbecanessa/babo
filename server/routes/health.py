"""Health endpoint — inference backend, sleep queue, agent overview."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict:
    """Health check with system status."""
    app = request.app

    model_status = app.state.model_manager.get_status()
    sleep_status = app.state.sleep_scheduler.get_status()
    agent_overview = app.state.agent_manager.get_overview()

    consciousness_status = None
    consciousness_scheduler = getattr(
        app.state, "consciousness_scheduler", None,
    )
    if consciousness_scheduler is not None:
        consciousness_status = consciousness_scheduler.get_status()

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

    return {
        "status": "healthy" if model_status["loaded"] else "loading",
        "model": model_status,
        "sleep_queue": sleep_status,
        "consciousness": consciousness_status,
        "agents": agent_overview,
        "agent_energy": agent_energy,
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
    logger.info("Graceful shutdown requested via /admin/shutdown")

    async def _exit():
        await asyncio.sleep(0.5)
        os.kill(os.getpid(), signal.SIGINT)

    asyncio.get_running_loop().create_task(_exit())
    return {"ok": True}
