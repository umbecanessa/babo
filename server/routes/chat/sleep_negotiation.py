"""Drowsy sleep negotiation — confirm/deny via command or natural-language."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_AFFIRMATIVE = re.compile(
    r"^(?:yes|yep|yeah|yup|sure|ok(?:ay)?|go ahead|rest up|"
    r"do it|affirmative|sounds good|that'?s fine|please do)\b",
    re.IGNORECASE,
)
_DENIAL = re.compile(
    r"^(?:no|nope|nah|don't|do not|stay awake|deny|not now|wait|later)\b",
    re.IGNORECASE,
)


def classify_sleep_response(text: str) -> str | None:
    """Return ``confirm``, ``deny``, or None for a short user reply."""
    text = (text or "").strip()
    if not text:
        return None
    if _DENIAL.match(text):
        return "deny"
    if _AFFIRMATIVE.match(text) or text.lower() in {
        "y", "yes.", "ok.", "sleep", "nap",
    }:
        return "confirm"
    return None


def _get_inner_loop(app: Any, agent_id: str):
    from server.routes.chat.commands import _get_inner_loop

    return _get_inner_loop(app, agent_id)


async def apply_sleep_confirm(
    app: Any,
    agent_id: str,
    websocket: WebSocket,
    *,
    source: str = "command",
) -> bool:
    inner_loop = _get_inner_loop(app, agent_id)
    if inner_loop is None or not inner_loop.is_drowsy:
        logger.info(
            "Agent %s: sleep confirm ignored (%s) — not drowsy "
            "(has_loop=%s)",
            agent_id,
            source,
            inner_loop is not None,
        )
        await websocket.send_json({
            "type": "sleep_command_result",
            "ok": False,
            "action": "confirm",
            "content": "Agent is not drowsy — nothing to confirm.",
        })
        return False

    inner_loop.confirm_sleep()
    await websocket.send_json({
        "type": "status",
        "agent_status": "sleeping",
        "sleep_reason": "User confirmed drowsy request",
    })
    await websocket.send_json({
        "type": "sleep_command_result",
        "ok": True,
        "action": "confirm",
    })
    logger.info("Agent %s: user confirmed sleep (%s)", agent_id, source)
    return True


async def apply_sleep_deny(
    app: Any,
    agent_id: str,
    websocket: WebSocket,
    *,
    source: str = "command",
) -> bool:
    inner_loop = _get_inner_loop(app, agent_id)
    if inner_loop is None or not inner_loop.is_drowsy:
        logger.info(
            "Agent %s: sleep deny ignored (%s) — not drowsy "
            "(has_loop=%s)",
            agent_id,
            source,
            inner_loop is not None,
        )
        await websocket.send_json({
            "type": "sleep_command_result",
            "ok": False,
            "action": "deny",
            "content": "Agent is not drowsy — nothing to deny.",
        })
        return False

    inner_loop.deny_sleep()
    await websocket.send_json({
        "type": "status",
        "agent_status": "alive",
        "content": "Sleep denied — staying awake.",
    })
    await websocket.send_json({
        "type": "sleep_command_result",
        "ok": True,
        "action": "deny",
    })
    logger.info("Agent %s: user denied sleep (%s)", agent_id, source)
    return True


async def try_handle_drowsy_text(
    app: Any,
    agent_id: str,
    websocket: WebSocket,
    text: str,
    *,
    source: str,
) -> bool:
    """If the agent is drowsy and *text* is yes/no, confirm or deny sleep."""
    kind = classify_sleep_response(text)
    if kind is None:
        return False

    inner_loop = _get_inner_loop(app, agent_id)
    if inner_loop is None or not inner_loop.is_drowsy:
        return False

    if kind == "confirm":
        return await apply_sleep_confirm(
            app, agent_id, websocket, source=source,
        )
    return await apply_sleep_deny(
        app, agent_id, websocket, source=source,
    )
