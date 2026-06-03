"""Agentic loop evaluator — completion, guards, stall detection.

Used by the v5 loop (``loop.py``):

- ``should_complete`` — exit decision after each iteration
- ``check_guards`` — iteration / tool-call / timeout limits
- ``detect_stall`` — repetitive-tool and assessment-loop nudges
- ``Directive`` / ``get_directive_message`` — error-recovery injection templates

``InteroceptiveSnapshot`` is collected by bridge hooks for biological
state (hormones, ANS, network dynamics) used elsewhere in the runtime.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from .types import AgentMode
from enum import Enum, auto
from typing import Any

from .types import LoopConfig, LoopState

logger = logging.getLogger(__name__)

# Tools that only gather information (don't count as "action").
# A tool in this set can succeed and return data, but that alone
# should NOT satisfy the "had_actions" gate for TASK_COMPLETE.
LOOKUP_TOOLS = frozenset({
    "read", "web_search", "web_fetch", "clawhub",
    "vision", "memory_search", "memory_get",
    "chat_history", "email_history",
    "drive_search", "drive_list", "drive_read",
    "calendar_list",
    "plan",
})

# Discovery / identity tools — do not count as task delivery for implicit exit.
NON_SUBSTANTIVE_TOOLS = LOOKUP_TOOLS | frozenset({
    "contacts", "list_dir", "glob", "grep", "semantic_search",
    "discover_tools", "file_history", "chat_history",
})


def has_substantive_tool_success(state: LoopState) -> bool:
    """True when at least one successful tool call actually changed state."""
    for name, count in (state.tool_successes or {}).items():
        if count > 0 and name not in NON_SUBSTANTIVE_TOOLS:
            return True
    return False


def requires_substantive_delivery(state: LoopState) -> bool:
    """Tasks that must run bash/write/etc. before prose-only loop exit."""
    hints = {h.strip().lower() for h in (state.hints or []) if h and h.strip()}
    if "setup:instruction_skill" in hints or "setup:native_skill" in hints:
        return True
    goals = " ".join(g or "" for g in (state.goals or [])).lower()
    return any(
        tok in goals
        for tok in (
            "install", "configure", "deploy", "build", "setup",
            "verify bot", "connect",
        )
    )

_FAILURE_PATTERNS = (
    "not logged in", "not found", "command not found",
    "permission denied", "error:", "failed",
    "unknown flag", "unknown shorthand", "unknown command",
    "INTERACTIVE PROMPT DETECTED",
    "No such file", "connection refused",
    "not authenticated", "not recognized",
    "no files found", "no events found", "no results",
    "no matching", "0 results", "0 files",
)

# Patterns that indicate a tool returned successfully but the action
# is NOT yet complete (e.g. confirmation gates, pending user approval).
# These block TASK_COMPLETE the same way failure patterns do.
_PENDING_PATTERNS = (
    "call again with confirmed",
    "needs_confirmation",
    "draft email for review",
    "draft reply for review",
    "present this draft to the user",
)


# ---------------------------------------------------------------------------
# Interoceptive snapshot — full biological state for evaluator input
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InteroceptiveSnapshot:
    """Full biological state collected once per iteration.

    Every field is already computed by an existing NLS subsystem —
    the snapshot just collects them into one struct for the evaluator.
    """

    # --- Hormones (from hypothalamus.get_levels()) ---
    cortisol: float = 0.20
    dopamine: float = 0.50
    norepinephrine: float = 0.30
    serotonin: float = 0.50
    oxytocin: float = 0.20
    acetylcholine: float = 0.30

    # --- Thalamus modifiers (from hypothalamus.get_thalamus_modifiers()) ---
    suppression_shift: float = 0.0
    exploration_bonus: float = 0.0
    confidence_boost: float = 0.0
    trust_boost: float = 0.0
    meta_weight_shift: float = 0.0

    # --- ANS signals ---
    success_streak: int = 0
    failure_streak: int = 0
    energy: float = 1.0

    # --- Predictive processing ---
    prediction_error: float = 0.0
    uncertainty: float = 0.0

    # --- Self state ---
    cognitive_load: float = 0.0
    valence: float = 0.0
    arousal: float = 0.0

    # --- Calibrator ---
    skill_relevance: float = 0.0

    # --- OFC ---
    social_value: float = 0.0

    # --- Network Dynamics (ECN/SN/DMN) ---
    network_ecn: float = 0.0
    network_sn: float = 0.0
    network_dmn: float = 0.0
    dominant_network: str = ""


# ---------------------------------------------------------------------------
# Directive — injected recovery nudges (error-recovery path in loop.py)
# ---------------------------------------------------------------------------


class Directive(Enum):
    """The ONE signal the evaluator produces per turn."""

    CONTINUE = auto()
    ERROR_RECOVERY = auto()
    EXPLORE = auto()
    TASK_COMPLETE = auto()
    STALLED = auto()
    VERIFY = auto()
    ABORT = auto()


# Directive → message template (injected as a system message)
_DIRECTIVE_TEMPLATES: dict[Directive, str] = {
    Directive.CONTINUE: "",  # no injection
    Directive.ERROR_RECOVERY: (
        "DIAGNOSE: Read the error output above — it tells you exactly "
        "what went wrong. Fix the specific issue (wrong flag, missing "
        "binary, wrong path) and retry with the corrected command. "
        "If unsure, search ClawHub: clawhub(action='search', query='...'). "
        "If you find a skill, INSTALL it: clawhub(action='install', slug='...'). "
        "Do NOT retry the same failing command."
    ),
    Directive.EXPLORE: (
        "PIVOT: Your current approach is not working. Try something "
        "genuinely different:\n"
        "1. Search ClawHub for a skill: clawhub(action='search', query='...')\n"
        "2. If found, INSTALL it: clawhub(action='install', slug='...')\n"
        "3. Or web_search for the correct procedure\n"
        "4. Or ask_user() if you're stuck\n"
        "Do NOT repeat the same tool or command."
    ),
    Directive.TASK_COMPLETE: (
        "The task appears complete — all plan steps are done and tools "
        "confirm success. Call task_complete(summary='...') with a brief "
        "summary of what you accomplished. If you still need one more "
        "action, do it first — then call task_complete."
    ),
    Directive.STALLED: (
        "You have not taken any action yet. Use a tool call NOW to "
        "make progress on the user's request. Do not respond with "
        "only text when a tool exists for the action. "
        "If uncertain about exact names or paths, DISCOVER them "
        "first (e.g. list files, list repos, check installed tools)."
    ),
    Directive.VERIFY: (
        "VERIFY: You just took an action — confirm it actually worked "
        "before moving on. Check the output, read the file, or run "
        "a status command. Do not assume success."
    ),
    Directive.ABORT: "",  # handled by loop termination
}



def get_directive_message(directive: Directive) -> str | None:
    """Return the message template for a directive, or None for CONTINUE/ABORT."""
    msg = _DIRECTIVE_TEMPLATES.get(directive, "")
    return msg if msg else None


# ===================================================================
# Loop completion evaluator (v5)
# ===================================================================


_MAX_GOAL_BLOCKS = 2


def _delegates_in_background(
    state: "LoopState",
    delegate_manager: Any | None = None,
) -> bool:
    """True when sub-agents are running, including across loop restarts."""
    if state.delegate_count > 0:
        return True
    if delegate_manager is not None:
        try:
            return delegate_manager.has_active_delegates()
        except Exception:
            pass
    return False


async def should_complete(
    state: "LoopState",
    config: "LoopConfig",
    hooks: Any = None,
    vllm_client: Any = None,
    delegate_manager: Any | None = None,
    adapter_name: str | None = None,
) -> bool:
    """Determine if the v5 loop should exit (task delivered or force exit).

    Simple decision tree:
    1. consecutive_text_only >= limit → True (force exit)
    2. Plan with pending steps → False
    3. Goals exist → evaluate; if pending + blocks < limit → False
    4. No plan, no goals → True (trust the model)
    """
    logger.info(
        "[EVAL] should_complete? consec_text=%d/%d goals=%d "
        "total_tc=%d goal_blocks=%d/%d profile=%s",
        state.consecutive_text_only, config.consecutive_text_only_limit,
        len(state.goals), state.total_tool_calls,
        state.goal_block_count, _MAX_GOAL_BLOCKS,
        getattr(state, "orchestration_profile", ""),
    )

    _profile = getattr(state, "orchestration_profile", "") or ""
    _last_text = getattr(state, "_last_iter_text", "") or ""
    from nls.agentic.orchestration_profile_spec import (
        evaluate_plan_artifact_complete,
        evaluate_plan_step_started_complete,
        get_profile_spec,
    )
    _spec = get_profile_spec(_profile)

    if evaluate_plan_artifact_complete(state, hooks):
        logger.info("[EVAL] -> COMPLETE (plan deliverables verified)")
        return True

    if evaluate_plan_step_started_complete(state, hooks):
        logger.info("[EVAL] -> COMPLETE (plan created + step in progress + summary)")
        return True

    if (
        _spec.complete_on_prose
        and state.consecutive_text_only >= 1
        and len(_last_text) > 80
        and state.total_tool_calls == 0
    ):
        logger.info(
            "[EVAL] -> COMPLETE (conversational profile, prose delivered)"
        )
        return True

    if (
        _spec.profile == "conversational"
        and _spec.complete_on_prose
        and state.total_tool_calls > 0
        and state.consecutive_text_only >= 1
        and len(_last_text) > 80
    ):
        logger.info(
            "[EVAL] -> COMPLETE (conversational profile after lookup tools)"
        )
        return True

    if state.has_pending_escalation:
        logger.info("[EVAL] -> CONTINUE (pending escalation from sub-agents)")
        return False

    if state.consecutive_text_only >= config.consecutive_text_only_limit:
        # Coordinators with active plans should NOT auto-exit on text-only
        # limit — the loop.py stall nudge will redirect them to act.
        if state.coordinator_mode and hooks and hooks.has_active_plan:
            try:
                if hooks.has_active_plan():
                    logger.info(
                        "[EVAL] -> CONTINUE (text_only limit hit but "
                        "coordinator has active plan — nudge will fire)"
                    )
                    return False
            except Exception:
                pass
        logger.info("[EVAL] -> COMPLETE (consecutive_text_only limit)")
        return True

    # Plan-complete override: if the orchestrator recently called
    # plan(complete), the task is definitively finished regardless of
    # stale in-progress todos left over from sub-agent waves.
    _plan_just_completed = False
    if state.coordinator_mode and state.cumulative_actions:
        _tail = state.cumulative_actions[-5:]
        _plan_just_completed = any(
            "plan(complete" in a or "plan(status=done" in a.lower()
            for a in _tail
        )
    if _plan_just_completed:
        logger.info(
            "[EVAL] -> COMPLETE (plan(complete) called — "
            "orchestrator task finished)"
        )
        return True

    # Active plan/team check — must come before the implicit delivery
    # shortcut.  If the orchestrator just launched a team and then
    # writes a status update, that is NOT task completion.
    # HOWEVER: if the agent delivered a substantive response that asks
    # the user for input (contains "?"), yield control — the plan can
    # resume on the next user message.
    if hooks and hooks.has_active_plan:
        try:
            if hooks.has_active_plan():
                _last_text = getattr(state, "_last_iter_text", "") or ""
                _asking_user = (
                    state.consecutive_text_only == 1
                    and state.total_tool_calls > 0
                    and state.total_tool_calls <= 5
                    and len(_last_text) > 100
                    and "?" in _last_text[-500:]
                    and (
                        not state.coordinator_mode
                        or not _delegates_in_background(
                            state, delegate_manager,
                        )
                    )
                )
                if _asking_user:
                    logger.info(
                        "[EVAL] -> COMPLETE (active plan but agent is "
                        "asking user for input — yielding, text_len=%d)",
                        len(_last_text),
                    )
                    return True

                # Coordinator status updates while a plan still has open
                # steps must not exit the loop — fall through to CONTINUE
                # (active plan) below.  Yield-on-status only applies when
                # the orchestrator is asking the user a question and there
                # is no remaining plan work (handled outside this block).

                # MONITORING wrap-up: orchestrator launched delegates and
                # produced a status update.  Delegates run in the background
                # — the orchestrator can exit to save tokens and will be
                # re-activated when delegates finish.  Without this, the
                # loop spins in wait→inspect→text cycles burning tokens.
                # Also covers RESPONDING mode: the agent answered the user
                # inline while delegates run — same exit logic applies.
                _recent = state.cumulative_actions[-8:]
                _used_await = any("await_delegates" in a for a in _recent)
                _monitoring_wrap_up = (
                    state.active_mode in (AgentMode.MONITORING, AgentMode.RESPONDING)
                    and state.consecutive_text_only >= 1
                    and state.total_tool_calls >= 3
                    and _delegates_in_background(state, delegate_manager)
                    and len(_last_text) > 100
                    and _used_await
                )
                if _monitoring_wrap_up:
                    logger.info(
                        "[EVAL] -> COMPLETE (%s — await_delegates used, "
                        "background delegate(s), text_len=%d)",
                        state.active_mode.value, len(_last_text),
                    )
                    return True
                if (
                    state.active_mode in (AgentMode.MONITORING, AgentMode.DELEGATING)
                    and _delegates_in_background(state, delegate_manager)
                    and state.consecutive_text_only >= 1
                    and not _used_await
                ):
                    logger.info(
                        "[EVAL] -> CONTINUE (monitoring — call "
                        "await_delegates, not task_complete or text-only exit)"
                    )
                    return False

                # Idle monitoring hard exit: the agent has been cycling
                # through wait/inspect without progress.  Force exit
                # even if the agent hasn't called task_complete.
                _idle_limit = getattr(state, "idle_monitor_cycles", 0)
                if (
                    _idle_limit >= 4
                    and _delegates_in_background(state, delegate_manager)
                    and _used_await
                ):
                    logger.info(
                        "[EVAL] -> COMPLETE (idle monitor exit after "
                        "await_delegates — %d cycles, delegate_count=%d)",
                        _idle_limit, state.delegate_count,
                    )
                    return True

                logger.info("[EVAL] -> CONTINUE (active plan)")
                return False
        except Exception:
            pass

    # Coordinator in early discussion phase: entered PLANNING mode due to
    # goal count but hasn't created a plan or launched any teams yet.
    # When the agent asks the user a question, yield control — the user
    # needs to respond before the agent can meaningfully plan/delegate.
    # Without this, pending goals ("build", "deploy") keep the loop running
    # and the agent produces duplicate architecture summaries.
    if (
        state.coordinator_mode
        and state.consecutive_text_only == 1
        and state.total_tool_calls > 0
    ):
        _last_text_cd = getattr(state, "_last_iter_text", "") or ""
        _no_active_plan = not (
            hooks and hooks.has_active_plan
            and hooks.has_active_plan()
        )
        _no_delegates = state.delegate_count == 0
        _is_question = "?" in _last_text_cd[-500:] and len(_last_text_cd) > 100

        if _no_active_plan and _no_delegates and _is_question:
            logger.info(
                "[EVAL] -> COMPLETE (coordinator asking user questions "
                "before plan/team — yielding, text_len=%d)",
                len(_last_text_cd),
            )
            return True

    # When the model delivers a prose answer after successful tool use,
    # treat it as implicit completion (model saying "I'm done" in text).
    # task_complete() is the explicit path; evaluate_goals() is the
    # micro-inference fallback when prose alone is ambiguous.
    _has_deferred_goals = any(
        "send notification" in (g or "").lower()
        for g in state.goals
    )
    _last_text_len = len(getattr(state, "_last_iter_text", "") or "")
    _implicit_min_chars = 100
    if (
        _spec.profile in ("conversational", "solo_structured")
        and state.total_tool_calls <= 5
    ):
        _implicit_min_chars = 25

    _prose_verdict = getattr(state, "last_prose_verdict", "") or ""
    if _prose_verdict == "duplicate":
        logger.info("[EVAL] -> COMPLETE (duplicate prose — suppressed)")
        return True
    if _prose_verdict == "awaiting_user_input":
        logger.info("[EVAL] -> COMPLETE (awaiting user input — yield once)")
        return True
    if (
        _prose_verdict == "deliverable_done"
        and state.consecutive_text_only >= 1
        and _last_text_len >= _implicit_min_chars
        and (
            has_substantive_tool_success(state)
            or not requires_substantive_delivery(state)
        )
    ):
        logger.info(
            "[EVAL] -> COMPLETE (prose verdict deliverable_done, text_len=%d)",
            _last_text_len,
        )
        return True

    if (
        state.consecutive_text_only >= 1
        and len(state.final_response or state.user_input) > 0
        and not _has_deferred_goals
        and not state.coordinator_mode
        and _spec.complete_on_implicit_delivery
        and _last_text_len >= _implicit_min_chars
        and _instruction_skill_setup_in_progress(state)
        and (state.goals or requires_substantive_delivery(state))
        and _prose_verdict not in ("awaiting_user_input", "deliverable_done")
    ):
        logger.info(
            "[EVAL] -> CONTINUE (instruction-skill setup: call task_complete "
            "after verify — prose-only exit blocked)",
        )
        return False
    if (
        state.consecutive_text_only >= 1
        and state.total_tool_calls > 0
        and len(state.final_response or state.user_input) > 0
        and not _has_deferred_goals
        and not state.coordinator_mode
        and _spec.complete_on_implicit_delivery
        and _last_text_len >= _implicit_min_chars
        and (
            has_substantive_tool_success(state)
            or not requires_substantive_delivery(state)
        )
    ):
        logger.info(
            "[EVAL] -> COMPLETE (tool + prose delivery, text_len=%d, "
            "tools=%d — implicit task delivery)",
            _last_text_len, state.total_tool_calls,
        )
        return True
    if (
        state.consecutive_text_only >= 1
        and requires_substantive_delivery(state)
        and not has_substantive_tool_success(state)
        and _spec.complete_on_implicit_delivery
    ):
        logger.info(
            "[EVAL] -> CONTINUE (lookup-only tools so far; "
            "setup/install task needs bash or write)",
        )
        return False

    if state.goals:
        if (
            state.total_tool_calls == 0
            and _profile == "conversational"
            and state.consecutive_text_only >= 1
            and len(_last_text) > 80
        ):
            logger.info(
                "[EVAL] -> COMPLETE (light profile delivered without tools)"
            )
            return True
        if state.total_tool_calls == 0:
            logger.info("[EVAL] -> CONTINUE (goals exist, zero tool calls)")
            return False

        from .goals import evaluate_goals

        if vllm_client is not None:
            pending = await evaluate_goals(
                vllm_client,
                state.goals,
                "\n".join(state.cumulative_actions[-20:]),
                previous_pending=state.last_pending_indices,
                hints=state.hints or None,
                adapter_name=adapter_name,
            )
            if hooks is not None:
                from nls.agentic.task_epoch_hygiene import apply_goal_evaluation_to_wm

                apply_goal_evaluation_to_wm(
                    hooks,
                    state.goals,
                    pending,
                    previous_pending=state.last_pending_indices,
                )
            state.last_pending_indices = pending
            if pending:
                state.goal_block_count += 1
                logger.info(
                    "[EVAL] pending_goals=%s block=%d/%d",
                    pending, state.goal_block_count, _MAX_GOAL_BLOCKS,
                )
                if state.goal_block_count < _MAX_GOAL_BLOCKS:
                    logger.info("[EVAL] -> CONTINUE (pending goals)")
                    return False
        else:
            if (
                requires_substantive_delivery(state)
                and not has_substantive_tool_success(state)
            ):
                logger.info(
                    "[EVAL] -> CONTINUE (goals exist, no vllm_client, "
                    "lookup-only — need substantive tools)",
                )
                return False
            logger.info("[EVAL] -> COMPLETE (goals exist, no vllm_client)")
            return True

    # Verification gate: for coordinator loops, check if the plan had
    # acceptance criteria or involves a runnable service before allowing
    # completion.  Only fires once (uses state metadata to avoid loops).
    if state.coordinator_mode and not state.verification_gate_passed:
        try:
            if hooks and hooks.has_active_plan:
                _plan_active = hooks.has_active_plan()
            else:
                _plan_active = False

            _has_files_written = len(state.files_written) > 0
            _has_enough_actions = state.total_tool_calls >= 5

            if _has_files_written and _has_enough_actions and not _plan_active:
                state.verification_gate_passed = True
                logger.info(
                    "[EVAL] VERIFICATION GATE: coordinator completed work "
                    "(%d files, %d tool calls) — injecting verification nudge",
                    len(state.files_written), state.total_tool_calls,
                )
                return False
        except Exception:
            pass

    if (
        getattr(state, "must_delegate_before_impl", False)
        and not getattr(state, "orchestrator_recovery", False)
    ):
        logger.info(
            "[EVAL] -> CONTINUE (orchestrator must plan+team before exiting)"
        )
        return False

    logger.info("[EVAL] -> COMPLETE (default)")
    return True


def check_guards(
    state: "LoopState",
    config: "LoopConfig",
    *,
    has_pending_plan: bool = False,
    has_active_team: bool = False,
) -> str | None:
    """Check all v4 guards. Returns exit_reason string or None.

    Checks in order:
    1. Iteration limit exceeded (auto-extends if plan has pending steps)
    2. Total tool calls exceeded
    3. Per-tool retry limit exceeded (any tool)
    4. Total timeout exceeded (skipped when orchestrator has active teams)
    5. Consecutive errors exceeded
    """
    if state.iteration - state.wait_only_iterations >= config.max_iterations:
        if has_active_team:
            # Orchestrator supervising teams — extend generously.
            # Most iterations are wait/inspect, not real work.
            new_limit = config.max_iterations + config.max_iterations_extension
            logger.info(
                "[GUARD] Budget extended %d → %d (active team running)",
                config.max_iterations, new_limit,
            )
            config.max_iterations = new_limit
        elif (
            has_pending_plan
            and state.consecutive_errors < 2
            and config.max_iterations < config.max_total_iterations
            and not getattr(state, "orchestrator_recovery", False)
            and state.guard_iteration_extensions < 4
        ):
            new_limit = min(
                config.max_iterations + config.max_iterations_extension,
                config.max_total_iterations,
            )
            state.guard_iteration_extensions += 1
            logger.info(
                "[GUARD] Budget extended %d → %d (plan has pending steps, "
                "ext %d/4)",
                config.max_iterations, new_limit,
                state.guard_iteration_extensions,
            )
            config.max_iterations = new_limit
        else:
            logger.info(
                "[GUARD] max_iterations reached (iter=%d wait=%d effective=%d)",
                state.iteration, state.wait_only_iterations,
                state.iteration - state.wait_only_iterations,
            )
            return "max_iterations"

    if state.total_tool_calls >= config.max_tool_calls:
        logger.info("[GUARD] tool_call_budget (%d)", state.total_tool_calls)
        return "tool_call_budget"

    for name, err_count in state.tool_errors.items():
        if err_count >= config.per_tool_retry_limit:
            nudges_given = state.tool_nudges_given.get(name, 0)
            if nudges_given < config.max_tool_nudges:
                logger.info(
                    "[GUARD] tool_nudge %s (%d errors, nudge %d/%d)",
                    name, err_count, nudges_given + 1, config.max_tool_nudges,
                )
                return f"tool_nudge:{name}"
            logger.info("[GUARD] per_tool_retry_limit %s (%d)", name, err_count)
            return f"per_tool_retry_limit:{name}"

    if state.start_time and config.total_timeout_seconds > 0:
        elapsed = time.time() - state.start_time
        if elapsed > config.total_timeout_seconds:
            # Orchestrator in supervisor mode: teams are running, so the
            # timeout is not meaningful — the loop will exit naturally
            # when all work is done or the user aborts.
            if has_active_team:
                if not getattr(state, "_timeout_team_logged", False):
                    logger.info(
                        "[GUARD] timeout bypassed — orchestrator has active "
                        "team(s) (%.0fs elapsed)", elapsed,
                    )
                    state._timeout_team_logged = True  # type: ignore[attr-defined]
            else:
                # Mirror max_iterations extension: grant extra wall-clock time when
                # the agent is making genuine progress (pending plan, low error rate).
                can_extend = (
                    config.max_timeout_extensions > 0
                    and state.timeout_extensions < config.max_timeout_extensions
                    and has_pending_plan
                    and state.consecutive_errors < 3
                    and sum(state.tool_successes.values()) > 0
                    and not getattr(state, "orchestrator_recovery", False)
                )
                if can_extend:
                    state.timeout_extensions += 1
                    config.total_timeout_seconds += config.total_timeout_extension_seconds
                    logger.info(
                        "[GUARD] Timeout extended +%.0fs → %.0fs total "
                        "(extension %d/%d, plan pending, err_streak=%d)",
                        config.total_timeout_extension_seconds,
                        config.total_timeout_seconds,
                        state.timeout_extensions,
                        config.max_timeout_extensions,
                        state.consecutive_errors,
                    )
                else:
                    logger.info(
                        "[GUARD] total_timeout (%.1fs, extensions=%d/%d, "
                        "pending_plan=%s, consecutive_errors=%d)",
                        elapsed,
                        state.timeout_extensions,
                        config.max_timeout_extensions,
                        has_pending_plan,
                        state.consecutive_errors,
                    )
                    return "total_timeout"

    if state.consecutive_errors >= config.consecutive_error_limit:
        logger.info("[GUARD] consecutive_errors (%d)", state.consecutive_errors)
        return "consecutive_errors"

    return None


# ---------------------------------------------------------------------------
# Stall detection — "I'm stuck" self-awareness
# ---------------------------------------------------------------------------

_STALL_NUDGE_MESSAGE = (
    "You appear to be stuck — repeating similar actions without progress. "
    "STOP and use a fundamentally different approach:\n"
    "1. Read the error messages carefully — they often contain the fix.\n"
    "2. Search for a skill: clawhub(action='search', query='...') or "
    "discover_tools(query='...'). Install matches with "
    "clawhub(action='install', slug='...').\n"
    "3. If bash/shell commands keep failing, use the write tool to create "
    "files manually instead of relying on CLI scaffolding tools (e.g. "
    "write tailwind.config.js directly instead of running npx tailwindcss init).\n"
    "4. If you are on Windows/PowerShell, avoid bash-only syntax. Use "
    "PowerShell cmdlets or the write tool.\n"
    "5. GitHub/gh auth failures: authenticate first with "
    "bash('echo TOKEN | gh auth login --with-token'), then "
    "bash('gh auth status').\n"
    "6. If you are a delegate, call escalate() — the orchestrator can hint "
    "you or extend budget.\n"
    "7. Skip this step and move on to the next one only if truly blocked.\n"
    "8. If you've tried 2+ approaches, provide a partial result rather "
    "than wasting more iterations."
)


_SOLO_STALL_NUDGE_MESSAGE = (
    "You appear to be stuck — repeating similar actions without progress.\n"
    "Try ONE of these now:\n"
    "1. Read the error message and fix the specific issue.\n"
    "2. Use a different tool or command — do not retry the same call.\n"
    "3. On Windows, prefer PowerShell or the write tool over bash-only syntax.\n"
    "4. If you have enough information, answer in chat or call task_complete.\n"
    "Do NOT search for skills or create a plan unless the task truly requires it."
)


_SOLO_REPEAT_NUDGE_MESSAGE = (
    "STOP — you are repeating the exact same tool call with identical "
    "arguments. The output will not change.\n"
    "Either use the result you already have, try a different tool/command, "
    "or finish with your answer now.\n"
    "Do NOT call the same tool with the same arguments again."
)


def _stall_nudge_for_state(state: "LoopState", em_message: str, solo_message: str) -> str:
    from nls.agentic.profile_guard_policy import normalize_profile

    profile = normalize_profile(getattr(state, "orchestration_profile", None))
    if profile == "orchestrated":
        msg = em_message
    else:
        msg = solo_message
    from nls.agentic.profile_depth_policy import append_depth_to_stall_message

    return append_depth_to_stall_message(state, msg)


def _stall_context_suffix(state: "LoopState") -> str:
    """Optional error context appended to stall nudges."""
    if state.last_error_preview:
        return (
            f"\n\nLast error:\n  {state.last_error_preview}\n"
            f"Address THIS specific error — do not retry the same approach."
        )
    return ""


_REPEAT_NUDGE_MESSAGE = (
    "STOP — you are repeating the exact same tool call with identical "
    "arguments. This is wasting iterations. The output will not change.\n"
    "Either:\n"
    "1. Use the result you already got and act on it (read a specific file, "
    "fix the code, write a response).\n"
    "2. Try clawhub(action='search', query='...') or discover_tools(query='...') "
    "for a skill that handles this task.\n"
    "3. Try a completely different command or tool.\n"
    "4. If you have enough information, write your final response now.\n"
    "Do NOT call the same tool with the same arguments again."
)


_CYCLE_NUDGE_MESSAGE = (
    "STOP — you are stuck in a loop, cycling through the same sequence "
    "of actions repeatedly (e.g. write → run → fix → write → run → fix). "
    "This pattern will not converge.\n"
    "Either:\n"
    "1. Accept the current state and move on to the next part of your task.\n"
    "2. Try a fundamentally different approach to the problem.\n"
    "3. If the task is substantially complete, wrap up with a summary.\n"
    "Do NOT repeat the same cycle again."
)


# Bash patterns that look repetitive but are legitimate batch maintenance
# (delete → verify → delete → verify).  Signatures use the first 200 chars of
# tool-call JSON from the loop (see loop.py record_tool).
_INVENTORY_BASH_RE = re.compile(
    r"(gh\s+repo\s+list|gh\s+auth\s+status|git\s+status|Select-String|"
    r"Get-ChildItem|\bls\b|\bdir\b|\bgrep\b|\bfind\b)",
    re.IGNORECASE,
)
_MUTATION_BASH_RE = re.compile(
    r"(gh\s+repo\s+(delete|create)|rm\s+-|Remove-Item|"
    r"git\s+(push|commit|clone)|npm\s+(install|uninstall)|pip\s+install)",
    re.IGNORECASE,
)


def _fingerprint_is_inventory(sig: str) -> bool:
    return bool(_INVENTORY_BASH_RE.search(sig))


def _fingerprint_is_mutation(sig: str) -> bool:
    return bool(_MUTATION_BASH_RE.search(sig))


def _recent_bash_all_success(state: "LoopState", window: int = 8) -> bool:
    recent = state.tool_history[-window:]
    bash_entries = [(n, err) for n, err in recent if n == "bash"]
    return len(bash_entries) >= 2 and all(not err for _, err in bash_entries)


def _is_productive_bash_maintenance(
    state: "LoopState",
    sigs: list[str],
) -> bool:
    """Delete/list (or similar) loops where every bash call succeeded."""
    if not sigs or not _recent_bash_all_success(state, window=min(8, len(sigs))):
        return False
    window = sigs[-8:]
    has_inventory = any(_fingerprint_is_inventory(s) for s in window)
    has_mutation = any(_fingerprint_is_mutation(s) for s in window)
    return has_inventory and has_mutation


# Orchestrator supervision (inspect / wait) mixed with reads is legitimate.
_ORCHESTRATOR_SUPERVISION_TOOLS = frozenset({
    "team", "wait", "await_delegates", "delegate_status", "scheduler",
})
_MONITORING_CYCLE_TOOLS = _ORCHESTRATOR_SUPERVISION_TOOLS | frozenset({
    "read", "list_dir", "glob", "grep",
})
_LOOKUP_ONLY_TOOLS = frozenset({
    "read", "list_dir", "glob", "grep", "web_search", "web_fetch",
    "semantic_search", "plan",
})
_MUTATION_TOOLS = frozenset({
    "write", "edit", "team", "bash", "delegate", "todo",
})

_ASSESSMENT_LOOP_NUDGE = (
    "STOP — you are re-assessing the same project repeatedly without advancing.\n"
    "The user already knows the high-level status. Do NOT post another summary.\n"
    "Take ONE of these actions NOW:\n"
    "1. plan(action='read') then plan(update/accept_partial/complete) to reconcile steps.\n"
    "2. team(action='create'/'launch'/'advance') for the next pending wave.\n"
    "3. write/edit/bash to finish the remaining step if you are executing directly.\n"
    "No more than one list_dir/read pass before a plan or team mutation."
)


def _is_orchestrator_monitoring_cycle(names: list[str]) -> bool:
    """True for team→read→read / inspect loops — not a status re-scan stall."""
    if len(names) < 6:
        return False
    window = names[-9:]
    if not all(n in _MONITORING_CYCLE_TOOLS for n in window):
        return False
    if not any(n in _ORCHESTRATOR_SUPERVISION_TOOLS for n in window):
        return False
    for cycle_len in (2, 3):
        need = cycle_len * 2
        if len(window) >= need:
            tail = window[-need:]
            if tail[:cycle_len] == tail[cycle_len:]:
                return True
    if (
        window.count("team") >= 1
        and sum(1 for n in window if n in ("read", "list_dir", "glob", "grep")) >= 2
    ):
        return True
    return False


def _instruction_skill_setup_in_progress(state: "LoopState") -> bool:
    hints = {h.strip().lower() for h in (state.hints or []) if h and h.strip()}
    return "setup:instruction_skill" in hints or "setup:native_skill" in hints


def should_run_prose_eval(state: LoopState) -> bool:
    """Run prose micro-inference on every prose-only turn during agentic work.

    Pure conversational chat (CHAT mode, zero tool calls) is exempt: one
    prose reply is the normal deliverable, and the user must speak before
    another agent message — so duplicate-prose / premature-ask gating is
    unnecessary there.
    """
    if state.consecutive_text_only < 1:
        return False
    if not (getattr(state, "_last_iter_text", "") or "").strip():
        return False

    from nls.agentic.orchestration_profile_spec import get_profile_spec

    spec = get_profile_spec(getattr(state, "orchestration_profile", None))
    if (
        spec.profile == "conversational"
        and state.active_mode == AgentMode.CHAT
        and state.total_tool_calls == 0
        and not state.coordinator_mode
    ):
        return False
    return True


async def refresh_prose_verdict(
    state: "LoopState",
    vllm_client: Any = None,
    *,
    adapter_name: str | None = None,
) -> None:
    """Classify the latest prose-only loop iteration via micro-inference."""
    from nls.agentic.goals import evaluate_prose_turn, prose_fingerprint

    prose = getattr(state, "_last_iter_text", "") or ""
    if not prose.strip():
        state.last_prose_verdict = ""
        state.prose_show_to_user = True
        state.prose_gate_active = False
        return

    if not should_run_prose_eval(state):
        state.last_prose_verdict = ""
        state.prose_show_to_user = True
        state.prose_gate_active = False
        return

    verdict, show = await evaluate_prose_turn(
        vllm_client,
        goals=state.goals,
        action_summary="\n".join(state.cumulative_actions[-20:]),
        prose=prose,
        hints=state.hints or None,
        last_error=state.last_error_preview or "",
        prior_prose_hash=state.last_prose_hash or "",
        consecutive_text_only=state.consecutive_text_only,
        adapter_name=adapter_name,
    )
    state.last_prose_verdict = verdict
    state.prose_show_to_user = show
    state.prose_gate_active = (
        verdict == "should_continue" and not show
    )
    fp = prose_fingerprint(prose)
    if fp != state.last_prose_hash:
        state.last_prose_hash = fp
    logger.info(
        "[EVAL] prose_verdict=%s show_to_user=%s consec_text=%d",
        verdict, show, state.consecutive_text_only,
    )


def prose_stream_text(state: "LoopState", response_text: str) -> str:
    """Suppress held or duplicate prose from reaching the user."""
    text = (response_text or "").strip()
    if not text:
        return ""
    verdict = getattr(state, "last_prose_verdict", "")
    if verdict == "duplicate":
        return ""
    if not getattr(state, "prose_show_to_user", True):
        return ""
    return response_text or ""


def _diverse_bash_signatures(state: "LoopState", window: int) -> bool:
    sigs = getattr(state, "tool_call_signatures", [])[-window:]
    bash_sigs = [s for s in sigs if s.startswith("bash:")]
    if len(bash_sigs) < 4:
        return False
    return len(set(bash_sigs)) >= 3


def _detect_assessment_loop(state: "LoopState") -> str | None:
    """Lookup-only IC re-scan loops — not EM team(inspect)+read supervision.

  Intentionally narrow: orchestrator monitoring repeats team/inspect/read
  patterns by design. Only nudge when there is no supervision tool in recent
  history, delegates are not running, and the agent keeps posting status text
  without plan/team mutations (typical post-crash resume loop).
    """
    from nls.agentic.profile_guard_policy import em_assessment_loop_enabled

    if not em_assessment_loop_enabled(
        getattr(state, "orchestration_profile", None),
    ):
        return None
    names = [n for n, _err in state.tool_history]
    if len(names) < 8:
        return None

    if state.delegate_count > 0:
        return None
    if getattr(state, "idle_monitor_cycles", 0) > 0:
        return None
    if _is_orchestrator_monitoring_cycle(names):
        logger.debug(
            "[STALL] assessment-loop skipped — orchestrator monitoring cycle",
        )
        return None

    if state.active_mode in (
        AgentMode.MONITORING, AgentMode.EVALUATING, AgentMode.DELEGATING,
    ):
        if any(n in _ORCHESTRATOR_SUPERVISION_TOOLS for n in names[-12:]):
            logger.debug(
                "[STALL] assessment-loop skipped — %s with supervision tools",
                state.active_mode.value,
            )
            return None

    window = names[-10:]
    if not all(n in _LOOKUP_ONLY_TOOLS for n in window):
        return None
    if any(n in _ORCHESTRATOR_SUPERVISION_TOOLS for n in names[-14:]):
        return None
    if any(
        n in _MUTATION_TOOLS and not err
        for n, err in state.tool_history[-16:]
    ):
        return None

    _ic_rescan = state.active_mode in (
        AgentMode.EXECUTING, AgentMode.CHAT, AgentMode.RESPONDING,
    )
    _repeated_status = (
        state.consecutive_text_only >= 2
        or (
            state.consecutive_text_only >= 1
            and state.total_tool_calls >= 8
        )
    )
    if not (_ic_rescan and _repeated_status):
        return None

    logger.info(
        "[STALL] assessment loop: lookup-only window (%d tools), "
        "mode=%s, consec_text=%d",
        len(window), state.active_mode.value, state.consecutive_text_only,
    )
    return _ASSESSMENT_LOOP_NUDGE


_SKILL_FILE_RE = re.compile(
    r"[a-z0-9_-]+-channel[/\\][^\"']+\.py",
    re.IGNORECASE,
)
_SKILL_LOADER_ERR_MARKERS = (
    "cannot import", "importerror", "router", "register",
)


def _detect_skill_loader_rewrite_stall(state: "LoopState") -> str | None:
    """skill_install failed then repeated write/edit on channel skill files."""
    skill_install_errors = state.tool_errors.get("skill_install", 0)
    history = getattr(state, "tool_history", [])[-12:]
    had_install_error = skill_install_errors > 0 or any(
        name == "skill_install" and err for name, err in history
    )
    if not had_install_error:
        return None

    err_blob = (getattr(state, "last_error_preview", "") or "").lower()
    if not any(m in err_blob for m in _SKILL_LOADER_ERR_MARKERS):
        install_err_in_actions = any(
            "skill_install" in (a or "").lower()
            and any(m in (a or "").lower() for m in _SKILL_LOADER_ERR_MARKERS)
            for a in (getattr(state, "cumulative_actions", None) or [])[-8:]
        )
        if not install_err_in_actions:
            return None

    sigs = getattr(state, "tool_call_signatures", [])
    skill_edits = 0
    for sig in sigs[-8:]:
        if not (sig.startswith("write:") or sig.startswith("edit:")):
            continue
        raw = sig.split(":", 1)[-1]
        m = re.search(r'"path"\s*:\s*"([^"]+)"', raw)
        path = m.group(1) if m else raw
        if _SKILL_FILE_RE.search(path.replace("\\", "/")):
            skill_edits += 1

    if skill_edits < 2:
        return None

    logger.info(
        "[STALL] skill_install error + %d rewrite(s) on channel skill files",
        skill_edits,
    )
    return _stall_nudge_for_state(
        state,
        (
            "skill_install failed on a loader/import error — stop rewriting "
            "the whole webhook.py or adapter. Fix the specific contract: "
            "webhook.py must export module-level `router = APIRouter(...)`, "
            "register() must import cleanly, startup should be async if it "
            "starts background tasks. Edit surgically, retry skill_install, "
            "then skill_configure — do not loop full-file rewrites."
        ),
        (
            "Loader import error + repeated skill file rewrites detected. "
            "Export `router` at module level, fix __init__.py imports, "
            "retry skill_install once fixed."
        ),
    )


def detect_stall(state: "LoopState", config: "LoopConfig") -> str | None:
    """Detect stall patterns and return a nudge message if stuck.

    Returns a system-message string to inject, or None if no stall.
    """
    _assessment = _detect_assessment_loop(state)
    if _assessment:
        return _assessment
    _skill_rewrite = _detect_skill_loader_rewrite_stall(state)
    if _skill_rewrite:
        return _skill_rewrite
    # Pattern 0: exact same tool+args repeated 3+ times (even if successful)
    # Exempt orchestrator monitoring calls (e.g. team(inspect) on same team).
    _MONITORING_TOOL_NAMES = frozenset({
        "team", "wait", "await_delegates", "delegate_status", "scheduler",
    })
    sigs = getattr(state, "tool_call_signatures", [])
    if len(sigs) >= 3:
        last_3 = sigs[-3:]
        if last_3[0] == last_3[1] == last_3[2]:
            _sig_tool = (
                last_3[0].split(":", 1)[0]
                if ":" in last_3[0]
                else last_3[0]
            )
            if _sig_tool in _MONITORING_TOOL_NAMES:
                logger.debug(
                    "[STALL] repeated monitoring call (%s) — "
                    "legitimate orchestrator pattern, skipping",
                    last_3[0][:60],
                )
            elif (
                _sig_tool == "bash"
                and _fingerprint_is_inventory(last_3[0])
                and state.consecutive_errors == 0
                and _is_productive_bash_maintenance(state, sigs)
            ):
                logger.debug(
                    "[STALL] repeated inventory bash (%s) — "
                    "delete/verify maintenance loop, skipping",
                    last_3[0][:60],
                )
            else:
                logger.info(
                    "[STALL] identical tool call repeated 3x: '%s'",
                    last_3[0][:80],
                )
                return _stall_nudge_for_state(
                    state, _REPEAT_NUDGE_MESSAGE, _SOLO_REPEAT_NUDGE_MESSAGE,
                )

    # Pattern 0b: re-reading the same file(s) without writing
    read_paths: list[str] = []
    for sig in sigs[-6:]:
        if not sig.startswith("read:"):
            continue
        raw = sig[5:]
        m = re.search(r'"path"\s*:\s*"([^"]+)"', raw)
        read_paths.append(m.group(1) if m else raw[:60])
    if len(read_paths) >= 4:
        unique_paths = set(read_paths)
        writes = (
            state.tool_successes.get("write", 0)
            + state.tool_successes.get("edit", 0)
        )
        if len(unique_paths) <= 2 and writes <= 1:
            logger.info(
                "[STALL] read loop: %d reads on %d path(s), writes=%d",
                len(read_paths), len(unique_paths), writes,
            )
            return _stall_nudge_for_state(
                state,
                (
                    "You keep re-reading the same file(s) — content has not "
                    "changed. Stop reading and take the next concrete action "
                    "(write, bash, or task_complete). Use read(force=true) "
                    "only if the file changed on disk."
                ),
                (
                    "Stop re-reading. You already have the file contents. "
                    "Build, run, or call task_complete(summary='...')."
                ),
            )

    # Pattern 1a: 2+ consecutive errors on the same tool
    if state.consecutive_errors >= 2 and len(state.tool_history) >= 2:
        recent = state.tool_history[-2:]
        recent_names = [t[0] for t in recent]
        if len(set(recent_names)) == 1 and all(err for _, err in recent):
            logger.info(
                "[STALL] %d consecutive errors on same tool '%s'",
                state.consecutive_errors, recent_names[0],
            )
            _err_ctx = ""
            if state.last_error_preview:
                _err_ctx = (
                    f"\n\nLast error from '{recent_names[0]}':\n"
                    f"  {state.last_error_preview}\n"
                    f"Address THIS specific error — do not retry the same approach."
                )
            return _stall_nudge_for_state(
                state, _STALL_NUDGE_MESSAGE, _SOLO_STALL_NUDGE_MESSAGE,
            ) + _err_ctx

    # Pattern 1b: 3+ consecutive errors regardless of which tool
    if state.consecutive_errors >= 3 and len(state.tool_history) >= 3:
        recent = state.tool_history[-3:]
        if all(err for _, err in recent):
            recent_names = [t[0] for t in recent]
            logger.info(
                "[STALL] %d consecutive errors across tools: %s",
                state.consecutive_errors, recent_names,
            )
            _err_ctx = ""
            if state.last_error_preview:
                _err_ctx = (
                    f"\n\nLast error:\n  {state.last_error_preview}\n"
                    f"You have {state.consecutive_errors} consecutive failures. "
                    f"Either fix this specific issue or abandon this approach entirely."
                )
            return _stall_nudge_for_state(
                state, _STALL_NUDGE_MESSAGE, _SOLO_STALL_NUDGE_MESSAGE,
            ) + _err_ctx

    # Pattern 2: high iteration count with low plan progress
    # Only flag if there are also recent errors — a delegate that successfully
    # writes many files with only write+read is making progress, not stuck.
    if (
        config.max_iterations > 0
        and state.iteration - state.wait_only_iterations > config.max_iterations * 0.7
    ):
        total_calls = sum(state.tool_successes.values())
        unique_tools = len(state.tool_successes)
        has_recent_errors = state.consecutive_errors > 0 or sum(state.tool_errors.values()) > 2
        if total_calls > 0 and unique_tools <= 2 and has_recent_errors:
            logger.info(
                "[STALL] iteration %d/%d with only %d unique tools used "
                "(and %d consecutive errors)",
                state.iteration, config.max_iterations, unique_tools,
                state.consecutive_errors,
            )
            return _stall_nudge_for_state(
                state, _STALL_NUDGE_MESSAGE, _SOLO_STALL_NUDGE_MESSAGE,
            )

    # Pattern 3: alternating tool-name cycle (e.g. write→bash→write→bash)
    # Cross-check with signatures — if each call targets different args
    # (e.g. writing 6 different files), it's productive work, not a cycle.
    # Exempt orchestrator monitoring cycles (team+wait+read, etc.).
    _MONITORING_TOOLS = frozenset({
        "team", "wait", "delegate_status", "scheduler",
        "read", "list_dir", "glob", "grep",
    })
    names = [t[0] for t in state.tool_history]
    sigs_all = getattr(state, "tool_call_signatures", [])
    if len(names) >= 6:
        for cycle_len in (2, 3):
            window = names[-(cycle_len * 3):]
            if len(window) == cycle_len * 3:
                chunks = [
                    tuple(window[i : i + cycle_len])
                    for i in range(0, len(window), cycle_len)
                ]
                if chunks[0] == chunks[1] == chunks[2]:
                    if set(chunks[0]).issubset(_MONITORING_TOOLS):
                        logger.debug(
                            "[STALL] monitoring cycle detected (%s) — "
                            "legitimate orchestrator pattern, skipping",
                            " → ".join(chunks[0]),
                        )
                        continue
                    sig_window = sigs_all[-(cycle_len * 3):]
                    if (
                        set(chunks[0]) <= {"bash"}
                        and _is_productive_bash_maintenance(state, sigs_all)
                    ):
                        logger.debug(
                            "[STALL] bash maintenance cycle (%s) — "
                            "mutations + verify, skipping",
                            " → ".join(chunks[0]),
                        )
                        continue
                    if len(set(sig_window)) >= len(sig_window) * 0.7:
                        logger.debug(
                            "[STALL] name cycle detected (%s) but signatures "
                            "are diverse — likely productive scaffolding",
                            " → ".join(chunks[0]),
                        )
                        continue
                    if (
                        set(chunks[0]) == {"bash"}
                        and _instruction_skill_setup_in_progress(state)
                        and _diverse_bash_signatures(state, cycle_len * 3)
                    ):
                        logger.debug(
                            "[STALL] bash-only instruction-skill setup (%s) — "
                            "diverse commands, skipping cycle stall",
                            " → ".join(chunks[0]),
                        )
                        continue
                    logger.info(
                        "[STALL] tool-name cycle of length %d repeated 3x: %s",
                        cycle_len, " → ".join(chunks[0]),
                    )
                    return _CYCLE_NUDGE_MESSAGE

    # Pattern 4: prose repetition loop — model keeps talking without new tools.
    if (
        state.consecutive_text_only >= 4
        and state.total_tool_calls <= 3
        and state.iteration >= 8
    ):
        logger.info(
            "[STALL] prose loop: %d consecutive text-only iterations, "
            "%d tool calls",
            state.consecutive_text_only, state.total_tool_calls,
        )
        return _stall_nudge_for_state(
            state,
            (
                "You are repeating status text without acting. Call the "
                "required tool now (e.g. delegate(...) or task_complete("
                "summary='...')) — do not describe what you will do next."
            ),
            (
                "Stop narrating. Use the next tool call now or call "
                "task_complete(summary='...') if the deliverable is done."
            ),
        )

    return None


def inject_subagent_pacing_nudges(
    state: LoopState,
    config: LoopConfig,
    context: list[dict],
) -> None:
    """Soft budget nudges for worker sub-agents (completion-focused)."""
    if config.enable_delegation or config.max_iterations <= 0:
        return

    effective = state.iteration - state.wait_only_iterations
    if effective <= 0:
        return

    ratio = effective / config.max_iterations
    writes = (
        state.tool_successes.get("write", 0)
        + state.tool_successes.get("edit", 0)
    )
    reads = (
        state.tool_successes.get("read", 0)
        + state.tool_successes.get("list_dir", 0)
        + state.tool_successes.get("glob", 0)
    )

    milestones = (
        (0.5, "50", (
            f"Half your iteration budget is used ({effective}/{config.max_iterations}). "
            "If key deliverables are not on disk yet, stop exploring and start "
            "building the next concrete piece of your task."
        )),
        (0.75, "75", (
            f"Budget is getting tight ({effective}/{config.max_iterations}). "
            "Finish the file you are on, verify it once, then complete the "
            "remaining deliverables — or call escalate() if blocked."
        )),
        (0.9, "90", (
            f"Near your iteration limit ({effective}/{config.max_iterations}). "
            "Prefer escalate() with your progress over silent looping. "
            "Partial delivery with files beats zero files."
        )),
    )
    for threshold, key, message in milestones:
        if ratio >= threshold and key not in state.budget_milestones_sent:
            state.budget_milestones_sent.add(key)
            context.append({"role": "user", "content": message})
            logger.info(
                "[PACING] budget milestone %s%% at iter %d/%d",
                key, effective, config.max_iterations,
            )

    if (
        not state.read_heavy_nudge_sent
        and effective >= 8
        and reads >= 6
        and writes <= 1
    ):
        state.read_heavy_nudge_sent = True
        context.append({
            "role": "user",
            "content": (
                "You have done enough reading. Stop re-reading configs and "
                "start building. Based on what you have read, what is the "
                "next concrete deliverable for this task? Create it now."
            ),
        })
        logger.info(
            "[PACING] read-heavy nudge at iter %d (reads=%d writes=%d)",
            effective, reads, writes,
        )


# Deprecated aliases (pre-v5 naming)
should_complete_v4 = should_complete
