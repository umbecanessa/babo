"""Agent-attached session router facade."""

from __future__ import annotations

import logging
from typing import Any

from nls.runtime.session_routing.config import (
    SessionRoutingConfig,
    is_valid_reachability_session_key,
    load_session_routing_config,
    save_session_routing_config,
)
from nls.runtime.session_routing.delivery import deliver_message, foreground_ws_session_key
from nls.runtime.session_routing.resolver import resolve_delivery_targets, resolve_report_session_keys
from nls.runtime.session_routing.types import DeliveryIntent, DeliveryOutcome, RoutingContext

logger = logging.getLogger(__name__)


class AgentSessionRouter:
    """Unified session routing authority for one agent runtime."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self._cfg: SessionRoutingConfig | None = None

    @property
    def runtime(self) -> Any:
        return self._runtime

    def config(self) -> SessionRoutingConfig:
        if self._cfg is None:
            agent_dir = getattr(self._runtime, "agent_dir", None)
            if agent_dir is None:
                self._cfg = SessionRoutingConfig()
            else:
                self._cfg = load_session_routing_config(agent_dir, self._runtime)
        return self._cfg

    def refresh(self) -> SessionRoutingConfig:
        self._cfg = None
        return self.config()

    def save(self) -> None:
        agent_dir = getattr(self._runtime, "agent_dir", None)
        if agent_dir is None:
            return
        save_session_routing_config(agent_dir, self.config())

    def set_primary_reachability(self, session_key: str) -> bool:
        sk = (session_key or "").strip()
        if not is_valid_reachability_session_key(sk, self._runtime):
            return False
        cfg = self.config()
        cfg.primary_reachability_session_key = sk
        self.save()
        logger.info(
            "Agent %s: primary reachability → %s",
            getattr(self._runtime, "agent_id", "?"),
            sk,
        )
        return True

    def clear_primary_reachability(self) -> bool:
        cfg = self.config()
        cfg.primary_reachability_session_key = cfg.default_home_session_key
        self.save()
        return True

    def routing_context_from_runtime(
        self,
        *,
        source: str = "",
        origin_session_key: str = "",
        todo_id: str = "",
        todo_title: str = "",
        todo_description: str = "",
        prompt: str = "",
        explicit_targets: list[str] | None = None,
        broadcast: bool = False,
    ) -> RoutingContext:
        return RoutingContext(
            source=source or getattr(self._runtime, "_foreground_source", "") or "",
            foreground_session_key=getattr(self._runtime, "_foreground_session_key", "") or "",
            foreground_source=getattr(self._runtime, "_foreground_source", "") or "",
            origin_session_key=origin_session_key or self._active_origin_session_key(),
            todo_id=todo_id,
            todo_title=todo_title,
            todo_description=todo_description,
            prompt=prompt,
            explicit_targets=list(explicit_targets or []),
            broadcast=broadcast,
        )

    def _active_origin_session_key(self) -> str:
        src = getattr(self._runtime, "_foreground_source", "") or ""
        sk = getattr(self._runtime, "_foreground_session_key", "") or ""
        if src.startswith("user:channel") and sk:
            return sk
        return ""

    def resolve_report_keys(
        self,
        *,
        ctx: RoutingContext | None = None,
        todo_item: Any | None = None,
        prompt: str = "",
    ) -> list[str]:
        if ctx is None:
            ctx = self.routing_context_from_runtime(prompt=prompt)
        elif prompt:
            ctx = RoutingContext(
                **{**ctx.__dict__, "prompt": prompt},
            )
        return resolve_report_session_keys(
            self._runtime, self.config(), ctx=ctx, todo_item=todo_item,
        )

    def resolve_targets(
        self,
        intent: DeliveryIntent,
        *,
        ctx: RoutingContext | None = None,
        todo_item: Any | None = None,
    ):
        if ctx is None:
            ctx = self.routing_context_from_runtime()
        return resolve_delivery_targets(
            self._runtime, self.config(), intent=intent, ctx=ctx, todo_item=todo_item,
        )

    async def deliver(
        self,
        message: str,
        intent: DeliveryIntent,
        *,
        ctx: RoutingContext | None = None,
        todo_item: Any | None = None,
        connection_manager: Any | None = None,
        user_facing: bool = True,
        autonomous: bool = False,
        source: str = "",
        include_default_home: bool = False,
    ) -> DeliveryOutcome:
        if ctx is None:
            ctx = self.routing_context_from_runtime(source=source)
        return await deliver_message(
            self._runtime,
            self.config(),
            message=message,
            intent=intent,
            ctx=ctx,
            todo_item=todo_item,
            connection_manager=connection_manager,
            user_facing=user_facing,
            autonomous=autonomous,
            source=source or ctx.source,
            include_default_home=include_default_home,
        )

    def ws_session_key(self, websocket_state: Any = None) -> str:
        return foreground_ws_session_key(
            self._runtime, self.config(), websocket_state,
        )

    def bind_todo_report_session(
        self,
        todo_item: Any,
        *,
        origin_session_key: str = "",
    ) -> str:
        from nls.runtime.session_routing.todo_binding import ensure_todo_report_session

        return ensure_todo_report_session(
            self._runtime,
            self,
            todo_item,
            origin_session_key=origin_session_key,
        )


def get_session_router(runtime: Any) -> AgentSessionRouter:
    router = getattr(runtime, "_session_router", None)
    # MagicMock and other stand-ins are truthy but are not routers.
    if not isinstance(router, AgentSessionRouter):
        router = AgentSessionRouter(runtime)
        try:
            runtime._session_router = router
        except Exception:
            pass
    return router
