"""Token-efficient orchestration policy — engineering manager runtime guards.

The orchestrator is the engineering manager: owns the plan and Kanban,
launches waves, reviews deliverables, and steers stuck members. While a
wave executes, the EM does not do IC work (write/bash) or idle-poll — they
end the management turn and return when escalations, completion, or a
scheduled review need a decision.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from nls.runtime.dispatch_sources import is_orchestration_dispatch_source

from .coordinator_guard import hook_suppresses_raw_delegate
from .types import AgentMode, LoopState, get_allowed_tools

# Modes where the user acts as a general assistant (not EM wave orchestration).
_ASSISTANT_FREEFORM_MODES = frozenset({
    AgentMode.CHAT,
    AgentMode.EXECUTING,
    AgentMode.RESPONDING,
})

logger = logging.getLogger(__name__)

_CONVERSATIONAL_NAME_RE = re.compile(
    r"\b(?:your name is|call you|i(?:'ll| will) call you|you are|you're)\b",
    re.IGNORECASE,
)
_CONVERSATIONAL_GREETING_RE = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|good morning|good afternoon)\b",
    re.IGNORECASE,
)
_BUILD_TASK_RE = re.compile(
    r"\b(build|develop|implement|scaffold|deploy|platform|monorepo|end-to-end|repository|github)\b",
    re.IGNORECASE,
)


def is_conversational_user_turn(user_input: str) -> bool:
    """True when the user message is social/onboarding, not an actionable task."""
    ui = (user_input or "").strip()
    if not ui:
        return True
    if _BUILD_TASK_RE.search(ui):
        return False
    if len(ui) <= 160:
        if _CONVERSATIONAL_NAME_RE.search(ui):
            return True
        if _CONVERSATIONAL_GREETING_RE.search(ui):
            return True
    return False

# --- Coordinator phases (persisted on Cryptex orchestration ring) ---
PHASE_IDLE = "idle"
PHASE_AWAITING_DELEGATES = "awaiting_delegates"
PHASE_EVALUATING_WAVE = "evaluating_wave"
PHASE_LAUNCHED_PENDING_EXIT = "launched_pending_exit"

# --- Limits ---
COORDINATOR_MONITOR_MAX_ITERS = 3
COORDINATOR_BURN_MAX_ITERS = 6
COORDINATOR_WAKE_PROMPT_TOKEN_BUDGET = 150_000
TERMINATE_MIN_MEMBER_ITERATIONS = 18
TERMINATE_REQUIRES_ESCALATION = True

# Tools allowed while delegates run (schema-level — model cannot call others).
MONITORING_DELEGATES_ACTIVE_TOOLS = frozenset({
    "team", "await_delegates", "communicate", "switch_mode",
    "delegate_status", "scheduler",
})

# Immediately after team launch — force exit path.
POST_LAUNCH_TOOLS = frozenset({
    "communicate", "await_delegates", "switch_mode",
})

# Babysitting-only tools (used for idle/burn detection).
_BABYSIT_TOOLS = frozenset({"wait", "team", "delegate_status"})

# Tools that must not run while delegates are active (hard block).
_FORBIDDEN_WHILE_DELEGATES = frozenset({
    "write", "edit", "delete_file", "move_file", "bash", "server_install",
    "plan", "todo", "read", "list_dir", "grep", "glob", "semantic_search",
    "web_search", "web_fetch", "delegate", "task_complete",
})

# Team actions allowed while delegates run without escalation pending.
_TEAM_ACTIONS_WHILE_ACTIVE = frozenset({
    "inspect", "hint", "intervene", "list", "brief",
})

# Team actions blocked while delegates run (unless evaluating after terminal).
_TEAM_ACTIONS_BLOCKED_WHILE_ACTIVE = frozenset({
    "create", "launch", "advance", "pause", "resume", "disband", "rewake",
})


def delegates_running(delegate_manager: Any | None) -> bool:
    if delegate_manager is None:
        return False
    try:
        return delegate_manager.has_active_delegates()
    except Exception:
        return False


@dataclass(frozen=True)
class ToolPolicyInputs:
    """Snapshot of everything that affects the effective tool allowlist."""

    mode: AgentMode
    must_await_delegates: bool
    delegates_active: bool
    suppress_raw_delegate: bool
    is_coordinator: bool
    all_unlocked: frozenset[str]


def build_tool_policy_inputs(
    mode: AgentMode,
    state: LoopState,
    delegate_manager: Any | None,
    all_unlocked: set[str],
    hooks: Any | None,
) -> ToolPolicyInputs:
    return ToolPolicyInputs(
        mode=mode,
        must_await_delegates=bool(getattr(state, "must_await_delegates", False)),
        delegates_active=delegates_running(delegate_manager),
        suppress_raw_delegate=hook_suppresses_raw_delegate(hooks),
        is_coordinator=bool(state.coordinator_mode),
        all_unlocked=frozenset(all_unlocked),
    )


def compute_tool_policy_fingerprint(inputs: ToolPolicyInputs) -> str:
    """Hashable key — re-apply schema filter only when this changes."""
    blob = "|".join((
        inputs.mode.value,
        "1" if inputs.must_await_delegates else "0",
        "1" if inputs.delegates_active else "0",
        "1" if inputs.suppress_raw_delegate else "0",
        "1" if inputs.is_coordinator else "0",
        ",".join(sorted(inputs.all_unlocked)),
    ))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _base_tools_for_mode(mode: AgentMode, all_unlocked: frozenset[str]) -> frozenset[str]:
    if mode == AgentMode.EXECUTING:
        return all_unlocked
    mode_tools = get_allowed_tools(mode)
    if not mode_tools:
        return all_unlocked
    return frozenset(mode_tools)


def resolve_allowed_tools(inputs: ToolPolicyInputs) -> frozenset[str]:
    """Single policy: effective tool names for schema + executor enforcement."""
    allowed = _base_tools_for_mode(inputs.mode, inputs.all_unlocked)

    # General assistant use (chat, solo executing, responding): keep mode menu.
    # Do not shrink for background delegates or plan-wave rules.
    if inputs.mode in _ASSISTANT_FREEFORM_MODES:
        return allowed

    if not inputs.is_coordinator:
        return allowed

    if inputs.must_await_delegates:
        return POST_LAUNCH_TOOLS

    if inputs.delegates_active and inputs.mode in (
        AgentMode.MONITORING,
        AgentMode.DELEGATING,
        AgentMode.EVALUATING,
    ):
        return MONITORING_DELEGATES_ACTIVE_TOOLS

    if inputs.mode == AgentMode.MONITORING:
        allowed = allowed - frozenset({"wait", "todo", "task_complete", "read"})

    if inputs.suppress_raw_delegate:
        allowed = allowed - frozenset({"delegate"})

    return allowed


def tool_policy_schema_refresh_needed(
    state: LoopState,
    fingerprint: str,
) -> bool:
    return getattr(state, "_tool_policy_fingerprint", "") != fingerprint


def mark_tool_policy_applied(state: LoopState, fingerprint: str) -> None:
    state._tool_policy_fingerprint = fingerprint


def invalidate_tool_policy_cache(state: LoopState) -> None:
    """Force mode + runtime schema recompute on next refresh."""
    state._tool_policy_fingerprint = ""
    state._mode_schemas_applied = False


def apply_runtime_tool_filter(
    all_schemas: list[dict],
    all_unlocked: set[str],
    mode: AgentMode,
    state: LoopState,
    delegate_manager: Any | None,
    *,
    hooks: Any | None = None,
    suppress_raw_delegate: bool | None = None,
) -> tuple[list[dict], set[str]]:
    """Filter tool schemas to match resolve_allowed_tools."""
    if suppress_raw_delegate is not None:
        _suppress = suppress_raw_delegate
    else:
        _suppress = hook_suppresses_raw_delegate(hooks)
    inputs = ToolPolicyInputs(
        mode=mode,
        must_await_delegates=bool(getattr(state, "must_await_delegates", False)),
        delegates_active=delegates_running(delegate_manager),
        suppress_raw_delegate=_suppress,
        is_coordinator=bool(state.coordinator_mode),
        all_unlocked=frozenset(all_unlocked),
    )
    allowed = resolve_allowed_tools(inputs)

    if not allowed:
        return list(all_schemas), set(all_unlocked)

    filtered = [
        s for s in all_schemas
        if s.get("function", {}).get("name", "") in allowed
    ]
    unlocked = {t for t in all_unlocked if t in allowed}
    return filtered, unlocked


def refresh_tool_schemas(
    state: LoopState,
    all_schemas: list[dict],
    all_unlocked: set[str],
    mode: AgentMode,
    delegate_manager: Any | None,
    hooks: Any | None,
    *,
    force: bool = False,
) -> tuple[list[dict], set[str], bool]:
    """Re-filter schemas when policy fingerprint changes. Returns (schemas, unlocked, changed)."""
    inputs = build_tool_policy_inputs(mode, state, delegate_manager, all_unlocked, hooks)
    fingerprint = compute_tool_policy_fingerprint(inputs)
    if not force and not tool_policy_schema_refresh_needed(state, fingerprint):
        return all_schemas, set(all_unlocked), False
    filtered, unlocked = apply_runtime_tool_filter(
        all_schemas,
        all_unlocked,
        mode,
        state,
        delegate_manager,
        hooks=hooks,
    )
    mark_tool_policy_applied(state, fingerprint)
    return filtered, unlocked, True


def tool_not_allowed_message(
    tool_name: str,
    mode: AgentMode,
    allowed: frozenset[str],
) -> str:
    if mode == AgentMode.EXECUTING:
        return (
            f"BLOCKED: tool '{tool_name}' is not registered for this agent."
        )
    return (
        f"{mode.value.upper()} MODE: tool '{tool_name}' is not available. "
        f"Switch with switch_mode() — e.g. switch_mode(mode='executing') for "
        f"bash/files, switch_mode(mode='chat') for quick lookup and search."
    )


def block_tool_call(
    tool_name: str,
    args: dict[str, Any],
    state: LoopState,
    mode: AgentMode,
    delegate_manager: Any | None,
    *,
    has_pending_escalation: bool = False,
    hooks: Any | None = None,
    all_unlocked: set[str] | None = None,
) -> str | None:
    """Return block message if call must not proceed."""
    _unlocked = all_unlocked if all_unlocked is not None else set(state.unlocked_tools)
    _inputs = build_tool_policy_inputs(mode, state, delegate_manager, _unlocked, hooks)

    # Orchestration guards only apply in coordinator modes (not chat/executing).
    if mode in _ASSISTANT_FREEFORM_MODES or not state.coordinator_mode:
        _allowed = resolve_allowed_tools(_inputs)
        if tool_name not in _allowed and tool_name not in ("get_tool_schema",):
            return tool_not_allowed_message(tool_name, mode, _allowed)
        return None

    if getattr(state, "must_await_delegates", False):
        if tool_name not in POST_LAUNCH_TOOLS:
            return (
                "BLOCKED: Wave just launched — finish your management turn.\n"
                "Optional: communicate(status) to the stakeholder.\n"
                "Required: await_delegates(summary='Wave N executing — "
                "return on escalation/completion').\n"
                "Your team runs in the background; you will wake for "
                "escalations, wave completion, or scheduled review."
            )

    running = _inputs.delegates_active
    if not running:
        return None

    if mode in (AgentMode.MONITORING, AgentMode.DELEGATING):
        if tool_name in _FORBIDDEN_WHILE_DELEGATES:
            return (
                f"BLOCKED: Your team is executing — '{tool_name}' is IC work, "
                "not engineering-manager work right now.\n"
                "If a member escalated: team(inspect/hint/intervene).\n"
                "Otherwise: await_delegates(summary='...') and return when "
                "there is a decision to make.\n"
                "After the wave lands, switch_mode(evaluating) to review "
                "deliverables as second pair of eyes."
            )

        if tool_name == "wait":
            seconds = args.get("seconds", 0)
            try:
                sec = int(seconds)
            except (TypeError, ValueError):
                sec = 60
            if sec > 15:
                return (
                    f"BLOCKED: wait({sec}s) is idle polling — not EM oversight.\n"
                    "Your Kanban/WM already tracks wave state. Use "
                    "await_delegates(summary='Team executing — wake on "
                    "escalation/completion') unless a member needs a hint NOW."
                )

        if tool_name == "team":
            action = (args.get("action") or "").strip().lower()
            if action in _TEAM_ACTIONS_BLOCKED_WHILE_ACTIVE:
                return (
                    f"BLOCKED: team(action='{action}') while delegates are still "
                    "running.\n"
                    "Wait for wave completion (you will be re-invoked), or use "
                    "inspect/hint/intervene on the active team only."
                )
            if action == "intervene" and args.get("decision") == "terminate":
                msg = block_terminate_intervention(
                    state, args, has_pending_escalation=has_pending_escalation,
                )
                if msg:
                    return msg

    return None


def parse_escalation_steering(content: str) -> dict[str, Any]:
    """Extract team/member/budget hints from a TEAM MEMBER HELP REQUEST."""
    out: dict[str, Any] = {}
    if not content:
        return out
    m = re.search(r"Member #(\d+)", content)
    if m:
        out["member_idx"] = int(m.group(1))
    m = re.search(r"team_id='([^']+)'", content)
    if m:
        out["team_id"] = m.group(1)
    else:
        m = re.search(r"\[(team_[a-f0-9]+)\]", content)
        if m:
            out["team_id"] = m.group(1)
    m = re.search(r"writes:\s*(\d+)", content, re.IGNORECASE)
    if m:
        out["writes"] = int(m.group(1))
    m = re.search(r"paths_requested:\s*(.+)", content, re.IGNORECASE)
    if m:
        out["paths"] = [
            p.strip().strip("'\"")
            for p in m.group(1).split(",")
            if p.strip()
        ]
    if "escalate:" in content.lower():
        out["proactive"] = True
    if "file_access" in content.lower():
        out["file_access"] = True
    return out


def block_terminate_intervention(
    state: LoopState,
    args: dict[str, Any],
    *,
    has_pending_escalation: bool = False,
) -> str | None:
    """Gate orchestrator-initiated terminate — prefer hint/extend/rewake."""
    if args.get("decision") != "terminate":
        return None
    if TERMINATE_REQUIRES_ESCALATION and not (
        has_pending_escalation or getattr(state, "has_pending_escalation", False)
    ):
        return (
            "BLOCKED: team(intervene, decision='terminate') requires a delegate "
            "escalation or repeated failure evidence.\n"
            "Try: team(action='hint') with ONE concrete next file, or "
            "team(action='intervene', decision='extend') first.\n"
            "After the wave ends use switch_mode(evaluating) + plan(accept_partial) "
            "if artifacts exist on disk."
        )

    if has_pending_escalation or getattr(state, "has_pending_escalation", False):
        _pending_member = getattr(state, "pending_escalation_member_idx", -1)
        _member = args.get("member")
        if _pending_member >= 0 and _member is not None:
            try:
                if int(_member) != _pending_member:
                    return (
                        f"BLOCKED: The pending escalation is from team member "
                        f"#{_pending_member}, not #{int(_member)}.\n"
                        f"Use team(action='intervene', member={_pending_member}, "
                        f"decision='extend' or 'hint', message='...')."
                    )
            except (TypeError, ValueError):
                pass

        _writes = getattr(state, "pending_escalation_writes", 0)
        if _writes > 0:
            return (
                "BLOCKED: The escalating member reported files on disk "
                f"(writes={_writes}). Do NOT terminate yet.\n"
                "Use team(action='intervene', decision='extend', "
                "extra_iterations=15, message='Finish these specific fixes "
                "then task_complete') or a targeted hint listing exact files.\n"
                "Terminate only if the member wrote nothing useful or you are "
                "abandoning the step (then plan(accept_partial) after the wave)."
            )

        return (
            "BLOCKED: A member is paused on escalate() waiting for you.\n"
            "Default: team(action='intervene', decision='extend', "
            "extra_iterations=15, message='<one paragraph of exact next edits>').\n"
            "Only terminate if there is zero useful output on disk."
        )
    return None


def on_team_launched(
    state: LoopState,
    team_id: str,
    *,
    record_phase: Callable[[str, str], None] | None = None,
) -> None:
    state.must_await_delegates = True
    state.coordinator_phase = PHASE_LAUNCHED_PENDING_EXIT
    state.coordinator_monitor_iters = 0
    state.coordinator_burn_iters = 0
    state.active_mode = AgentMode.MONITORING
    invalidate_tool_policy_cache(state)
    if record_phase:
        record_phase(PHASE_LAUNCHED_PENDING_EXIT, f"team={team_id}")


def on_await_delegates(
    state: LoopState,
    *,
    record_phase: Callable[[str, str], None] | None = None,
) -> None:
    state.must_await_delegates = False
    state.coordinator_phase = PHASE_AWAITING_DELEGATES
    state.coordinator_monitor_iters = 0
    state.coordinator_burn_iters = 0
    if record_phase:
        record_phase(PHASE_AWAITING_DELEGATES, "await_delegates")


def on_evaluating_wave(
    state: LoopState,
    *,
    record_phase: Callable[[str, str], None] | None = None,
) -> None:
    state.must_await_delegates = False
    state.coordinator_phase = PHASE_EVALUATING_WAVE
    if record_phase:
        record_phase(PHASE_EVALUATING_WAVE, "wave_terminal")


def iter_tool_names(tool_calls: list[dict] | None) -> list[str]:
    if not tool_calls:
        return []
    return [
        tc.get("function", {}).get("name", "")
        for tc in tool_calls
    ]


def _parse_tool_args(tc: dict) -> dict:
    args = tc.get("function", {}).get("arguments", "{}")
    if isinstance(args, str):
        import json
        try:
            return json.loads(args) or {}
        except Exception:
            return {}
    return args or {}


def is_passive_review_iteration(
    tool_names: list[str],
    tool_calls: list[dict] | None,
) -> bool:
    """EM review/monitor iteration — inspect, plan(read), read/list_dir only."""
    if not tool_names:
        return False
    _passive = frozenset({
        "read", "list_dir", "glob", "switch_mode", "communicate",
        "delegate_status", "await_delegates", "todo",
    })
    for name in tool_names:
        if name in _passive:
            continue
        if name == "team":
            continue
        if name == "plan":
            continue
        return False
    for tc in tool_calls or []:
        fn = tc.get("function", {}).get("name", "")
        parsed = _parse_tool_args(tc)
        if fn == "team":
            if parsed.get("action", "") not in ("inspect", "list", "brief"):
                return False
        elif fn == "plan":
            if parsed.get("action", "read") not in ("read", "list", ""):
                return False
    return True


def is_babysit_iteration(tool_names: list[str], tool_calls: list[dict] | None) -> bool:
    """True when iteration is passive monitoring (wait/inspect only)."""
    if is_passive_review_iteration(tool_names, tool_calls):
        return True
    if not tool_names:
        return False
    if not all(n in _BABYSIT_TOOLS for n in tool_names):
        return False
    for tc in tool_calls or []:
        if tc.get("function", {}).get("name") == "team":
            parsed = _parse_tool_args(tc)
            if parsed.get("action", "") not in ("inspect", "list", "brief"):
                return False
    return True


def is_coordinator_burn_iteration(
    tool_names: list[str],
    delegate_manager: Any | None,
) -> bool:
    """Any coordinator iteration while delegates run counts toward burn budget."""
    if not tool_names or not delegates_running(delegate_manager):
        return False
    allowed_while_bg = MONITORING_DELEGATES_ACTIVE_TOOLS | frozenset({"wait"})
    return all(n in allowed_while_bg for n in tool_names)


def update_coordinator_counters(
    state: LoopState,
    tool_names: list[str],
    tool_calls: list[dict] | None,
    delegate_manager: Any | None,
) -> None:
    if not delegates_running(delegate_manager):
        state.coordinator_monitor_iters = 0
        state.coordinator_burn_iters = 0
        return

    # Review-only turns (inspect / plan read / list_dir) are not babysitting
    # violations and should not consume burn/monitor budgets.
    if is_passive_review_iteration(tool_names, tool_calls):
        return

    if is_babysit_iteration(tool_names, tool_calls):
        state.idle_monitor_cycles += 1
    elif is_coordinator_burn_iteration(tool_names, delegate_manager):
        state.coordinator_burn_iters += 1
        state.coordinator_monitor_iters += 1
    else:
        state.idle_monitor_cycles = 0

    if state.must_await_delegates:
        state.coordinator_monitor_iters += 1


def should_force_coordinator_yield(
    state: LoopState,
    delegate_manager: Any | None,
    *,
    dispatch_source: str = "",
) -> tuple[bool, str]:
    """Return (True, reason) when loop must exit to background."""
    if not delegates_running(delegate_manager):
        return False, ""

    _src = dispatch_source or getattr(state, "dispatch_source", "") or ""
    _mgmt_wake = (
        _src.startswith("team_completion_review:")
        or _src.startswith("team_wave_complete:")
        or _src.startswith("team_member_escalation:")
        or _src.startswith("pending_wave_launch:")
    )

    # Evaluating = EM review/advance — do not cap inspect/read/plan work
    # the way we cap idle monitoring while a wave executes.
    if getattr(state, "active_mode", None) == AgentMode.EVALUATING:
        if (
            not _mgmt_wake
            and state.coordinator_wake_prompt_tokens >= COORDINATOR_WAKE_PROMPT_TOKEN_BUDGET
        ):
            return True, "wake_token_budget"
        return False, ""

    if getattr(state, "must_await_delegates", False):
        if state.coordinator_monitor_iters >= 1:
            return True, "post_launch_yield"

    if state.coordinator_monitor_iters >= COORDINATOR_MONITOR_MAX_ITERS:
        return True, "monitor_iter_cap"

    if state.coordinator_burn_iters >= COORDINATOR_BURN_MAX_ITERS:
        return True, "coordinator_burn"

    if state.idle_monitor_cycles >= 3:
        return True, "idle_monitor"

    if (
        not _mgmt_wake
        and state.coordinator_wake_prompt_tokens >= COORDINATOR_WAKE_PROMPT_TOKEN_BUDGET
    ):
        return True, "wake_token_budget"

    return False, ""


def should_auto_launch_next_wave(
    team_manager: Any,
    delegate_manager: Any,
    team_id: str,
) -> tuple[bool, str]:
    """Policy guard for system-initiated team(launch) after auto-reconcile."""
    try:
        if delegate_manager.has_active_delegates():
            return False, "delegates still running from a prior wave"
    except Exception:
        return False, "could not verify delegate state"

    next_team = team_manager.load(team_id)
    if next_team is None:
        return False, f"team {team_id} not found"
    if next_team.status != "created":
        return False, f"team status is {next_team.status!r} (need created)"
    if next_team.batch_id:
        return False, "team already has a batch_id"

    for team in team_manager._teams.values():
        if team.id == team_id:
            continue
        if team.status == "active":
            return False, f"another team is active ({team.id})"

    return True, ""


def build_pending_wave_launch_wake(
    team_id: str,
    *,
    team_name: str = "",
    reconcile_reason: str = "",
    block_reason: str = "",
) -> str:
    """User-visible wake when auto-launch was deferred."""
    label = f"{team_name} [{team_id}]" if team_name else team_id
    lines = [
        "[WAVE READY — LAUNCH REQUIRED]",
        f"Next wave team {label} is prepared but delegates are not running.",
    ]
    if reconcile_reason:
        lines.append(f"Prepared by: {reconcile_reason}")
    if block_reason:
        lines.append(f"Auto-launch blocked: {block_reason}")
    lines.append(
        f"[BREADCRUMB] NEXT: team(action='launch', team_id='{team_id}'). "
        "Do NOT implement delegatable steps yourself (bash/write/edit)."
    )
    return "\n".join(lines)


def build_orchestration_wake_message(
    *,
    dispatch_source: str,
    dual_wm: Any | None,
    plan_progress: str = "",
    delegate_summary: str = "",
    coordinator_phase: str = "",
) -> str:
    """Compact system message for orchestration wake-ups."""
    lines = [
        "[ENGINEERING MANAGER WAKE]",
        "You own the plan, Kanban, and holistic view. Your team executes "
        "in the background.",
        "Act when there is a management decision: escalation, stuck member, "
        "wave landed, or acceptance review.",
        "Do NOT idle-poll with wait(60+) or repeated inspect loops — "
        "Cryptex WM holds continuity.",
    ]
    if coordinator_phase:
        lines.append(f"Phase: {coordinator_phase}")
    if plan_progress:
        lines.append(f"Plan: {plan_progress}")
    if delegate_summary:
        lines.append(f"Delegates: {delegate_summary}")
    if dual_wm is not None:
        try:
            for line in dual_wm.get_orchestration_wake_lines():
                lines.append(line)
        except Exception:
            pass
    src = dispatch_source or "orchestration"
    lines.append(f"Wake source: {src}")
    if src.startswith("team_completion_review:"):
        lines.append(
            "MANDATORY ACTION: One or more delegates are in completion review "
            "(batched below). Work the list once."
        )
        lines.append(
            "team(inspect) only if needed, then team(intervene, decision='approve') "
            "per waiting member. Do NOT approve members already done."
        )
        lines.append(
            "See [WAKE ATTENTION] in working memory for the pending list. "
            "Do NOT call await_delegates until all reviews are resolved."
        )
        return "\n".join(lines)
    if src.startswith("team_member_escalation:"):
        lines.append(
            "URGENT: A delegate called escalate() and is paused for your decision."
        )
        lines.append(
            "Respond with team(action='intervene', decision='extend' or 'hint'). "
            "This is NOT a completion review — do not batch-approve the whole wave."
        )
        lines.append(
            "If blocked on GitHub/gh: hint them to run "
            "bash('echo TOKEN | gh auth login --with-token') with the user's "
            "token, or search clawhub(action='search', query='github'). "
            "You may also clawhub/discover_tools yourself before hinting."
        )
        return "\n".join(lines)
    if src.startswith("team_wave_complete:"):
        lines.append(
            "Wave landed — review deliverables, then team(advance) or "
            "intervene on any stuck member."
        )
        lines.append(
            "If you stall without advancing, the system may auto-close the "
            "wave after the grace window and auto-launch the next wave when "
            "policy allows (no active delegates)."
        )
        return "\n".join(lines)
    if src.startswith("pending_wave_launch:"):
        lines.append(
            "A next-wave team exists but delegates did not start automatically."
        )
        lines.append(
            "Call team(action='launch', team_id=...) — do NOT implement wave "
            "steps yourself with bash/write."
        )
        return "\n".join(lines)
    lines.append(
        "Typical flow: team(inspect) if needed → hint/intervene if stuck → "
        "switch_mode(evaluating) to review deliverables OR "
        "await_delegates(summary='...') if wave still running cleanly."
    )
    return "\n".join(lines)


def trim_context_for_orchestration_wake(
    context: list[dict],
    dispatch_source: str,
    *,
    keep_tail: int = 8,
) -> list[dict]:
    """Drop heavy history on scheduler/checkback wakes — WM holds continuity."""
    if not is_orchestration_dispatch_source(dispatch_source):
        return context

    system_msgs: list[dict] = []
    other: list[dict] = []
    for msg in context:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            other.append(msg)

    if len(other) <= keep_tail + 2:
        return context

    trimmed = system_msgs[:1] + other[-keep_tail:]
    logger.info(
        "Orchestration wake: trimmed context %d → %d messages",
        len(context), len(trimmed),
    )
    return trimmed


def checkback_interval_seconds(delegates_active: bool) -> float:
    """Longer check-back when event-driven wakes are wired."""
    return 600.0 if delegates_active else 120.0


def wake_context_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def should_suppress_checkback_wake(
    dual_wm: Any | None,
    dispatch_source: str,
    delegates_active: bool,
) -> bool:
    """Skip redundant scheduler check-backs when WM orch state unchanged."""
    if not delegates_active or dual_wm is None:
        return False
    src = dispatch_source or ""
    if not (
        src.startswith("scheduler")
        or "checkback" in src.lower()
        or src.startswith("team_checkback:")
    ):
        return False
    try:
        new_hash = dual_wm.get_orchestration_wake_hash()
        old_hash = dual_wm.get_last_checkback_hash()
        if old_hash and old_hash == new_hash:
            return True
        dual_wm.set_last_checkback_hash(new_hash)
    except Exception:
        return False
    return False
