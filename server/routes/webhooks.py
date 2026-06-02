"""Webhook endpoints for external integrations.

Provides HTTP endpoints that external services (Telegram, Slack,
generic webhooks) can POST to, routing messages to the appropriate
agent's chat handler.

For Telegram specifically, supports both:
    - Polling mode (agent runs a polling loop internally)
    - Webhook mode (Telegram POSTs updates to /webhooks/telegram/<agent_id>)

Configuration via environment variables:
    TELEGRAM_BOT_TOKEN     Bot token from @BotFather
    TELEGRAM_CHAT_ID       Default chat ID (optional)
    NLS_WEBHOOK_SECRET     Shared secret for webhook validation (optional)

Usage:
    Include this router in the FastAPI app::

        from server.routes.webhooks import router
        app.include_router(router, prefix="/webhooks")
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API = "https://api.telegram.org"
WEBHOOK_SECRET = os.environ.get("NLS_WEBHOOK_SECRET", "")


# ===================================================================
# Telegram Integration
# ===================================================================


async def telegram_send_message(
    chat_id: int | str,
    text: str,
    token: str | None = None,
) -> dict:
    """Send a message via Telegram Bot API."""
    bot_token = token or TELEGRAM_BOT_TOKEN
    if not bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"

    # Telegram max message length is 4096
    if len(text) > 4096:
        text = text[:4090] + "\n[...]"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        })
        resp.raise_for_status()
        return resp.json()


async def telegram_get_updates(
    token: str | None = None,
    offset: int | None = None,
    timeout: int = 30,
) -> list[dict]:
    """Long-poll for Telegram updates."""
    bot_token = token or TELEGRAM_BOT_TOKEN
    if not bot_token:
        return []

    url = f"{TELEGRAM_API}/bot{bot_token}/getUpdates"
    params: dict[str, Any] = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset

    async with httpx.AsyncClient(timeout=timeout + 10) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", [])


@router.post("/telegram/{agent_id}")
async def telegram_webhook(agent_id: str, request: Request):
    """Receive Telegram webhook updates for a specific agent.

    Telegram sends POST requests with update JSON.  We extract the
    message text, route it to the agent, and send the response back.
    """
    app = request.app

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message = body.get("message", {})
    text = message.get("text", "")
    chat_id = message.get("chat", {}).get("id")

    if not text or not chat_id:
        return {"ok": True, "status": "no_message"}

    logger.info(
        "Telegram webhook for agent %s: chat_id=%s, text=%s",
        agent_id, chat_id, text[:100],
    )

    # Route to agent
    agent_manager = getattr(app.state, "agent_manager", None)
    if agent_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager not ready")

    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(
            status_code=404, detail=f"Agent {agent_id} not found",
        )

    # Process message through the agent
    try:
        result = runtime.process_message(text, history=None)
        response_text = result.get("response", "")

        if response_text:
            await telegram_send_message(chat_id, response_text)

        return {"ok": True, "response_length": len(response_text)}
    except Exception as exc:
        logger.error(
            "Telegram webhook processing failed for agent %s: %s",
            agent_id, exc, exc_info=True,
        )
        try:
            await telegram_send_message(
                chat_id,
                "Sorry, I encountered an error processing your message.",
            )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/telegram/{agent_id}/setup")
async def telegram_setup(agent_id: str, request: Request):
    """Set up Telegram webhook for an agent.

    POST body: {"bot_token": "...", "chat_id": 123456}

    Stores the credentials and optionally sets the Telegram webhook URL.
    """
    app = request.app

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    bot_token = body.get("bot_token", "")
    chat_id = body.get("chat_id")
    webhook_url = body.get("webhook_url", "")

    if not bot_token:
        raise HTTPException(status_code=400, detail="bot_token required")

    agent_manager = getattr(app.state, "agent_manager", None)
    if agent_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager not ready")

    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(
            status_code=404, detail=f"Agent {agent_id} not found",
        )

    # Store credentials in agent config
    integrations = runtime.config.setdefault("integrations", {})
    integrations["telegram"] = {
        "bot_token": bot_token,
        "chat_id": chat_id,
        "webhook_url": webhook_url,
        "enabled": True,
    }

    # Save config
    config_path = runtime.agent_dir / "config" / "runtime.json"
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                config_data = json.load(f)
            config_data.setdefault("integrations", {})["telegram"] = integrations["telegram"]
            with open(config_path, "w") as f:
                json.dump(config_data, f, indent=2)
        except Exception as exc:
            logger.warning("Failed to persist telegram config: %s", exc)

    # If webhook_url provided, set it with Telegram
    if webhook_url:
        try:
            url = f"{TELEGRAM_API}/bot{bot_token}/setWebhook"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json={
                    "url": f"{webhook_url}/webhooks/telegram/{agent_id}",
                })
                resp.raise_for_status()
                logger.info(
                    "Telegram webhook set for agent %s: %s",
                    agent_id, webhook_url,
                )
        except Exception as exc:
            logger.error("Failed to set Telegram webhook: %s", exc)
            return {
                "ok": True,
                "webhook_set": False,
                "error": str(exc),
                "credentials_stored": True,
            }

    return {
        "ok": True,
        "credentials_stored": True,
        "webhook_set": bool(webhook_url),
        "chat_id": chat_id,
    }


# ===================================================================
# Generic Webhook
# ===================================================================


@router.post("/generic/{agent_id}")
async def generic_webhook(agent_id: str, request: Request):
    """Generic webhook endpoint for any external service.

    Accepts POST with JSON body containing a "message" field.
    Routes to the agent and returns the response.

    Optional: include "secret" field for validation against
    NLS_WEBHOOK_SECRET env var.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Validate secret if configured
    if WEBHOOK_SECRET:
        provided_secret = body.get("secret", "")
        if not hmac.compare_digest(provided_secret, WEBHOOK_SECRET):
            raise HTTPException(status_code=403, detail="Invalid secret")

    message = body.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="message field required")

    app = request.app
    agent_manager = getattr(app.state, "agent_manager", None)
    if agent_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager not ready")

    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(
            status_code=404, detail=f"Agent {agent_id} not found",
        )

    try:
        result = runtime.process_message(message, history=None)
        return {
            "ok": True,
            "response": result.get("response", ""),
            "signals": result.get("signals", []),
            "hormones": result.get("hormones", {}),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ===================================================================
# Health / Status
# ===================================================================


@router.get("/status")
async def webhook_status():
    """Check webhook endpoint status and configuration."""
    return {
        "ok": True,
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN),
        "webhook_secret_configured": bool(WEBHOOK_SECRET),
    }
