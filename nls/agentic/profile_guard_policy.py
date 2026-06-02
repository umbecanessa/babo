"""Profile-aware guard strictness for orchestration depth.

Delegates to ``orchestration_profile_spec`` — the single source of truth.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from nls.agentic.goals import OrchestrationProfile
from nls.agentic.orchestration_profile_spec import (
    get_profile_spec,
    normalize_profile,
)

logger = logging.getLogger(__name__)

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
    "reconcile_triage_orchestration_depth",
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

HINT_INSTRUCTION_SKILL_SETUP = frozenset({
    "setup:instruction_skill",
})

HINT_NATIVE_SKILL_SETUP = frozenset({
    "setup:native_skill",
})

_CONFIGURE_INTENT_RE = re.compile(
    r"\b(configure|set\s*up)\s+(?:the\s+)?(?:skill|bot|integration|channel|installed)\b"
    r"|\b(?:skill|bot|integration)\s+(?:token|setup|configure|credentials?)\b"
    r"|\b(?:bot|api)\s+token\b"
    r"|\bcredential(?:s)?\s*:\s*\S",
    re.I,
)

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
    if p in ("solo_structured",):
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


def reconcile_triage_orchestration_depth(
    *,
    profile: str,
    goals: list[str],
    hints: list[str],
    intent: str,
) -> tuple[str, list[str]]:
    """Fix contradictory triage JSON (classifier errors, not user-message heuristics).

    Common failure: TASK_THINK with 3+ coarse goals for a platform build plus
    spurious forbid:team while profile is solo_structured — that blocks EM cold
    start. Honor explicit solo caps (orchestration:solo); otherwise prefer
    orchestrated when goal count implies multi-phase engineering work.
    """
    p = normalize_profile(profile)
    if p == "conversational" or not goals:
        return p, hints

    intent_u = (intent or "").upper()
    if not intent_u.startswith("TASK"):
        return p, hints

    tokens = {h.strip().lower() for h in hints if h and h.strip()}
    explicit_solo = "orchestration:solo" in tokens
    team_forbidden = bool(tokens & HINT_FORBID_TEAM)
    n_goals = len(goals)

    if explicit_solo or n_goals < EM_COLD_START_GOAL_THRESHOLD:
        return p, hints

    if p in ("solo_structured",) and team_forbidden:
        cleaned = [
            h for h in hints
            if h.strip().lower() not in HINT_FORBID_TEAM
        ]
        logger.info(
            "Turn triage reconcile: %d goals + spurious team-forbid hints "
            "→ profile=orchestrated",
            n_goals,
        )
        return "orchestrated", cleaned

    return p, hints


_PROSE_ONLY_TOOL_DENY = frozenset({
    "web_search", "web_fetch", "browser", "read", "list_dir", "grep", "glob",
    "semantic_search", "screenshot", "clawhub", "discover_tools",
    "skill_configure", "crystallize_skill", "mcp_manage",
    "bash", "write", "edit", "delete_file", "move_file",
    "server_install", "project_install", "plan", "todo", "team", "delegate",
    "scheduler", "switch_mode", "offer_download",
})


def tools_denied_by_hints(hints: list[str] | None) -> frozenset[str]:
    """Extra tool denylist from structured triage hints (language-agnostic)."""
    tokens = {h.strip().lower() for h in (hints or []) if h and h.strip()}
    denied: set[str] = set()
    if tokens & HINT_FORBID_PLAN:
        denied.update({"plan", "todo"})
    if tokens & HINT_FORBID_TOOLS:
        denied.update(_PROSE_ONLY_TOOL_DENY)
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


_EXECUTION_MODE_RE = re.compile(
    r"\b(?:switch\s+to\s+execution|execution\s+mode|unlock\s+bash|enable\s+bash)\b",
    re.IGNORECASE,
)
_CONTINUATION_RE = re.compile(
    r"^\s*(?:ok\s+done|done|proceed(?:\s+then)?|continue|retry|go\s+ahead|"
    r"try\s+again|yes|yep|please\s+do)\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_TASK_CONTEXT_RE = re.compile(
    r"\b(?:discord|setup|configure|install|bash|skill\.md|bot\s+token|"
    r"guild|server\s+structure|discord-admin)\b",
    re.IGNORECASE,
)


def conversational_tool_surface(
    user_input: str,
    *,
    history: list[dict] | None = None,
    intent: str = "",
) -> str:
    """Tool surface for conversational profile: chat (lookup) vs executing (bash/write).

    Orchestration *profile* stays conversational — this only selects AgentMode.
    """
    ui = (user_input or "").strip()
    if not ui:
        return "chat"
    recent_text = ui
    if history:
        for turn in history[-8:]:
            if turn.get("role") in ("user", "assistant"):
                recent_text += "\n" + (turn.get("content") or "")[:500]
    has_task_context = bool(_TASK_CONTEXT_RE.search(recent_text))
    wants_execution = (
        bool(_EXECUTION_MODE_RE.search(ui))
        or _message_implies_shell_work(ui)
    )
    is_continuation = bool(_CONTINUATION_RE.match(ui)) and has_task_context
    intent_u = (intent or "").upper()
    if wants_execution or is_continuation:
        return "executing"
    if intent_u.startswith("TASK"):
        return "executing"
    return "chat"


def boost_triage_for_work_continuation(
    triage: Any,
    user_input: str,
    *,
    history: list[dict] | None = None,
) -> None:
    """Ensure agentic + shell tools without upgrading orchestration profile depth."""
    ui = (user_input or "").strip()
    if not ui:
        return
    surface = conversational_tool_surface(
        ui, history=history, intent=getattr(triage, "intent", ""),
    )
    if surface != "executing":
        return
    # Profile unchanged: conversational quick-task path, not solo_structured.
    triage.intent = "TASK_THINK"
    triage.thinking = True
    recent_text = ui
    if history:
        for turn in history[-8:]:
            if turn.get("role") in ("user", "assistant"):
                recent_text += "\n" + (turn.get("content") or "")[:500]
    has_task_context = bool(_TASK_CONTEXT_RE.search(recent_text))
    is_continuation = bool(_CONTINUATION_RE.match(ui)) and has_task_context
    if is_continuation and not triage.goals:
        triage.goals = ["Continue the in-progress task"]
    hints = list(triage.hints or [])
    hint_tokens = {h.strip().lower() for h in hints if h}
    if has_task_context and not (hint_tokens & HINT_NATIVE_SKILL_SETUP):
        from nls.skills_setup_policy import looks_like_native_skill_authoring

        if looks_like_native_skill_authoring(recent_text):
            if "setup:native_skill" not in hint_tokens:
                hints.append("setup:native_skill")
        elif "setup:instruction_skill" not in hint_tokens:
            hints.append("setup:instruction_skill")
    triage.hints = hints


def _message_implies_shell_work(text: str) -> bool:
    low = (text or "").lower()
    return any(
        m in low
        for m in (
            "bash", "powershell", "run the script", "discord-admin",
            "set up discord", "setup discord", "create channel",
        )
    )


def enrich_instruction_skill_hints(
    user_input: str,
    goals: list[str] | None,
    hints: list[str],
) -> None:
    """Add setup:instruction_skill when user is configuring an AgentSkill/ClawHub pkg."""
    tokens = {h.strip().lower() for h in hints if h and h.strip()}
    if tokens & (HINT_INSTRUCTION_SKILL_SETUP | HINT_NATIVE_SKILL_SETUP):
        return
    blob = f"{user_input or ''} {' '.join(goals or [])}"
    from nls.skills_setup_policy import looks_like_native_skill_authoring

    if looks_like_native_skill_authoring(blob):
        return
    if not _CONFIGURE_INTENT_RE.search(blob):
        return
    hints.append("setup:instruction_skill")
    hints.append(
        "ClawHub/AgentSkill setup: read installed SKILL.md under data/skills/ "
        "and use bash — not skill_configure"
    )


def enrich_native_skill_hints(
    user_input: str,
    goals: list[str] | None,
    hints: list[str],
) -> None:
    """Add setup:native_skill when user asks to build a bundled/native Python skill."""
    tokens = {h.strip().lower() for h in hints if h and h.strip()}
    if tokens & (HINT_INSTRUCTION_SKILL_SETUP | HINT_NATIVE_SKILL_SETUP):
        return
    blob = f"{user_input or ''} {' '.join(goals or [])}"
    from nls.skills_setup_policy import (
        NATIVE_SKILL_DOCS_URL,
        looks_like_native_skill_authoring,
    )

    if not looks_like_native_skill_authoring(blob):
        return
    hints.append("setup:native_skill")
    hints.append(
        f"Native NLS skill: scaffold nls/skills/bundled/{{name}}/ with register() — "
        f"see {NATIVE_SKILL_DOCS_URL}"
    )
