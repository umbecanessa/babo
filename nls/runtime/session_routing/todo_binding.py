"""Todo report session binding via session router."""

from __future__ import annotations

import logging
from typing import Any

from nls.runtime.session_routing.types import DeliveryIntent, RoutingContext

logger = logging.getLogger(__name__)


def ensure_todo_report_session(
    runtime: Any,
    router: Any,
    todo_item: Any,
    *,
    origin_session_key: str = "",
) -> str:
    if todo_item is None:
        return ""

    direct = str(getattr(todo_item, "report_session_key", "") or "").strip()
    if direct:
        return direct

    ctx = router.routing_context_from_runtime(
        origin_session_key=origin_session_key,
        todo_title=str(getattr(todo_item, "title", "") or ""),
        todo_description=str(getattr(todo_item, "description", "") or ""),
    )
    keys = router.resolve_report_keys(ctx=ctx, todo_item=todo_item)
    if not keys:
        return ""

    resolved = keys[0]
    from nls.runtime.session_routing.surface import is_routable_surface_session_key

    if not is_routable_surface_session_key(resolved, runtime):
        return ""
    todo_id = str(getattr(todo_item, "id", "") or "").strip()
    if not todo_id:
        return resolved

    try:
        from server.main import app

        skill_loader = getattr(app.state, "skill_loader", None)
        if skill_loader is None:
            return resolved
        todo_skill = skill_loader.skills.get("todo-list")
        if todo_skill is None or todo_skill.context is None:
            return resolved
        mgr = getattr(todo_skill.context, "adapter", None)
        if mgr is None:
            return resolved
        store = mgr.get_store(getattr(runtime, "agent_id", ""))
        updated = store.update(todo_id, report_session_key=resolved)
        if updated is not None:
            logger.info(
                "Agent %s: todo [%s] report_session_key → %s",
                getattr(runtime, "agent_id", "?"),
                todo_id,
                resolved,
            )
    except Exception:
        logger.debug("ensure_todo_report_session persist failed", exc_info=True)

    return resolved
