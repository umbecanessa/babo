"""Admin endpoints -- Deep agent inspection for the NLS Admin Panel.

Provides read access to agent disk state: chain state, DomainDB facts,
event logs, conversation history, brain configs, and memory tiers.

Also handles tool enable/disable with background onboarding.

All endpoints are protected by the shared secret (same as other runtime
endpoints).  The NestJS backend proxies these for the admin panel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# Per-agent lock for tool onboarding.  Only one tool can onboard at a
# time per agent, preventing the model from being overwhelmed by dozens
# of concurrent process_message() calls.
_onboarding_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


# ===================================================================
# Helpers
# ===================================================================

def _get_agent_dir(request: Request, agent_id: str) -> Path:
    """Resolve and validate agent directory."""
    agents_dir = request.app.state.settings.agents_dir
    agent_dir = agents_dir / agent_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return agent_dir


def _parse_jsonl_events(
    agent_dir: Path,
    event_type: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Parse JSONL event log files with optional filtering."""
    events_dir = agent_dir / "events"
    if not events_dir.exists():
        return []

    all_events: list[dict] = []
    event_files = sorted(events_dir.glob("events_*.jsonl"))

    for ef in event_files:
        try:
            with open(ef, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Filter by event type
                    if event_type and not record.get("event", "").startswith(event_type):
                        continue

                    # Filter by time range
                    ts = record.get("ts", "")
                    if from_ts and ts < from_ts:
                        continue
                    if to_ts and ts > to_ts:
                        continue

                    all_events.append(record)
        except Exception as exc:
            logger.warning("Error reading event file %s: %s", ef, exc)

    # Return most recent events first, capped at limit
    all_events.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return all_events[:limit]


# ===================================================================
# Update Safety Check
# ===================================================================

@router.get("/safe-to-update")
async def safe_to_update(request: Request):
    """Check whether it is safe to apply an app update and restart.

    Returns ``safe: true`` only when no agents are actively processing
    user messages, dreaming, or holding the inference lock.  Sleeping
    agents are fine -- they resume automatically after restart.
    """
    app = request.app

    agent_manager = app.state.agent_manager
    connection_manager = getattr(app.state, "connection_manager", None)
    model_manager = getattr(app.state, "model_manager", None)
    consciousness = getattr(app.state, "consciousness_scheduler", None)

    from server.services.agent_manager import AgentStatus

    chatting = len([
        s for s in agent_manager._status.values()
        if s == AgentStatus.CHATTING
    ])

    active_dreams = 0
    if consciousness is not None:
        for entry in consciousness._agents.values():
            if entry.inner_loop is not None:
                il_status = entry.inner_loop.get_status()
                if il_status.get("active_dreaming"):
                    active_dreams += 1

    active_ws = 0
    if connection_manager is not None:
        active_ws = connection_manager.stats.get("total_connections", 0)

    reasons = []
    if chatting:
        reasons.append(f"{chatting} agent(s) chatting")
    if active_dreams:
        reasons.append(f"{active_dreams} active dream(s)")
    if active_ws:
        reasons.append(f"{active_ws} WebSocket connection(s)")

    safe = len(reasons) == 0
    sleeping = len([
        s for s in agent_manager._status.values()
        if s == AgentStatus.SLEEPING
    ])

    return {
        "safe": safe,
        "reason": "; ".join(reasons) if reasons else None,
        "details": {
            "chatting_agents": chatting,
            "active_dreams": active_dreams,
            "active_websockets": active_ws,
            "sleeping_agents": sleeping,
        },
    }


# ===================================================================
# Chain State
# ===================================================================

def _enrich_chain_display(
    chain_data: dict,
    agent_id: str,
    request: Request,
) -> dict:
    """Fill display gaps: height from blocks, BYO base_model from live inference."""
    if not isinstance(chain_data, dict):
        return chain_data

    blocks: list[dict] = []
    for key in ("consolidated", "frozen_epochs", "active_deltas"):
        blocks.extend(chain_data.get(key) or [])
    active_epoch = chain_data.get("active_epoch")
    if active_epoch:
        blocks.append(active_epoch)
    # Genesis may only exist in consolidated — already included above

    if blocks:
        heights = [int(b.get("height", 0)) for b in blocks if b.get("height") is not None]
        if heights:
            max_height = max(heights)
            if not chain_data.get("current_height"):
                chain_data["current_height"] = max_height
            chain_data["block_count"] = len(blocks)

    base = (chain_data.get("base_model") or "").strip().lower()
    if base in ("", "bring-your-own", "byo"):
        agent_manager = getattr(request.app.state, "agent_manager", None)
        runtime = (
            agent_manager.get_runtime(agent_id)
            if agent_manager is not None
            else None
        )
        resolved = ""
        if runtime is not None:
            cfg = getattr(runtime, "config", {}) or {}
            resolved = (
                cfg.get("inference", {}).get("hf_model")
                or cfg.get("hf_model")
                or getattr(runtime, "adapter_name", "")
                or ""
            )
        if not resolved:
            meta_path = _get_agent_dir(request, agent_id) / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    resolved = meta.get("base_model") or meta.get("hf_model") or ""
                except Exception:
                    pass
        if resolved:
            chain_data["base_model"] = resolved
            chain_data["base_model_label"] = resolved.split("/")[-1]

    return chain_data


@router.get("/agents/{agent_id}/chain")
async def get_agent_chain(agent_id: str, request: Request):
    """Return the full Merkle chain state from ledger.yaml."""
    agent_dir = _get_agent_dir(request, agent_id)
    ledger_path = agent_dir / "ledger.yaml"

    if not ledger_path.exists():
        raise HTTPException(status_code=404, detail="No ledger.yaml found for agent")

    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            chain_data = yaml.safe_load(f)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading ledger: {exc}")

    chain_data = chain_data or {}
    try:
        from nls.ledger.chain_sleep import (
            reconcile_chain_from_session_meta,
            sync_manifest_from_db,
        )

        if not chain_data.get("consolidated") and not chain_data.get("active_epoch"):
            reconcile_chain_from_session_meta(agent_dir)
            with open(ledger_path, "r", encoding="utf-8") as f:
                chain_data = yaml.safe_load(f) or {}
        sync_manifest_from_db(agent_dir)
        with open(ledger_path, "r", encoding="utf-8") as f:
            chain_data = yaml.safe_load(f) or chain_data
    except Exception as exc:
        logger.debug("Chain reconcile for %s: %s", agent_id, exc)

    return _enrich_chain_display(chain_data, agent_id, request)


# ===================================================================
# DomainDB Facts
# ===================================================================

@router.get("/agents/{agent_id}/facts")
async def get_agent_facts(
    agent_id: str,
    request: Request,
    search: str = Query(default="", description="Search in domain_path or current_value"),
    domain: str = Query(default="", description="Filter by domain prefix"),
    fluid: str = Query(default="", description="Filter: 'true' for fluid, 'false' for stable"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Query the agent's DomainDB (knowledge.db) with filtering and pagination."""
    agent_dir = _get_agent_dir(request, agent_id)
    db_path = agent_dir / "knowledge.db"

    if not db_path.exists():
        return {"facts": [], "total": 0, "page": page, "limit": limit}

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Build WHERE clauses
        conditions = []
        params: list[Any] = []

        if search:
            conditions.append("(domain_path LIKE ? OR current_value LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        if domain:
            conditions.append("domain_path LIKE ?")
            params.append(f"{domain}%")

        if fluid == "true":
            conditions.append("is_fluid = 1")
        elif fluid == "false":
            conditions.append("is_fluid = 0")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Count total
        count_sql = f"SELECT COUNT(*) FROM facts {where_clause}"
        cursor.execute(count_sql, params)
        total = cursor.fetchone()[0]

        # Fetch page
        offset = (page - 1) * limit
        select_sql = f"""
            SELECT id, domain_path, current_value, canonical_question,
                   block_height, flip_count, is_fluid, meta_layer,
                   hormonal_fingerprint, last_modified, created_at
            FROM facts {where_clause}
            ORDER BY last_modified DESC
            LIMIT ? OFFSET ?
        """
        cursor.execute(select_sql, params + [limit, offset])
        rows = cursor.fetchall()

        facts = []
        for row in rows:
            fact = dict(row)
            fact["is_fluid"] = bool(fact.get("is_fluid", 0))
            facts.append(fact)

        conn.close()
        return {"facts": facts, "total": total, "page": page, "limit": limit}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading DomainDB: {exc}")


@router.patch("/agents/{agent_id}/facts/{fact_id}/fluid")
async def toggle_fact_fluid(agent_id: str, fact_id: int, request: Request):
    """Toggle a fact's is_fluid flag."""
    agent_dir = _get_agent_dir(request, agent_id)
    db_path = agent_dir / "knowledge.db"

    if not db_path.exists():
        raise HTTPException(status_code=404, detail="No knowledge.db found")

    try:
        body = await request.json()
        is_fluid = bool(body.get("is_fluid", False))

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "UPDATE facts SET is_fluid = ? WHERE id = ?",
            (int(is_fluid), fact_id),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
        conn.close()

        if row is None:
            raise HTTPException(status_code=404, detail="Fact not found")

        fact = dict(row)
        fact["is_fluid"] = bool(fact.get("is_fluid", 0))
        return fact
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error updating fact: {exc}")


# ===================================================================
# Event Logs
# ===================================================================

@router.get("/agents/{agent_id}/events")
async def get_agent_events(
    agent_id: str,
    request: Request,
    event_type: str = Query(default="", description="Filter by event type prefix (e.g. 'turn_', 'hormone_')"),
    from_ts: str = Query(default="", alias="from", description="ISO timestamp lower bound"),
    to_ts: str = Query(default="", alias="to", description="ISO timestamp upper bound"),
    limit: int = Query(default=200, ge=1, le=2000),
):
    """Parse and return JSONL event log entries with filtering."""
    agent_dir = _get_agent_dir(request, agent_id)

    events = _parse_jsonl_events(
        agent_dir,
        event_type=event_type or None,
        from_ts=from_ts or None,
        to_ts=to_ts or None,
        limit=limit,
    )

    return {"events": events, "count": len(events)}


# ===================================================================
# Conversation History
# ===================================================================

@router.get("/agents/{agent_id}/conversation")
async def get_agent_conversation(agent_id: str, request: Request):
    """Return the agent's conversation history."""
    agent_dir = _get_agent_dir(request, agent_id)
    history_path = agent_dir / "conversation_history.json"

    if not history_path.exists():
        return {"messages": []}

    try:
        with open(history_path, "r", encoding="utf-8") as f:
            messages = json.load(f)
        return {"messages": messages}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading conversation: {exc}")


# ===================================================================
# Per-Agent Brain Config
# ===================================================================

@router.get("/agents/{agent_id}/config")
async def get_agent_config(agent_id: str, request: Request):
    """Return the agent's brain configuration files."""
    agent_dir = _get_agent_dir(request, agent_id)
    config_dir = agent_dir / "config"

    result: dict[str, Any] = {}

    config_files = [
        "runtime.json", "hormones.json", "autonomic.json",
        "drives.json", "dmn.json", "signals.json",
    ]

    for filename in config_files:
        config_path = config_dir / filename
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    result[filename.replace(".json", "")] = json.load(f)
            except Exception as exc:
                result[filename.replace(".json", "")] = {"error": str(exc)}
        else:
            # Fallback to global config
            global_path = Path(__file__).resolve().parent.parent.parent / "nls" / "config" / filename
            if global_path.exists():
                try:
                    with open(global_path, "r", encoding="utf-8") as f:
                        result[filename.replace(".json", "")] = json.load(f)
                except Exception:
                    pass

    return result


@router.patch("/agents/{agent_id}/config/circadian")
async def update_circadian_config(agent_id: str, request: Request):
    """Update an agent's circadian sleep schedule and hot-reload it.

    Accepts partial updates -- only provided fields are changed.
    """
    agent_dir = _get_agent_dir(request, agent_id)
    config_path = agent_dir / "config" / "autonomic.json"

    body = await request.json()

    ALLOWED_FIELDS = {
        "timezone", "bedtime", "wake_time", "nap_windows",
        "wake_on_user_message", "enabled", "max_nightly_cycles",
        "signal_pressure_cap_multiplier",
    }
    invalid = set(body.keys()) - ALLOWED_FIELDS
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown fields: {', '.join(sorted(invalid))}",
        )

    # Validate time formats (HH:MM)
    import re
    _time_re = re.compile(r"^\d{2}:\d{2}$")
    for field_name in ("bedtime", "wake_time"):
        if field_name in body:
            val = body[field_name]
            if not isinstance(val, str) or not _time_re.match(val):
                raise HTTPException(
                    status_code=400,
                    detail=f"{field_name} must be HH:MM format",
                )
            h, m = int(val[:2]), int(val[3:])
            if h > 23 or m > 59:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field_name} has invalid hour/minute",
                )

    # Validate timezone
    if "timezone" in body:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(body["timezone"])
        except (ZoneInfoNotFoundError, KeyError):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid timezone: {body['timezone']}",
            )

    # Validate nap_windows
    if "nap_windows" in body:
        if not isinstance(body["nap_windows"], list):
            raise HTTPException(
                status_code=400,
                detail="nap_windows must be a list",
            )
        for i, w in enumerate(body["nap_windows"]):
            if not isinstance(w, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"nap_windows[{i}] must be an object",
                )
            for k in ("start", "end"):
                if k not in w or not _time_re.match(str(w[k])):
                    raise HTTPException(
                        status_code=400,
                        detail=f"nap_windows[{i}].{k} must be HH:MM",
                    )

    # Load current config
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            autonomic = json.load(f)
    else:
        global_path = (
            Path(__file__).resolve().parent.parent.parent
            / "nls" / "config" / "autonomic.json"
        )
        with open(global_path, "r", encoding="utf-8") as f:
            autonomic = json.load(f)

    # Update circadian section
    circ = autonomic.setdefault("circadian", {})
    for key, value in body.items():
        circ[key] = value

    # Write back
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(autonomic, f, indent=2, ensure_ascii=False)

    # Hot-reload on the live ANS instance
    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is not None:
        ans = getattr(runtime, "ans", None)
        if ans is not None:
            from nls.brain.circadian import (
                CircadianClock,
                load_circadian_config,
            )
            ans.circadian = CircadianClock(
                load_circadian_config({"circadian": circ}),
            )
            logger.info(
                "Agent %s: circadian config hot-reloaded "
                "(bedtime=%s, wake=%s, tz=%s)",
                agent_id,
                circ.get("bedtime"), circ.get("wake_time"),
                circ.get("timezone"),
            )

    return {"circadian": circ}


# ===================================================================
# Memory Tiers
# ===================================================================

@router.get("/agents/{agent_id}/memory-tiers")
async def get_agent_memory_tiers(agent_id: str, request: Request):
    """Return the agent's memory tier structure from the manifest."""
    agent_dir = _get_agent_dir(request, agent_id)
    ledger_path = agent_dir / "ledger.yaml"

    if not ledger_path.exists():
        raise HTTPException(status_code=404, detail="No ledger.yaml found")

    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            chain_data = yaml.safe_load(f)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading ledger: {exc}")

    # Extract tier structure
    tiers = {
        "active_deltas": chain_data.get("active_deltas", []),
        "active_epoch": chain_data.get("active_epoch"),
        "frozen_epochs": chain_data.get("frozen_epochs", []),
        "consolidated": chain_data.get("consolidated", []),
        "current_height": chain_data.get("current_height", 0),
        "genesis_hash": chain_data.get("genesis_hash", ""),
        "soul_hash": chain_data.get("soul_hash", ""),
    }

    # Check adapter directories on disk
    adapters_dir = agent_dir / "adapters"
    if adapters_dir.exists():
        adapter_dirs = [d.name for d in adapters_dir.iterdir() if d.is_dir()]
        tiers["adapter_directories"] = sorted(adapter_dirs)
    else:
        tiers["adapter_directories"] = []

    return tiers


# ===================================================================
# Working Memory / Cryptex State
# ===================================================================

@router.get("/agents/{agent_id}/wm")
async def get_agent_working_memory(agent_id: str, request: Request):
    """Return the agent's Cryptex (working memory) ring state."""
    agent_manager = request.app.state.agent_manager
    runtime = agent_manager._runtimes.get(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent not loaded")

    wm = getattr(runtime, "working_memory", None)
    if wm is None:
        return {"cryptex": None, "message": "No working memory on this runtime"}

    result: dict = {}
    if hasattr(wm, "get_ring_summary"):
        result["rings"] = wm.get_ring_summary()
    elif hasattr(wm, "rings"):
        rings_data = {}
        for name, ring in wm.rings.items():
            positions = {}
            if hasattr(ring, "positions"):
                for pos_name, pos in ring.positions.items():
                    positions[pos_name] = {
                        "filled": bool(getattr(pos, "content", None)),
                        "content_preview": (
                            str(pos.content)[:120]
                            if getattr(pos, "content", None) else None
                        ),
                    }
            rings_data[name] = {
                "filled_positions": sum(
                    1 for p in positions.values() if p.get("filled")
                ),
                "total_positions": len(positions),
                "positions": positions,
            }
        result["rings"] = rings_data
    else:
        result["raw_type"] = type(wm).__name__

    return {"cryptex": result}


# ===================================================================
# Hormone History (from event logs)
# ===================================================================

@router.get("/agents/{agent_id}/hormones/history")
async def get_hormone_history(agent_id: str, request: Request):
    """Extract hormone time-series from event logs."""
    agent_dir = _get_agent_dir(request, agent_id)

    events = _parse_jsonl_events(agent_dir, event_type="turn_hormones", limit=2000)
    events.reverse()  # chronological order

    series: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        data = event.get("data", {})
        ts = event.get("ts", "")
        turn = data.get("turn", 0)

        for key, value in data.items():
            if key == "turn":
                continue
            if isinstance(value, (int, float)):
                if key not in series:
                    series[key] = []
                series[key].append({"ts": ts, "turn": turn, "value": round(value, 4)})

    return {"hormones": series, "data_points": sum(len(v) for v in series.values())}


# ===================================================================
# Network Dynamics History (from event logs)
# ===================================================================

@router.get("/agents/{agent_id}/network/history")
async def get_network_history(agent_id: str, request: Request):
    """Extract network dynamics time-series from event logs."""
    agent_dir = _get_agent_dir(request, agent_id)

    events = _parse_jsonl_events(agent_dir, event_type="turn_network", limit=2000)
    events.reverse()  # chronological order

    series: dict[str, list[dict[str, Any]]] = {"ecn": [], "sn": [], "dmn": []}
    for event in events:
        data = event.get("data", {})
        turn = data.get("turn", 0)
        ts = event.get("ts", "")
        for key in ("ecn", "sn", "dmn"):
            val = data.get(key)
            if isinstance(val, (int, float)):
                series[key].append({"ts": ts, "turn": turn, "value": round(val, 4)})

    return {"network": series, "data_points": sum(len(v) for v in series.values())}


# ===================================================================
# Signal History (from event logs)
# ===================================================================

@router.get("/agents/{agent_id}/signals/history")
async def get_signal_history(agent_id: str, request: Request):
    """Extract signal collection history from event logs."""
    agent_dir = _get_agent_dir(request, agent_id)

    events = _parse_jsonl_events(agent_dir, event_type="signal_collected", limit=2000)
    events.reverse()  # chronological order

    signals = []
    for event in events:
        data = event.get("data", {})
        signals.append({
            "ts": event.get("ts", ""),
            "signal_type": data.get("signal_type", ""),
            "domain_path": data.get("domain_path", ""),
            "content": data.get("content", ""),
            "turn": data.get("turn", 0),
            "meta_layer": data.get("meta_layer", ""),
        })

    # Type distribution
    type_counts: dict[str, int] = {}
    for s in signals:
        t = s["signal_type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    return {"signals": signals, "type_counts": type_counts, "total": len(signals)}


# ===================================================================
# Force Sleep
# ===================================================================

@router.post("/agents/{agent_id}/sleep")
async def force_agent_sleep(agent_id: str, request: Request):
    """Force a sleep cycle for an agent."""
    agent_manager = request.app.state.agent_manager
    sleep_scheduler = request.app.state.sleep_scheduler

    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent not loaded or not found")

    from nls.models import SleepRequest

    hormones: dict[str, float] = {}
    if runtime.hypothalamus is not None:
        hormones = {
            name: round(h.level, 3)
            for name, h in runtime.hypothalamus.hormones.items()
        }

    signal_count = 0
    if runtime.ans is not None:
        summary = runtime.ans.get_buffer_summary()
        signal_count = summary.get("learnable_signals", 0)

    sleep_request = SleepRequest(
        agent_id=agent_id,
        reason="admin_requested",
        signal_count=signal_count,
        hormones=hormones,
    )
    await sleep_scheduler.enqueue(sleep_request)

    return {"status": "sleep_queued", "agent_id": agent_id}


# ===================================================================
# Tool Catalog & Per-Agent Tool Management
# ===================================================================

@router.get("/tools/catalog")
async def get_tool_catalog(request: Request):
    """Return the full tool catalog from JSON definitions in nls/config/tools/."""
    tools_dir = Path(__file__).resolve().parent.parent.parent / "nls" / "config" / "tools"

    if not tools_dir.exists():
        return []

    catalog: list[dict[str, Any]] = []
    for tool_file in sorted(tools_dir.glob("*.json")):
        try:
            with open(tool_file, "r", encoding="utf-8") as f:
                tool_def = json.load(f)
            catalog.append({
                "name": tool_def.get("name", tool_file.stem),
                "description": tool_def.get("description", ""),
                "version": tool_def.get("version", "1.0.0"),
                "category": tool_def.get("category", ""),
                "hormone_affinity": tool_def.get("hormone_affinity", ""),
                "risk_level": tool_def.get("risk_level", ""),
                "platform": tool_def.get("platform", "all"),
                "learning_yield": tool_def.get("learning_yield", ""),
                "permissions": tool_def.get("permissions", []),
                "input_schema": tool_def.get("input_schema", {}),
                "manual": tool_def.get("manual", {}),
            })
        except Exception as exc:
            logger.warning("Error reading tool file %s: %s", tool_file, exc)

    return catalog


@router.get("/tools/catalog/v2")
async def get_tool_catalog_v2():
    """Return the v2 tool catalog (4 composable primitives)."""
    from nls.tools.agent_tools import create_coding_tools, tools_to_openai_schema

    tools, _ = create_coding_tools(".")
    return [
        {
            "name": t.name,
            "description": t.description,
            "category": "core",
            "risk_level": "execute" if t.name == "bash" else "write" if t.name in ("write", "edit") else "read",
            "platform": "all",
            "hormone_affinity": "dopamine",
            "enabled": True,
            "always_on": True,
            "parameters": t.parameters,
        }
        for t in tools
    ]


def _agent_uses_v2(agent_dir: Path) -> bool:
    """Check if an agent is configured for v2 tools."""
    config_file = agent_dir / "config" / "runtime.json"
    if not config_file.exists():
        config_file = Path(__file__).resolve().parent.parent.parent / "nls" / "config" / "runtime.json"
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
            return config.get("agency", {}).get("agentic_loop", {}).get("use_v2", False)
        except Exception:
            pass
    return False


@router.get("/agents/{agent_id}/tools")
async def get_agent_tools(agent_id: str, request: Request):
    """Return the list of enabled tools for an agent."""
    agent_dir = _get_agent_dir(request, agent_id)

    if _agent_uses_v2(agent_dir):
        mgr = getattr(request.app.state, "agent_manager", None)
        if mgr:
            runtime = mgr.get_runtime(agent_id)
            if runtime and hasattr(runtime, "_agent_tools") and runtime._agent_tools:
                tools = [
                    {"name": t.name, "description": getattr(t, "description", "")}
                    for t in runtime._agent_tools
                ]
                return {"enabled": tools, "version": 2}
        return {
            "enabled": [
                {"name": "read", "description": "Read file contents"},
                {"name": "write", "description": "Write/create files"},
                {"name": "edit", "description": "Surgical find-and-replace"},
                {"name": "bash", "description": "Execute shell commands"},
            ],
            "version": 2,
        }

    tools_file = agent_dir / "enabled_tools.json"

    if tools_file.exists():
        try:
            with open(tools_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {"enabled": data.get("enabled", [])}
        except Exception:
            pass

    # Default: only core knowledge tools enabled out of the box.
    # All others must be explicitly installed via the Tool Shop.
    return {"enabled": ["web_search", "wikipedia"]}


def _run_tool_onboarding(
    agent_id: str,
    agent_dir: Path,
    tool_name: str,
    agent_manager: Any,
    sleep_scheduler: Any = None,
) -> None:
    """Mark a tool enabled — product build has no weight-training onboarding pipeline."""
    _ = agent_manager, sleep_scheduler
    status_file = agent_dir / f"tool_onboarding_status_{tool_name}.json"
    payload = {
        "tool": tool_name,
        "agent_id": agent_id,
        "status": "completed",
        "onboarded": True,
        "mode": "product",
        "message": "Tool enabled; manuals are loaded from JSON at runtime.",
        "updated_at": datetime.utcnow().isoformat(),
    }
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    report_file = agent_dir / f"tool_onboarding_report_{tool_name}.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump({**payload, "tool_name": tool_name}, f, indent=2)


async def _get_sleep_event(sleep_scheduler: Any, agent_id: str) -> asyncio.Event:
    """Get the sleep event on the event loop thread (must run on loop)."""
    return sleep_scheduler.get_sleep_event(agent_id)


async def _wait_on_event(event: asyncio.Event) -> None:
    """Await an already-acquired sleep event."""
    await asyncio.wait_for(event.wait(), timeout=300.0)


@router.post("/agents/{agent_id}/tools/{tool_name}/enable")
async def enable_agent_tool(
    agent_id: str,
    tool_name: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Enable a tool for an agent and trigger background onboarding."""
    agent_dir = _get_agent_dir(request, agent_id)
    tools_file = agent_dir / "enabled_tools.json"

    enabled = set()
    if tools_file.exists():
        try:
            with open(tools_file, "r", encoding="utf-8") as f:
                enabled = set(json.load(f).get("enabled", []))
        except Exception:
            pass
    else:
        # Default: only core tools
        enabled = {"web_search", "wikipedia"}

    enabled.add(tool_name)

    with open(tools_file, "w", encoding="utf-8") as f:
        json.dump({"enabled": sorted(enabled)}, f, indent=2)

    # Hot-reload: register the tool in the live runtime immediately
    agent_manager = getattr(request.app.state, "agent_manager", None)
    if agent_manager:
        runtime = agent_manager.get_runtime(agent_id)
        if runtime is not None:
            try:
                runtime.reload_tools(tool_name)
            except Exception as exc:
                logger.warning(
                    "Agent %s: hot-reload after enable failed: %s",
                    agent_id, exc,
                )

    # Trigger background onboarding
    sleep_scheduler = getattr(request.app.state, "sleep_scheduler", None)
    background_tasks.add_task(
        _run_tool_onboarding,
        agent_id,
        agent_dir,
        tool_name,
        agent_manager,
        sleep_scheduler,
    )

    return {
        "status": "enabled",
        "tool": tool_name,
        "enabled": sorted(enabled),
        "onboarding": "queued",
    }


@router.post("/agents/{agent_id}/tools/{tool_name}/disable")
async def disable_agent_tool(agent_id: str, tool_name: str, request: Request):
    """Disable a tool for an agent."""
    agent_dir = _get_agent_dir(request, agent_id)
    tools_file = agent_dir / "enabled_tools.json"

    enabled = set()
    if tools_file.exists():
        try:
            with open(tools_file, "r", encoding="utf-8") as f:
                enabled = set(json.load(f).get("enabled", []))
        except Exception:
            pass
    else:
        # First modification: start from all-enabled
        tools_dir = Path(__file__).resolve().parent.parent.parent / "nls" / "config" / "tools"
        if tools_dir.exists():
            enabled = {f.stem for f in tools_dir.glob("*.json")}

    enabled.discard(tool_name)

    with open(tools_file, "w", encoding="utf-8") as f:
        json.dump({"enabled": sorted(enabled)}, f, indent=2)

    # Hot-reload: unregister the tool from the live runtime
    agent_manager = getattr(request.app.state, "agent_manager", None)
    if agent_manager:
        runtime = agent_manager.get_runtime(agent_id)
        if runtime is not None:
            try:
                runtime.reload_tools(tool_name)
            except Exception as exc:
                logger.warning(
                    "Agent %s: hot-reload after disable failed: %s",
                    agent_id, exc,
                )

    return {"status": "disabled", "tool": tool_name, "enabled": sorted(enabled)}


@router.get("/agents/{agent_id}/tools/{tool_name}/status")
async def get_tool_onboarding_status(agent_id: str, tool_name: str, request: Request):
    """Return the onboarding status for a specific tool.

    The frontend polls this endpoint after enabling a tool to track
    onboarding progress (queued -> in_progress -> completed/failed).
    """
    agent_dir = _get_agent_dir(request, agent_id)
    status_file = agent_dir / f"tool_onboarding_status_{tool_name}.json"

    if not status_file.exists():
        # Check if there's a completed report
        report_file = agent_dir / f"tool_onboarding_report_{tool_name}.json"
        if report_file.exists():
            try:
                with open(report_file, "r", encoding="utf-8") as f:
                    report = json.load(f)
                return {
                    "tool": tool_name,
                    "status": "completed" if report.get("onboarded") else "failed",
                    "report": report,
                }
            except Exception:
                pass
        return {"tool": tool_name, "status": "not_started"}

    try:
        with open(status_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading status: {exc}")


# ===================================================================
# Tool Bundles & Batch Enable
# ===================================================================

_BUNDLES_PATH = Path(__file__).resolve().parent.parent.parent / "nls" / "config" / "tool_bundles.json"

# In-memory store for batch onboarding jobs: batch_id -> status dict.
_batch_jobs: dict[str, dict[str, Any]] = {}


@router.get("/tools/bundles")
async def get_tool_bundles():
    """Return predefined tool bundles for the Tool Shop."""
    if not _BUNDLES_PATH.exists():
        return []
    try:
        with open(_BUNDLES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Failed to read tool_bundles.json: %s", exc)
        return []


@router.post("/agents/{agent_id}/tools/batch-enable")
async def batch_enable_tools(
    agent_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Enable multiple tools at once, with batch onboarding pipeline.

    Body:  { "tools": ["tool1", "tool2"] }
    or     { "bundle": "developer" }

    Returns a batch_id for status polling.
    """
    agent_dir = _get_agent_dir(request, agent_id)
    body = await request.json()

    # Resolve tool list from bundle or explicit list
    tool_names: list[str] = body.get("tools", [])
    bundle_id = body.get("bundle")

    if bundle_id and not tool_names:
        if _BUNDLES_PATH.exists():
            try:
                with open(_BUNDLES_PATH, "r", encoding="utf-8") as f:
                    bundles = json.load(f)
                for b in bundles:
                    if b.get("id") == bundle_id:
                        tool_names = b.get("tools", [])
                        break
            except Exception:
                pass

    if not tool_names:
        raise HTTPException(status_code=400, detail="No tools specified and bundle not found")

    # Filter out tools that are already enabled
    tools_file = agent_dir / "enabled_tools.json"
    already_enabled: set[str] = set()
    if tools_file.exists():
        try:
            with open(tools_file, "r", encoding="utf-8") as f:
                already_enabled = set(json.load(f).get("enabled", []))
        except Exception:
            pass

    # Enable all requested tools in enabled_tools.json immediately
    new_enabled = already_enabled | set(tool_names)
    with open(tools_file, "w", encoding="utf-8") as f:
        json.dump({"enabled": sorted(new_enabled)}, f, indent=2)

    # Hot-reload all new tools
    agent_manager = getattr(request.app.state, "agent_manager", None)
    if agent_manager:
        runtime = agent_manager.get_runtime(agent_id)
        if runtime is not None:
            for tn in tool_names:
                try:
                    runtime.reload_tools(tn)
                except Exception as exc:
                    logger.warning("Agent %s: hot-reload %s failed: %s", agent_id, tn, exc)

    # Tools that actually need onboarding (not already completed)
    tools_to_onboard = []
    for tn in tool_names:
        report_file = agent_dir / f"tool_onboarding_report_{tn}.json"
        if report_file.exists():
            try:
                with open(report_file, "r", encoding="utf-8") as f:
                    rpt = json.load(f)
                if rpt.get("onboarded", False):
                    continue
            except Exception:
                pass
        tools_to_onboard.append(tn)

    # Create batch job
    import uuid
    batch_id = str(uuid.uuid4())[:8]

    tool_statuses = {}
    for tn in tools_to_onboard:
        tool_statuses[tn] = {"status": "queued", "phase": "waiting"}

    _batch_jobs[batch_id] = {
        "batch_id": batch_id,
        "agent_id": agent_id,
        "bundle": bundle_id,
        "tools": tools_to_onboard,
        "tool_status": tool_statuses,
        "status": "in_progress",
        "started_at": datetime.utcnow().isoformat(),
        "completed_at": None,
        "total": len(tools_to_onboard),
        "completed_count": 0,
    }

    # Queue batch onboarding in background
    sleep_scheduler = getattr(request.app.state, "sleep_scheduler", None)
    background_tasks.add_task(
        _run_batch_tool_onboarding,
        batch_id,
        agent_id,
        agent_dir,
        tools_to_onboard,
        agent_manager,
        sleep_scheduler,
    )

    return {
        "status": "batch_queued",
        "batch_id": batch_id,
        "tools": tools_to_onboard,
        "already_onboarded": [tn for tn in tool_names if tn not in tools_to_onboard],
        "enabled": sorted(new_enabled),
    }


@router.get("/agents/{agent_id}/tools/batch/{batch_id}/status")
async def get_batch_onboarding_status(agent_id: str, batch_id: str):
    """Poll batch onboarding progress."""
    job = _batch_jobs.get(batch_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Batch job '{batch_id}' not found")
    if job["agent_id"] != agent_id:
        raise HTTPException(status_code=403, detail="Batch belongs to a different agent")
    return job


def _run_batch_tool_onboarding(
    batch_id: str,
    agent_id: str,
    agent_dir: Path,
    tool_names: list[str],
    agent_manager: Any,
    sleep_scheduler: Any = None,
) -> None:
    """Mark batch tool enable complete (no weight-training onboarding in product)."""
    _ = agent_manager, sleep_scheduler
    job = _batch_jobs.get(batch_id)
    if job is None:
        return
    for tn in tool_names:
        job["tool_status"][tn] = {"status": "completed", "onboarded": True}
        status_file = agent_dir / f"tool_onboarding_status_{tn}.json"
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump({
                "tool": tn,
                "agent_id": agent_id,
                "status": "completed",
                "onboarded": True,
                "mode": "product",
                "updated_at": datetime.utcnow().isoformat(),
            }, f, indent=2)
    job["status"] = "completed"
    job["completed_count"] = len(tool_names)
    job["completed_at"] = datetime.utcnow().isoformat()


# ===================================================================
# ANS Working Memory (live signal buffer)
# ===================================================================

@router.post("/agents/{agent_id}/safety-net")
async def toggle_safety_net(
    agent_id: str,
    request: Request,
    enabled: bool = Query(True),
):
    """Enable/disable the ANS safety net on a live agent (testing use)."""
    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent not loaded")
    runtime._safety_net_disabled = not enabled
    return {"safety_net_enabled": enabled, "agent_id": agent_id}


@router.post("/agents/{agent_id}/daydream")
async def toggle_daydream(
    agent_id: str,
    request: Request,
    enabled: bool = Query(True),
):
    """Enable/disable background activity (inner loop, DMN, VC) for testing.

    When disabled the agent stays loaded and chat works normally, but
    the inner loop is paused and the visual cortex heartbeat is frozen.
    This prevents DMN dreams and VC screenshots from competing with
    test inference calls on the GPU.
    """
    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent not loaded")

    cs = getattr(request.app.state, "consciousness_scheduler", None)
    il = cs.get_inner_loop(agent_id) if cs is not None else None

    vc = getattr(runtime, "visual_cortex", None)

    if not enabled:
        if il is not None:
            il.interrupt()
        if vc is not None:
            vc.set_enabled(False)
        logger.info("Agent %s: daydream PAUSED (inner loop + VC frozen)", agent_id)
    else:
        if il is not None:
            il.resume()
        if vc is not None:
            vc.set_enabled(True)
        logger.info("Agent %s: daydream RESUMED", agent_id)

    return {"agent_id": agent_id, "daydream_enabled": enabled}


@router.get("/agents/{agent_id}/ans/context")
async def get_ans_context(agent_id: str, request: Request):
    """Return the live context-relevant signals from the ANS buffer."""
    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent not loaded or not found")
    if runtime.ans is None:
        return {"items": [], "total": 0}

    items = runtime.ans.get_context_items()
    return {"items": items, "total": len(items)}


@router.delete("/agents/{agent_id}/ans/context/{index}")
async def delete_ans_context_item(agent_id: str, index: int, request: Request):
    """Remove a signal from the ANS buffer by index."""
    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent not loaded or not found")
    if runtime.ans is None:
        raise HTTPException(status_code=404, detail="ANS not initialized")

    if not runtime.ans.remove_signal(index):
        raise HTTPException(status_code=404, detail=f"Signal index {index} out of range")

    try:
        runtime.ans.save_state(runtime.agent_dir / "ans_state.json")
    except Exception:
        pass

    return {"status": "deleted", "index": index}


@router.patch("/agents/{agent_id}/ans/context/{index}")
async def update_ans_context_item(agent_id: str, index: int, request: Request):
    """Update a signal's content in the ANS buffer."""
    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent not loaded or not found")
    if runtime.ans is None:
        raise HTTPException(status_code=404, detail="ANS not initialized")

    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Content must not be empty")

    if not runtime.ans.update_signal(index, content):
        raise HTTPException(status_code=404, detail=f"Signal index {index} out of range")

    # Immediately store the corrected fact in DomainDB (hippocampal one-shot)
    domain_db = getattr(runtime, "domain_db", None)
    if domain_db is not None:
        sig = runtime.ans._signal_buffer[index] if index < len(runtime.ans._signal_buffer) else None
        if sig and sig.domain_path:
            try:
                existing = domain_db.get_fact(sig.domain_path)
                if existing:
                    domain_db.update_fact(
                        sig.domain_path, content,
                        block_height=existing.block_height,
                        skip_flip=True,
                    )
                else:
                    from nls.models import Fact
                    domain_db.insert_fact(Fact(
                        domain_path=sig.domain_path,
                        current_value=content,
                    ))
            except Exception:
                pass

    try:
        runtime.ans.save_state(runtime.agent_dir / "ans_state.json")
    except Exception:
        pass

    return {"status": "updated", "index": index, "content": content}


@router.post("/agents/{agent_id}/feedback")
async def submit_message_feedback(agent_id: str, request: Request):
    """Submit user feedback on a specific agent message (e.g. channel reply).

    Maps feedback to the existing ANS signal taxonomy so it flows
    naturally through the sleep pipeline:

    - **Correction / negative** -> ``EVALUATE:incorrect`` (error_correction
      bucket — highest triage priority, trained first during sleep)
    - **Positive** -> ``user_positive`` (behavior_reinforcement bucket —
      reinforces good behavior during sleep)
    - **Behavioral rule** -> ``LEARN`` with ``Feedback.*`` domain path
      (new_knowledge bucket — stored in DomainDB immediately for frontal
      lobe lookup, then consolidated during sleep)

    All feedback also gets stored as a LEARN signal with a ``Feedback.*``
    domain path so the frontal lobe can retrieve it via keyword matching
    before the next sleep cycle.
    """
    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent not loaded or not found")

    body = await request.json()
    comment = (body.get("comment") or "").strip()
    message_content = (body.get("messageContent") or "").strip()
    feedback_type = body.get("feedbackType", "SPECIFIC")
    channel = body.get("channel", "")
    session_key = body.get("sessionKey", "")

    if not comment:
        raise HTTPException(status_code=400, detail="Feedback comment must not be empty")

    hypothalamus = getattr(runtime, "hypothalamus", None)
    lower = comment.lower()
    injected_signals = []

    # ── Classify feedback intent ────────────────────────────────────
    _negative_hints = ("don't", "dont", "never", "stop", "wrong", "bad",
                       "incorrect", "no ", "not ", "fix", "avoid", "shouldn't")
    _positive_hints = ("good", "great", "keep", "nice", "perfect", "love",
                       "well done", "correct", "exactly", "yes")

    is_negative = any(h in lower for h in _negative_hints)
    is_positive = not is_negative and any(h in lower for h in _positive_hints)

    # ── 1. Inject evaluation signal (drives hormonal response) ──────
    if runtime.ans is not None:
        if is_negative:
            runtime.ans.inject_signal(
                signal_type="EVALUATE:incorrect",
                domain_path=f"Feedback.Correction",
                content=f"User corrected agent reply: {comment}",
                hypothalamus=hypothalamus,
                source="user",
                response=message_content,
            )
            injected_signals.append("EVALUATE:incorrect")
        elif is_positive:
            runtime.ans.inject_signal(
                signal_type="user_positive",
                domain_path=f"Feedback.Positive",
                content=f"User praised agent reply: {comment}",
                hypothalamus=hypothalamus,
                source="user",
                response=message_content,
            )
            injected_signals.append("user_positive")

    # ── 2. Inject LEARN signal (DomainDB storage + sleep training) ──
    # Build a clean behavioral rule as the fact value
    scope_label = "all channels" if feedback_type == "GLOBAL" else (
        f"{channel} channel" if channel else "this conversation"
    )
    fact_value = f"{comment} (applies to {scope_label})"

    # Build domain path: Feedback.{Channel}.{SubCategory}
    domain_parts = ["Feedback"]
    if channel:
        domain_parts.append(channel.capitalize())
    if is_negative:
        domain_parts.append("Correction")
    elif is_positive:
        domain_parts.append("Reinforcement")
    else:
        domain_parts.append("Guidance")
    domain_path = ".".join(domain_parts)

    if runtime.ans is not None:
        runtime.ans.inject_signal(
            signal_type="LEARN",
            domain_path=domain_path,
            content=fact_value,
            hypothalamus=hypothalamus,
            source="user",
        )
        injected_signals.append("LEARN")

        # Trigger immediate DomainDB storage (hippocampal one-shot)
        # so the frontal lobe can find it before the next sleep
        domain_db = getattr(runtime, "domain_db", None)
        if domain_db is not None:
            try:
                from nls.brain.autonomic import NerveSignal
                learn_sig = NerveSignal(
                    signal_type="LEARN",
                    domain_path=domain_path,
                    content=fact_value,
                    pipe_fact=fact_value,
                    source="user",
                    meta_layer="pfc_judgment",
                )
                runtime._store_learn_signals([learn_sig], "")
                injected_signals.append("DomainDB")
            except Exception as exc:
                logger.warning(
                    "Agent %s: feedback DomainDB store failed: %s",
                    agent_id, exc,
                )

    return {
        "status": "ok",
        "signals": injected_signals,
        "domain": domain_path,
        "classification": "negative" if is_negative else (
            "positive" if is_positive else "neutral"
        ),
    }


# ===================================================================
# System Adapters
# ===================================================================

@router.get("/system/adapters")
async def get_adapter_registry(request: Request):
    """Legacy endpoint — adapter registry removed in product mode."""
    return {"enabled": False, "mode": "consolidation_only"}


# ===================================================================
# Analytics Overview
# ===================================================================

@router.get("/analytics/overview")
async def get_analytics_overview(request: Request):
    """Return aggregate analytics across all agents."""
    agent_manager = request.app.state.agent_manager
    agents = agent_manager.list_agents()

    overview = {
        "total_agents": len(agents),
        "agents_by_status": {},
        "total_facts": 0,
        "total_turns": 0,
        "total_sleep_cycles": 0,
    }

    for agent in agents:
        status = agent.get("status", "unknown")
        overview["agents_by_status"][status] = overview["agents_by_status"].get(status, 0) + 1
        overview["total_facts"] += agent.get("facts_in_memory", 0)
        overview["total_turns"] += agent.get("turn_count", 0)
        overview["total_sleep_cycles"] += agent.get("sleep_count", 0)

    return overview


# ===================================================================
# Comparative Analytics
# ===================================================================

@router.get("/analytics/agents/compare")
async def compare_agents(
    request: Request,
    ids: str = Query(..., description="Comma-separated agent IDs"),
):
    """Return comparative metrics for multiple agents."""
    agent_ids = [aid.strip() for aid in ids.split(",") if aid.strip()]
    if len(agent_ids) < 2:
        raise HTTPException(status_code=400, detail="Provide at least 2 agent IDs")

    agent_manager = request.app.state.agent_manager
    results = []

    for agent_id in agent_ids:
        status = agent_manager.get_agent_status(agent_id)
        results.append(status)

    return {"agents": results}


# ===================================================================
# Soul Package — Export / Import
# ===================================================================

@router.post("/agents/{agent_id}/soul/export")
async def export_soul_package(agent_id: str, request: Request):
    """Export an agent's state as a downloadable Soul Package.

    Query params:
        include_sessions (bool): include conversation history (default false)
    """
    from starlette.responses import FileResponse
    from nls.ledger.soul_package import export_soul

    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent not loaded")

    include_sessions = request.query_params.get("include_sessions", "false").lower() == "true"

    try:
        package_path = export_soul(
            runtime.agent_dir,
            include_sessions=include_sessions,
        )
        return FileResponse(
            str(package_path),
            media_type="application/zip",
            filename=package_path.name,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/agents/{agent_id}/soul/import")
async def import_soul_package(agent_id: str, request: Request):
    """Import a Soul Package into an existing agent directory.

    Expects a multipart file upload with the .soul.zip file.
    """
    from nls.ledger.soul_package import import_soul
    import tempfile

    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent not loaded")

    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(status_code=400, detail="No file uploaded")

    with tempfile.NamedTemporaryFile(suffix=".soul.zip", delete=False) as tmp:
        content = await upload.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        manifest = import_soul(
            tmp_path,
            runtime.agent_dir,
            new_agent_id=agent_id,
        )
        return {"status": "imported", "manifest": manifest}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/agents/{agent_id}/soul/fork")
async def fork_agent_memory(agent_id: str, request: Request):
    """Fork an agent's memory at a specific chain height.

    Creates a new agent with a copy of the source agent's knowledge
    up to the specified height.  Everything above that height is
    discarded in the fork.

    Body:
        fork_height (int): chain height to fork at
        new_agent_name (str, optional): display name for the new agent
    """
    from nls.ledger.soul_package import fork_at_height
    import uuid

    body = await request.json()
    fork_height = body.get("fork_height", 0)
    new_name = body.get("new_agent_name", "")

    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Source agent not loaded")

    new_agent_id = str(uuid.uuid4())
    agents_dir = runtime.agent_dir.parent
    target_dir = agents_dir / new_agent_id

    try:
        result = fork_at_height(
            source_dir=runtime.agent_dir,
            target_dir=target_dir,
            fork_height=fork_height,
            new_agent_id=new_agent_id,
        )

        if new_name:
            meta_path = target_dir / "agent_meta.json"
            if meta_path.exists():
                import json as _json
                meta = _json.loads(meta_path.read_text(encoding="utf-8"))
                meta["agent_name"] = new_name
                meta_path.write_text(
                    _json.dumps(meta, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

        try:
            await agent_manager.load_agent(new_agent_id)
            logger.info("Forked agent %s auto-loaded", new_agent_id)
        except Exception as load_exc:
            logger.warning(
                "Forked agent %s created but failed to auto-load: %s",
                new_agent_id, load_exc,
            )

        return {"status": "forked", **result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/agents/{agent_id}/soul/snapshot")
async def create_snapshot(agent_id: str, request: Request):
    """Create a user-initiated snapshot (save point) of the agent's current state.

    Exports a lightweight soul package to the snapshots directory.
    The snapshot can be used to restore the agent to this exact state.

    Body (optional):
        label (str): Human-readable label for this snapshot.
    """
    from nls.ledger.soul_package import export_soul
    import json as _json

    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent not loaded")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    label = body.get("label", "")

    try:
        snapshots_dir = runtime.agent_dir / "snapshots"
        snapshots_dir.mkdir(exist_ok=True)

        from datetime import datetime
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        snapshot_name = f"snapshot_{ts}"
        if label:
            safe_label = "".join(c for c in label if c.isalnum() or c in "-_ ")[:40]
            snapshot_name = f"snapshot_{ts}_{safe_label.strip().replace(' ', '_')}"

        output_path = snapshots_dir / f"{snapshot_name}.soul.zip"
        export_soul(runtime.agent_dir, output_path=output_path, include_sessions=False)

        # Write a manifest for this snapshot
        from nls.ledger.manifest import load_manifest
        try:
            state = load_manifest(runtime.agent_dir)
            chain_height = state.current_height
        except Exception:
            chain_height = 0

        manifest = {
            "snapshot_name": snapshot_name,
            "label": label,
            "chain_height": chain_height,
            "created_at": datetime.utcnow().isoformat(),
            "file": str(output_path.name),
        }

        manifest_path = snapshots_dir / f"{snapshot_name}.json"
        manifest_path.write_text(
            _json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info("Agent %s: snapshot created at height %d (%s)", agent_id, chain_height, snapshot_name)
        return {"status": "snapshot_created", **manifest}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/agents/{agent_id}/soul/snapshots")
async def list_snapshots(agent_id: str, request: Request):
    """List all available snapshots for an agent."""
    import json as _json

    agent_dir = _get_agent_dir(request, agent_id)
    snapshots_dir = agent_dir / "snapshots"

    if not snapshots_dir.exists():
        return {"snapshots": []}

    snapshots = []
    for f in sorted(snapshots_dir.glob("*.json"), reverse=True):
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
            snapshots.append(data)
        except Exception:
            pass

    return {"snapshots": snapshots}


@router.post("/agents/{agent_id}/soul/snapshot/restore")
async def restore_snapshot(agent_id: str, request: Request):
    """Restore an agent from a previously created snapshot.

    Body:
        file (str): The snapshot .soul.zip filename to restore.
    """
    from nls.ledger.soul_package import import_soul

    agent_manager = request.app.state.agent_manager
    agent_dir = _get_agent_dir(request, agent_id)
    snapshots_dir = agent_dir / "snapshots"

    body = await request.json()
    filename = body.get("file", "")

    if not filename:
        raise HTTPException(status_code=400, detail="Missing 'file' parameter")

    snapshot_path = snapshots_dir / filename
    if not snapshot_path.exists():
        raise HTTPException(status_code=404, detail=f"Snapshot file not found: {filename}")

    try:
        manifest = import_soul(
            snapshot_path,
            agent_dir,
            new_agent_id=agent_id,
        )

        # Reload the agent so the runtime picks up the restored state
        try:
            await agent_manager.reload_agent(agent_id)
        except Exception:
            try:
                await agent_manager.load_agent(agent_id)
            except Exception:
                pass

        logger.info("Agent %s: restored from snapshot %s", agent_id, filename)
        return {"status": "restored", "manifest": manifest, "snapshot": filename}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ─── Inference hot reload (desktop settings — no full runtime restart) ───


@router.post("/inference/hot-reload")
async def hot_reload_inference(request: Request):
    """Apply primary and delegate model ids to the live runtime."""
    import os

    body = await request.json()
    hf_model = (body.get("hf_model") or "").strip()
    delegate_hf_model = body.get("delegate_hf_model")
    delegate_use_primary = body.get("delegate_use_primary", True)

    model_manager = request.app.state.model_manager
    agent_manager = request.app.state.agent_manager

    if hf_model:
        model_manager.hf_model = hf_model
        if getattr(model_manager, "vllm_client", None) is not None:
            model_manager.vllm_client.default_model = hf_model
        os.environ["NLS_HF_MODEL"] = hf_model

    if delegate_use_primary:
        os.environ.pop("NLS_DELEGATE_HF_MODEL", None)
        delegate_value = None
    elif delegate_hf_model is not None:
        delegate_value = str(delegate_hf_model).strip() or None
        if delegate_value:
            os.environ["NLS_DELEGATE_HF_MODEL"] = delegate_value
        else:
            os.environ.pop("NLS_DELEGATE_HF_MODEL", None)
    else:
        delegate_value = None

    updated = 0
    for runtime in agent_manager.get_loaded_runtimes().values():
        if hf_model and getattr(runtime, "vllm_client", None) is not None:
            runtime.vllm_client.default_model = hf_model
        if delegate_use_primary:
            runtime.delegate_model = None
        elif delegate_hf_model is not None:
            runtime.delegate_model = delegate_value
        updated += 1

    logger.info(
        "Inference hot-reload: hf_model=%s delegate=%s use_primary=%s agents=%d",
        hf_model or "(unchanged)",
        delegate_value if not delegate_use_primary else "(primary)",
        delegate_use_primary,
        updated,
    )
    return {
        "ok": True,
        "hf_model": hf_model or model_manager.hf_model,
        "delegate_hf_model": delegate_value,
        "agents_updated": updated,
    }


# ─── Visual Cortex ─────────────────────────────────────────────────


@router.get("/agents/{agent_id}/visual-cortex/buffer")
async def get_visual_cortex_buffer(
    agent_id: str,
    request: Request,
    channel: str | None = Query(None, description="Filter by channel: agent | user"),
    limit: int = Query(50, ge=1, le=200),
):
    """Return the recent visual events from the Visual Cortex ring buffer."""
    agent_manager = request.app.state.agent_manager
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent not loaded or not found")

    vc = getattr(runtime, "visual_cortex", None)
    if vc is None or not vc.config.enabled:
        return {
            "enabled": False,
            "running": False,
            "events": [],
            "total": 0,
            "config": {},
        }

    events_raw = vc.buffer.get_all()
    if channel:
        events_raw = [e for e in events_raw if e.channel == channel]
    events_raw = events_raw[-limit:]

    task_alive = vc._task is not None and not vc._task.done() if vc._task else False

    return {
        "enabled": vc.config.enabled,
        "running": vc._running,
        "events": [e.to_dict() for e in events_raw],
        "total": len(vc.buffer),
        "config": {
            "fps": vc.config.fps,
            "agent_fps": vc._agent_fps,
            "user_fps": vc._user_fps,
            "model_preference": vc.config.model_preference,
            "buffer_size": vc.config.buffer_size,
            "attention_level": vc.config.attention_level,
            "agent_active": vc._agent_active,
        },
        "_diag": {
            "task_alive": task_alive,
            "buffer_deque_len": len(vc.buffer._buffer),
            "events_this_minute": vc._events_this_minute,
            "vlm_loaded": vc._vlm.is_loaded if vc._vlm else False,
            "browser_engine_set": vc._browser_engine is not None,
            "callbacks_count": len(vc._callbacks),
        },
    }
