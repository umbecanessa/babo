"""Skills admin API -- list, enable, disable, delete skills + skill reviews."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/skills", tags=["skills"])


def _loader(request: Request):
    loader = getattr(request.app.state, "skill_loader", None)
    if loader is None:
        raise HTTPException(503, "Skill loader not initialized")
    return loader


def _reviews_dir(request: Request) -> Path:
    data_dir: Path = request.app.state.settings.data_dir
    d = data_dir / "skill_reviews"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Loaded skills ──────────────────────────────────────────────

@router.get("")
async def list_skills(request: Request) -> list[dict[str, Any]]:
    """List all discovered skills."""
    loader = _loader(request)
    return [sk.to_dict() for sk in loader.skills.values()]


@router.get("/{name}")
async def get_skill(request: Request, name: str) -> dict[str, Any]:
    """Get skill detail including file listing."""
    loader = _loader(request)
    sk = loader.skills.get(name)
    if not sk:
        raise HTTPException(404, f"Skill '{name}' not found")
    detail = sk.to_dict()
    detail["files"] = loader.get_skill_files(name)
    return detail


@router.post("/{name}/enable")
async def enable_skill(request: Request, name: str) -> dict[str, str]:
    """Enable a disabled skill (requires restart to take effect)."""
    loader = _loader(request)
    if loader.enable_skill(name):
        return {"status": "enabled", "message": f"Skill '{name}' enabled. Restart to load."}
    raise HTTPException(404, f"Skill '{name}' not found or already enabled")


@router.post("/{name}/disable")
async def disable_skill(request: Request, name: str) -> dict[str, str]:
    """Disable a skill (requires restart to take effect)."""
    loader = _loader(request)
    if loader.disable_skill(name):
        return {"status": "disabled", "message": f"Skill '{name}' disabled. Restart to unload."}
    raise HTTPException(404, f"Skill '{name}' not found")


@router.delete("/{name}")
async def delete_skill(request: Request, name: str) -> dict[str, str]:
    """Delete a skill directory entirely."""
    loader = _loader(request)
    if loader.delete_skill(name):
        return {"status": "deleted"}
    raise HTTPException(404, f"Skill '{name}' not found")


# ── Skill onboarding ───────────────────────────────────────────

@router.get("/{name}/onboarding")
async def get_skill_onboarding(request: Request, name: str) -> dict[str, Any]:
    """Return the skill's onboarding specification (if any)."""
    loader = _loader(request)
    sk = loader.skills.get(name)
    if not sk:
        raise HTTPException(404, f"Skill '{name}' not found")
    meta = sk.meta
    if meta and hasattr(meta, "onboarding") and meta.onboarding is not None:
        return meta.onboarding.to_dict()
    return {"setup_type": "manual", "intro_message": "", "setup_prompt": "", "completion_event": ""}


# ── Skill config ───────────────────────────────────────────────

def _skill_dir(request: Request, name: str) -> Path:
    loader = _loader(request)
    sk = loader.skills.get(name)
    if sk is not None and sk.path.is_dir():
        return sk.path
    data_dir: Path = request.app.state.settings.data_dir
    d = data_dir / "skills" / name
    if not d.is_dir():
        raise HTTPException(404, f"Skill '{name}' not found")
    return d


def _resolve_agent_id(request: Request) -> str | None:
    """Return the first (usually only) active agent ID, or None."""
    am = getattr(request.app.state, "agent_manager", None)
    if am is None:
        return None
    try:
        agents = am.list_agents()
        if agents:
            return agents[0].get("id") or agents[0].get("agent_id")
    except Exception:
        pass
    return None


def _skill_config_path(request: Request, name: str) -> Path:
    """Config always lives in the writable data/skills/ directory."""
    loader = _loader(request)
    if name not in loader.skills:
        raise HTTPException(404, f"Skill '{name}' not found")
    data_dir: Path = request.app.state.settings.data_dir
    cfg_dir = data_dir / "skills" / name
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir / "config.json"


_CHANNEL_SKILL_NAMES = frozenset({
    "discord-channel",
    "slack-channel",
    "telegram-channel",
    "whatsapp-channel",
    "email-channel",
})

_CHANNEL_CREDENTIAL_KEYS = frozenset({
    "bot_token",
    "signing_secret",
    "linked_phone",
    "connected_email",
})


def _channel_skill_connected(name: str, config: dict[str, Any]) -> bool:
    if name == "discord-channel" or name == "slack-channel":
        return bool(config.get("enabled") and str(config.get("bot_token", "")).strip())
    if name == "telegram-channel":
        return bool(
            str(config.get("bot_token", "")).strip()
            or str(config.get("linked_id", "")).strip()
        )
    if name == "whatsapp-channel":
        return bool(str(config.get("linked_phone", "")).strip())
    if name == "email-channel":
        return bool(str(config.get("connected_email", "")).strip())
    return False


def _read_skill_config_for_agent(
    cfg_path: Path,
    name: str,
    resolved_agent_id: str | None,
) -> tuple[dict[str, Any], bool]:
    """Load skill config for UI/runtime. Never leak global credentials to other agents."""
    global_cfg: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            global_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(500, f"Failed to read config: {exc}") from exc

    if not resolved_agent_id:
        return global_cfg, False

    agent_cfg_path = cfg_path.parent / "agents" / f"{resolved_agent_id}.json"
    if agent_cfg_path.exists():
        try:
            agent_cfg = json.loads(agent_cfg_path.read_text(encoding="utf-8"))
        except Exception:
            agent_cfg = {}
        merged = dict(global_cfg)
        merged.update(agent_cfg)
        return merged, True

    if name in _CHANNEL_SKILL_NAMES:
        # Per-agent channel skills: non-secret defaults only until this agent is linked.
        config = {
            k: v for k, v in global_cfg.items()
            if k not in _CHANNEL_CREDENTIAL_KEYS
        }
        return config, False

    return dict(global_cfg), False


@router.get("/{name}/config/schema")
async def get_skill_config_schema(request: Request, name: str) -> list[dict[str, Any]]:
    """Return the skill's declared config_schema (empty list if none)."""
    loader = _loader(request)
    sk = loader.skills.get(name)
    if not sk:
        raise HTTPException(404, f"Skill '{name}' not found")
    if sk.meta and sk.meta.config_schema:
        return [f.to_dict() for f in sk.meta.config_schema]
    return []


@router.get("/{name}/config")
async def get_skill_config(
    request: Request,
    name: str,
    with_schema: bool = Query(False, description="Include config_schema annotations"),
    agent_id: str | None = Query(None, description="Agent ID for per-agent config"),
) -> dict[str, Any]:
    """Return skill config for an agent. Channel credentials are per-agent only."""
    cfg_path = _skill_config_path(request, name)
    resolved_agent_id = agent_id or _resolve_agent_id(request)
    config, per_agent_configured = _read_skill_config_for_agent(
        cfg_path, name, resolved_agent_id,
    )
    channel_connected = (
        per_agent_configured
        and name in _CHANNEL_SKILL_NAMES
        and _channel_skill_connected(name, config)
    )

    if with_schema:
        loader = _loader(request)
        sk = loader.skills.get(name)
        schema = []
        if sk and sk.meta and sk.meta.config_schema:
            schema = [f.to_dict() for f in sk.meta.config_schema]
        return {
            "config": config,
            "schema": schema,
            "per_agent_configured": per_agent_configured,
            "channel_connected": channel_connected,
        }
    return config


@router.patch("/{name}/config")
async def update_skill_config(
    request: Request,
    name: str,
    config: dict[str, Any] = Body(...),
    agent_id: str | None = Query(None, description="Agent ID for per-agent config"),
) -> dict[str, str]:
    """Merge and save the skill's config. Writes to per-agent file when agent_id is available."""
    cfg_path = _skill_config_path(request, name)
    resolved_agent_id = agent_id or _resolve_agent_id(request)

    if resolved_agent_id:
        agent_cfg_path = cfg_path.parent / "agents" / f"{resolved_agent_id}.json"
        existing: dict[str, Any] = {}
        if agent_cfg_path.exists():
            try:
                existing = json.loads(agent_cfg_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        merged = {**existing}
        for key, val in config.items():
            if isinstance(val, str) and "***masked***" in val:
                if existing.get(key):
                    val = existing[key]
            if key == "scoped_channels" and isinstance(val, dict):
                incoming_channels = val.get("channels") if isinstance(val.get("channels"), dict) else {}
                existing_channels = (
                    existing.get("scoped_channels", {}).get("channels")
                    if isinstance(existing.get("scoped_channels"), dict)
                    else {}
                )
                if not incoming_channels and existing_channels:
                    continue
            merged[key] = val
        existing = merged
        agent_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        agent_cfg_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        loader = _loader(request)
        sk = loader.skills.get(name)
        if sk and sk.context:
            adapter = getattr(sk.context, "adapter", None)
            if adapter and hasattr(adapter, "update_config"):
                try:
                    adapter.update_config(existing, agent_id=resolved_agent_id)
                except Exception:
                    pass
    else:
        existing = {}
        if cfg_path.exists():
            try:
                existing = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.update(config)
        cfg_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return {"status": "saved"}


# ── Skill files ───────────────────────────────────────────────

@router.get("/{name}/files/{file_path:path}")
async def get_skill_file(
    request: Request, name: str, file_path: str,
) -> dict[str, Any]:
    """Return the text content of a file inside the skill directory."""
    skill_dir = _skill_dir(request, name)
    target = (skill_dir / file_path).resolve()
    if not str(target).startswith(str(skill_dir.resolve())):
        raise HTTPException(403, "Path traversal not allowed")
    if not target.is_file():
        raise HTTPException(404, f"File '{file_path}' not found in skill '{name}'")
    try:
        content = target.read_text(encoding="utf-8")
    except Exception as exc:
        raise HTTPException(500, f"Failed to read file: {exc}")
    return {"path": file_path, "content": content, "size": target.stat().st_size}


@router.put("/{name}/files/{file_path:path}")
async def update_skill_file(
    request: Request,
    name: str,
    file_path: str,
    body: dict[str, str] = Body(...),
) -> dict[str, str]:
    """Save text content to a file inside the skill directory."""
    skill_dir = _skill_dir(request, name)
    target = (skill_dir / file_path).resolve()
    if not str(target).startswith(str(skill_dir.resolve())):
        raise HTTPException(403, "Path traversal not allowed")
    content = body.get("content", "")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"status": "saved", "path": file_path}


# ── Skill reviews (created by request_restart tool) ────────────

@router.get("/reviews/list")
async def list_reviews(request: Request) -> list[dict[str, Any]]:
    """List all skill reviews (newest first)."""
    d = _reviews_dir(request)
    reviews = []
    for f in sorted(d.glob("*.json"), reverse=True):
        try:
            reviews.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return reviews


@router.get("/reviews/{review_id}")
async def get_review(request: Request, review_id: str) -> dict[str, Any]:
    """Get a single skill review."""
    p = _reviews_dir(request) / f"{review_id}.json"
    if not p.exists():
        raise HTTPException(404, f"Review '{review_id}' not found")
    return json.loads(p.read_text(encoding="utf-8"))


@router.post("/reviews/{review_id}/approve")
async def approve_review(request: Request, review_id: str) -> dict[str, str]:
    """Approve a skill review -- restart the server to load new skills."""
    import asyncio
    import time

    d = _reviews_dir(request)
    p = d / f"{review_id}.json"
    if not p.exists():
        raise HTTPException(404, f"Review '{review_id}' not found")

    review = json.loads(p.read_text(encoding="utf-8"))
    if review.get("status") != "pending":
        raise HTTPException(400, f"Review is already {review.get('status')}")

    review["status"] = "approved"
    review["approved_at"] = time.time()
    p.write_text(json.dumps(review, indent=2), encoding="utf-8")

    # Auto-enable the new skills for the creating agent
    creator_id = review.get("created_by", "")
    if creator_id:
        agent_manager = getattr(request.app.state, "agent_manager", None)
        if agent_manager is not None:
            runtime = agent_manager.get_runtime(creator_id)
            if runtime is not None:
                for skill in review.get("skills", []):
                    skill_name = skill.get("name")
                    if skill_name:
                        runtime.enable_skill(skill_name)
                logger.info(
                    "Auto-enabled skills %s for creator agent %s",
                    [s.get("name") for s in review.get("skills", [])],
                    creator_id,
                )

    # Write .creator file into each skill directory for persistence
    data_dir: Path = request.app.state.settings.data_dir
    skills_dir = data_dir / "skills"
    for skill in review.get("skills", []):
        skill_name = skill.get("name")
        if skill_name and creator_id:
            creator_file = skills_dir / skill_name / ".creator"
            try:
                creator_file.write_text(creator_id, encoding="utf-8")
            except Exception:
                pass

    logger.warning(
        "Skill review %s approved — restarting server", review_id,
    )

    from server.shutdown_trace import record_initiator

    record_initiator(
        "http:skill_review_approved",
        review_id=review_id,
    )

    from nls.tools.agent_tools.request_restart import _trigger_shutdown
    import nls.tools.agent_tools.request_restart as _rr
    _rr._restart_requested = True

    loop = asyncio.get_running_loop()
    loop.call_later(5.0, _trigger_shutdown)

    return {"status": "approved", "message": "Server restarting to load new skills..."}


@router.post("/reviews/{review_id}/reject")
async def reject_review(request: Request, review_id: str) -> dict[str, str]:
    """Reject a skill review -- delete the new skill directories."""
    import time

    d = _reviews_dir(request)
    p = d / f"{review_id}.json"
    if not p.exists():
        raise HTTPException(404, f"Review '{review_id}' not found")

    review = json.loads(p.read_text(encoding="utf-8"))
    if review.get("status") != "pending":
        raise HTTPException(400, f"Review is already {review.get('status')}")

    loader = _loader(request)
    for skill in review.get("skills", []):
        name = skill.get("name")
        if name:
            loader.delete_skill(name)
            logger.info("Deleted rejected skill '%s'", name)

    review["status"] = "rejected"
    review["rejected_at"] = time.time()
    p.write_text(json.dumps(review, indent=2), encoding="utf-8")

    return {"status": "rejected", "message": "Skills deleted."}


# ── Inline skill repair ───────────────────────────────────────

_MAX_REPAIR_PASSES = 3
_MAX_REPAIR_ITERATIONS = 8
_REPAIR_TIMEOUT_S = 90


@router.post("/{name}/repair")
async def repair_skill(
    request: Request,
    name: str,
    agent_id: str = Query(..., description="Agent whose runtime (model/adapters) to use"),
) -> StreamingResponse:
    """Run an inline repair sub-agent to fix a broken skill.

    Returns an SSE stream of repair progress events.
    """
    loader = _loader(request)
    sk = loader.skills.get(name)
    if not sk:
        raise HTTPException(404, f"Skill '{name}' not found")
    if sk.status != "error":
        raise HTTPException(400, f"Skill '{name}' is not in error state (status={sk.status})")

    rt = _agent_runtime(request, agent_id)

    return StreamingResponse(
        _repair_stream(loader, sk, rt, name),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _repair_stream(loader, sk, runtime, name: str):
    """Generator that drives the repair loop and yields SSE events.

    Uses an ``asyncio.Queue`` so ``on_event`` steps stream to the
    client in real time rather than being batched after each pass.
    """
    import json as _json
    import os

    import nls as _nls_mod
    from nls.tools.agent_tools import (
        create_bash_tool,
        create_edit_tool,
        create_read_tool,
        create_write_tool,
        tools_to_openai_schema,
    )
    from nls.agentic.loop import run_loop
    from nls.agentic.bridge import LoopHooks
    from nls.agentic.types import LoopConfig, AgentEvent

    def _sse(data: dict) -> str:
        return f"data: {_json.dumps(data)}\n\n"

    skill_dir = str(sk.path)
    file_list = "\n".join(
        f"  - {f['path']} ({f['size']}B)"
        for f in loader.get_skill_files(name)
        if not f["path"].startswith("__pycache__")
    )

    bash_tool = create_bash_tool(skill_dir, default_timeout=30)
    project_root = str(Path(_nls_mod.__file__).resolve().parent.parent)
    prev_pp = bash_tool._isolated_env.get("PYTHONPATH", "")
    bash_tool._isolated_env["PYTHONPATH"] = (
        project_root + os.pathsep + prev_pp if prev_pp else project_root
    )

    tools = [
        create_read_tool(skill_dir),
        create_write_tool(skill_dir),
        create_edit_tool(skill_dir),
        bash_tool,
    ]
    openai_tools = tools_to_openai_schema(tools)

    system_prompt = (
        f"You are a skill repair agent. Your ONLY job is to fix a broken "
        f"Python skill so it loads without errors.\n\n"
        f"SKILL: {name}\n"
        f"DIRECTORY: {skill_dir}\n"
        f"FILES:\n{file_list}\n\n"
        f"RULES:\n"
        f"- IMMEDIATELY call read() on the relevant files — do not plan first\n"
        f"- Fix the bug causing the load error\n"
        f"- You can use bash to test: python -c \"from {name} import ...\"\n"
        f"- The NLS server packages (nls.*, etc.) are available on PYTHONPATH\n"
        f"- After you edit, the skill will be automatically reloaded\n"
        f"- Be surgical — fix only what's broken, don't rewrite everything\n"
        f"- Do NOT ask questions — just read, fix, and verify\n"
    )

    cfg = LoopConfig(
        max_iterations=_MAX_REPAIR_ITERATIONS,
        result_max_chars=4000,
        tool_timeout_seconds=30,
        consecutive_text_only_limit=8,
    )

    hooks = LoopHooks()
    if hasattr(runtime, "_build_agentic_hooks"):
        try:
            runtime_hooks = runtime._build_agentic_hooks()
            hooks = LoopHooks(
                on_before_tool=getattr(runtime_hooks, "on_before_tool", None),
                on_after_tool=getattr(runtime_hooks, "on_after_tool", None),
            )
        except Exception:
            logger.warning("Could not build ANS hooks for repair", exc_info=True)

    vllm_client, adapter_name = runtime.inference_pipeline()
    if vllm_client is None:
        yield _sse({"type": "error", "message": "No inference client configured for this agent"})
        return
    tool_dict = {t.name: t for t in tools}

    current_error = sk.error or "Unknown error"
    pass_num = 0

    _SENTINEL = object()
    q: asyncio.Queue = asyncio.Queue()

    try:
        while pass_num < _MAX_REPAIR_PASSES:
            pass_num += 1
            user_input = (
                f"Skill '{name}' failed to load with this error:\n\n"
                f"  {current_error}\n\n"
                f"Read the relevant files, find the bug, and fix it."
            )
            if pass_num > 1:
                user_input = (
                    f"The skill STILL fails to load after your previous fix. "
                    f"New error:\n\n  {current_error}\n\n"
                    f"Read the files again and fix this new error."
                )

            yield _sse({"type": "pass_start", "pass": pass_num, "error": current_error})

            async def _on_event(event: AgentEvent, _p: int = pass_num) -> None:
                d = event.data or {}
                if event.type.value == "tool_execution_end":
                    await q.put({
                        "type": "step",
                        "pass": _p,
                        "iteration": d.get("iteration", 0),
                        "tool": d.get("tool_name", "?"),
                        "file": d.get("arguments", {}).get("path", ""),
                        "is_error": d.get("is_error", False),
                    })

            async def _run_loop():
                try:
                    context = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_input},
                    ]
                    return await asyncio.wait_for(
                        run_loop(
                            context=context,
                            tools=tool_dict,
                            config=cfg,
                            hooks=hooks,
                            vllm_client=vllm_client,
                            on_event=_on_event,
                            user_input=user_input,
                            enable_thinking=False,
                            adapter_name=adapter_name,
                        ),
                        timeout=_REPAIR_TIMEOUT_S,
                    )
                finally:
                    await q.put(_SENTINEL)

            loop_task = asyncio.create_task(_run_loop())

            while True:
                item = await q.get()
                if item is _SENTINEL:
                    break
                yield _sse(item)

            try:
                result = await loop_task
            except asyncio.TimeoutError:
                yield _sse({"type": "complete", "success": False,
                            "new_status": "error", "error": "Repair timed out",
                            "passes": pass_num})
                return
            except Exception as exc:
                yield _sse({"type": "complete", "success": False,
                            "new_status": "error", "error": str(exc),
                            "passes": pass_num})
                return

            yield _sse({"type": "reloading", "pass": pass_num})

            try:
                reloaded = await loader.reload_skill(name)
            except Exception as exc:
                yield _sse({"type": "complete", "success": False,
                            "new_status": "error", "error": str(exc),
                            "passes": pass_num})
                return

            if reloaded.status == "loaded":
                yield _sse({
                    "type": "complete", "success": True,
                    "new_status": "loaded",
                    "skill": reloaded.to_dict(),
                    "passes": pass_num,
                })
                return

            current_error = reloaded.error or "Unknown error after reload"
            yield _sse({
                "type": "reload_failed", "pass": pass_num,
                "error": current_error,
            })

        yield _sse({
            "type": "complete", "success": False,
            "new_status": "error",
            "error": f"Still failing after {pass_num} passes: {current_error}",
            "passes": pass_num,
        })

    except Exception as exc:
        logger.error("Skill repair stream error: %s", exc, exc_info=True)
        yield _sse({"type": "complete", "success": False,
                    "new_status": "error", "error": str(exc),
                    "passes": pass_num})


# ── Per-agent skill enablement ─────────────────────────────────

agent_skills_router = APIRouter(
    prefix="/admin/agents", tags=["agent-skills"],
)


def _agent_runtime(request: Request, agent_id: str):
    """Get the agent's runtime, or raise 404."""
    am = getattr(request.app.state, "agent_manager", None)
    if am is None:
        raise HTTPException(503, "Agent manager not initialized")
    rt = am.get_runtime(agent_id)
    if rt is None:
        raise HTTPException(404, f"Agent '{agent_id}' not found or not loaded")
    return rt


@agent_skills_router.get("/{agent_id}/skills")
async def list_agent_skills(
    request: Request, agent_id: str,
) -> list[dict[str, Any]]:
    """List all skills with an ``enabled`` flag for the given agent."""
    rt = _agent_runtime(request, agent_id)
    loader = _loader(request)
    enabled = rt.get_enabled_skills()
    all_enabled = enabled == ["*"]

    result = []
    for sk in loader.skills.values():
        d = sk.to_dict()
        d["enabled_for_agent"] = all_enabled or sk.name in enabled
        result.append(d)
    return result


@agent_skills_router.post("/{agent_id}/skills/{name}/enable")
async def enable_agent_skill(
    request: Request, agent_id: str, name: str,
) -> dict[str, str]:
    """Enable a skill for a specific agent."""
    rt = _agent_runtime(request, agent_id)
    loader = _loader(request)
    if name not in loader.skills:
        raise HTTPException(404, f"Skill '{name}' not found")
    rt.enable_skill(name)
    return {"status": "enabled", "skill": name, "agent_id": agent_id}


@agent_skills_router.post("/{agent_id}/skills/{name}/disable")
async def disable_agent_skill(
    request: Request, agent_id: str, name: str,
) -> dict[str, str]:
    """Disable a skill for a specific agent."""
    rt = _agent_runtime(request, agent_id)
    loader = _loader(request)
    if name not in loader.skills:
        raise HTTPException(404, f"Skill '{name}' not found")
    rt.disable_skill(name)
    return {"status": "disabled", "skill": name, "agent_id": agent_id}


# ── Brain stats (myelination, crystallization readiness) ──────

@router.get("/{name}/brain")
async def get_skill_brain_stats(request: Request, name: str) -> dict[str, Any]:
    """Return brain-level metrics for a skill (myelination, usage, crystallization)."""
    loader = _loader(request)
    sk = loader.skills.get(name)
    if not sk:
        raise HTTPException(404, f"Skill '{name}' not found")

    data_dir: Path = request.app.state.settings.data_dir
    result: dict[str, Any] = {
        "skill_name": name,
        "skill_type": sk.meta.skill_type if sk.meta else "native",
        "myelination_score": 0.0,
        "total_uses": 0,
        "success_rate": 0.0,
        "associated_domains": [],
        "readiness_score": None,
    }

    tracker_path = data_dir / "skill_tracker.json"
    if tracker_path.exists():
        try:
            tracker = json.loads(tracker_path.read_text(encoding="utf-8"))
            entry = tracker.get(name, {})
            result["myelination_score"] = entry.get("myelination_score", 0.0)
            total = entry.get("encounter_count", 0)
            result["total_uses"] = total
            success = entry.get("success_count", 0)
            result["success_rate"] = success / total if total > 0 else 0.0
            result["associated_domains"] = entry.get("associated_domains", [])
        except Exception:
            pass

    candidates_path = data_dir / "crystallization_candidates.json"
    if candidates_path.exists():
        try:
            candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
            for c in candidates:
                if c.get("skill_name") == name:
                    result["readiness_score"] = c.get("readiness_score", 0.0)
                    result["crystallization_ready"] = c.get("ready", False)
                    break
        except Exception:
            pass

    return result


# ── Crystallization endpoints ─────────────────────────────────

crystallization_router = APIRouter(
    prefix="/admin/skills/crystallization", tags=["crystallization"],
)


@crystallization_router.get("/candidates")
async def list_crystallization_candidates(request: Request) -> list[dict[str, Any]]:
    """List all evaluated crystallization candidates."""
    data_dir: Path = request.app.state.settings.data_dir
    p = data_dir / "crystallization_candidates.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


# ── ClawHub proxy (for frontend) ─────────────────────────────

clawhub_router = APIRouter(prefix="/api/clawhub", tags=["clawhub"])

_CLAWHUB_API = "https://clawhub.ai/api/v1"
_CLAWHUB_CACHE_TTL = 600  # 10 minutes
_clawhub_cache: dict[str, tuple[float, Any]] = {}


def _clawhub_get(path: str, timeout: int = 10) -> Any:
    """Fetch from ClawHub API, returning parsed JSON or raising."""
    import urllib.request

    url = f"{_CLAWHUB_API}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _clawhub_cached_get(cache_key: str, path: str, timeout: int = 10) -> Any:
    """Fetch from ClawHub API with in-memory TTL cache.

    On failure, returns stale cached data if available; otherwise re-raises.
    """
    import time as _time

    cached = _clawhub_cache.get(cache_key)
    if cached and (_time.time() - cached[0]) < _CLAWHUB_CACHE_TTL:
        return cached[1]

    try:
        data = _clawhub_get(path, timeout)
        _clawhub_cache[cache_key] = (_time.time(), data)
        return data
    except Exception:
        if cached:
            return cached[1]
        raise


@clawhub_router.get("/search")
async def clawhub_search(
    q: str = Query("", description="Search query"),
    limit: int = Query(12, ge=1, le=50),
) -> list[dict[str, Any]]:
    """Search ClawHub skills via vector search."""
    import urllib.parse

    cache_key = f"search:{q.lower().strip()}:{limit}"
    try:
        data = _clawhub_cached_get(
            cache_key,
            f"/search?q={urllib.parse.quote(q)}&limit={limit}",
        )
        return data.get("results", [])
    except Exception as exc:
        logger.warning("ClawHub search failed: %s", exc)
        return []


@clawhub_router.get("/featured")
async def clawhub_featured(
    sort: str = Query("downloads", description="Sort: downloads, highlighted, newest, stars"),
    limit: int = Query(12, ge=1, le=50),
) -> list[dict[str, Any]]:
    """List featured/popular skills from ClawHub."""
    import urllib.parse

    cache_key = f"featured:{sort}:{limit}"
    try:
        data = _clawhub_cached_get(
            cache_key,
            f"/skills?limit={limit}&sort={urllib.parse.quote(sort)}",
        )
        return data.get("items", [])
    except Exception as exc:
        logger.warning("ClawHub featured listing failed: %s", exc)
        return []


@clawhub_router.get("/skill/{slug}")
async def clawhub_skill_info(slug: str) -> dict[str, Any]:
    """Resolve skill detail from ClawHub."""
    import urllib.parse

    try:
        return _clawhub_get(f"/resolve?slug={urllib.parse.quote(slug)}")
    except Exception as exc:
        raise HTTPException(502, f"ClawHub API error: {exc}")


@clawhub_router.post("/install")
async def clawhub_install(
    request: Request,
    body: dict[str, str] = Body(...),
) -> dict[str, str]:
    """Install a ClawHub skill by downloading and extracting it locally."""
    import io
    import urllib.parse
    import urllib.request
    import zipfile

    slug = body.get("slug", "")
    if not slug:
        raise HTTPException(400, "Missing 'slug'")

    data_dir: Path = request.app.state.settings.data_dir
    skill_dir = data_dir / "skills" / slug
    if skill_dir.exists():
        raise HTTPException(409, f"Skill '{slug}' already installed")

    import time
    import urllib.error

    url = f"{_CLAWHUB_API}/download?slug={urllib.parse.quote(slug)}&tag=latest"
    raw: bytes | None = None
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "NLS/1.0", "Accept": "*/*"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            break
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code == 429 and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise HTTPException(502, f"ClawHub returned HTTP {exc.code}")
        except Exception as exc:
            raise HTTPException(502, f"Failed to download skill bundle: {exc}")

    if raw is None:
        raise HTTPException(502, f"Failed to download after retries: {last_err}")

    try:
        skill_dir.mkdir(parents=True, exist_ok=True)

        if raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                zf.extractall(skill_dir)
        else:
            text = raw.decode("utf-8", errors="replace")
            if text.strip().startswith("---"):
                (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
            else:
                try:
                    bundle = json.loads(text)
                    if isinstance(bundle, dict) and "files" in bundle:
                        for fname, fcontent in bundle["files"].items():
                            (skill_dir / fname).write_text(
                                str(fcontent), encoding="utf-8"
                            )
                    elif isinstance(bundle, dict) and "skillMd" in bundle:
                        (skill_dir / "SKILL.md").write_text(
                            bundle["skillMd"], encoding="utf-8"
                        )
                    else:
                        (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
                except json.JSONDecodeError:
                    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")

        (skill_dir / ".clawhub").write_text(slug, encoding="utf-8")
    except Exception as exc:
        import shutil
        shutil.rmtree(skill_dir, ignore_errors=True)
        raise HTTPException(500, f"Failed to extract skill bundle: {exc}")

    return {"status": "installed", "slug": slug}


@clawhub_router.get("/installed")
async def clawhub_list_installed(request: Request) -> list[dict[str, Any]]:
    """List locally installed ClawHub skills."""
    data_dir: Path = request.app.state.settings.data_dir
    skills_dir = data_dir / "skills"
    result = []
    if skills_dir.is_dir():
        for d in skills_dir.iterdir():
            marker = d / ".clawhub"
            if marker.exists():
                result.append({
                    "slug": marker.read_text(encoding="utf-8").strip(),
                    "path": str(d),
                })
    return result
