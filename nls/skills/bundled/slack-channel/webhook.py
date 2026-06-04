"""Slack inbound webhook — Events API payloads relayed from NestJS."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from nls.skills.channel_adapter_util import broadcast_channel_event, strip_signal_tags

logger = logging.getLogger(__name__)
router = APIRouter(tags=["slack-channel"])


def _get_adapter(app: Any) -> Any | None:
    sl = getattr(app.state, "skill_loader", None)
    if sl is None:
        return None
    sk = sl.skills.get("slack-channel")
    if sk and sk.context:
        return sk.context.adapter
    return None


@router.post("/webhook/{agent_id}")
async def slack_inbound(agent_id: str, request: Request):
    app = request.app
    import json
    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8") if raw else "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    adapter = _get_adapter(app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Slack skill not loaded")

    cfg = adapter._agent_cfg(agent_id)
    signing_secret = cfg.get("signing_secret", "")
    sig = request.headers.get("x-slack-signature", "")
    ts = request.headers.get("x-slack-request-timestamp", "")
    # Relay path (NestJS → sidecar) does not forward Slack headers; only verify
    # when the request came directly from Slack with signature headers present.
    if signing_secret and sig and ts and not adapter.verify_signature(signing_secret, ts, raw, sig):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    event = body.get("event") or {}
    if event.get("type") in ("member_joined_channel", "member_left_channel"):
        await adapter.handle_member_event(agent_id, event)
        return {"ok": True, "status": "synced"}

    normalized = adapter.normalize(body, agent_id=agent_id)
    if normalized is None:
        return {"ok": True, "status": "skip"}

    if not adapter.should_respond(event, agent_id=agent_id):
        return {"ok": True, "status": "policy_rejected"}

    agent_manager = getattr(app.state, "agent_manager", None)
    if agent_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager not ready")
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    adapter.register_known_sender(normalized["sender_id"], agent_id)
    session_key = normalized["session_key"]
    text = normalized.get("content", "")
    channel_id = normalized["metadata"]["channel_id"]
    thread_ts = normalized["metadata"].get("thread_ts") or normalized["metadata"].get("ts")
    sender_name = normalized["sender_name"]

    history = runtime.load_session_history(session_key)
    runtime.save_session_history(
        history + [{"role": "user", "content": text or "[empty]"}],
        session_key=session_key,
        metadata={"channel": "slack", "sender": sender_name},
    )
    broadcast_channel_event(app, agent_id, "slack", normalized, direction="inbound")

    try:
        from nls.skills.channel_processing import (
            process_channel_message,
            try_feed_pending_answer,
        )

        if try_feed_pending_answer(agent_id, session_key, text):
            return {"ok": True, "status": "answer_routed"}

        user_input = f"[{sender_name} via Slack]: {text}" if text else f"[{sender_name} via Slack]:"
        response_text = await process_channel_message(
            app, runtime, agent_id, user_input, history,
            channel_adapter=adapter,
            reply_target=channel_id,
            session_key=session_key,
        )
        clean = strip_signal_tags(response_text) if response_text else ""
        if not clean and response_text:
            clean = response_text.strip()
        if clean:
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": clean})
            runtime.save_session_history(
                history, session_key=session_key,
                metadata={"channel": "slack", "sender": sender_name},
            )
            await adapter.send(
                channel_id, clean, agent_id=agent_id, thread_ts=thread_ts or None,
            )
            broadcast_channel_event(
                app, agent_id, "slack", normalized, clean, direction="response",
            )
        return {"ok": True, "response_length": len(response_text or "")}
    except Exception as exc:
        logger.error("Slack webhook failed [%s]: %s", agent_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/channels/{agent_id}")
async def slack_list_channels(agent_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Slack skill not loaded")
    await adapter.sync_channels_from_platform(agent_id)
    return adapter.get_status(agent_id=agent_id)


@router.post("/channels/{agent_id}/sync")
async def slack_sync_channels(agent_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Slack skill not loaded")
    await adapter.sync_channels_from_platform(agent_id)
    return {"ok": True, **adapter.get_status(agent_id=agent_id)}


@router.patch("/channels/{agent_id}/{channel_id}")
async def slack_update_channel(agent_id: str, channel_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Slack skill not loaded")
    try:
        body = await request.json()
    except Exception:
        body = {}
    updated = await adapter.apply_channel_desired(
        agent_id,
        channel_id,
        enabled=bool(body.get("enabled", True)),
        require_mention=body.get("require_mention"),
    )
    return {"ok": True, "scoped_channels": updated.get("scoped_channels", {})}


@router.get("/status/{agent_id}")
async def slack_status(agent_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is not None:
        return adapter.get_status(agent_id=agent_id)
    return {"channel": "slack", "connected": False, "enabled": False}
