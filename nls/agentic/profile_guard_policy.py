"""Profile-aware guard strictness for orchestration depth.

Delegates to ``orchestration_profile_spec`` — the single source of truth.
"""

from __future__ import annotations

from nls.agentic.goals import OrchestrationProfile
from nls.agentic.orchestration_profile_spec import (
    get_profile_spec,
    normalize_profile,
)

# Re-export for backward compatibility.
__all__ = [
    "HINT_FORBID_CODE",
    "HINT_FORBID_PLAN",
    "HINT_FORBID_TEAM",
    "HINT_FORBID_TOOLS",
    "EM_COLD_START_GOAL_THRESHOLD",
    "normalize_profile",
    "em_pre_delegate_blocks_enabled",
    "em_cold_start_goal_blocks_enabled",
    "em_static_tool_hints_enabled",
    "solo_static_tool_hints_enabled",
    "skill_discovery_on_stall_enabled",
    "em_assessment_loop_enabled",
    "breadcrumb_rule_matches_profile",
    "normalize_goals_for_profile",
    "apply_structured_hint_caps",
]

# Machine-readable hint tokens triage may emit (language-agnostic downstream).
HINT_FORBID_TOOLS = frozenset({
    "forbid:tools", "conversational_only", "orchestration:conversational",
})
HINT_FORBID_TEAM = frozenset({
    "forbid:team", "forbid:teams", "forbid:delegate", "forbid:delegates",
    "forbid:subagent", "forbid:subagents", "orchestration:solo",
})
HINT_FORBID_CODE = frozenset({
    "forbid:code", "forbid:repos", "orchestration:direct",
})
HINT_FORBID_PLAN = frozenset({
    "forbid:plan", "orchestration:delegate_only",
})

EM_COLD_START_GOAL_THRESHOLD = 3


def em_pre_delegate_blocks_enabled(
    profile: str | None,
    *,
    plan_requires_team_delegation: bool,
) -> bool:
    spec = get_profile_spec(profile)
    if plan_requires_team_delegation:
        return spec.em_pre_delegate_blocks
    return spec.em_pre_delegate_blocks and normalize_profile(profile) == "orchestrated"


def em_cold_start_goal_blocks_enabled(profile: str | None) -> bool:
    return get_profile_spec(profile).em_cold_start_goal_blocks


def em_static_tool_hints_enabled(profile: str | None) -> bool:
    return get_profile_spec(profile).em_static_tool_hints


def solo_static_tool_hints_enabled(profile: str | None) -> bool:
    return get_profile_spec(profile).solo_static_tool_hints


def skill_discovery_on_stall_enabled(profile: str | None) -> bool:
    return get_profile_spec(profile).skill_discovery_on_stall


def em_assessment_loop_enabled(profile: str | None) -> bool:
    return get_profile_spec(profile).em_assessment_loop


def breadcrumb_rule_matches_profile(
    rule_profiles: frozenset[str] | None,
    profile: str | None,
) -> bool:
    if not rule_profiles:
        return True
    return normalize_profile(profile) in rule_profiles


def normalize_goals_for_profile(
    goals: list[str],
    profile: str | None,
) -> list[str]:
    if not goals:
        return goals
    p = normalize_profile(profile)
    if p == "orchestrated" or len(goals) < EM_COLD_START_GOAL_THRESHOLD:
        return goals
    if p == "conversational":
        return []
    if p in ("solo_structured", "direct_tool"):
        primary = goals[0].strip()
        return [primary] if primary else goals[:1]
    return goals


def apply_structured_hint_caps(profile: str, hints: list[str]) -> str:
    tokens = {h.strip().lower() for h in hints if h and h.strip()}
    if tokens & HINT_FORBID_TOOLS:
        return "conversational"
    if profile == "orchestrated":
        if tokens & HINT_FORBID_TEAM:
            return "solo_structured"
        if tokens & HINT_FORBID_CODE:
            return "solo_structured"
    return profile


def tools_denied_by_hints(hints: list[str] | None) -> frozenset[str]:
    """Extra tool denylist from structured triage hints (language-agnostic)."""
    tokens = {h.strip().lower() for h in (hints or []) if h and h.strip()}
    denied: set[str] = set()
    if tokens & HINT_FORBID_PLAN:
        denied.update({"plan", "todo"})
    return frozenset(denied)


def inject_prompt_structured_hints(user_input: str, hints: list[str]) -> None:
    """Add machine hints from explicit prompt constraints (no keyword lists)."""
    low = (user_input or "").lower()
    tokens = {h.strip().lower() for h in hints if h and h.strip()}
    delegate_only = (
        "only delegate" in low
        or ("do not implement" in low and "delegate" in low)
        or ("using delegate" in low and "do not implement" in low)
    )
    if delegate_only and not (tokens & HINT_FORBID_PLAN):
        hints.append("forbid:plan")
