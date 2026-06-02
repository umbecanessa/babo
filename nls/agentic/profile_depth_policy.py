"""Orchestration depth reconsideration — mid-loop profile upgrades.

Separates **AgentMode** (tool surface) from **orchestration profile** (goals,
evaluator, Cryptex depth).  Nudges are advisory; ``adopt_orchestration_profile``
commits a depth change and refreshes tool policy.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from nls.agentic.orchestration_profile_spec import (
    normalize_profile,
    profile_anchor_message,
)
from nls.agentic.orchestration_policy import (
    ToolPolicyInputs,
    invalidate_tool_policy_cache,
    resolve_allowed_tools,
)
from nls.agentic.profile_guard_policy import (
    HINT_FORBID_TEAM,
    HINT_FORBID_TOOLS,
)
from nls.agentic.types import AgentMode, LoopState

logger = logging.getLogger(__name__)

SuggestedProfile = Literal["solo_structured", "orchestrated"]

_PROFILE_ORDER = ("conversational", "solo_structured", "orchestrated")

_PLAN_ACTIONS = frozenset({
    "create", "update", "read", "verify", "complete",
    "accept_partial", "fix_dependencies", "add_step", "status",
})
_DEPTH_DENIED_TOOLS = frozenset({
    "plan", "todo", "team", "delegate", "await_delegates", "delegate_status",
})
_IC_TOOLS = frozenset({"bash", "write", "edit", "delete_file", "move_file"})
_COORDINATOR_MODE_NAMES = frozenset({
    "planning", "delegating", "monitoring", "evaluating",
})
_LIGHT_BASH_RE = re.compile(
    r"(?:^|\s)(?:curl|Invoke-RestMethod|wget)\b.*(?:@me|users/@me|/users/@me)",
    re.IGNORECASE,
)
_EXECUTION_MODE_RE = re.compile(
    r"\b(?:switch\s+to\s+execution|execution\s+mode|unlock\s+bash|enable\s+bash)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProfileDepthNudge:
    """Advisory nudge to consider a deeper orchestration profile."""

    trigger_id: str
    message: str
    suggested_profile: SuggestedProfile
    append_to_tool_result: bool = False


def _profile_rank(profile: str) -> int:
    try:
        return _PROFILE_ORDER.index(normalize_profile(profile))
    except ValueError:
        return 1


def _is_upgrade(current: str, suggested: SuggestedProfile) -> bool:
    return _profile_rank(suggested) > _profile_rank(current)


def suppress_depth_nudges(state: LoopState) -> bool:
    hints = {h.strip().lower() for h in (state.hints or []) if h and h.strip()}
    if hints & HINT_FORBID_TOOLS:
        return True
    if state.profile_depth_adopted_this_loop:
        return True
    return False


def _cooldown_key(trigger_id: str, suggested: SuggestedProfile) -> str:
    return f"{trigger_id}:{suggested}"


def _nudge_already_sent(
    state: LoopState,
    trigger_id: str,
    suggested: SuggestedProfile,
) -> bool:
    return _cooldown_key(trigger_id, suggested) in state.profile_depth_nudges_given


def _record_nudge(
    state: LoopState,
    trigger_id: str,
    suggested: SuggestedProfile,
) -> None:
    state.profile_depth_nudges_given.add(_cooldown_key(trigger_id, suggested))


def _multi_step_signals(state: LoopState) -> bool:
    if len(state.goals or []) >= 2:
        return True
    hints = {h.strip().lower() for h in (state.hints or []) if h and h.strip()}
    if "setup:instruction_skill" in hints or "setup:native_skill" in hints:
        return True
    ui = (state.user_input or "").lower()
    if any(
        tok in ui
        for tok in (
            "install", "configure", "deploy", "scaffold", "setup",
            "discord", "monorepo", "end-to-end",
        )
    ):
        return True
    return False


def _ic_success_count(state: LoopState) -> int:
    return sum(
        int(state.tool_successes.get(t, 0) or 0)
        for t in _IC_TOOLS
    )


def _tool_denied_by_profile(
    tool_name: str,
    profile: str,
    *,
    mode: AgentMode,
    all_unlocked: frozenset[str],
) -> bool:
    if tool_name not in _DEPTH_DENIED_TOOLS and tool_name not in _IC_TOOLS:
        return False
    inputs = ToolPolicyInputs(
        mode=mode,
        must_await_delegates=False,
        delegates_active=False,
        suppress_raw_delegate=False,
        is_coordinator=False,
        all_unlocked=all_unlocked,
        orchestration_profile=profile,
    )
    allowed = resolve_allowed_tools(inputs)
    return tool_name not in allowed


def format_adopt_tool_hint() -> str:
    return (
        "To commit a depth change mid-loop, call "
        "adopt_orchestration_profile(profile='solo_structured'|"
        "'orchestrated', reason='...')."
    )


def enrich_profile_blocked_message(
    tool_name: str,
    block_message: str,
    state: LoopState,
    *,
    mode: AgentMode,
    all_unlocked: frozenset[str] | None = None,
) -> str:
    """Append depth guidance when a block is due to profile tool_deny (T2, T4, T6)."""
    if suppress_depth_nudges(state):
        return block_message
    profile = normalize_profile(state.orchestration_profile or "solo_structured")
    unlocked = all_unlocked or frozenset(state.unlocked_tools or ())
    if not _tool_denied_by_profile(tool_name, profile, mode=mode, all_unlocked=unlocked):
        return block_message
    if tool_name in ("plan", "todo"):
        suggested: SuggestedProfile = "solo_structured"
        trigger = "T2_blocked_plan_todo"
    elif tool_name in ("team", "delegate", "await_delegates", "delegate_status"):
        if not state.coordinator_mode and normalize_profile(profile) == "conversational":
            suggested = "orchestrated"
            trigger = "T6_blocked_team"
        else:
            return block_message
    else:
        return block_message
    if not _is_upgrade(profile, suggested):
        return block_message
    if _nudge_already_sent(state, trigger, suggested):
        return block_message
    _record_nudge(state, trigger, suggested)
    extra = (
        f"\n\n[ORCHESTRATION DEPTH] Tool '{tool_name}' is limited while profile "
        f"is '{profile}'. {format_adopt_tool_hint()} "
        f"Suggested profile: {suggested}."
    )
    logger.info(
        "[PROFILE_DEPTH] %s block enrich tool=%s current=%s → %s",
        trigger, tool_name, profile, suggested,
    )
    return block_message + extra


def enrich_mode_switch_block_message(
    target_mode: str,
    block_message: str,
    state: LoopState,
) -> str:
    """When switch_mode to EM modes is blocked by profile, suggest adopt path."""
    if suppress_depth_nudges(state):
        return block_message
    if "orchestration depth" in (block_message or "").lower():
        return block_message
    target = (target_mode or "").strip().lower()
    if target not in _COORDINATOR_MODE_NAMES:
        return block_message
    profile = normalize_profile(state.orchestration_profile or "solo_structured")
    hints = {h.strip().lower() for h in (state.hints or []) if h and h.strip()}
    if profile == "solo_structured":
        trigger = "T7_blocked_switch_solo"
        if _nudge_already_sent(state, trigger, "solo_structured"):
            return block_message
        _record_nudge(state, trigger, "solo_structured")
        return (
            f"{block_message}\n\n[ORCHESTRATION DEPTH] Profile is solo_structured — "
            "use executing mode with plan/todo directly; EM modes (planning/delegating) "
            "are not used. Adopt orchestrated only if you need team waves and "
            "the user did not forbid teams."
        )
    if profile != "conversational":
        return block_message
    if hints & HINT_FORBID_TEAM:
        trigger = "T7_blocked_switch_forbid_team"
        if _nudge_already_sent(state, trigger, "solo_structured"):
            return block_message
        _record_nudge(state, trigger, "solo_structured")
        return (
            f"{block_message}\n\n[ORCHESTRATION DEPTH] User forbade teams. "
            f"{format_adopt_tool_hint()} Suggested profile: solo_structured "
            "(then plan/todo in executing mode — not switch_mode(planning))."
        )
    suggested: SuggestedProfile = "orchestrated"
    trigger = "T7_blocked_switch_conversational"
    if _nudge_already_sent(state, trigger, suggested):
        return block_message
    _record_nudge(state, trigger, suggested)
    return (
        f"{block_message}\n\n[ORCHESTRATION DEPTH] EM mode switch requires "
        f"orchestrated depth. {format_adopt_tool_hint()} "
        f"Suggested profile: {suggested}."
    )


def evaluate_after_tool(
    state: LoopState,
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    *,
    blocked: bool = False,
    block_message: str = "",
    mode: AgentMode | None = None,
    enable_delegation: bool = True,
) -> ProfileDepthNudge | None:
    """Return a post-tool nudge after a tool call, if warranted."""
    if suppress_depth_nudges(state):
        return None
    profile = normalize_profile(state.orchestration_profile or "solo_structured")
    mode = mode or state.active_mode
    is_error = bool(getattr(result, "is_error", False)) if result is not None else blocked

    if blocked:
        return _evaluate_blocked_tool(
            state, tool_name, profile, mode=mode, block_message=block_message,
        )

    if is_error:
        return _evaluate_error_recovery(state, tool_name, args, profile)

    if tool_name == "switch_mode":
        return evaluate_switch_mode_success(
            state,
            str(args.get("mode", "") or "").lower(),
            enable_delegation=enable_delegation,
        )

    if tool_name == "plan" and not is_error:
        return _evaluate_plan_success(state, args, profile)

    if tool_name == "todo" and not is_error:
        action = str(args.get("action", "") or "").lower()
        if action in ("add", "update", "complete", "list"):
            return _maybe_nudge(
                state, "T4_todo_success", profile, "solo_structured",
                "You are using todo while still on conversational depth. "
                "Adopt solo_structured for checklist + plan verify semantics.",
            )

    if tool_name in _IC_TOOLS and profile == "conversational":
        return _evaluate_ic_on_conversational(state, tool_name, args)

    if tool_name == "get_tool_schema" and not is_error:
        requested = str(args.get("tool_name", "") or "")
        return _evaluate_schema_unlock_request(state, requested, profile, mode)

    return None


def _evaluate_blocked_tool(
    state: LoopState,
    tool_name: str,
    profile: str,
    *,
    mode: AgentMode,
    block_message: str,
) -> ProfileDepthNudge | None:
    if "orchestration depth" in (block_message or "").lower():
        return None
    if tool_name in ("plan", "todo"):
        return _maybe_nudge(
            state, "T2_blocked_plan", profile, "solo_structured",
            "plan/todo requires solo_structured (or orchestrated for team waves). "
            f"{format_adopt_tool_hint()}",
            append_to_tool_result=True,
        )
    if tool_name in ("team", "delegate") and profile == "conversational":
        if state.coordinator_mode or "orchestrated" in (block_message or "").lower():
            return None
        return _maybe_nudge(
            state, "T6_blocked_team", profile, "orchestrated",
            "team/delegate requires orchestrated profile when delegation is enabled. "
            f"{format_adopt_tool_hint()}",
            append_to_tool_result=True,
        )
    return None


def _evaluate_plan_success(
    state: LoopState,
    args: dict[str, Any],
    profile: str,
) -> ProfileDepthNudge | None:
    action = str(args.get("action", "") or "").lower()
    if action not in _PLAN_ACTIONS:
        return None
    if profile != "conversational":
        return None
    return _maybe_nudge(
        state, "T3_plan_success", profile, "solo_structured",
        f"plan(action='{action}') succeeded on conversational depth — unusual. "
        "Adopt solo_structured to align evaluator + plan verify rules.",
    )


def _evaluate_ic_on_conversational(
    state: LoopState,
    tool_name: str,
    args: dict[str, Any],
) -> ProfileDepthNudge | None:
    if tool_name == "bash":
        cmd = str(args.get("command", "") or "")
        if _LIGHT_BASH_RE.search(cmd):
            return None
    count = _ic_success_count(state)
    if count < 2:
        return None
    if not _multi_step_signals(state):
        return None
    return _maybe_nudge(
        state, "T5_sustained_ic", "conversational", "solo_structured",
        f"You have {count} successful file/shell actions on conversational depth. "
        "If this is multi-step implementation work, adopt solo_structured for "
        "plan/todo + verify semantics. Otherwise continue with executing mode only.",
    )


def _evaluate_schema_unlock_request(
    state: LoopState,
    requested: str,
    profile: str,
    mode: AgentMode,
) -> ProfileDepthNudge | None:
    if not requested:
        return None
    unlocked = frozenset(state.unlocked_tools or ())
    if not _tool_denied_by_profile(requested, profile, mode=mode, all_unlocked=unlocked):
        return None
    if requested in ("plan", "todo"):
        suggested: SuggestedProfile = "solo_structured"
        trigger = "T10_schema_plan"
    elif requested in ("team", "delegate"):
        suggested = "orchestrated"
        trigger = "T10_schema_team"
    else:
        return None
    return _maybe_nudge(
        state, trigger, profile, suggested,
        f"get_tool_schema('{requested}') targets a tool denied at profile '{profile}'. "
        f"{format_adopt_tool_hint()}",
    )


def _evaluate_error_recovery(
    state: LoopState,
    tool_name: str,
    args: dict[str, Any],
    profile: str,
) -> ProfileDepthNudge | None:
    if profile != "conversational":
        return None
    hints = {h.strip().lower() for h in (state.hints or []) if h and h.strip()}
    if "setup:instruction_skill" not in hints and "setup:native_skill" not in hints:
        return None
    if tool_name not in _IC_TOOLS and tool_name not in ("plan", "todo"):
        return None
    return _maybe_nudge(
        state, "T9_error_recovery", profile, "solo_structured",
        "Setup/install task hit errors on conversational depth. "
        "Consider adopt_orchestration_profile(profile='solo_structured') "
        "before retrying bash/plan.",
    )


def evaluate_switch_mode_success(
    state: LoopState,
    target_mode: str,
    *,
    enable_delegation: bool = True,
) -> ProfileDepthNudge | None:
    """T1 / T7 — after successful switch_mode."""
    if suppress_depth_nudges(state):
        return None
    profile = normalize_profile(state.orchestration_profile or "solo_structured")

    if target_mode == "executing" and profile == "conversational":
        if not _multi_step_signals(state) and not _EXECUTION_MODE_RE.search(
            state.user_input or "",
        ):
            return None
        return _maybe_nudge(
            state, "T1_switch_executing", profile, "solo_structured",
            "You switched to executing mode for shell/files. Profile is still "
            "conversational — adopt solo_structured if you need plan/todo/verify; "
            "otherwise executing mode alone may be enough.",
        )

    hints = {h.strip().lower() for h in (state.hints or []) if h and h.strip()}
    if (
        enable_delegation
        and target_mode in ("planning", "delegating", "monitoring")
        and profile == "conversational"
        and not state.coordinator_mode
    ):
        if hints & HINT_FORBID_TEAM:
            return _maybe_nudge(
                state, "T7_switch_solo_instead", profile, "solo_structured",
                f"switch_mode({target_mode}) is blocked and teams are forbidden. "
                "Adopt solo_structured and use plan/todo in executing mode.",
            )
        return _maybe_nudge(
            state, "T7_switch_coordinator", profile, "orchestrated",
            f"switch_mode({target_mode}) is an engineering-manager workflow. "
            f"Adopt orchestrated before staffing waves. {format_adopt_tool_hint()}",
        )

    return None


def _maybe_nudge(
    state: LoopState,
    trigger_id: str,
    current_profile: str,
    suggested: SuggestedProfile,
    message: str,
    *,
    append_to_tool_result: bool = False,
) -> ProfileDepthNudge | None:
    if not _is_upgrade(current_profile, suggested):
        return None
    if _nudge_already_sent(state, trigger_id, suggested):
        return None
    _record_nudge(state, trigger_id, suggested)
    logger.info(
        "[PROFILE_DEPTH] nudge %s current=%s suggested=%s",
        trigger_id, current_profile, suggested,
    )
    return ProfileDepthNudge(
        trigger_id=trigger_id,
        message=(
            f"[ORCHESTRATION DEPTH] {message}"
        ),
        suggested_profile=suggested,
        append_to_tool_result=append_to_tool_result,
    )


def append_depth_to_stall_message(
    state: LoopState,
    stall_msg: str,
) -> str:
    """T8 — conversational TASK work stalling without substantive tools."""
    if suppress_depth_nudges(state):
        return stall_msg
    profile = normalize_profile(state.orchestration_profile or "solo_structured")
    if profile != "conversational":
        return stall_msg
    from nls.agentic.evaluator import (
        has_substantive_tool_success,
        requires_substantive_delivery,
    )

    if not requires_substantive_delivery(state):
        return stall_msg
    if has_substantive_tool_success(state):
        return stall_msg
    nudge = _maybe_nudge(
        state, "T8_stall_substantive", profile, "solo_structured",
        "This task needs bash/write/plan but profile is conversational. "
        "Either adopt solo_structured or call switch_mode(mode='executing') "
        "and continue with shell tools.",
    )
    if nudge is None:
        return stall_msg
    return f"{stall_msg}\n\n{nudge.message}"


def evaluate_wm_profile_mismatch(
    state: LoopState,
    *,
    wm_has_strategic_goals: bool,
    wm_has_plan_position: bool,
) -> ProfileDepthNudge | None:
    """T11 — WM shows structured work but profile stayed conversational."""
    if suppress_depth_nudges(state):
        return None
    profile = normalize_profile(state.orchestration_profile or "solo_structured")
    if profile != "conversational":
        return None
    if not wm_has_strategic_goals and not wm_has_plan_position:
        return None
    return _maybe_nudge(
        state, "T11_wm_mismatch", profile, "solo_structured",
        "Working memory shows an active plan/goals but orchestration profile "
        "is still conversational. Adopt solo_structured to align depth.",
    )


def validate_profile_adoption(
    state: LoopState,
    target: str,
    *,
    enable_delegation: bool = True,
    hooks: Any | None = None,
) -> str | None:
    """Return error message if adoption is not allowed, else None."""
    target_norm = normalize_profile(target)
    current = normalize_profile(state.orchestration_profile or "solo_structured")

    if target_norm == current:
        return f"Already on profile '{current}'."

    if target_norm not in _PROFILE_ORDER:
        return f"Unknown profile '{target}'. Use conversational, solo_structured, or orchestrated."

    hints = {h.strip().lower() for h in (state.hints or []) if h and h.strip()}
    if hints & HINT_FORBID_TEAM and target_norm == "orchestrated":
        return "Cannot adopt orchestrated: user forbade teams (forbid:team / orchestration:solo)."

    if target_norm == "orchestrated" and not enable_delegation:
        return "Cannot adopt orchestrated: delegation is disabled for this loop."

    if _profile_rank(target_norm) < _profile_rank(current):
        if target_norm == "conversational":
            if hooks and hooks.has_active_plan:
                try:
                    if hooks.has_active_plan():
                        return (
                            "Cannot downgrade to conversational while an active "
                            "plan exists. Complete or archive the plan first."
                        )
                except Exception:
                    pass
            if state.delegate_count > 0:
                return (
                    "Cannot downgrade to conversational while delegates are "
                    "tracked on this loop."
                )
        return f"Cannot downgrade from '{current}' to '{target_norm}' in-loop."

    return None


def apply_orchestration_profile_adoption(
    state: LoopState,
    target: str,
    *,
    reason: str = "",
    enable_delegation: bool = True,
    hooks: Any | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Commit profile change. Caller refreshes tool schemas + context anchor."""
    err = validate_profile_adoption(
        state, target, enable_delegation=enable_delegation, hooks=hooks,
    )
    if err:
        return False, err, {"type": "adopt_profile_rejected"}

    prev = normalize_profile(state.orchestration_profile or "solo_structured")
    new_profile = normalize_profile(target)
    state.orchestration_profile = new_profile
    state.profile_depth_adopted_this_loop = True
    state.profile_depth_nudges_given.add(f"adopted:{new_profile}")
    invalidate_tool_policy_cache(state)

    anchor = profile_anchor_message(new_profile)
    if anchor:
        state.pending_profile_anchor = anchor

    _sync_cryptex_depth(hooks, prev, new_profile, reason=reason)

    msg = (
        f"Adopted orchestration profile '{new_profile}' (was '{prev}'). "
        f"Tool policy and Cryptex depth updated."
    )
    if reason.strip():
        msg += f" Reason: {reason.strip()[:300]}"
    msg += (
        "\n\nTool schemas refresh on this turn. "
        "Use plan/todo/bash as allowed by the new depth."
    )
    logger.info(
        "[PROFILE_DEPTH] adopted %s → %s reason=%s",
        prev, new_profile, (reason or "")[:120],
    )
    return True, msg, {
        "type": "adopt_profile",
        "adopted_profile": new_profile,
        "previous_profile": prev,
        "reason": (reason or "")[:500],
    }


def _sync_cryptex_depth(
    hooks: Any | None,
    prev: str,
    new: str,
    *,
    reason: str = "",
) -> None:
    if hooks is None:
        return
    cryptex = getattr(hooks, "_accumulator_wm_target", None)
    if cryptex is None:
        return
    try:
        if hasattr(cryptex, "upsert_behavioral"):
            cryptex.upsert_behavioral(
                domain="orchestration_depth",
                content=(
                    f"Active orchestration profile: {new} "
                    f"(adopted from {prev}"
                    f"{'; ' + reason[:200] if reason else ''}). "
                    "Follow depth-appropriate plan/team/bash rules."
                ),
                render_mode="agentic",
                salience=0.92,
            )
        if hasattr(cryptex, "upsert_orchestration_slot"):
            cryptex.upsert_orchestration_slot(
                domain="profile_adoption",
                content=f"{prev} → {new}",
                source="profile_depth_policy",
            )
    except Exception:
        logger.debug("Cryptex depth sync failed", exc_info=True)


def journal_depth_event(
    event: str,
    *,
    loop_id: str = "",
    trigger_id: str = "",
    profile_from: str = "",
    profile_to: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event": event,
        "loop_id": loop_id,
        "trigger_id": trigger_id,
        "profile_from": profile_from,
        "profile_to": profile_to,
    }
    if extra:
        row.update(extra)
    return row
