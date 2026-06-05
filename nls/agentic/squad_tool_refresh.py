"""Refresh agentic loop tool surface after squad_setup creates a squad."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

SQUAD_RUNTIME_TOOL_NAMES = frozenset({
    "squad",
    "squad_escalate",
    "squad_message",
    "squad_report_done",
})
SQUAD_SETUP_TOOL_NAME = "squad_setup"

POST_SQUAD_SETUP_NUDGE = (
    "[SQUAD CREATED — squad() tools are now registered on this agent]\n"
    "Persistent members: squad(action='spawn_member', name='...', job_title='...', "
    "mission='...'), then squad(action='set_member_job', ...).\n"
    "Do NOT use team() for permanent Discord mod/QA staffing — team() is one-run waves only.\n"
    "MULTI FACE: one squad(action='configure_member', ...) per member — "
    "skill_config={bot_token, owner_identity}, interaction_mode='shared_only' "
    "(top-level, not in skill_config), owner_confirmed=true. "
    "Then sync_member_channels if scope empty. Test members: discord_send in scoped "
    "channel with @mention of member bot_id (lead sends as self; squad bots hear each other)."
)


def _parse_tool_action(args_raw: str | dict) -> str:
    try:
        parsed = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        if isinstance(parsed, dict):
            return (parsed.get("action") or "").strip().lower()
    except Exception:
        pass
    return ""


def is_successful_squad_setup_create(
    tool_name: str,
    args_raw: str | dict,
    result: Any,
) -> bool:
    if tool_name != SQUAD_SETUP_TOOL_NAME:
        return False
    if getattr(result, "is_error", True):
        return False
    return _parse_tool_action(args_raw) == "create"


def refresh_agentic_tools_after_squad_setup(
    agent_id: str,
    tools: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Sync runtime squad tools into the loop executor dict. Returns (added, removed)."""
    if not agent_id:
        return [], []
    try:
        from server.main import app

        am = app.state.agent_manager
        rt = am.get_runtime(agent_id)
        if rt is None:
            return [], []
        if hasattr(rt, "sync_squad_tools"):
            rt.sync_squad_tools()
    except Exception as exc:
        logger.warning(
            "squad tool refresh: sync failed for %s: %s", agent_id, exc,
        )
        return [], []

    added: list[str] = []
    for tool in getattr(rt, "_agent_tools", None) or []:
        name = getattr(tool, "name", "") or ""
        if not name:
            continue
        tools[name] = tool
        if name in SQUAD_RUNTIME_TOOL_NAMES:
            added.append(name)

    removed: list[str] = []
    if SQUAD_SETUP_TOOL_NAME in tools:
        del tools[SQUAD_SETUP_TOOL_NAME]
        removed.append(SQUAD_SETUP_TOOL_NAME)

    if added or removed:
        logger.info(
            "Agent %s: squad tool refresh — added %s removed %s",
            agent_id, sorted(added), sorted(removed),
        )
    return added, removed


def merge_squad_tool_schemas(
    schemas: list[dict],
    tools: dict[str, Any],
    *,
    added: list[str],
    removed: list[str],
) -> list[dict]:
    """Return schema list with squad_setup removed and squad tools appended."""
    drop = set(removed)
    merged = [
        s for s in schemas
        if (s.get("function") or {}).get("name", "") not in drop
    ]
    present = {
        (s.get("function") or {}).get("name", "")
        for s in merged
    }
    from nls.tools.agent_tools.base import AgentTool, tool_to_openai_schema

    for name in added:
        if name in present:
            continue
        tool = tools.get(name)
        if isinstance(tool, AgentTool):
            merged.append(tool_to_openai_schema(tool))
            present.add(name)
    return merged


def apply_if_squad_setup_created(
    tool_name: str,
    args_raw: str | dict,
    result: Any,
    *,
    agent_id: str,
    tools: dict[str, Any],
    all_schemas: list[dict],
    all_unlocked: set[str],
    base_schemas: list[dict] | None = None,
    state: Any | None = None,
) -> str | None:
    """Refresh loop tool surface after squad_setup(create). Returns nudge text or None."""
    if not is_successful_squad_setup_create(tool_name, args_raw, result):
        return None

    added, removed = refresh_agentic_tools_after_squad_setup(agent_id, tools)
    if not added and SQUAD_SETUP_TOOL_NAME not in removed:
        return None

    for target in (all_schemas, base_schemas):
        if target is None:
            continue
        merged = merge_squad_tool_schemas(
            target, tools, added=added, removed=removed,
        )
        target[:] = merged
    all_unlocked.difference_update(removed)
    all_unlocked.update(added)

    if state is not None:
        try:
            from nls.agentic.orchestration_policy import invalidate_tool_policy_cache

            invalidate_tool_policy_cache(state)
            state.unlocked_tools.difference_update(removed)
            state.unlocked_tools.update(added)
        except Exception:
            pass

    return POST_SQUAD_SETUP_NUDGE


def ensure_squad_tools_in_loop(
    agent_id: str,
    tools: dict[str, Any],
    all_schemas: list[dict],
    all_unlocked: set[str],
    *,
    base_schemas: list[dict] | None = None,
    state: Any | None = None,
) -> bool:
    """If agent belongs to a squad, ensure runtime squad tools are in the loop dict."""
    if not agent_id:
        return False
    try:
        from server.main import app

        sm = getattr(app.state, "squad_manager", None)
        if sm is None or sm.get_squad_for_agent(agent_id) is None:
            return False
    except Exception:
        return False

    if "squad" in tools and SQUAD_SETUP_TOOL_NAME not in tools:
        return False

    added, removed = refresh_agentic_tools_after_squad_setup(agent_id, tools)
    if not added and SQUAD_SETUP_TOOL_NAME not in removed:
        return False

    for target in (all_schemas, base_schemas):
        if target is None:
            continue
        target[:] = merge_squad_tool_schemas(
            target, tools, added=added, removed=removed,
        )
    all_unlocked.difference_update(removed)
    all_unlocked.update(added)
    if state is not None:
        try:
            from nls.agentic.orchestration_policy import invalidate_tool_policy_cache

            invalidate_tool_policy_cache(state)
            state.unlocked_tools.difference_update(removed)
            state.unlocked_tools.update(added)
        except Exception:
            pass
    return True
