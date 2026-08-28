"""Explicit resolution of where todo / background completions should be reported.

Delegates to the unified session router — no registry guessing.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _load_todo_item(agent_id: str, todo_id: str) -> Any | None:
    if not agent_id or not todo_id:
        return None
    try:
        from server.main import app

        skill_loader = getattr(app.state, "skill_loader", None)
        if skill_loader is None:
            return None
        todo_skill = skill_loader.skills.get("todo-list")
        if todo_skill is None or todo_skill.context is None:
            return None
        mgr = getattr(todo_skill.context, "adapter", None)
        if mgr is None:
            return None
        store = mgr.get_store(agent_id)
        return store.get(todo_id)
    except Exception:
        return None


def resolve_explicit_report_session_key(
    rt: Any,
    *,
    todo_item: Any | None = None,
    prompt: str = "",
    final_response: str = "",
    title: str = "",
    description: str = "",
    notes: str = "",
    todo_id: str | None = None,
) -> str | None:
    from nls.runtime.session_routing import get_session_router

    if todo_id and todo_item is None:
        todo_item = _load_todo_item(getattr(rt, "agent_id", ""), todo_id)

    router = get_session_router(rt)
    ctx = router.routing_context_from_runtime(
        prompt=prompt or final_response,
        todo_title=title or (getattr(todo_item, "title", "") if todo_item else ""),
        todo_description=description or (getattr(todo_item, "description", "") if todo_item else ""),
    )
    keys = router.resolve_report_keys(ctx=ctx, todo_item=todo_item, prompt=prompt or final_response)
    return keys[0] if keys else None


def ensure_todo_report_session_key(rt: Any, todo_item: Any) -> str:
    from nls.runtime.session_routing import get_session_router

    return get_session_router(rt).bind_todo_report_session(todo_item)
