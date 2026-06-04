"""Fleet / squad policy — hint tokens, loop nudges, and triage output cleanup.

Intent classification (fleet vs skill vs team) is triage micro-inference only.
This module does not regex-parse user messages to add hints or goals.
"""

from __future__ import annotations

import re

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
) -> tuple[list[str], list[str]]:
    """Enforce mutual exclusion on triage output — no user-text heuristics."""
    if not fleet_hint_active(hints):
        return goals, hints
    return strip_skill_scaffold_goals(goals), strip_skill_setup_hints(hints)


def fleet_squad_team_block_message() -> str:
    return (
        "BLOCKED: team() is for one-run plan delegation waves inside a single task — "
        "not for persistent Discord/community squads.\n"
        "Use squad_setup(action='create', owner_confirmed=true, ...) after ask_user(), "
        "then squad(action='spawn_member', ...) and set_member_job for each role."
    )
