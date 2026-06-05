"""Fleet / squad policy — hint tokens, loop nudges, and triage output cleanup.

Intent classification (fleet vs skill vs team) is triage micro-inference only.
This module does not regex-parse user messages to add hints or goals.
"""

from __future__ import annotations

import re
from typing import Any

HINT_FLEET_SQUAD = "fleet:squad_candidate"

HINT_SKILL_SETUP_TOKENS = frozenset({
    "setup:native_skill",
    "setup:configure_bundled",
    "setup:instruction_skill",
    "continuation:configure_not_build",
})

_SKILL_SCAFFOLD_GOAL_RE = re.compile(
    r"\b(?:scaffold|build|create|author|write|implement|install)\b"
    r"[\s\S]{0,80}\b(?:skill|discord-channel|native|bundled)\b",
    re.IGNORECASE,
)

_PRE_SQUAD_ACTIVE_TOOLS = frozenset({
    "squad_setup",
    "channel_inspect",
    "contacts",
})

_IN_SQUAD_LEAD_TOOLS = frozenset({
    "squad",
    "squad_message",
    "squad_escalate",
    "squad_report_done",
    "channel_manage",
    "channel_inspect",
    "discord_send",
})


def squad_role_for_agent(agent_id: str) -> str | None:
    """Return ``lead``, ``member``, or None if not in a squad."""
    if not (agent_id or "").strip():
        return None
    try:
        from server.main import app

        sm = getattr(app.state, "squad_manager", None)
        if sm is None:
            return None
        squad = sm.get_squad_for_agent(agent_id)
        if squad is None:
            return None
        if squad.is_lead(agent_id):
            return "lead"
        return "member"
    except Exception:
        return None


def agent_in_squad(agent_id: str) -> bool:
    return squad_role_for_agent(agent_id) is not None


def strip_skill_setup_hints(hints: list[str]) -> list[str]:
    """Remove skill-setup hints when fleet staffing hint is active."""
    cleaned: list[str] = []
    for hint in hints:
        token = (hint or "").strip().lower()
        if token in HINT_SKILL_SETUP_TOKENS:
            continue
        if token.startswith("active discord channel:"):
            continue
        if token.startswith("native nls skill:"):
            continue
        if token.startswith("clawhub/agentskill setup:"):
            continue
        if "skill_configure(skill_name=" in token and "discord" in token:
            continue
        cleaned.append(hint)
    return cleaned


def strip_skill_scaffold_goals(goals: list[str]) -> list[str]:
    """Drop skill-scaffold goals when triage also emitted fleet:squad_candidate."""
    return [
        g for g in goals
        if g and not _SKILL_SCAFFOLD_GOAL_RE.search(g)
    ]


def fleet_hint_active(hints: list[str] | None) -> bool:
    """True when triage (or WM Task.Hints) includes fleet:squad_candidate."""
    return HINT_FLEET_SQUAD in {
        h.strip().lower() for h in (hints or []) if h and h.strip()
    }


def apply_fleet_hint_policy(
    hints: list[str],
    goals: list[str],
    *,
    agent_id: str = "",
) -> tuple[list[str], list[str]]:
    """Enforce mutual exclusion on triage output — no user-text heuristics."""
    hints = list(hints or [])
    goals = list(goals or [])
    if agent_in_squad(agent_id):
        hints = [
            h for h in hints
            if (h or "").strip().lower() != HINT_FLEET_SQUAD
        ]
        return goals, hints
    if not fleet_hint_active(hints):
        return goals, hints
    return strip_skill_scaffold_goals(goals), strip_skill_setup_hints(hints)


def fleet_active_tool_names(agent_id: str = "") -> frozenset[str]:
    """Tools to pre-unlock when triage emitted fleet:squad_candidate."""
    role = squad_role_for_agent(agent_id)
    if role == "lead":
        return _IN_SQUAD_LEAD_TOOLS
    if role == "member":
        return frozenset({"squad", "squad_escalate", "squad_message", "squad_report_done"})
    return _PRE_SQUAD_ACTIVE_TOOLS


def fleet_squad_team_block_message() -> str:
    return (
        "BLOCKED: team() is for one-run plan delegation waves inside a single task — "
        "not for persistent Discord/community squads.\n"
        "Use squad_setup(action='create', owner_confirmed=true, ...) after ask_user(), "
        "then squad(action='spawn_member', ...) and set_member_job for each role."
    )


def fleet_squad_bootstrap_message() -> str:
    return (
        "[FLEET SQUAD — persistent staffing, not team() waves]\n"
        "You are not in a squad yet. Use squad_setup(action='create', "
        "owner_confirmed=true, ...) after ask_user() confirms structure.\n"
        "There is no squad() tool until the squad exists — do not call squad(action=...).\n"
        "Use channel_inspect(action='get', channel='discord') to see scoped channels.\n"
        "After creation: adopt_orchestration_profile(profile='squad_lead'), "
        "squad(action='spawn_member', ...), squad(action='set_member_job', ...).\n"
        "MULTI FACE member tokens: squad(action='configure_member', target_agent_id=..., "
        "skill='discord-channel', skill_config={bot_token, owner_identity}, "
        "interaction_mode='shared_only' as TOP-LEVEL param, owner_confirmed=true). "
        "Never skill_configure on the lead for member tokens."
    )


def fleet_squad_lead_operations_message() -> str:
    from nls.agentic.channel_tool_policy import (
        DISCORD_PLAN_A_PIPELINE,
        discord_channel_primary_guidance,
    )

    guidance = discord_channel_primary_guidance(_IN_SQUAD_LEAD_TOOLS) or ""
    return (
        "[SQUAD LEAD — Discord multi-face operations]\n"
        "You are the squad lead. squad() and channel_manage(channel='discord') are registered.\n"
        "Do NOT use team() for persistent Discord bots.\n"
        "Plan A — standard pipeline:\n"
        f"  {DISCORD_PLAN_A_PIPELINE}\n"
        f"{guidance}\n"
        "Member config: squad(action='configure_member', ...) with interaction_mode top-level only."
    )


def fleet_squad_member_message() -> str:
    return (
        "[SQUAD MEMBER]\n"
        "You are a squad member — use squad(action='propose'|...) and squad_escalate to the lead. "
        "Discord inbound arrives on your own bot when channel scope is synced and enabled."
    )


def fleet_loop_context_message(agent_id: str, hints: list[str] | None) -> str | None:
    """One system message for fleet/squad context — bootstrap OR lead ops, never both."""
    role = squad_role_for_agent(agent_id)
    if role == "lead":
        return fleet_squad_lead_operations_message()
    if role == "member":
        return fleet_squad_member_message()
    if fleet_hint_active(hints):
        return fleet_squad_bootstrap_message()
    return None
