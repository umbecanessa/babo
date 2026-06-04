"""Agent CRUD endpoints."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Request

from nls.models import AgentStatus

logger = logging.getLogger(__name__)
from pydantic import BaseModel, Field

router = APIRouter(prefix="/agents", tags=["agents"])


# --- Request / Response Models ---


class CreateAgentRequest(BaseModel):
    model_config = {"populate_by_name": True}

    genesis_version: str = Field(
        default="",
        alias="genesisVersion",
        description="Genesis template version (default: server default).",
    )
    agent_id: str = Field(
        default="",
        description="Custom agent ID (default: auto UUID).",
    )
    name: str = Field(
        default="",
        description="Human-readable agent name.",
    )
    sovereignty: str = Field(
        default="local",
        description="Sovereignty mode: local, masked, full.",
    )
    config_overrides: dict | None = Field(
        default=None,
        description="Per-agent config overrides (deep-merged over genesis).",
    )
    soul_wish: str = Field(
        default="",
        alias="soulWish",
        description="Founding purpose / soul wish for the agent.",
    )
    owner_email: str = Field(
        default="",
        alias="ownerEmail",
        description="Owner's account email (from sign-up). Stored in agent_meta for contacts resolution.",
    )
    owner_name: str = Field(
        default="",
        alias="ownerName",
        description="Owner's display name (from sign-up). Stored in agent_meta for contacts resolution.",
    )


class CreateAgentResponse(BaseModel):
    agent_id: str
    name: str
    genesis_version: str
    status: str


# --- Endpoints ---


@router.get("/genesis")
async def list_genesis_templates(request: Request):
    """List available genesis templates from the local genesis directory."""
    from nls.ledger.genesis import list_genesis_templates_detail
    return list_genesis_templates_detail()


@router.post("", response_model=CreateAgentResponse)
async def create_agent(body: CreateAgentRequest, request: Request):
    """Create a new agent from a genesis template.

    The agent is immediately initialized and ready for chat.
    """
    settings = request.app.state.settings
    genesis = body.genesis_version or settings.default_genesis

    genesis_path = settings.genesis_dir / genesis
    if not genesis_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Genesis template '{genesis}' not found. "
            f"Available: {_list_genesis(settings.genesis_dir)}",
        )

    try:
        meta = await request.app.state.agent_manager.create_agent(
            genesis_version=genesis,
            agent_id=body.agent_id,
            name=body.name,
            sovereignty=body.sovereignty,
            config_overrides=body.config_overrides,
            soul_wish=body.soul_wish,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    new_agent_id = meta["agent_id"]

    # Persist the owner's account email and name so contacts(action='owner') always
    # knows the user's identity even when no skill is connected.
    if body.owner_email or body.owner_name:
        try:
            import json as _json
            _meta_path = request.app.state.settings.agents_dir / new_agent_id / "agent_meta.json"
            if _meta_path.exists():
                _meta_data = _json.loads(_meta_path.read_text(encoding="utf-8"))
                if body.owner_email:
                    _meta_data["owner_email"] = body.owner_email
                if body.owner_name:
                    _meta_data["owner_name"] = body.owner_name
                _meta_path.write_text(_json.dumps(_meta_data, indent=2), encoding="utf-8")
                logger.info(
                    "Agent %s: owner identity stored in meta (email=%s, name=%s)",
                    new_agent_id, body.owner_email, body.owner_name,
                )
        except Exception as _me:
            logger.warning("Agent %s: failed to persist owner identity: %s", new_agent_id, _me)

    from server.services.agent_relay import ensure_agent_relay

    connection_manager = getattr(request.app.state, "connection_manager", None)
    runtime = request.app.state.agent_manager.get_loaded_runtimes().get(new_agent_id)
    await ensure_agent_relay(
        connection_manager,
        new_agent_id,
        request.app.state.settings.agents_dir,
        runtime=runtime,
    )

    return CreateAgentResponse(
        agent_id=new_agent_id,
        name=meta.get("name", ""),
        genesis_version=meta.get("genesis_version", genesis),
        status=meta.get("status", "alive"),
    )


@router.get("")
async def list_agents(request: Request):
    """List all agents with their current status."""
    return request.app.state.agent_manager.list_agents()


@router.get("/{agent_id}/relay-status")
async def get_relay_status(agent_id: str, request: Request):
    """Whether this agent's ChannelRelayClient is connected to NestJS."""
    agent_dir = request.app.state.settings.agents_dir / agent_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    online = request.app.state.connection_manager.relay_connected(agent_id)
    return {"online": online, "connected": online}


@router.get("/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    """Get detailed status for a specific agent."""
    agent_dir = request.app.state.settings.agents_dir / agent_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    return request.app.state.agent_manager.get_agent_status(agent_id)


@router.get("/{agent_id}/processes")
async def list_project_processes(agent_id: str, request: Request):
    """List detached project dev servers tracked by the agent bash tool."""
    agent_dir = request.app.state.settings.agents_dir / agent_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    runtime = request.app.state.agent_manager.get_runtime(agent_id)
    cm = getattr(request.app.state, "connection_manager", None)
    agentic_running = cm.agentic_running(agent_id) if cm else False
    if runtime is None:
        return {"processes": [], "agentic_running": agentic_running}
    return {
        "processes": runtime.list_project_processes(),
        "agentic_running": agentic_running,
    }


@router.delete("/{agent_id}/processes/{pid}")
async def kill_project_process(agent_id: str, pid: int, request: Request):
    """Stop a detached project process started by the agent."""
    agent_dir = request.app.state.settings.agents_dir / agent_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    runtime = request.app.state.agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent runtime not loaded")

    if not await runtime.kill_project_process(pid):
        raise HTTPException(status_code=404, detail=f"Process {pid} not found")

    return {
        "ok": True,
        "pid": pid,
        "processes": runtime.list_project_processes(),
    }


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, request: Request):
    """Delete an agent (evict from VRAM + remove from disk)."""
    agent_dir = request.app.state.settings.agents_dir / agent_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    # Remove from squad roster before disk delete (dashboard direct-delete path).
    reg = getattr(request.app.state, "squad_registry", None)
    sm = getattr(request.app.state, "squad_manager", None)
    if reg is not None and sm is not None:
        squad = reg.get_for_agent(agent_id)
        if squad is not None and squad.is_member(agent_id):
            try:
                sm._remove_member_from_squad(squad, agent_id)
            except ValueError as exc:
                logger.warning("Agent %s squad cleanup on delete: %s", agent_id, exc)

    try:
        await request.app.state.agent_manager.delete_agent(agent_id)
    except Exception as exc:
        logger.error("Failed to delete agent %s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    cm = getattr(request.app.state, "connection_manager", None)
    if cm is not None:
        await cm.stop_relay(agent_id)

    return {"deleted": agent_id}


class UpdateAgentNameRequest(BaseModel):
    name: str = Field(..., description="New agent name.")


class UpdateOwnerEmailRequest(BaseModel):
    owner_email: str = Field(default="", alias="ownerEmail", description="Owner account email.")
    owner_name: str = Field(default="", alias="ownerName", description="Owner display name.")
    model_config = {"populate_by_name": True}


@router.patch("/{agent_id}/owner-email")
async def update_owner_email(
    agent_id: str, body: UpdateOwnerEmailRequest, request: Request,
):
    """Persist the owner's account email and display name into agent_meta.json.

    Called by NestJS when the user connects to an existing agent so that
    contacts(action='owner') always returns the user's email and name regardless
    of which skills are connected.
    """
    import json as _json
    agent_dir = request.app.state.settings.agents_dir / agent_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    meta_path = agent_dir / "agent_meta.json"
    meta: dict = {}
    if meta_path.exists():
        try:
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if body.owner_email:
        meta["owner_email"] = body.owner_email
    if body.owner_name:
        meta["owner_name"] = body.owner_name
    try:
        meta_path.write_text(_json.dumps(meta, indent=2), encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    logger.info(
        "Agent %s: owner identity updated (email=%s, name=%s)",
        agent_id, body.owner_email, body.owner_name,
    )
    return {"agent_id": agent_id, "owner_email": body.owner_email, "owner_name": body.owner_name}


@router.patch("/{agent_id}/name")
async def update_agent_name(
    agent_id: str, body: UpdateAgentNameRequest, request: Request,
):
    """Update an agent's display name.

    Persists to ``agent_meta.json`` and updates the manager cache.
    Called by the NestJS backend after the agent accepts a name in chat.
    """
    agent_dir = request.app.state.settings.agents_dir / agent_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    runtime = request.app.state.agent_manager.get_runtime(agent_id)
    if runtime is not None:
        runtime._save_agent_name(body.name)
    else:
        # Agent not loaded -- write directly to meta file
        import json
        meta_path = agent_dir / "agent_meta.json"
        meta: dict = {}
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        meta["agent_name"] = body.name
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    request.app.state.agent_manager.update_agent_name(agent_id, body.name)
    return {"agent_id": agent_id, "name": body.name}


class AgentInferenceSettings(BaseModel):
    orchestrator_model: str | None = Field(
        default=None,
        description="Default orchestrator model for this agent (OpenRouter-style id).",
    )
    delegate_model: str | None = Field(
        default=None,
        description="Default sub-agent/delegate model when not locked to orchestrator.",
    )
    delegate_lock_orchestrator: bool | None = Field(
        default=None,
        description="When true, delegates use the same model as the orchestrator turn.",
    )
    clear_orchestrator: bool = Field(
        default=False,
        description="Clear agent session orchestrator default (use install default).",
    )
    clear_delegate: bool = Field(
        default=False,
        description="Clear agent session delegate default.",
    )


@router.get("/{agent_id}/inference")
async def get_agent_inference(agent_id: str, request: Request):
    """Return per-agent session inference defaults."""
    agent_dir = request.app.state.settings.agents_dir / agent_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    runtime = request.app.state.agent_manager.get_runtime(agent_id)
    if runtime is not None:
        return runtime.session_inference_snapshot()

    import json as _json
    meta_path = agent_dir / "session_meta.json"
    if not meta_path.exists():
        return {
            "orchestrator_model": None,
            "delegate_model": None,
            "delegate_lock_orchestrator": True,
        }
    try:
        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    return {
        "orchestrator_model": meta.get("orchestrator_model"),
        "delegate_model": meta.get("delegate_model"),
        "delegate_lock_orchestrator": meta.get(
            "delegate_lock_orchestrator", True,
        ),
    }


@router.patch("/{agent_id}/inference")
async def update_agent_inference(
    agent_id: str,
    body: AgentInferenceSettings,
    request: Request,
):
    """Set per-agent session orchestrator/delegate model defaults."""
    agent_dir = request.app.state.settings.agents_dir / agent_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        try:
            await agent_manager.load_agent(agent_id)
            runtime = agent_manager.get_runtime(agent_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent runtime not available")

    fields = body.model_dump(exclude_unset=True)
    kwargs: dict = {}
    if "orchestrator_model" in fields:
        if fields["orchestrator_model"] is None:
            kwargs["clear_orchestrator"] = True
        else:
            kwargs["orchestrator_model"] = fields["orchestrator_model"]
    if "delegate_model" in fields:
        if fields["delegate_model"] is None:
            kwargs["clear_delegate"] = True
        else:
            kwargs["delegate_model"] = fields["delegate_model"]
    if "delegate_lock_orchestrator" in fields:
        kwargs["delegate_lock_orchestrator"] = fields[
            "delegate_lock_orchestrator"
        ]
    if fields.get("clear_orchestrator"):
        kwargs["clear_orchestrator"] = True
    if fields.get("clear_delegate"):
        kwargs["clear_delegate"] = True
    snapshot = runtime.update_session_inference(**kwargs)
    return {"agent_id": agent_id, **snapshot}


@router.post("/{agent_id}/pause")
async def pause_agent(agent_id: str, request: Request):
    """Pause an agent: stop its inner loop and prevent auto-wake.

    The agent stays registered but will not be rotated into CONSCIOUS
    by the scheduler.  Direct chat still works.
    """
    agent_dir = request.app.state.settings.agents_dir / agent_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    cs = getattr(request.app.state, "consciousness_scheduler", None)
    if cs is None:
        raise HTTPException(
            status_code=503, detail="Consciousness scheduler not available",
        )

    ok = await cs.pause_agent(agent_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Agent not registered")

    # Offload the runtime from memory to free RAM.  The agent will be
    # re-loaded on-demand when unpaused or when a chat is opened.
    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is not None:
        try:
            runtime.save_state()
            await runtime.shutdown_async()
        except Exception:
            pass
        agent_manager._runtimes.pop(agent_id, None)
        agent_manager._status[agent_id] = AgentStatus.OFFLINE
        logger.info("Agent %s: runtime offloaded on pause", agent_id)

    return {"agent_id": agent_id, "paused": True}


@router.post("/{agent_id}/unpause")
async def unpause_agent(agent_id: str, request: Request):
    """Unpause an agent: allow the scheduler to make it conscious again.

    If the agent was skipped during startup (paused agents are not loaded
    to save memory), this also loads its runtime on-demand.
    """
    agent_dir = request.app.state.settings.agents_dir / agent_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    # On-demand runtime loading: if the agent was skipped at startup
    # because it was paused, load it now.
    agent_manager = request.app.state.agent_manager
    if agent_manager.get_runtime(agent_id) is None:
        try:
            await agent_manager.load_agent(agent_id)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to load agent runtime: {exc}",
            )

    cs = getattr(request.app.state, "consciousness_scheduler", None)
    if cs is None:
        raise HTTPException(
            status_code=503, detail="Consciousness scheduler not available",
        )

    ok = await cs.unpause_agent(agent_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Agent not registered")
    return {"agent_id": agent_id, "paused": False}


@router.post("/{agent_id}/evict")
async def evict_agent(agent_id: str, request: Request):
    """Evict an agent's adapters from VRAM (preserves disk state)."""
    await request.app.state.agent_manager.evict_agent(agent_id)
    return {"evicted": agent_id}


# --- Helpers ---


def _list_genesis(genesis_dir) -> list[str]:
    """List available genesis template versions."""
    if not genesis_dir.exists():
        return []
    return [
        d.name for d in sorted(genesis_dir.iterdir())
        if d.is_dir() and (d / "manifest.json").exists()
    ]


# --- Front-brain dedicated endpoints ---


def _get_runtime(request: Request, agent_id: str):
    """Get runtime for an agent, or None if not loaded."""
    return request.app.state.agent_manager.get_runtime(agent_id)


@router.get("/{agent_id}/working-memory")
async def get_working_memory(agent_id: str, request: Request):
    """Return full Working Memory state (slots, goals, intentions)."""
    runtime = _get_runtime(request, agent_id)
    if runtime is None or getattr(runtime, "working_memory", None) is None:
        return {"slot_count": 0, "max_slots": 0, "slots": [], "goals": [], "intentions": []}
    status = runtime.get_status(sections={"working_memory"})
    return status.get("working_memory", {"slot_count": 0, "max_slots": 0, "slots": [], "goals": [], "intentions": []})


@router.patch("/{agent_id}/working-memory/instructions/{index}")
async def update_wm_instruction(agent_id: str, index: int, request: Request):
    """Update a working memory instruction by index."""
    runtime = _get_runtime(request, agent_id)
    if runtime is None or getattr(runtime, "working_memory", None) is None:
        raise HTTPException(status_code=404, detail="Agent or WM not found")
    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")
    if not runtime.working_memory.update_instruction(index, content):
        raise HTTPException(status_code=404, detail=f"Instruction {index} not found")
    return {"ok": True, "index": index}


@router.delete("/{agent_id}/working-memory/instructions/{index}")
async def delete_wm_instruction(agent_id: str, index: int, request: Request):
    """Delete a working memory instruction by index."""
    runtime = _get_runtime(request, agent_id)
    if runtime is None or getattr(runtime, "working_memory", None) is None:
        raise HTTPException(status_code=404, detail="Agent or WM not found")
    if not runtime.working_memory.delete_instruction(index):
        raise HTTPException(status_code=404, detail=f"Instruction {index} not found")
    return {"ok": True, "index": index}


@router.get("/{agent_id}/theory-of-mind")
async def get_theory_of_mind(agent_id: str, request: Request):
    """Return Theory of Mind user models."""
    runtime = _get_runtime(request, agent_id)
    if runtime is None or getattr(runtime, "theory_of_mind", None) is None:
        return {"active_user": None, "user_count": 0, "users": []}
    status = runtime.get_status(sections={"theory_of_mind"})
    result = dict(status.get("theory_of_mind", {}))
    # Include all user models for the dedicated page
    users = []
    for uid, model in getattr(runtime.theory_of_mind, "_users", {}).items():
        users.append({
            "user_id": uid,
            "turn_count": getattr(model, "turn_count", 0),
            "style": ", ".join(
                f"{k}={v:.1f}" for k, v in getattr(model, "style", {}).items()
            ),
            "patience": round(getattr(model, "patience", 0.5), 2),
            "top_interests": sorted(
                getattr(model, "interests", {}).items(),
                key=lambda x: x[1], reverse=True,
            )[:5],
            "expertise": {
                k: round(v, 2)
                for k, v in sorted(
                    getattr(model, "domain_expertise", {}).items(),
                    key=lambda x: x[1], reverse=True,
                )[:10]
            },
            "channel_styles": getattr(model, "channel_styles", {}),
        })
    result["users"] = users
    return result


def _serialize_episode(ep: Any, index: int = 0) -> dict[str, Any]:
    """Serialize an Episode object into a rich JSON dict."""
    arc_summary = ""
    if hasattr(ep, "arc_summary") and callable(ep.arc_summary):
        try:
            arc_summary = ep.arc_summary()
        except Exception:
            arc_summary = ""

    arc_snapshots = getattr(ep, "arc", [])
    started = getattr(ep, "started_at", None)
    ended = getattr(ep, "ended_at", None)
    duration_s = 0.0
    if started:
        import time as _time
        duration_s = (ended or _time.time()) - started

    return {
        "index": index,
        "title": getattr(ep, "title", ""),
        "is_active": getattr(ep, "is_active", ended is None),
        "turns": getattr(ep, "turn_count", 0),
        "arc_summary": arc_summary,
        "arc_snapshots": arc_snapshots[:100],
        "start_time": started,
        "end_time": ended,
        "duration_min": round(duration_s / 60, 1) if duration_s else 0,
        "peak_resonance": round(getattr(ep, "peak_resonance", 0.0), 3),
        "peak_cortisol": round(getattr(ep, "peak_cortisol", 0.0), 3),
        "peak_engagement": round(getattr(ep, "peak_engagement", 0.0), 3),
        "coherence_contribution": round(getattr(ep, "coherence_contribution", 0.0), 3),
        "domains": list(getattr(ep, "domains", []))[:10],
        "opening_mood": getattr(ep, "opening_mood", ""),
        "closing_mood": getattr(ep, "closing_mood", ""),
        "dominant_emotion": getattr(ep, "dominant_emotion", ""),
        "topics": list(getattr(ep, "topics", []))[:10],
        "summary": getattr(ep, "summary", ""),
    }


@router.get("/{agent_id}/narrative/episodes")
async def get_narrative_episodes(agent_id: str, request: Request):
    """Return narrative episode history with full emotional arc data."""
    runtime = _get_runtime(request, agent_id)
    if runtime is None or getattr(runtime, "narrative_self", None) is None:
        return {"episode_count": 0, "episodes": [], "current_episode": None,
                "narrative_coherence": 0.0, "coherence_label": "unknown",
                "regulation_count": 0, "active_strategy": None, "values": []}
    ns = runtime.narrative_self

    episodes = []
    for i, ep in enumerate(getattr(ns, "_episodes", [])):
        episodes.append(_serialize_episode(ep, index=i + 1))

    current = None
    cur_ep = getattr(ns, "_current_episode", None)
    if cur_ep is not None:
        current = _serialize_episode(cur_ep, index=len(episodes) + 1)

    cfg = getattr(ns, "cfg", None)

    raw_blocks = getattr(ns, "_narrative_blocks", [])
    serialized_blocks = []
    for b in raw_blocks[-50:]:
        try:
            serialized_blocks.append(b.to_dict() if hasattr(b, "to_dict") else {
                "timestamp": getattr(b, "timestamp", 0),
                "block_type": getattr(b, "block_type", ""),
                "content": getattr(b, "content", ""),
                "source_episode": getattr(b, "source_episode", ""),
                "domains": list(getattr(b, "domains", [])),
                "coherence_delta": getattr(b, "coherence_delta", 0.0),
            })
        except Exception:
            pass

    return {
        "narrative_coherence": round(ns.narrative_coherence, 3),
        "coherence_label": ns.coherence_label() if hasattr(ns, "coherence_label") else "unknown",
        "active_strategy": getattr(ns, "_active_strategy", None),
        "regulation_count": getattr(ns, "_regulation_count", 0),
        "episode_count": len(episodes) + (1 if current else 0),
        "current_episode": current,
        "episodes": episodes,
        "values": getattr(cfg, "values", []) if cfg else [],
        "soul_wish": getattr(ns, "soul_wish", ""),
        "narrative_blocks": serialized_blocks,
    }


@router.get("/{agent_id}/network-dynamics")
async def get_network_dynamics(agent_id: str, request: Request):
    """Return network dynamics activation levels and transitions."""
    runtime = _get_runtime(request, agent_id)
    if runtime is None or getattr(runtime, "network_dynamics", None) is None:
        return {"ecn": 0, "sn": 0, "dmn": 0, "dominant": "none", "transitions": []}
    status = runtime.get_status(sections={"network_dynamics"})
    return status.get("network_dynamics", {"ecn": 0, "sn": 0, "dmn": 0, "dominant": "none", "transitions": []})
