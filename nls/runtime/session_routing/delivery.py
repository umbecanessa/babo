"""Outbound delivery through Home WS and external surfaces."""

from __future__ import annotations

import logging
from typing import Any

from nls.runtime.session_routing.resolver import resolve_delivery_targets
from nls.runtime.session_routing.surface import is_home_session_key, is_routable_surface_session_key
from nls.runtime.session_routing.types import (
    DeliveryIntent,
    DeliveryOutcome,
    DeliveryTarget,
    RoutingContext,
)

logger = logging.getLogger(__name__)


async def deliver_message(
    runtime: Any,
    cfg: Any,
    *,
    message: str,
    intent: DeliveryIntent,
    ctx: RoutingContext,
    todo_item: Any | None = None,
    connection_manager: Any | None = None,
    user_facing: bool = True,
    autonomous: bool = False,
    source: str = "",
    include_default_home: bool = False,
) -> DeliveryOutcome:
    text = (message or "").strip()
    outcome = DeliveryOutcome()
    if not text:
        outcome.skipped_reason = "empty_message"
        return outcome

    targets = resolve_delivery_targets(
        runtime, cfg, intent=intent, ctx=ctx, todo_item=todo_item,
    )
    if include_default_home:
        home = (cfg.default_home_session_key or "websocket:main").strip()
        if home and home not in {t.session_key for t in targets}:
            targets.append(DeliveryTarget(session_key=home, intent=intent))
    if not targets:
        outcome.skipped_reason = "no_targets"
        return outcome

    agent_id = getattr(runtime, "agent_id", "") or ""
    delivered_any = False

    for target in targets:
        sk = target.session_key
        if is_home_session_key(sk):
            ok = await _deliver_home(
                runtime,
                text,
                session_key=sk,
                connection_manager=connection_manager,
                user_facing=user_facing,
                autonomous=autonomous,
                source=source or ctx.source,
            )
            if ok:
                delivered_any = True
                outcome.home = True
                outcome.targets.append(sk)
            continue

        if is_routable_surface_session_key(sk, runtime):
            ok = await _deliver_surface(runtime, agent_id, sk, text)
            if ok:
                delivered_any = True
                outcome.targets.append(sk)
            else:
                outcome.errors.append(f"surface_send_failed:{sk}")

    outcome.delivered = delivered_any
    if not delivered_any and not outcome.skipped_reason:
        outcome.skipped_reason = "send_failed"
    return outcome


async def _deliver_home(
    runtime: Any,
    message: str,
    *,
    session_key: str,
    connection_manager: Any | None,
    user_facing: bool,
    autonomous: bool,
    source: str,
) -> bool:
    cm = connection_manager
    if cm is None:
        try:
            from server.main import app

            cm = getattr(app.state, "connection_manager", None)
        except Exception:
            cm = None
    if cm is None:
        return False
    home = session_key
    get_home = getattr(runtime, "get_default_home_session_key", None)
    if callable(get_home) and is_home_session_key(session_key):
        home = (get_home() or session_key).strip() or session_key
    try:
        await cm.broadcast(getattr(runtime, "agent_id", ""), {
            "type": "communicate",
            "message": message,
            "user_facing": user_facing,
            "autonomous": autonomous,
            "source": source,
            "session_key": home,
        })
        return True
    except Exception:
        logger.debug("Home communicate broadcast failed", exc_info=True)
        return False


async def _deliver_surface(
    runtime: Any,
    agent_id: str,
    session_key: str,
    message: str,
) -> bool:
    try:
        from server.main import app
        from nls.skills.surface_send import send_surface_message

        result = await send_surface_message(
            app,
            runtime,
            agent_id,
            session_key,
            message,
        )
        return bool(result.get("ok"))
    except Exception:
        logger.debug("Surface delivery failed for %s", session_key, exc_info=True)
        return False


def foreground_ws_session_key(runtime: Any, cfg: Any, websocket_state: Any = None) -> str:
    sk = ""
    if websocket_state is not None:
        sk = getattr(websocket_state, "session_key", "") or ""
    if not sk or sk == "websocket:main":
        home = getattr(cfg, "default_home_session_key", "") or ""
        if home and home != "websocket:main":
            sk = home
        elif callable(getattr(runtime, "get_default_home_session_key", None)):
            sk = runtime.get_default_home_session_key()
    return sk or "websocket:main"
