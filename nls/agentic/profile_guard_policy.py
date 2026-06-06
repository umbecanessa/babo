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
    "reconcile_triage_continuation_phase",
    "looks_like_credential_continuation_turn",
    "infer_bundled_channel_skill_name",
    "wm_get_tactical_goal_strings",
    "wm_has_orchestration_activity",
    "infer_continuation_profile_from_wm",
    "upgrade_profile_for_continuation",
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

HINT_FLEET_SQUAD = frozenset({
    "fleet:squad_candidate",
})

HINT_JOB_CHARTER = frozenset({
    "job:charter_candidate",
    "continuation:job_confirm",
})

HINT_INSTRUCTION_SKILL_SETUP = frozenset({
    "setup:instruction_skill",
})

HINT_NATIVE_SKILL_SETUP = frozenset({
    "setup:native_skill",
})

HINT_CONFIGURE_BUNDLED = frozenset({
    "setup:configure_bundled",
})

HINT_CONTINUATION_CREDENTIAL = frozenset({
    "continuation:credential",
})

HINT_CONTINUATION_CONFIGURE = frozenset({
    "continuation:configure_not_build",
})

HINT_SETUP_CONFLICT = HINT_INSTRUCTION_SKILL_SETUP | HINT_NATIVE_SKILL_SETUP

_DISCORD_BOT_TOKEN_RE = re.compile(
    r"\b(?:Bot\s+)?[MN][A-Za-z0-9]{20,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}\b",
)

_TELEGRAM_BOT_TOKEN_RE = re.compile(
    r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b",
)

_ASSISTANT_ASKED_CREDENTIAL_RE = re.compile(
    r"\b(?:bot\s+token|paste\s+(?:your|the)|provide\s+(?:your|the)|"
    r"send\s+(?:me\s+)?(?:your|the)|need\s+(?:your|the)\s+(?:token|credential)|"
    r"waiting\s+for\s+(?:your|the)|share\s+(?:your|the)|looks\s+like\s+MT)\b",
    re.IGNORECASE,
)

_CONFIGURE_INTENT_RE = re.compile(
    r"\b(configure|set\s*up)\s+(?:the\s+)?(?:skill|bot|integration|channel|installed)\b"
    r"|\b(?:skill|bot|integration)\s+(?:token|setup|configure|credentials?)\b"
    r"|\b(?:bot|api)\s+token\b"
    r"|\bcredential(?:s)?\s*:\s*\S",
    re.I,
)

EM_COLD_START_GOAL_THRESHOLD = 3

_PROFILE_ORDER = ("conversational", "solo_structured", "orchestrated")


def _profile_rank(profile: str) -> int:
    try:
        return _PROFILE_ORDER.index(normalize_profile(profile))
    except ValueError:
        return 1


def _max_profile(a: str, b: str) -> str:
    return _PROFILE_ORDER[max(_profile_rank(a), _profile_rank(b))]


def wm_get_tactical_goal_strings(working_memory: Any | None) -> list[str]:
    """Read persisted tactical goals from Cryptex/WM."""
    if working_memory is None:
        return []
    try:
        return [
            str(getattr(g, "content", g)).strip()
            for g in working_memory.get_goals()
            if getattr(g, "level", "") == "tactical"
            and isinstance(getattr(g, "content", None), str)
            and str(g.content).strip()
        ]
    except Exception:
        return []


def wm_get_task_hint_strings(working_memory: Any | None) -> list[str]:
    """Read persisted Task.Hints from Cryptex/WM."""
    if working_memory is None:
        return []
    try:
        rings: list[Any] = [working_memory]
        for attr in ("personal", "common", "professional"):
            child = getattr(working_memory, attr, None)
            if child is not None and child is not working_memory:
                rings.append(child)
        for ring in rings:
            slots = getattr(ring, "_slots", None)
            if not slots:
                continue
            for slot in slots:
                if getattr(slot, "domain", "") != "Task.Hints":
                    continue
                raw = (getattr(slot, "content", "") or "").strip()
                if raw:
                    return [h.strip() for h in raw.split("|") if h.strip()]
    except Exception:
        pass
    return []


def build_triage_continuation_context(
    user_input: str,
    *,
    history: list[dict] | None = None,
    working_memory: Any | None = None,
) -> str:
    """Factual continuation state for triage input — not post-hoc goal overrides."""
    parts: list[str] = []
    wm_goals = wm_get_tactical_goal_strings(working_memory)
    if wm_goals:
        parts.append(
            "Persisted tactical goals (continue unless the user changed topic):\n"
            + "\n".join(f"  • {g}" for g in wm_goals[:5])
        )
    wm_hints = wm_get_task_hint_strings(working_memory)
    if wm_hints:
        parts.append(
            "Persisted task hints:\n"
            + "\n".join(f"  • {h}" for h in wm_hints[:8])
        )
    last_asst = _last_assistant_message(history)
    if last_asst:
        parts.append(
            "Last assistant message (user may be replying to this):\n"
            + last_asst[:2000]
        )
    if looks_like_credential_continuation_turn(user_input, history=history):
        parts.append(
            "The latest user message looks like pasted credentials. Classify using "
            "the last assistant message and persisted goals — e.g. fleet member bot "
            "tokens after multi-face squad setup: use squad(action='configure_member') "
            "on the target member — NOT lead skill_configure."
        )
    return "\n\n".join(parts)


def _triage_has_classifier_output(triage: Any) -> bool:
    """True when micro-inference returned goals or hints — do not heuristic-stomp."""
    if not getattr(triage, "classifier_inferred", True):
        return False
    goals = [g for g in (getattr(triage, "goals", None) or []) if g and str(g).strip()]
    hints = [h for h in (getattr(triage, "hints", None) or []) if h and str(h).strip()]
    return bool(goals or hints)


def wm_has_orchestration_activity(working_memory: Any | None) -> bool:
    """True when WM shows active teams, coordinator work, or pending escalations."""
    if working_memory is None:
        return False
    try:
        orch_teams = getattr(working_memory, "orch_get_active_teams", None)
        if callable(orch_teams) and orch_teams():
            return True
        wake_fn = getattr(working_memory, "get_orchestration_wake_lines", None)
        if callable(wake_fn):
            for line in wake_fn():
                low = line.lower()
                if line.startswith("Team ") and "last team" not in low:
                    return True
                if "coordinator phase:" in low and "idle" not in low:
                    return True
                if "pending escalation" in low:
                    return True
    except Exception:
        pass
    return False


def infer_continuation_profile_from_wm(
    working_memory: Any | None,
    current_profile: str,
    *,
    post_restart_task: bool = False,
) -> str:
    """Upgrade orchestration depth from WM continuity; never downgrade."""
    current = normalize_profile(current_profile)
    floor = current

    if wm_has_orchestration_activity(working_memory):
        floor = _max_profile(floor, "orchestrated")
    else:
        tactical = wm_get_tactical_goal_strings(working_memory)
        if tactical and current == "conversational":
            floor = _max_profile(floor, "solo_structured")
        elif len(tactical) >= EM_COLD_START_GOAL_THRESHOLD:
            floor = _max_profile(floor, "solo_structured")

    if post_restart_task and _profile_rank(floor) <= _profile_rank("conversational"):
        floor = "solo_structured"
    return floor


def upgrade_profile_for_continuation(
    current_profile: str,
    working_memory: Any | None = None,
    *,
    post_restart_task: bool = False,
) -> str:
    """Never downgrade profile; lift conversational using WM when available."""
    return infer_continuation_profile_from_wm(
        working_memory,
        current_profile,
        post_restart_task=post_restart_task,
    )


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


_EM_TRIAGE_GOAL_RE = re.compile(
    r"\b(plan|team|delegate|delegat|wave|launch|monitor|orchestrat)\b",
    re.IGNORECASE,
)
_IC_IMPL_TRIAGE_GOAL_RE = re.compile(
    r"\b(build|implement|scaffold|deploy|platform|monorepo|backend|frontend|"
    r"end-to-end|ship|create repo|github repo)\b",
    re.IGNORECASE,
)
_READ_EXTRACT_TRIAGE_GOAL_RE = re.compile(
    r"\b(read|extract|review|parse|understand)\b",
    re.IGNORECASE,
)


def normalize_orchestrated_triage_goals(goals: list[str]) -> list[str]:
    """Replace IC implementation bullets with EM orchestration goals when triage drifts solo."""
    if len(goals) < 2:
        return goals
    joined = " ".join(goals)
    if _EM_TRIAGE_GOAL_RE.search(joined):
        return goals
    if (
        not _IC_IMPL_TRIAGE_GOAL_RE.search(joined)
        and len(goals) < EM_COLD_START_GOAL_THRESHOLD
    ):
        return goals
    orchestration = [
        "Create master plan with delegatable implementation steps",
        "Launch team waves for build steps and monitor progress",
        "Review delegate output and integrate before shipping",
    ]
    first = goals[0].strip()
    if first and _READ_EXTRACT_TRIAGE_GOAL_RE.search(first):
        return ([first] + orchestration)[:5]
    return orchestration[:3]


def normalize_goals_for_profile(
    goals: list[str],
    profile: str | None,
) -> list[str]:
    if not goals:
        return goals
    p = normalize_profile(profile)
    if p == "conversational":
        return []
    if p == "orchestrated":
        return normalize_orchestrated_triage_goals(goals)
    if p in ("solo_structured",) and len(goals) >= EM_COLD_START_GOAL_THRESHOLD:
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

    if tokens & HINT_CONTINUATION_CONFIGURE:
        return p, hints

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


def _last_assistant_message(history: list[dict] | None) -> str:
    if not history:
        return ""
    for turn in reversed(history):
        if turn.get("role") == "assistant":
            return turn.get("content") or ""
    return ""


def infer_bundled_channel_skill_name(
    text: str,
    *,
    default_platform: str = "telegram",
) -> str:
    """Pre-shipped bundled channel skill slug (telegram/whatsapp/email only)."""
    from nls.skills_setup_policy import infer_pre_shipped_channel_skill

    name = infer_pre_shipped_channel_skill(text)
    if name:
        return name
    return f"{default_platform}-channel"


def looks_like_credential_continuation_turn(
    user_input: str,
    *,
    history: list[dict] | None = None,
) -> bool:
    """User pasted a credential after assistant asked, or message is token-only."""
    ui = (user_input or "").strip()
    if not ui:
        return False
    has_token = bool(
        _DISCORD_BOT_TOKEN_RE.search(ui)
        or _TELEGRAM_BOT_TOKEN_RE.search(ui)
    )
    if not has_token:
        return False
    last_asst = _last_assistant_message(history)
    if last_asst and _ASSISTANT_ASKED_CREDENTIAL_RE.search(last_asst):
        return True
    recent = ui
    if history:
        for turn in history[-6:]:
            if turn.get("role") in ("user", "assistant"):
                recent += "\n" + (turn.get("content") or "")[:400]
    if _TASK_CONTEXT_RE.search(recent) and len(ui) < 160:
        return True
    return False


def _post_restart_channel_context(recent_text: str) -> tuple[str, str, bool]:
    """Return (skill_name, platform, is_pre_shipped) for post-restart continuation."""
    from nls.skills_setup_policy import (
        infer_channel_platform,
        infer_pre_shipped_channel_skill,
        is_pre_shipped_channel_skill,
    )

    pre_shipped = infer_pre_shipped_channel_skill(recent_text)
    platform = infer_channel_platform(recent_text) or "discord"
    if pre_shipped and is_pre_shipped_channel_skill(pre_shipped):
        return pre_shipped, platform, True
    skill_name = pre_shipped or f"{platform}-channel"
    return skill_name, platform, False


def _post_restart_fallback_goals(skill_name: str, platform: str, *, pre_shipped: bool) -> list[str]:
    if pre_shipped:
        return [
            f"Verify {skill_name} skill loaded after restart",
            f"Configure {skill_name} with saved credentials if needed",
            f"Verify {platform} channel connection with a smoke test",
        ]
    return [
        f"Verify {skill_name} skill loaded after restart",
        f"Configure {skill_name} with saved credentials if needed",
        "Verify inbound listener starts on skill startup",
    ]


def reconcile_triage_continuation_phase(
    triage: Any,
    user_input: str,
    *,
    history: list[dict] | None = None,
    working_memory: Any | None = None,
) -> None:
    """Fallback repair when triage classifier returned empty structure.

    When classifier_inferred is true and goals/hints are non-empty, triage output
    is authoritative — this function does not replace it with keyword heuristics.
    """
    if _triage_has_classifier_output(triage):
        logger.debug(
            "Turn triage continuation reconcile: skipped — classifier output preserved "
            "(goals=%d hints=%d)",
            len(getattr(triage, "goals", None) or []),
            len(getattr(triage, "hints", None) or []),
        )
        return

    ui = (user_input or "").strip()
    recent_for_restart = ui
    if history:
        for turn in history[-12:]:
            if turn.get("role") in ("user", "assistant"):
                recent_for_restart += "\n" + (turn.get("content") or "")[:500]
    if (
        _POST_RESTART_RE.search(ui)
        and (
            _TASK_CONTEXT_RE.search(recent_for_restart)
            or "skill review" in recent_for_restart.lower()
            or "discord-channel" in recent_for_restart.lower()
        )
    ):
        goals = list(getattr(triage, "goals", None) or [])
        if not goals:
            wm_goals = wm_get_tactical_goal_strings(working_memory)
            if wm_goals:
                goals = wm_goals
            else:
                skill_name, platform, pre_shipped = _post_restart_channel_context(
                    recent_for_restart,
                )
                goals = _post_restart_fallback_goals(
                    skill_name, platform, pre_shipped=pre_shipped,
                )
        triage.intent = "TASK_THINK"
        triage.thinking = True
        triage.profile = upgrade_profile_for_continuation(
            getattr(triage, "profile", "") or "solo_structured",
            working_memory,
            post_restart_task=True,
        )
        triage.goals = goals[:5]
        hints = list(getattr(triage, "hints", None) or [])
        hint_tokens = {h.strip().lower() for h in hints if h and h.strip()}
        _, _, pre_shipped_channel = _post_restart_channel_context(recent_for_restart)
        setup_token = (
            "setup:configure_bundled" if pre_shipped_channel else "setup:native_skill"
        )
        for token in ("continuation:configure_not_build", setup_token):
            if token not in hint_tokens:
                hints.append(token)
        triage.hints = hints
        logger.info(
            "Turn triage continuation reconcile: post-restart channel setup "
            "profile=%s wm_goals=%d",
            triage.profile,
            len(wm_get_tactical_goal_strings(working_memory)),
        )
        return

    hints = list(getattr(triage, "hints", None) or [])
    hint_tokens = {h.strip().lower() for h in hints if h and h.strip()}

    credential_continuation = (
        bool(hint_tokens & HINT_CONTINUATION_CREDENTIAL)
        or looks_like_credential_continuation_turn(user_input, history=history)
    )

    recent_text = (user_input or "").strip()
    if history:
        for turn in history[-8:]:
            if turn.get("role") in ("user", "assistant"):
                recent_text += "\n" + (turn.get("content") or "")[:500]

    from nls.skills_setup_policy import (
        infer_channel_platform,
        infer_pre_shipped_channel_skill,
        is_pre_shipped_channel_skill,
    )

    pre_shipped_skill = infer_pre_shipped_channel_skill(recent_text)
    channel_platform = infer_channel_platform(recent_text)

    wants_configure_bundled = bool(
        hint_tokens & HINT_CONFIGURE_BUNDLED
        or (
            credential_continuation
            and pre_shipped_skill is not None
            and is_pre_shipped_channel_skill(pre_shipped_skill)
        )
    )

    if not wants_configure_bundled:
        return

    skill_name = pre_shipped_skill or infer_bundled_channel_skill_name(recent_text)
    cleaned = [
        h for h in hints
        if h.strip().lower() not in HINT_SETUP_CONFLICT
    ]
    if "setup:configure_bundled" not in {
        h.strip().lower() for h in cleaned
    }:
        cleaned.append("setup:configure_bundled")
    if credential_continuation and "continuation:credential" not in {
        h.strip().lower() for h in cleaned
    }:
        cleaned.append("continuation:credential")
    if "continuation:configure_not_build" not in {
        h.strip().lower() for h in cleaned
    }:
        cleaned.append("continuation:configure_not_build")
    cleaned.append(
        f"Use skill_configure(skill_name='{skill_name}') — bundled skill exists; "
        "do NOT scaffold or skill_install from scratch"
    )

    goals = list(getattr(triage, "goals", None) or [])
    if credential_continuation or not goals:
        goals = [
            f"Configure {skill_name} with the provided credentials via skill_configure",
            f"Enable {skill_name} for this agent if not already enabled",
            "Verify channel connection with a smoke test",
        ]
    elif goals and hint_tokens & HINT_SETUP_CONFLICT:
        goals = [
            f"Configure {skill_name} via skill_configure (not greenfield build)",
            *[
                g for g in goals
                if "scaffold" not in g.lower()
                and "author" not in g.lower()
                and "skill.md" not in g.lower()
            ][:2],
        ] or goals

    triage.intent = "TASK_THINK"
    triage.thinking = True
    triage.profile = upgrade_profile_for_continuation(
        getattr(triage, "profile", "") or "solo_structured",
        working_memory,
    )
    triage.goals = goals[:5]
    triage.hints = cleaned
    logger.info(
        "Turn triage continuation reconcile: credential=%s skill=%s goals=%d",
        credential_continuation,
        skill_name,
        len(triage.goals),
    )


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
    if tokens & HINT_FLEET_SQUAD:
        denied.update({"team", "delegate", "set_job"})
    if tokens & HINT_JOB_CHARTER:
        denied.update({"squad_setup", "squad", "team"})
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
    r"try\s+again|yes|yep|please\s+do|ok\s+server\s+restarted|"
    r"server\s+restarted(?:\s+successfully)?)\s*[.!?]?\s*$",
    re.IGNORECASE,
)

_POST_RESTART_RE = re.compile(
    r"\b(?:server\s+restarted|restart(?:ed)?\s+(?:complete|done|successfully)|"
    r"skill\(s\)\s+loaded)\b",
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
    if looks_like_credential_continuation_turn(ui, history=history):
        triage.intent = "TASK_THINK"
        triage.thinking = True
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
    """Deprecated — setup hints come from turn triage micro-inference only."""
    del user_input, goals, hints


def enrich_native_skill_hints(
    user_input: str,
    goals: list[str] | None,
    hints: list[str],
) -> None:
    """Deprecated — setup hints come from turn triage micro-inference only."""
    del user_input, goals, hints
