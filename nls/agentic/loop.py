"""v5 agentic loop — core orchestrator.

V5 architecture: clean message separation (system / WM / history / user).
All logic lives in imported modules:
- generator.py   — LLM generation + streaming + reasoning continuation
- executor.py    — tool execution (seq + parallel)
- compactor.py   — anchored iterative compaction
- evaluator.py   — completion + guard checks
- goals.py       — goal extraction + evaluation
- events.py      — event emission
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from nls.tools.agent_tools.base import AgentTool, tool_to_openai_schema

from .bridge import LoopHooks
from .breadcrumbs import BreadcrumbContext, BreadcrumbEngine
from .compactor import CompactionAnchor, compact, should_compact
from .context_supersession import (
    apply_supersession_with_cache_refs,
    register_tool_msg_outcome,
    resolve_deliverable_paths,
    resolve_supersession_policy,
    sync_open_blockers,
)
from .orchestration_policy import (
    build_evaluating_action_breadcrumb,
    build_orchestration_wake_message,
    invalidate_tool_policy_cache,
    is_conversational_user_turn,
    on_evaluating_wave,
    refresh_tool_schemas,
    should_force_coordinator_yield,
    should_suppress_checkback_wake,
    trim_context_for_orchestration_wake,
    update_coordinator_counters,
    iter_tool_names,
    delegates_running,
)
from .tool_mode_policy import (
    apply_dispatch_mode,
    apply_tool_mode_transition,
    compute_tool_mode_transition,
)
from nls.runtime.dispatch_sources import is_orchestration_dispatch_source
from .coordinator_guard import (
    coordinator_nudge_pre_delegate,
    record_team_inspect,
    filter_stale_tactical_goals,
    must_delegate_before_impl,
    pre_delegate_block_message,
    pre_delegate_reason,
    delegation_hallucination_nudge,
    recovery_mode_system_note,
    sync_goals_from_wm,
)
from .evaluator import check_guards, should_complete
from .events import AgentEvent, EventType, emit
from .executor import execute_tools, make_tool_message
from .generator import (
    generate,
    is_context_overflow,
    is_transient,
    sanitize_generation_error_for_user,
)
from .resume_guidance import (
    build_session_resume_guidance,
    user_requests_session_resume,
)
from .types import (
    AgentMode,
    COORDINATOR_TOOLS,
    LoopConfig,
    LoopResult,
    LoopState,
    MODE_PRIMARY_TOOLS,
    get_allowed_tools,
    is_override_tool,
    _ASK_USER_TOOL_SCHEMA,
    _COMMUNICATE_TOOL_SCHEMA,
    _SWITCH_MODE_TOOL_SCHEMA,
    _DELEGATE_STATUS_TOOL_SCHEMA,
    _WAIT_TOOL_SCHEMA,
    _DELEGATE_TOOL_SCHEMA,
    _get_plan_position,
    _select_thinking_mode,
    virtual_tool_schemas_for_loop,
    virtual_tool_names_for_loop,
)
from nls.brain.thinking import assess_coherence, extract_trajectory

logger = logging.getLogger(__name__)

_CREDENTIAL_RE = re.compile(
    r"ghp_[A-Za-z0-9]{30,}|gho_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
    r"|sk-[A-Za-z0-9\-_]{20,}|xox[bpsa]-[A-Za-z0-9\-]{20,}"
    r"|postgres(?:ql)?://\S+"
    r"|-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----",
    re.IGNORECASE,
)


def _parse_tool_args_safe(raw: str) -> dict:
    """Best-effort JSON parse for tool arguments; returns {} on failure."""
    if not raw:
        return {}
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _apply_context_supersession_pass(
    context: list[dict],
    *,
    config: LoopConfig,
    state: LoopState,
    anchor: CompactionAnchor,
    is_delegate_loop: bool,
    dispatch_source: str,
    team_manager: Any | None,
    tools: dict[str, AgentTool],
    start_index: int,
    cwd: str = "",
    plan_tool: Any | None = None,
) -> None:
    if not getattr(config, "enable_context_supersession", True):
        return
    pending_cr = False
    if team_manager is not None:
        try:
            pending_cr = bool(team_manager.has_pending_completion_reviews())
        except Exception:
            pass
    sync_open_blockers(anchor, state=state, team_manager=team_manager)
    policy = resolve_supersession_policy(
        enabled=config.enable_context_supersession,
        is_delegate_loop=is_delegate_loop,
        dispatch_source=dispatch_source or state.dispatch_source,
        has_pending_completion_reviews=pending_cr,
        active_mode=state.active_mode,
        coordinator_mode=state.coordinator_mode,
    )
    read_tool = tools.get("read")
    read_index = None
    if getattr(config, "enable_read_index", True) and read_tool is not None:
        read_index = getattr(read_tool, "_read_index", None)
    deliverable_paths = resolve_deliverable_paths(plan_tool)
    apply_supersession_with_cache_refs(
        context,
        policy=policy,
        state=state,
        anchor=anchor,
        start_index=start_index,
        cwd=cwd,
        read_index=read_index,
        deliverable_paths=deliverable_paths,
    )
    try:
        from nls.tools.agent_tools import get_loop_metrics

        metrics = get_loop_metrics()
        if metrics is not None:
            state.read_cache_hits = metrics.get("read_cache_hits", 0)
    except Exception:
        pass


def _register_appended_tool_outcome(
    state: LoopState,
    context: list[dict],
    tool_name: str,
    result: "ToolResult",
    args_raw: str,
) -> None:
    """Track effective tool error by context message index for supersession."""
    msg_index = len(context) - 1
    if msg_index < 0 or context[msg_index].get("role") != "tool":
        return
    args = _parse_tool_args_safe(args_raw if isinstance(args_raw, str) else "")
    register_tool_msg_outcome(state, msg_index, tool_name, result, args=args)


# ---------------------------------------------------------------------------
# Session log — persistent per-loop JSONL that never overwrites
# ---------------------------------------------------------------------------

def _open_session_log(config: LoopConfig, state: LoopState) -> str | None:
    """Create a unique JSONL session log file.  Returns its path, or None."""
    base = config.session_log_dir
    if not base:
        import tempfile
        base = os.path.join(tempfile.gettempdir(), "nls_agentic_sessions")
    os.makedirs(base, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(base, f"loop_{ts}_{state.loop_id}.jsonl")
    return path


def _slog(path: str | None, entry: dict) -> None:
    """Append one JSON line to the session log.  Best-effort, never raises."""
    if not path:
        return
    try:
        entry["_ts"] = datetime.now(timezone.utc).isoformat()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Pre-API journaling — crash recovery
# ---------------------------------------------------------------------------

def _journal_path(config: LoopConfig) -> str:
    """Return the journal file path for an agent (stable across loop restarts)."""
    base = config.session_log_dir
    if not base:
        import tempfile
        base = os.path.join(tempfile.gettempdir(), "nls_agentic_sessions")
    os.makedirs(base, exist_ok=True)
    agent_tag = config.agent_id or "default"
    return os.path.join(base, f"loop_journal_{agent_tag}.jsonl")


def _journal_write(path: str, iteration: int, context: list[dict]) -> None:
    """Write the latest pre-API snapshot to the journal, replacing prior content.

    Only the most recent state matters for crash recovery, so we overwrite
    the file each time instead of appending (prevents unbounded growth).
    """
    tmp = path + ".tmp"
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "iteration": iteration,
            "n_messages": len(context),
            "messages": context,
        }
        from nls.security.secret_redact import redact_context_for_log, redact_secrets

        safe_entry = dict(entry)
        safe_entry["messages"] = redact_context_for_log(context)
        payload = json.dumps(safe_entry, default=str, ensure_ascii=False)
        payload = redact_secrets(payload)[0]
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _journal_delete(path: str) -> None:
    """Remove the journal (and any leftover .tmp) on clean loop completion."""
    for p in (path, path + ".tmp"):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def _journal_recover(path: str) -> list[dict] | None:
    """Load the last journal entry if the file exists (crash recovery)."""
    if not os.path.exists(path):
        return None
    try:
        last_line = ""
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line
        if last_line:
            entry = json.loads(last_line)
            msgs = entry.get("messages")
            if isinstance(msgs, list) and msgs:
                logger.info(
                    "[JOURNAL] recovered %d messages from crash journal (iter=%s)",
                    len(msgs), entry.get("iteration"),
                )
                return msgs
    except Exception as exc:
        logger.warning("[JOURNAL] recovery failed: %s", exc)
    return None


def _journal_cleanup(config: LoopConfig, max_age_seconds: float = 3600.0) -> None:
    """Remove stale journal files older than *max_age_seconds*."""
    base = config.session_log_dir
    if not base:
        return
    try:
        now = time.time()
        for fname in os.listdir(base):
            if not fname.startswith("loop_journal_"):
                continue
            fpath = os.path.join(base, fname)
            try:
                age = now - os.path.getmtime(fpath)
                if age > max_age_seconds:
                    os.remove(fpath)
                    logger.debug("[JOURNAL] cleaned stale journal: %s", fname)
            except OSError:
                pass
    except Exception:
        pass


def _build_consolidation_summary(
    user_input: str,
    state: LoopState,
) -> str:
    """Build a structured consolidation summary from loop state.

    Produces tagged text that CryptexMemory.consolidate_session()
    distributes across Progress / Knowledge / Context buckets.
    """
    _SIG_RE = re.compile(
        r"\[(?:EVALUATE|LEARN|PLAN|IDENTITY|REFLECT|BOND)[:\|][^\]]*\]\s*"
    )
    parts: list[str] = []

    # --- [Progress]: what was accomplished ---
    _outcome = (_SIG_RE.sub("", state.final_response or "")).strip()
    if len(_outcome) > 300:
        cut = _outcome[:300].rfind(". ")
        _outcome = _outcome[: cut + 1] if cut > 200 else _outcome[:300] + "..."

    tool_bits: list[str] = []
    if state.tool_successes:
        top = sorted(state.tool_successes.items(), key=lambda x: -x[1])[:5]
        tool_bits.append(", ".join(f"{t}×{n}" for t, n in top))
    if state.delegate_count:
        tool_bits.append(f"{state.delegate_count} delegate(s)")

    metrics = (
        f"{state.iteration} iters, {state.total_tool_calls} tools"
        + (f" [{', '.join(tool_bits)}]" if tool_bits else "")
    )
    if _outcome:
        parts.append(f"[Progress] ({metrics}): {_outcome}")
    else:
        parts.append(f"[Progress] ({metrics}, exit={state.exit_reason})")

    # --- [Knowledge]: files, errors, notable actions ---
    know: list[str] = []
    if state.files_written:
        know.append("Files: " + ", ".join(state.files_written[-8:]))
    if state.tool_errors:
        err = ", ".join(
            f"{t}×{n}"
            for t, n in sorted(state.tool_errors.items(), key=lambda x: -x[1])[:3]
        )
        know.append(f"Errors: {err}")
    if state.cumulative_actions:
        recent = state.cumulative_actions[-6:]
        know.append("Actions: " + "; ".join(recent))
    if know:
        parts.append(f"[Knowledge] {'; '.join(know)}")

    # --- [Context]: what the user asked ---
    _user = (_SIG_RE.sub("", user_input or "")).strip()
    _user = re.sub(
        r"\[The user attached \d+ file\(s\):.*?\]",
        "[attached file(s)]",
        _user,
        flags=re.DOTALL,
    )
    if len(_user) > 200:
        cut = _user[:200].rfind(". ")
        _user = _user[: cut + 1] if cut > 120 else _user[:200] + "..."
    if _user:
        parts.append(f"[Context] User: {_user}")

    return "\n".join(parts)


def apply_final_response_backfill(
    state: LoopState,
    last_substantive_text: str,
) -> None:
    """Fill ``state.final_response`` when the loop ended with it empty."""
    if (state.final_response or "").strip():
        return

    # Coordinator monitoring background work: when the loop exits while
    # delegates are running (timeout, idle monitor, or budget), do NOT
    # backfill with stale text or raw tool dumps.  The delegates keep
    # running in the background and the system will re-enter
    # automatically when they complete.
    _silent_exits = (
        "total_timeout", "max_iterations", "tool_call_budget",
        "idle_monitor_yield", "awaiting_delegates",
        "post_launch_yield", "coordinator_burn", "monitor_iter_cap",
        "checkback_suppressed", "wake_token_budget",
    )
    if (
        state.delegate_count > 0
        and state.exit_reason in _silent_exits
    ):
        state.final_response = ""
        return

    if (last_substantive_text or "").strip():
        state.final_response = last_substantive_text
        return
    if state.exit_reason in ("max_iterations", "stalled", "total_timeout"):
        lit = (getattr(state, "_last_iter_text", "") or "").strip()
        if lit:
            state.final_response = lit
            return
    if state.exit_reason == "task_complete":
        lit = (getattr(state, "_last_iter_text", "") or "").strip()
        if lit:
            state.final_response = lit
            return
        if state.cumulative_actions:
            tail = state.cumulative_actions[-8:]
            state.final_response = (
                "Task completed. Recent tool activity:\n"
                + "\n".join(tail)
            )
            return
        state.final_response = (
            f"[Task completed with no visible summary. "
            f"{state.iteration} iterations, {state.total_tool_calls} "
            "tool calls.]"
        )
        return
    if state.exit_reason == "generation_error":
        raw = (state.last_generation_error or "").strip()
        safe = sanitize_generation_error_for_user(raw)
        if safe:
            state.final_response = (
                f"[Generation failed after {state.iteration} iteration(s): "
                f"{safe}]"
            )
            return
    state.final_response = (
        f"[Loop stopped: {state.exit_reason}. "
        f"{state.iteration} iterations, {state.total_tool_calls} tool calls.]"
    )


async def _try_escalate(
    reason: str,
    state: LoopState,
    config: LoopConfig,
    copilot_queue: asyncio.Queue | None,
    context: list[dict],
    slog_path: str | None = None,
) -> bool:
    """Attempt to escalate to the orchestrator instead of hard-exiting.

    Returns True if the loop should continue (orchestrator extended/hinted),
    False if it should exit as usual.
    """
    if not config.escalate_on_limit or not copilot_queue:
        return False

    _write_count = (
        state.tool_successes.get("write", 0)
        + state.tool_successes.get("edit", 0)
    )
    summary_lines = [
        f"reason: {reason}",
        f"iteration: {state.iteration}/{config.max_iterations}",
        f"tool_calls: {state.total_tool_calls}",
        f"writes: {_write_count}",
        f"errors: {dict(state.tool_errors)}" if state.tool_errors else "errors: none",
        f"consecutive_errors: {state.consecutive_errors}",
        f"stall_nudges: {state.stall_nudges_given}",
    ]
    if state.files_written:
        _recent_files = state.files_written[-10:]
        summary_lines.append("files_created: " + ", ".join(_recent_files))
    if state.cumulative_actions:
        recent = state.cumulative_actions[-10:]
        summary_lines.append("recent_actions: " + ", ".join(recent))
    _recent_tool_hist = state.tool_history[-5:]
    if _recent_tool_hist:
        _hist_str = ", ".join(
            f"{name}({'ERR' if err else 'ok'})" for name, err in _recent_tool_hist
        )
        summary_lines.append(f"last_5_tools: {_hist_str}")

    if config.on_escalation:
        try:
            _cb = config.on_escalation(reason, state, "\n".join(summary_lines))
            if asyncio.iscoroutine(_cb):
                await _cb
        except Exception:
            logger.warning("[LOOP:%s] escalation callback failed", state.loop_id, exc_info=True)

    logger.info(
        "[LOOP:%s] ESCALATING to orchestrator (reason=%s) — "
        "waiting up to %.0fs for decision",
        state.loop_id, reason, config.escalation_wait_seconds,
    )
    _slog(slog_path, {
        "event": "escalation_start",
        "reason": reason,
        "iteration": state.iteration,
        "wait_seconds": config.escalation_wait_seconds,
    })

    action = "terminate"
    message = ""
    extra_iters = 10
    _loop = asyncio.get_running_loop()
    _deadline = _loop.time() + config.escalation_wait_seconds
    _got_decision = False

    while not _got_decision:
        _remaining = _deadline - _loop.time()
        if _remaining <= 0:
            logger.info("[LOOP:%s] escalation timed out — exiting", state.loop_id)
            _slog(slog_path, {"event": "escalation_timeout", "reason": reason})
            return False
        try:
            decision = await asyncio.wait_for(
                copilot_queue.get(),
                timeout=_remaining,
            )
        except asyncio.TimeoutError:
            logger.info("[LOOP:%s] escalation timed out — exiting", state.loop_id)
            _slog(slog_path, {"event": "escalation_timeout", "reason": reason})
            return False

        if isinstance(decision, dict) and "action" in decision:
            action = decision.get("action", "terminate")
            message = decision.get("message", "")
            extra_iters = decision.get("extra_iterations", 10)
            _got_decision = True
        elif isinstance(decision, dict) and "role" in decision:
            context.append(decision)
            logger.info("[LOOP:%s] escalation wait: got steering msg, re-queued", state.loop_id)
        elif isinstance(decision, str):
            context.append({"role": "user", "content": decision})
            logger.info("[LOOP:%s] escalation wait: got string msg, re-queued", state.loop_id)
        else:
            logger.warning("[LOOP:%s] escalation: unexpected item type %s", state.loop_id, type(decision))
            return False

    _slog(slog_path, {
        "event": "escalation_accepted",
        "action": action,
        "extra_iters": extra_iters,
        "message": message[:200],
    })

    if action in ("extend", "hint"):
        config.max_iterations += extra_iters
        config.max_total_iterations += extra_iters
        _time_bump = max(extra_iters * 30.0, 300.0)
        config.total_timeout_seconds += _time_bump
        state.consecutive_errors = 0
        state.stall_nudges_given = 0
        state.exit_reason = ""
        if action == "hint":
            hint_text = message or "Try a different approach."
            context.append({"role": "user", "content": f"[ORCHESTRATOR HINT] {hint_text}"})
        elif message:
            context.append({"role": "user", "content": f"[ORCHESTRATOR] {message}"})
        logger.info(
            "[LOOP:%s] orchestrator %s — extended by %d iters (new max=%d) "
            "timeout +%.0fs (new timeout=%.0fs)",
            state.loop_id, action.upper(), extra_iters, config.max_iterations,
            _time_bump, config.total_timeout_seconds,
        )
        return True

    else:
        state.exit_reason = "orchestrator_terminated"
        logger.info("[LOOP:%s] orchestrator says TERMINATE", state.loop_id)
        return False


async def _await_completion_review(
    state: "LoopState",
    config: "LoopConfig",
    copilot_queue: asyncio.Queue | None,
    context: list[dict],
    slog_path: str | None = None,
) -> str:
    """Block until the orchestrator explicitly approves or rejects a
    delegate's self-reported completion.

    Returns ``"approved"`` or ``"rejected"``.  On rejection the hint is
    injected into *context* and iteration budget is extended so the
    delegate can continue.

    The delegate stays alive the entire time — there is no silent
    timeout-as-approval.  A long safety-net (10 min) exists only for
    truly orphaned delegates whose orchestrator loop has died.  Every
    60 s a reminder is re-sent so the orchestrator doesn't forget.
    """
    if not copilot_queue:
        return "approved"

    _write_count = (
        state.tool_successes.get("write", 0)
        + state.tool_successes.get("edit", 0)
    )
    summary = (
        f"reason: completion_review\n"
        f"iteration: {state.iteration}/{config.max_iterations}\n"
        f"tool_calls: {state.total_tool_calls}\n"
        f"writes: {_write_count}\n"
    )
    if state.files_written:
        summary += "files_written:\n"
        for _fp in state.files_written:
            summary += f"  - {_fp}\n"
    if state.cumulative_actions:
        recent = state.cumulative_actions[-10:]
        summary += "recent_actions: " + ", ".join(recent)

    # Notify orchestrator
    if config.on_escalation:
        try:
            _cb = config.on_escalation("completion_review", state, summary)
            if asyncio.iscoroutine(_cb):
                await _cb
        except Exception:
            logger.warning(
                "[LOOP:%s] completion review callback failed",
                state.loop_id, exc_info=True,
            )

    logger.info(
        "[LOOP:%s] COMPLETION REVIEW — waiting for orchestrator "
        "(writes=%d, tc=%d)",
        state.loop_id, _write_count, state.total_tool_calls,
    )
    _slog(slog_path, {
        "event": "completion_review_start",
        "iteration": state.iteration,
        "writes": _write_count,
        "tool_calls": state.total_tool_calls,
    })

    _REMINDER_INTERVAL = 60.0
    _SAFETY_TIMEOUT = 600.0
    _loop = asyncio.get_running_loop()
    _start = _loop.time()
    _last_reminder = _start

    while True:
        _elapsed = _loop.time() - _start
        if _elapsed >= _SAFETY_TIMEOUT:
            logger.warning(
                "[LOOP:%s] completion review safety timeout (%.0fs) — "
                "auto-approving (orchestrator likely dead)",
                state.loop_id, _elapsed,
            )
            _slog(slog_path, {"event": "completion_review_safety_timeout"})
            return "approved"

        _wait = min(_REMINDER_INTERVAL, _SAFETY_TIMEOUT - _elapsed)
        try:
            decision = await asyncio.wait_for(
                copilot_queue.get(), timeout=_wait,
            )
        except asyncio.TimeoutError:
            # No decision yet — re-notify as reminder
            if (
                _loop.time() - _last_reminder >= _REMINDER_INTERVAL
                and config.on_escalation
            ):
                _last_reminder = _loop.time()
                try:
                    _cb = config.on_escalation(
                        "completion_review_reminder", state, summary,
                    )
                    if asyncio.iscoroutine(_cb):
                        await _cb
                except Exception:
                    pass
            continue

        # --- Explicit orchestrator decision ---
        if isinstance(decision, dict) and "action" in decision:
            action = decision.get("action", "terminate")
            message = decision.get("message", "")
            extra_iters = decision.get("extra_iterations", 10)

            _slog(slog_path, {
                "event": "completion_review_decision",
                "action": action,
                "message": message[:200],
            })

            if action in ("extend", "hint"):
                config.max_iterations += extra_iters
                config.max_total_iterations += extra_iters
                _time_bump = max(extra_iters * 30.0, 300.0)
                config.total_timeout_seconds += _time_bump
                state.stall_nudges_given = 0
                state.exit_reason = ""
                hint_text = (
                    message
                    or "The orchestrator reviewed your work and found it "
                       "incomplete. Continue working."
                )
                context.append({
                    "role": "user",
                    "content": f"[ORCHESTRATOR REVIEW — REJECTED] {hint_text}",
                })
                logger.info(
                    "[LOOP:%s] completion REJECTED — delegate continues "
                    "(+%d iters)",
                    state.loop_id, extra_iters,
                )
                return "rejected"

            # "approve" / "terminate" / anything else = approved
            logger.info(
                "[LOOP:%s] completion APPROVED by orchestrator",
                state.loop_id,
            )
            return "approved"

        # Non-decision items (steering msgs) — queue as context but
        # keep waiting for the actual decision.
        if isinstance(decision, dict) and "role" in decision:
            context.append(decision)
        elif isinstance(decision, str):
            context.append({"role": "user", "content": decision})
        else:
            logger.warning(
                "[LOOP:%s] completion review: unexpected item %s",
                state.loop_id, type(decision),
            )


def _build_bc_ctx(
    tool_name: str,
    result: "ToolResult",
    state: "LoopState",
    deferred_actions: list[dict],
    anchor: "CompactionAnchor",
) -> BreadcrumbContext:
    """Build a ``BreadcrumbContext`` from the current tool result and state."""
    details = result.details
    return BreadcrumbContext(
        tool_name=tool_name,
        action=details.get("action", ""),
        is_error=result.is_error,
        result_details=details,
        unlocked_tools=frozenset(state.unlocked_tools),
        deferred_actions=tuple(deferred_actions),
        communications_sent=tuple(anchor.communications_sent),
        is_coordinator=state.coordinator_mode,
        goals=tuple(state.goals),
        orchestration_profile=getattr(state, "orchestration_profile", "") or "solo_structured",
    )


async def run_loop(
    *,
    context: list[dict],
    tools: dict[str, AgentTool],
    config: LoopConfig,
    hooks: LoopHooks,
    vllm_client: Any,
    abort_signal: asyncio.Event | None = None,
    on_event: Callable[[AgentEvent], Any] | None = None,
    user_input: str = "",
    adapter_name: str | None = None,
    copilot_queue: asyncio.Queue | None = None,
    first_response: str | None = None,
    first_tool_calls: list | None = None,
    enable_thinking: bool = True,
    pre_extracted_goals: list[str] | None = None,
    pre_extracted_hints: list[str] | None = None,
    pre_triage: Any | None = None,
    visual_cortex: Any | None = None,
    state_holder: list | None = None,
    wrap_up_signal: asyncio.Event | None = None,
    delegate_manager: Any | None = None,
    active_tool_names: set[str] | None = None,
    dispatch_source: str = "",
) -> LoopResult:
    """Core v5 agentic loop. Thin orchestrator — all logic in modules.

    V5 architecture: context arrives with clean message separation:
      system[0] = identity + instructions + tool directory
      system[1] = working memory (optional, merged by sanitize_context)
      history   = prior turns with <think> blocks preserved
      user      = raw user request only
    """

    state = LoopState(user_input=user_input, start_time=time.time())
    from nls.tools.agent_tools import enter_file_cache_scope, enter_loop_metrics_scope
    enter_file_cache_scope(state.loop_id)
    enter_loop_metrics_scope()
    state.dispatch_source = dispatch_source or ""
    if state_holder is not None:
        state_holder.append(state)
    _session_log_path = _open_session_log(config, state)
    anchor = CompactionAnchor()
    _breadcrumb_engine = BreadcrumbEngine()
    digest_count = 0
    _loop_start_idx = len(context)

    # Pre-API journaling setup
    _journal = _journal_path(config)
    _journal_cleanup(config)

    # Crash recovery: if a journal exists from a previous crash, restore
    recovered_ctx = _journal_recover(_journal)
    if recovered_ctx:
        context = recovered_ctx
        _loop_start_idx = len(context)
        logger.info("[LOOP:%s] resumed from crash journal (%d msgs)", state.loop_id, len(context))

    # Reasoning continuity state (ported from v3)
    _reasoning_trajectory: str = ""
    _last_coherence: float = 1.0

    # Track the last substantive text response (for final_response fallback)
    _last_text_response: str = ""

    # Deferred post-completion actions (e.g. "send me on WhatsApp when done")
    _deferred_actions: list[dict] = []

    # Cache team manager reference once to avoid repeated dict lookups.
    _cached_team_manager: Any = None
    _team_tool = tools.get("team")
    if _team_tool is not None:
        _cached_team_manager = getattr(_team_tool, "_tm", None)
    if _cached_team_manager is not None:
        hooks._cached_team_manager = _cached_team_manager  # type: ignore[attr-defined]

    # Recovery / auto-reconcile: launch prepared next waves without a full
    # EM review loop when policy allows (saves tokens vs stall → idle).
    if (
        _cached_team_manager is not None
        and config.enable_delegation
        and delegate_manager is not None
    ):
        _cached_team_manager.enqueue_unlaunched_for_auto_launch()
        if not dispatch_source.startswith("team_wave_complete:"):
            try:
                from nls.agentic.executor import try_auto_launch_pending_wave

                await try_auto_launch_pending_wave(
                    _cached_team_manager,
                    delegate_manager,
                    tools,
                    config,
                    state,
                    hooks,
                    vllm_client,
                    on_event,
                    abort_signal,
                    user_input,
                )
            except Exception:
                logger.debug(
                    "pending wave auto-launch at loop start failed",
                    exc_info=True,
                )

    # Follow-up user messages start a fresh LoopState.  Sync running
    # delegates from the manager so monitoring wrap-up / idle yield logic
    # sees background work launched in a prior loop.
    if delegate_manager is not None:
        _running_delegates = delegate_manager.running_count()
        if _running_delegates:
            state.delegate_count = max(state.delegate_count, _running_delegates)
            logger.info(
                "[LOOP:%s] synced delegate_count=%d from %d running "
                "delegate(s) in prior loop",
                state.loop_id, state.delegate_count, _running_delegates,
            )

    if copilot_queue is not None and hooks.copilot_queue is None:
        hooks.copilot_queue = copilot_queue

    # --- Tool schema loading ---
    # V5: pass ALL tools as schemas so the model sees exactly the same
    # tools in the structured schema as in the system prompt's directory.
    # Lazy loading (get_tool_schema) caused mismatches: the system prompt
    # listed 20+ tools but only 10 had schemas, making the model
    # hallucinate non-existent tools and fall back to XML format.
    _base_schemas: list[dict] = virtual_tool_schemas_for_loop(
        enable_delegation=config.enable_delegation,
        enable_detached_delegates=config.enable_detached_delegates,
        delegate_manager=delegate_manager,
    )
    state.unlocked_tools.update(
        virtual_tool_names_for_loop(
            enable_delegation=config.enable_delegation,
            enable_detached_delegates=config.enable_detached_delegates,
            delegate_manager=delegate_manager,
        )
    )
    for _tool_name, _tool_obj in tools.items():
        if isinstance(_tool_obj, AgentTool):
            if active_tool_names is not None and _tool_name not in active_tool_names:
                continue
            try:
                _base_schemas.append(tool_to_openai_schema(_tool_obj))
                state.unlocked_tools.add(_tool_name)
            except Exception:
                pass

    # Snapshot the full schema list before any mode filtering.
    # Mode switches need to re-filter from this complete set —
    # otherwise switching to a broader mode can't recover tools.
    _all_schemas: list[dict] = list(_base_schemas)
    _all_unlocked: set[str] = set(state.unlocked_tools)

    # Pre-populate from first_tool_calls
    if first_tool_calls:
        for _ftc in first_tool_calls:
            _ftc_name = (_ftc.get("function") or {}).get("name", "")
            if _ftc_name and _ftc_name in tools:
                state.unlocked_tools.add(_ftc_name)

    # Resolve the plan tool for position tracking + event bridge
    _plan_tool = tools.get("plan")
    _plan_steps: list[str] = []
    _plan_statuses: list[str] = []

    from nls.tools.agent_tools.plan import PlanReadOnlyTool as _PlanRO
    _is_delegate_loop = isinstance(_plan_tool, _PlanRO)
    _supersession_cwd = ""
    _bash_for_cwd = tools.get("bash")
    if _bash_for_cwd is not None:
        _supersession_cwd = getattr(_bash_for_cwd, "_cwd", "") or ""
    elif tools.get("read") is not None:
        _supersession_cwd = getattr(tools.get("read"), "_cwd", "") or ""

    _stale_board_msg: str | None = None
    if (
        config.enable_delegation
        and not _is_delegate_loop
        and dispatch_source.startswith("team_wave_complete:")
        and _cached_team_manager is not None
    ):
        from nls.agentic.plan_work import apply_stale_wave_wake_redirect

        dispatch_source, _stale_board_msg, _stale_exit = (
            apply_stale_wave_wake_redirect(
                dispatch_source,
                team_manager=_cached_team_manager,
                plan_tool=_plan_tool,
                todo_tool=tools.get("todo"),
            )
        )
        if _stale_exit:
            logger.info(
                "[LOOP:%s] stale wave-complete wake — no board work (%s)",
                state.loop_id, _stale_exit,
            )
            return LoopResult(
                final_response="",
                exit_reason=_stale_exit,
                iterations=0,
                total_tool_calls=0,
            )
        if _stale_board_msg and hooks.wm_refresh_todo_board:
            try:
                hooks.wm_refresh_todo_board()
            except Exception:
                pass

    _gr_boot = getattr(hooks, "guardrails_registry", None)
    if _gr_boot is not None:
        _cryptex_boot = getattr(hooks, "_accumulator_wm_target", None)
        if _cryptex_boot is not None:
            try:
                _cryptex_boot._guardrails_registry = _gr_boot  # type: ignore[attr-defined]
                from nls.tools.agent_tools.guardrails_registry import (
                    inject_guardrails_into_cryptex,
                )
                inject_guardrails_into_cryptex(_cryptex_boot, _gr_boot)
            except Exception:
                pass

    # Orchestration wake: trim history and inject compact WM packet
    _dual_wm = getattr(hooks, "_accumulator_wm_target", None)
    if (
        config.enable_delegation
        and is_orchestration_dispatch_source(dispatch_source)
    ):
        context = trim_context_for_orchestration_wake(
            context, dispatch_source,
        )
        if delegates_running(delegate_manager):
            if should_suppress_checkback_wake(
                _dual_wm, dispatch_source, delegates_active=True,
            ):
                logger.info(
                    "[LOOP:%s] check-back suppressed — WM unchanged",
                    state.loop_id,
                )
                state.exit_reason = "checkback_suppressed"
                return LoopResult(
                    final_response="",
                    exit_reason="checkback_suppressed",
                    iterations=0,
                    total_tool_calls=0,
                )
        if not state.orch_wake_injected:
            _delegate_summary = ""
            if delegate_manager is not None:
                try:
                    _running = delegate_manager.running_count()
                    if _running:
                        _delegate_summary = f"{_running} delegate(s) running"
                except Exception:
                    pass
            _plan_progress = ""
            _plan_audit_issues: list[str] = []
            _plan_incomplete_steps: list[str] = []
            _plan_board_lines: list[str] = []
            if _plan_tool is not None and hasattr(_plan_tool, "_store"):
                try:
                    _ap = _plan_tool._store.find_active()
                    if _ap is not None:
                        _plan_progress = _ap.progress_summary()
                        if _ap.audit and _ap.audit.issues:
                            _plan_audit_issues = list(_ap.audit.issues[:6])
                        _plan_incomplete_steps = [
                            f"[{s.id}] {s.label} ({s.status})"
                            for s in _ap.steps
                            if s.status not in ("done", "skipped")
                        ][:6]
                        from nls.agentic.plan_work import build_board_snapshot_lines

                        _todo_store = getattr(
                            tools.get("todo"), "_store", None,
                        )
                        _plan_board_lines = build_board_snapshot_lines(
                            _ap,
                            todo_store=_todo_store,
                            team_manager=_cached_team_manager,
                        )
                except Exception:
                    pass
            context.append({
                "role": "system",
                "content": build_orchestration_wake_message(
                    dispatch_source=dispatch_source,
                    dual_wm=_dual_wm,
                    plan_progress=_plan_progress,
                    delegate_summary=_delegate_summary,
                    coordinator_phase=getattr(state, "coordinator_phase", ""),
                    plan_audit_issues=_plan_audit_issues,
                    plan_incomplete_steps=_plan_incomplete_steps,
                    board_snapshot_lines=_plan_board_lines,
                ),
            })
            state.orch_wake_injected = True

    if _stale_board_msg:
        context.append({
            "role": "system",
            "content": _stale_board_msg,
        })

    if (
        dispatch_source.startswith("team_wave_complete:")
        or dispatch_source.startswith("team_completion_review:")
        or dispatch_source.startswith("board_reconcile:")
    ):
        state.active_mode = AgentMode.EVALUATING
        invalidate_tool_policy_cache(state)
        _record_phase = None
        if hooks and hooks.wm_orch_set_coordinator_phase:
            _record_phase = hooks.wm_orch_set_coordinator_phase
        on_evaluating_wave(state, record_phase=_record_phase)
        if _cached_team_manager is not None:
            try:
                from nls.agentic.wake_coordination import sync_wake_attention_board
                sync_wake_attention_board(_cached_team_manager)
            except Exception:
                pass
        logger.info(
            "[LOOP:%s] %s dispatch — entering EVALUATING mode",
            state.loop_id,
            dispatch_source.split(":", 1)[0],
        )
    elif config.enable_delegation:
        _dispatch_mt = apply_dispatch_mode(
            state, dispatch_source, enable_delegation=True,
        )
        if _dispatch_mt is not None:
            apply_tool_mode_transition(state, _dispatch_mt)
            if _dispatch_mt.hint:
                context.append({"role": "system", "content": _dispatch_mt.hint})
            _rmode_ref = getattr(hooks, "_render_mode_ref", None)
            if _rmode_ref:
                _rmode_ref[0] = state.active_mode.value
            logger.info(
                "[LOOP:%s] dispatch %s — mode %s",
                state.loop_id,
                _dispatch_mt.reason,
                state.active_mode.value,
            )

    # --- Hook: on_loop_start ---
    if hooks.on_loop_start:
        try:
            hooks.on_loop_start()
        except Exception:
            pass

    # Refresh todo board in WM so agent sees Kanban state from iter 1
    if hooks.wm_refresh_todo_board:
        try:
            hooks.wm_refresh_todo_board()
        except Exception:
            pass

    # --- Pre-loop: goal extraction / turn triage ---
    if pre_triage is not None:
        from .goals import TurnTriage, cap_triage_profile_for_tools

        _pt = (
            pre_triage
            if isinstance(pre_triage, TurnTriage)
            else TurnTriage(
                profile=getattr(pre_triage, "profile", "solo_structured"),
                goals=list(getattr(pre_triage, "goals", None) or []),
                hints=list(getattr(pre_triage, "hints", None) or []),
                deferred=list(getattr(pre_triage, "deferred", None) or []),
                intent=getattr(pre_triage, "intent", "CHAT_THINK"),
                thinking=getattr(pre_triage, "thinking", True),
            )
        )
        if not isinstance(pre_triage, TurnTriage):
            _pt.cap_profile_from_hints()
            _pt.reconcile_orchestration_depth()
        cap_triage_profile_for_tools(
            _pt, frozenset(state.unlocked_tools or ()),
        )
        state.orchestration_profile = _pt.profile or "solo_structured"
        if pre_extracted_goals is None:
            pre_extracted_goals = list(getattr(pre_triage, "goals", None) or [])
        if pre_extracted_hints is None:
            pre_extracted_hints = list(getattr(pre_triage, "hints", None) or [])
        _deferred_actions = list(getattr(pre_triage, "deferred", None) or [])
    if pre_extracted_hints:
        state.hints = list(pre_extracted_hints)
    _active_plan_for_goals = None
    _plan_tool_goals = tools.get("plan")
    if _plan_tool_goals is not None and hasattr(_plan_tool_goals, "_store"):
        try:
            _active_plan_for_goals = _plan_tool_goals._store.find_active()
        except Exception:
            pass
    if pre_extracted_goals:
        state.goals = filter_stale_tactical_goals(
            list(pre_extracted_goals), _active_plan_for_goals,
        )
    elif pre_triage is None and not (hooks.has_active_plan and hooks.has_active_plan()):
        try:
            from .goals import triage_turn

            triage = await triage_turn(
                vllm_client, user_input, adapter_name=adapter_name,
            )
            from .goals import cap_triage_profile_for_tools

            cap_triage_profile_for_tools(
                triage, frozenset(state.unlocked_tools or ()),
            )
            state.orchestration_profile = triage.profile
            if triage.goals:
                state.goals = triage.goals
                if hooks.on_goals_extracted:
                    try:
                        hooks.on_goals_extracted(triage.goals)
                    except Exception:
                        pass
            if triage.hints:
                state.hints = triage.hints
                if hooks.on_hints_extracted:
                    try:
                        hooks.on_hints_extracted(triage.hints)
                    except Exception:
                        pass
            if triage.deferred:
                _deferred_actions = triage.deferred
                logger.info(
                    "[LOOP:%s] deferred actions extracted: %s",
                    state.loop_id,
                    [d.get("channel") for d in triage.deferred],
                )
        except Exception:
            logger.debug("Pre-loop goal extraction failed", exc_info=True)

    from nls.agentic.profile_guard_policy import inject_prompt_structured_hints
    inject_prompt_structured_hints(user_input, state.hints)
    from nls.agentic.profile_guard_policy import enrich_instruction_skill_hints
    enrich_instruction_skill_hints(user_input, state.goals, state.hints)
    from nls.agentic.profile_guard_policy import enrich_native_skill_hints
    enrich_native_skill_hints(user_input, state.goals, state.hints)

    from nls.agentic.task_epoch_hygiene import reconcile_goals_with_hints
    state.goals = reconcile_goals_with_hints(state.goals, state.hints)

    if _deferred_actions:
        from .goals import deferred_actions_to_goal_strings

        for _dg in deferred_actions_to_goal_strings(_deferred_actions):
            if _dg not in state.goals:
                state.goals.append(_dg)

    from nls.agentic.task_epoch_hygiene import is_fresh_task_dispatch

    _is_user_dispatch = is_fresh_task_dispatch(dispatch_source)
    _wm_goals_fn = getattr(hooks, "wm_get_tactical_goals", None)
    if not (_is_user_dispatch and state.goals):
        sync_goals_from_wm(state, _wm_goals_fn)
    if state.goals:
        state.goals = filter_stale_tactical_goals(
            state.goals, _active_plan_for_goals,
        )

    _profile = state.orchestration_profile or "solo_structured"
    if _profile == "conversational" and not state.coordinator_mode:
        from nls.agentic.profile_guard_policy import conversational_tool_surface

        _triage_intent = (
            getattr(pre_triage, "intent", "") if pre_triage is not None else ""
        )
        _hist_for_surface: list[dict] = []
        for _m in context[-10:]:
            if _m.get("role") in ("user", "assistant"):
                _hist_for_surface.append(_m)
        if (
            conversational_tool_surface(
                user_input,
                history=_hist_for_surface or None,
                intent=_triage_intent,
            )
            == "executing"
        ):
            if state.active_mode != AgentMode.EXECUTING:
                state.active_mode = AgentMode.EXECUTING
                invalidate_tool_policy_cache(state)
        elif state.active_mode == AgentMode.EXECUTING:
            state.active_mode = AgentMode.CHAT
            invalidate_tool_policy_cache(state)
    if state.goals:
        from nls.agentic.profile_guard_policy import normalize_goals_for_profile

        state.goals = normalize_goals_for_profile(state.goals, _profile)

    _wm_begin_epoch = getattr(hooks, "wm_begin_task_epoch", None)
    if _wm_begin_epoch and _is_user_dispatch:
        try:
            _wm_begin_epoch(
                loop_id=state.loop_id,
                goals=list(state.goals),
                dispatch_source=dispatch_source or "user",
            )
        except Exception:
            logger.debug("wm_begin_task_epoch failed", exc_info=True)

    # Push fresh task goals/instructions into WM (clears stale slots).
    _wm_push_task_goals = getattr(hooks, "wm_push_task_goals", None)
    if hooks.wm_push_instructions and (state.goals or state.hints):
        try:
            _instr_items: list[str] = []
            for g in state.goals:
                _instr_items.append(g)
            for h in state.hints:
                if h.startswith("lookup:"):
                    continue
                if h not in _instr_items and not _CREDENTIAL_RE.search(h):
                    _instr_items.append(h)
            if _instr_items:
                hooks.wm_push_instructions(_instr_items)
        except Exception:
            pass
    if _wm_push_task_goals and state.goals and _is_user_dispatch:
        try:
            _wm_push_task_goals(list(state.goals))
        except Exception:
            pass

    from nls.agentic.orchestration_profile_spec import profile_anchor_message

    _skip_profile_anchor = (
        config.enable_delegation
        and (
            state.coordinator_mode
            or is_orchestration_dispatch_source(dispatch_source)
            or state.active_mode in (
                AgentMode.MONITORING,
                AgentMode.DELEGATING,
                AgentMode.EVALUATING,
            )
        )
    )
    _anchor = profile_anchor_message(_profile)
    if _anchor and not _skip_profile_anchor:
        context.append({"role": "system", "content": _anchor})

    _hint_tokens = {h.strip().lower() for h in state.hints if h and h.strip()}
    if "setup:native_skill" in _hint_tokens:
        try:
            from nls.skills_setup_policy import (
                build_native_skill_setup_lines,
                infer_channel_platform,
            )

            _platform = infer_channel_platform(
                f"{user_input} {' '.join(state.goals or [])}",
            )
            context.append({
                "role": "system",
                "content": "\n".join(
                    build_native_skill_setup_lines(
                        channel_platform=_platform,
                    ),
                ),
            })
        except Exception:
            pass
    if "lookup:chat_history" in _hint_tokens:
        context.append({
            "role": "system",
            "content": (
                "The user is referencing an earlier conversation turn. "
                "Older chat is stored in chat_transcript.jsonl and is NOT "
                "in your automatic context. "
                "Call chat_history(action='search', query='<keywords>') "
                "before answering from memory."
            ),
        })
    if "setup:instruction_skill" in _hint_tokens:
        try:
            from nls.skills_setup_policy import resolve_data_skills_dir

            _skills_base = resolve_data_skills_dir()
            if _skills_base is not None:
                from nls.skills_setup_policy import build_instruction_skill_setup_lines

                _read_tool = tools.get("read") if tools else None
                _read_index = (
                    getattr(_read_tool, "_read_index", None)
                    if _read_tool is not None
                    else None
                )
                _setup_lines = build_instruction_skill_setup_lines(
                    _skills_base,
                    read_index=_read_index,
                )
                context.append({
                    "role": "system",
                    "content": "\n".join(_setup_lines),
                })
        except Exception:
            pass
    if "setup:configure_bundled" in _hint_tokens:
        try:
            from nls.agentic.profile_guard_policy import infer_bundled_channel_skill_name
            from nls.skills_setup_policy import (
                build_configure_bundled_setup_lines,
                infer_pre_shipped_channel_skill,
            )

            _blob = f"{user_input} {' '.join(state.goals or [])}"
            _skill_name = infer_pre_shipped_channel_skill(_blob) or infer_bundled_channel_skill_name(_blob)
            for _h in state.hints or []:
                _hl = (_h or "").lower()
                if "skill_configure(skill_name='" in _hl:
                    _m = re.search(r"skill_configure\(skill_name='([^']+)'", _h, re.I)
                    if _m:
                        _skill_name = _m.group(1)
                        break
            context.append({
                "role": "system",
                "content": "\n".join(
                    build_configure_bundled_setup_lines(
                        skill_name=_skill_name,
                        channel_platform=_skill_name.replace("-channel", ""),
                    ),
                ),
            })
        except Exception:
            pass

    # Inject [CHANNEL ROUTING] system message when deferred external channels
    if _deferred_actions:
        _ext_chs = {
            da.get("channel", "")
            for da in _deferred_actions
            if da.get("channel") in ("whatsapp", "telegram", "email")
        }
        if _ext_chs:
            _ch_list = ", ".join(sorted(_ext_chs))
            _routing_msg = (
                f"[CHANNEL ROUTING] The user is AFK. "
                f"Requested channel(s): {_ch_list}.\n"
                f"- Send updates ONLY on channels that show CONNECTED in "
                f"your Channels ring (whatsapp_send / telegram_send / "
                f"email_send must succeed).\n"
                f"- If a channel is NOT CONNECTED, use communicate() in chat "
                f"and do NOT label messages 'Status Update ({_ch_list})'.\n"
                f"- When a channel IS connected, send the FULL report there, "
                f"not a placeholder."
            )
            context.append({"role": "system", "content": _routing_msg})
            logger.info(
                "[LOOP:%s] Injected CHANNEL ROUTING system message for: %s",
                state.loop_id, _ch_list,
            )

    # --- Sub-agent coordinator guard ---
    # Sub-agents (enable_delegation=False) must NEVER enter coordinator
    # mode, even if goals extraction finds many objectives in their task
    # description.  Force it off here as a belt-and-suspenders check.
    if not config.enable_delegation and state.coordinator_mode:
        state.coordinator_mode = False
        logger.warning(
            "[LOOP:%s] Forced coordinator_mode=False for sub-agent "
            "(enable_delegation is disabled)",
            state.loop_id,
        )

    # --- Auto-mode detection ---
    # Trigger 1: active plan with 2+ delegatable steps → restore an
    # appropriate orchestration mode across loops.
    # SKIP if team_manager already has active teams — Trigger 3 handles that.
    _trigger1_has_active_teams = False
    if config.enable_delegation:
        for _t1_tool_name, _t1_tool_obj in tools.items():
            if _t1_tool_name == "team":
                _t1_tm = getattr(_t1_tool_obj, "_tm", None)
                if _t1_tm is not None:
                    try:
                        _trigger1_has_active_teams = (
                            _t1_tm.has_orchestrator_blocking_team()
                        )
                    except Exception:
                        pass
                break

    if (
        config.enable_delegation
        and state.active_mode == AgentMode.EXECUTING
        and not _trigger1_has_active_teams
        and _plan_tool
        and hasattr(_plan_tool, "get_store")
    ):
        try:
            _ps = _plan_tool.get_store()
            _tm = _cached_team_manager
            _active_plan = _ps.resolve_work_plan(
                "", _tm, reopen=False,
            )
            if (
                _active_plan
                and _active_plan.status not in ("done", "archived")
            ):
                # Ensure orchestrator CWD is inside the project folder.
                _cwd_fn = getattr(_plan_tool, "_cwd_switch_fn", None)
                if _cwd_fn and _active_plan.project_dir:
                    from pathlib import Path as _PPath
                    _pd_target = str(
                        _PPath(getattr(_plan_tool, "_workspace", ""))
                        / _active_plan.project_dir
                    )
                    try:
                        _cwd_fn(_pd_target)
                    except Exception:
                        pass
                _delegatable = [
                    s for s in _active_plan.steps
                    if s.delegatable and s.status not in ("done", "skipped")
                ]
                if _delegatable:
                    state.active_mode = AgentMode.DELEGATING
                    logger.info(
                        "[LOOP:%s] AUTO-MODE (plan): active plan "
                        "'%s' has %d pending delegatable steps — "
                        "entering DELEGATING mode",
                        state.loop_id, _active_plan.id, len(_delegatable),
                    )
                    _done_count = sum(
                        1 for s in _active_plan.steps
                        if s.status in ("done", "skipped")
                    )
                    _step_labels = ", ".join(
                        f"'{s.label}'" for s in _delegatable[:3]
                    )
                    if len(_delegatable) > 3:
                        _step_labels += f", +{len(_delegatable)-3} more"
                    _wave_hint = (
                        "team(action='create', plan_id="
                        f"'{_active_plan.id}', wave=0, "
                        f"name='Wave 0 - {_delegatable[0].label}')"
                        if len(_delegatable) >= 2
                        else (
                            f"team(action='create', plan_id='{_active_plan.id}', "
                            f"wave=N, name='Final wave - {_delegatable[0].label}')"
                        )
                    )
                    _plan_coord_msg = (
                        "DELEGATING MODE RESTORED — you have an active "
                        f"plan '{_active_plan.id}' with "
                        f"{len(_delegatable)} pending delegatable step(s) "
                        f"({_done_count}/{len(_active_plan.steps)} done): "
                        f"{_step_labels}.\n"
                        "Your NEXT action should be:\n"
                        f"  {_wave_hint}\n"
                        "Then: team(action='launch', team_id=...)\n\n"
                        "CRITICAL: Do NOT create project files, "
                        "directories, scaffolding, or git repos yourself. "
                        "ALL of that is the team's job. Your role is "
                        "orchestration — delegate, monitor, report."
                    )
                    _rmode_ref = getattr(hooks, "_render_mode_ref", None)
                    if _rmode_ref:
                        _rmode_ref[0] = AgentMode.DELEGATING.value
                    context.append({
                        "role": "system",
                        "content": _plan_coord_msg,
                    })
        except Exception:
            pass

    # Trigger 2: 3+ goals → enter planning mode (orchestrated profile only)
    _AUTO_COORD_GOAL_THRESHOLD = 3
    if (
        config.enable_delegation
        and state.active_mode == AgentMode.EXECUTING
        and state.orchestration_profile == "orchestrated"
        and len(state.goals) >= _AUTO_COORD_GOAL_THRESHOLD
    ):
        state.active_mode = AgentMode.PLANNING
        logger.info(
            "[LOOP:%s] AUTO-MODE: %d goals detected (>= %d) — "
            "entering PLANNING mode",
            state.loop_id, len(state.goals), _AUTO_COORD_GOAL_THRESHOLD,
        )
        _auto_coord_msg = (
            "PLANNING MODE has been AUTO-ACTIVATED because this task "
            f"has {len(state.goals)} distinct goals. You are the engineering "
            "manager — decompose into todos, create plans, and delegate "
            "ALL implementation to sub-agents.\n\n"
            "CRITICAL WORKFLOW (follow this EXACT order):\n"
            "1. Create ONE master todo: todo(action='add')\n"
            "2. Note the EXACT short ID returned (e.g. '903ec6d4')\n"
            "3. Create a MASTER PLAN covering the ENTIRE project lifecycle "
            "(7-12 steps from scaffolding through deployment): "
            "plan(action='create', todo_id='903ec6d4', steps=[...]) — "
            "include ALL phases: scaffolding, DB schema, backend, frontend, "
            "integrations, auth, deployment. Do NOT create a plan with only "
            "setup/scaffolding steps.\n"
            "4. switch_mode(mode='delegating')\n"
            "5. Create a team: team(action='create', plan_id=..., "
            "wave=0, name='Wave 0 - Scaffolding')\n"
            "6. Launch the team: team(action='launch', team_id=...)\n"
            "7. switch_mode(mode='monitoring')\n"
            "Do NOT skip the todo_id linkage — it drives the Kanban board.\n\n"
            "CRITICAL: Do NOT create project files, directories, or "
            "scaffolding yourself. That is Wave 0's job. Go STRAIGHT from "
            "plan creation to team creation. No git init, no repo creation."
        )
        _rmode_ref = getattr(hooks, "_render_mode_ref", None)
        if _rmode_ref:
            _rmode_ref[0] = AgentMode.PLANNING.value
        context.append({"role": "system", "content": _auto_coord_msg})

    # Trigger 3: team manager has non-terminal teams → restore orchestration
    # mode even when Trigger 1 didn't fire (e.g. all plan steps delegated,
    # or teams exist from a prior wave that needs reviewing/advancing).
    # GUARD: skip if the user explicitly switched mode recently (within 3
    # iterations) — don't fight a deliberate user intent.
    _t3_grace = 3
    _t3_user_switched_recently = (
        (state.iteration - state.user_mode_switch_iter) < _t3_grace
    )
    if (
        config.enable_delegation
        and state.active_mode == AgentMode.EXECUTING
        and not _t3_user_switched_recently
    ):
        _trigger3_tm = None
        for _t3_tool_name, _t3_tool_obj in tools.items():
            if _t3_tool_name == "team":
                _trigger3_tm = getattr(_t3_tool_obj, "_tm", None)
                break
        if _trigger3_tm is not None:
            try:
                _all_teams = _trigger3_tm.list_teams(include_terminal=True)
                _non_terminal = [t for t in _all_teams if not t.is_terminal]
                _recently_completed = [
                    t for t in _all_teams
                    if t.is_terminal
                    and (time.time() - getattr(t, "completed_at", t.created_at)) < 600
                ]
                if _non_terminal:
                    state.active_mode = AgentMode.MONITORING
                    invalidate_tool_policy_cache(state)  # re-filter schemas for MONITORING
                    _team_ids = ", ".join(t.id[:8] for t in _non_terminal[:3])
                    logger.info(
                        "[LOOP:%s] AUTO-MODE (teams): %d active teams "
                        "(%s) — entering MONITORING mode",
                        state.loop_id, len(_non_terminal), _team_ids,
                    )
                    context.append({"role": "system", "content": (
                        "MONITORING MODE — engineering manager on the board.\n"
                        f"You have {len(_non_terminal)} active wave(s). "
                        "Your team executes; you steer when stuck and review "
                        "when waves land.\n"
                        "After launch: communicate(optional) → "
                        "await_delegates(summary='...') to end this turn.\n"
                        "On wake: inspect → hint if blocked → evaluating "
                        "to review deliverables → advance plan/Kanban.\n"
                        "Do NOT do IC work (write/bash) or idle-poll wait(60+)."
                    )})
                elif _recently_completed:
                    state.active_mode = AgentMode.EVALUATING
                    logger.info(
                        "[LOOP:%s] AUTO-MODE (teams): %d recently "
                        "completed teams — entering EVALUATING mode",
                        state.loop_id, len(_recently_completed),
                    )
                    context.append({"role": "system", "content": (
                        "EVALUATING MODE — engineering manager code review.\n"
                        "A wave finished. Inspect outputs, verify acceptance "
                        "criteria, patch small gaps, update plan/Kanban.\n"
                        "Use plan(accept_partial) if delegates failed but "
                        "artifacts exist. Then team(advance) or launch next wave.\n"
                        "When ALL plan steps are done: plan(verify) → "
                        "plan(complete) → task_complete."
                    )})
            except Exception:
                pass

    # Session resume: user returned after downtime — avoid status re-scan loops.
    if (
        config.enable_delegation
        and user_requests_session_resume(user_input)
        and _plan_tool
        and hasattr(_plan_tool, "get_store")
    ):
        try:
            _resume_plan = _plan_tool.get_store().find_active()
            if _resume_plan and _resume_plan.status != "done":
                _resume_blocking = False
                for _rtool_name, _rtool_obj in tools.items():
                    if _rtool_name == "team":
                        _rtm = getattr(_rtool_obj, "_tm", None)
                        if _rtm is not None:
                            try:
                                _resume_blocking = (
                                    _rtm.has_orchestrator_blocking_team()
                                )
                            except Exception:
                                pass
                        break
                _resume_msg = build_session_resume_guidance(
                    _resume_plan,
                    blocking_team=_resume_blocking,
                )
                context.append({"role": "system", "content": _resume_msg})
                _open_steps = [
                    s for s in _resume_plan.steps
                    if s.status not in ("done", "skipped")
                ]
                if (
                    state.active_mode == AgentMode.EXECUTING
                    and any(s.delegatable for s in _open_steps)
                ):
                    state.active_mode = AgentMode.DELEGATING
                    invalidate_tool_policy_cache(state)
                logger.info(
                    "[LOOP:%s] injected session-resume guidance for plan %s",
                    state.loop_id, _resume_plan.id,
                )
        except Exception:
            logger.debug("Session resume guidance failed", exc_info=True)

    # Set render mode for Cryptex compositor
    _rmode_ref = getattr(hooks, "_render_mode_ref", None)
    if _rmode_ref:
        _rmode_ref[0] = state.active_mode.value

    # Populate loop state ref for Cryptex ring priority computation
    _lstate_ref = getattr(hooks, "_loop_state_ref", None)
    if _lstate_ref is not None:
        _pending_cr = 0
        _tm_lc = getattr(hooks, "_cached_team_manager", None)
        if _tm_lc is not None:
            try:
                _pending_cr = len(
                    getattr(_tm_lc, "_pending_completion_reviews", {}) or {},
                )
            except Exception:
                pass
        _has_plan_now = False
        if hooks and hooks.has_active_plan:
            try:
                _has_plan_now = bool(hooks.has_active_plan())
            except Exception:
                pass
        _lstate_ref.update({
            "coordinator_mode": state.coordinator_mode,
            "active_mode": state.active_mode.value,
            "coordinator_phase": state.coordinator_phase,
            "pending_completion_reviews": _pending_cr,
            "iteration": state.iteration,
            "delegate_count": state.delegate_count,
            "orchestration_profile": state.orchestration_profile or "solo_structured",
            "has_active_plan": _has_plan_now,
            "last_tool": "",
            "last_tool_action": "",
            "recent_tools": [],
            # Network dynamics + hormones (populated per-iteration by bridge)
            "network_ecn": 0.0,
            "network_sn": 0.0,
            "network_dmn": 0.0,
            "dominant_network": "",
            "cortisol": 0.0,
            "oxytocin": 0.0,
        })

    logger.info(
        "[LOOP:%s] START goals=%d hints=%d context_msgs=%d first_resp=%s "
        "first_tc=%s thinking=%s suppress=%s user_input_len=%d coordinator=%s",
        state.loop_id,
        len(state.goals), len(state.hints),
        len(context),
        "yes" if first_response else "no",
        len(first_tool_calls) if first_tool_calls else 0,
        enable_thinking,
        bool(pre_extracted_goals),
        len(user_input),
        state.coordinator_mode,
    )
    _slog(_session_log_path, {
        "event": "loop_start",
        "loop_id": state.loop_id,
        "dispatch_source": dispatch_source or "",
        "goals": state.goals,
        "hints": state.hints[:5],
        "context_msgs": len(context),
        "user_input_preview": user_input[:500],
        "enable_thinking": enable_thinking,
        "max_iterations": config.max_iterations,
        "max_tool_calls": config.max_tool_calls,
        "enable_delegation": config.enable_delegation,
        "session_log_path": _session_log_path,
    })
    for _ci, _cm in enumerate(context):
        _cr = _cm.get("role", "?")
        _cc = len(_cm.get("content") or "")
        _ctc = bool(_cm.get("tool_calls"))
        logger.info(
            "[LOOP:%s] context[%d] role=%s content_len=%d has_tc=%s preview=%.80s",
            state.loop_id, _ci, _cr, _cc, _ctc, (_cm.get("content") or "")[:80],
        )

    # Apply per-mode schema restriction at loop start
    if state.active_mode != AgentMode.EXECUTING and not state._mode_schemas_applied:
        state._mode_schemas_applied = True
        _mode_allowed = get_allowed_tools(state.active_mode)
        if _mode_allowed:
            _base_schemas = [
                s for s in _all_schemas
                if s.get("function", {}).get("name", "") in _mode_allowed
            ]
            state.unlocked_tools = {
                t for t in _all_unlocked if t in _mode_allowed
            }
        else:
            _base_schemas = list(_all_schemas)
            state.unlocked_tools = set(_all_unlocked)
        _base_schemas, state.unlocked_tools, _ = refresh_tool_schemas(
            state,
            _all_schemas,
            _all_unlocked,
            state.active_mode,
            delegate_manager,
            hooks,
            force=True,
        )
        logger.info(
            "[LOOP:%s] MODE %s schemas applied — %d tools: %s",
            state.loop_id, state.active_mode.value, len(_base_schemas),
            sorted(s.get("function", {}).get("name", "") for s in _base_schemas),
        )
    elif state.active_mode == AgentMode.EXECUTING and not state._mode_schemas_applied:
        # Loop started directly in EXECUTING: expand to the full tool set
        # in case the snapshot was taken with a predict_tools filter.
        state._mode_schemas_applied = True
        _exec_schemas2: list[dict] = virtual_tool_schemas_for_loop(
            enable_delegation=config.enable_delegation,
            enable_detached_delegates=config.enable_detached_delegates,
            delegate_manager=delegate_manager,
        )
        _exec_unlocked2: set[str] = set(
            virtual_tool_names_for_loop(
                enable_delegation=config.enable_delegation,
                enable_detached_delegates=config.enable_detached_delegates,
                delegate_manager=delegate_manager,
            )
        )
        for _s2 in _all_schemas:
            _sn2 = (_s2.get("function") or {}).get("name", "")
            if _sn2:
                _exec_unlocked2.add(_sn2)
        for _en2, _eo2 in tools.items():
            if isinstance(_eo2, AgentTool):
                try:
                    _exec_schemas2.append(tool_to_openai_schema(_eo2))
                    _exec_unlocked2.add(_en2)
                except Exception:
                    pass
        _base_schemas = _exec_schemas2
        state.unlocked_tools = _exec_unlocked2
        _all_schemas = list(_exec_schemas2)
        _all_unlocked = set(_exec_unlocked2)
        if config.enable_delegation and not _is_delegate_loop:
            _base_schemas, state.unlocked_tools, _ = refresh_tool_schemas(
                state,
                _all_schemas,
                _all_unlocked,
                state.active_mode,
                delegate_manager,
                hooks,
                force=True,
            )
        logger.info(
            "[LOOP:%s] MODE %s schemas applied (full set) — %d tools",
            state.loop_id, state.active_mode.value, len(_base_schemas),
        )

    await emit(on_event, AgentEvent(EventType.AGENT_START, {"iteration": 0}))

    # --- Handle first_response / first_tool_calls passthrough ---
    if first_response or first_tool_calls:
        from .types import GenerationResult
        msg: dict = {"role": "assistant", "content": first_response or None}
        if first_tool_calls:
            msg["tool_calls"] = first_tool_calls
            msg["content"] = None
        context.append(msg)

        if first_tool_calls:
            _requested = {
                tc.get("function", {}).get("name", "")
                for tc in first_tool_calls
            }
            _requested.discard("")
            if _requested and state.active_mode == AgentMode.CHAT:
                from nls.agentic.orchestration_policy import (
                    build_tool_policy_inputs,
                    resolve_allowed_tools,
                )

                _inputs = build_tool_policy_inputs(
                    state.active_mode,
                    state,
                    delegate_manager,
                    set(state.unlocked_tools),
                    hooks,
                )
                _allowed = resolve_allowed_tools(_inputs)
                if _requested - _allowed:
                    state.active_mode = AgentMode.EXECUTING
                    invalidate_tool_policy_cache(state)
                    logger.info(
                        "[LOOP:%s] first_tool_calls promoted CHAT→EXECUTING "
                        "for tools=%s",
                        state.loop_id,
                        sorted(_requested - _allowed),
                    )

            results, digest_count = await execute_tools(
                first_tool_calls, tools, config, state,
                abort_signal=abort_signal,
                on_event=on_event,
                hooks=hooks,
                vllm_client=vllm_client,
                user_task=user_input,
                digest_count=digest_count,
                delegate_manager=delegate_manager,
                response_has_text=bool(first_response and len(first_response.strip()) > 50),
            )
            for tc, result in zip(first_tool_calls, results):
                context.append(make_tool_message(tc, result))
                state.total_tool_calls += 1
                _pre_name = tc.get("function", {}).get("name", "unknown")
                _pre_args = tc.get("function", {}).get("arguments", "")
                _pre_fp = _pre_args[:200] if isinstance(_pre_args, str) else str(_pre_args)[:200]
                state.record_tool(_pre_name, result, args_fingerprint=_pre_fp)
                _register_appended_tool_outcome(
                    state, context, _pre_name, result, _pre_args,
                )

                if not result.is_error:
                    _pbc_hint = _breadcrumb_engine.evaluate(
                        _build_bc_ctx(_pre_name, result, state, _deferred_actions, anchor)
                    )
                    if _pbc_hint:
                        context.append({"role": "system", "content": _pbc_hint})

            _apply_context_supersession_pass(
                context,
                config=config,
                state=state,
                anchor=anchor,
                is_delegate_loop=_is_delegate_loop,
                dispatch_source=dispatch_source,
                team_manager=_cached_team_manager,
                tools=tools,
                start_index=_loop_start_idx,
                cwd=_supersession_cwd,
                plan_tool=_plan_tool,
            )

            # Plan event bridge for pre-loop tool calls (iter=0).
            # If a plan was created/updated before the main loop starts, emit
            # the agentic_plan event so the frontend card appears immediately.
            _any_plan_call = any(
                (tc.get("function") or {}).get("name") == "plan"
                for tc in first_tool_calls
            )
            if _any_plan_call and _plan_tool and hasattr(_plan_tool, "get_store"):
                try:
                    _store = _plan_tool.get_store()
                    _refreshed = _store.find_active()
                    if _refreshed and _refreshed.steps:
                        _plan_steps = [s.label for s in _refreshed.steps]
                        _plan_statuses = [s.status for s in _refreshed.steps]
                        _rich = [
                            {"id": s.id, "label": s.label, "status": s.status}
                            for s in _refreshed.steps
                        ]
                        await emit(on_event, AgentEvent(
                            EventType.PLAN_UPDATE, {
                                "type": "agentic_plan",
                                "steps": _rich,
                                "plan_id": _refreshed.id,
                                "title": _refreshed.title,
                                "todo_id": _refreshed.todo_id or "",
                                "project_dir": _refreshed.project_dir or "",
                                "iteration": 0,
                            },
                        ))
                except Exception:
                    pass

    # --- Main loop ---
    while True:
        state.iteration += 1

        if _lstate_ref is not None:
            _lstate_ref["iteration"] = state.iteration
            _dc = state.delegate_count
            if delegate_manager is not None:
                try:
                    _running = delegate_manager.running_count()
                    if _running:
                        _dc = max(_dc, _running)
                        state.delegate_count = _dc
                except Exception:
                    pass
            _lstate_ref["delegate_count"] = _dc
            _lstate_ref["coordinator_mode"] = state.coordinator_mode
            _lstate_ref["active_mode"] = state.active_mode.value
            from nls.agentic.skill_discovery_boost import (
                sync_skill_discovery_boost_flag,
            )
            sync_skill_discovery_boost_flag(_lstate_ref, state.iteration)

        # Tombstone cleanup: remove partial messages from failed streams
        _pre_tomb = len(context)
        context = [m for m in context if not m.get("_tombstoned")]
        _tomb_removed = _pre_tomb - len(context)
        if _tomb_removed:
            logger.info(
                "[LOOP:%s] iter %d: cleaned %d tombstoned message(s)",
                state.loop_id, state.iteration, _tomb_removed,
            )

        logger.info(
            "[LOOP:%s] === ITERATION %d === total_tc=%d consec_text=%d "
            "ctx_msgs=%d unlocked=%s",
            state.loop_id,
            state.iteration,
            state.total_tool_calls,
            state.consecutive_text_only,
            len(context),
            list(state.unlocked_tools),
        )
        _slog(_session_log_path, {
            "event": "iteration_start",
            "loop_id": state.loop_id,
            "iteration": state.iteration,
            "total_tool_calls": state.total_tool_calls,
            "ctx_msgs": len(context),
            "tool_errors": dict(state.tool_errors),
            "tool_successes": dict(state.tool_successes),
        })

        # Periodic accumulator flush with LLM compression
        if not _is_delegate_loop:
            _acc = getattr(hooks, "_accumulator", None)
            _acc_wm = getattr(hooks, "_accumulator_wm_target", None)
            if _acc is not None and _acc.should_periodic_flush(state.iteration):
                try:
                    import asyncio as _aio_acc
                    _aio_acc.get_running_loop().create_task(
                        _acc.compress_and_flush(
                            _acc_wm,
                            reason=f"periodic-iter-{state.iteration}",
                        )
                    )
                except Exception:
                    _acc.flush(_acc_wm, reason=f"periodic-iter-{state.iteration}")

        # A. Guards
        if abort_signal and abort_signal.is_set():
            logger.info("[LOOP:%s] iter %d: ABORT signal set", state.loop_id, state.iteration)
            _slog(_session_log_path, {
                "event": "guard_exit", "loop_id": state.loop_id,
                "iteration": state.iteration, "reason": "user_abort",
            })
            state.exit_reason = "user_abort"
            break

        if hooks.plan_has_pending_steps:
            try:
                _has_pending = hooks.plan_has_pending_steps()
            except Exception:
                _has_pending = bool(
                    hooks.has_active_plan and hooks.has_active_plan()
                )
        else:
            _has_pending = bool(
                hooks.has_active_plan and hooks.has_active_plan()
            )
        _has_team = False
        _guard_tm = _cached_team_manager
        if _guard_tm is not None:
            try:
                _has_team = _guard_tm.has_orchestrator_blocking_team()
            except Exception:
                pass

        # Intra-loop coordinator activation: active teams exist and agent is in
        # EXECUTING mode — restore MONITORING so it doesn't try to implement
        # work itself while delegates are running.
        # GUARD: respect a recent explicit user switch for 3 iterations, so the
        # agent can briefly use EXECUTING tools (e.g. check calendar) without
        # being immediately overridden.
        _il_user_switched_recently = (
            (state.iteration - state.user_mode_switch_iter) < 3
        )
        if (
            _has_team
            and config.enable_delegation
            and state.active_mode == AgentMode.EXECUTING
            and not _il_user_switched_recently
        ):
            state.active_mode = AgentMode.MONITORING
            invalidate_tool_policy_cache(state)  # re-filter schemas for MONITORING
            _rmode_ref = getattr(hooks, "_render_mode_ref", None)
            if _rmode_ref:
                _rmode_ref[0] = AgentMode.MONITORING.value
            logger.info(
                "[LOOP:%s] iter %d: INTRA-LOOP coordinator activation — "
                "active teams detected, switching to MONITORING",
                state.loop_id, state.iteration,
            )
            context.append({"role": "system", "content": (
                "MONITORING MODE ACTIVATED — you just launched delegates. "
                "You are now the ORCHESTRATOR. Do NOT implement code yourself. "
                "Use team(action='inspect') to check progress, wait() for "
                "running delegates, and team(action='advance') when waves "
                "complete."
            )})

        _has_plan_now = bool(
            hooks.has_active_plan and hooks.has_active_plan()
        )
        _has_running_del = False
        if delegate_manager is not None:
            try:
                _has_running_del = delegate_manager.has_active_delegates()
            except Exception:
                pass
        _plan_req_team = False
        if hooks.plan_requires_team_delegation:
            try:
                _plan_req_team = hooks.plan_requires_team_delegation()
            except Exception:
                pass
        _pd_reason = pre_delegate_reason(
            state,
            config,
            plan_requires_team_delegation=_plan_req_team,
            has_active_plan=_has_plan_now,
            has_running_delegates=_has_running_del,
            has_non_terminal_team=_has_team,
            is_delegate_loop=_is_delegate_loop,
            orchestrator_recovery=state.orchestrator_recovery,
        )
        state.pre_delegate_reason = _pd_reason or ""
        state.must_delegate_before_impl = _pd_reason is not None

        guard_reason = check_guards(
            state, config,
            has_pending_plan=_has_pending,
            has_active_team=_has_team,
        )
        if guard_reason:
            if guard_reason.startswith("tool_nudge:"):
                _nudge_tool = guard_reason.split(":", 1)[1]
                state.tool_nudges_given[_nudge_tool] = (
                    state.tool_nudges_given.get(_nudge_tool, 0) + 1
                )
                state.tool_errors[_nudge_tool] = 0
                _recent_errs = [
                    f"  - {n}: {c} error(s)"
                    for n, c in state.tool_errors.items() if c > 0
                ]
                _nudge_msg = (
                    f"WARNING: The '{_nudge_tool}' tool has failed "
                    f"{config.per_tool_retry_limit} times. "
                    f"Stop using it the same way — the approach is not working.\n"
                    f"Consider alternative tools (list_dir, glob, read) or a "
                    f"completely different strategy.\n"
                    f"If you have enough information already, write your final "
                    f"response now."
                )
                context.append({"role": "user", "content": _nudge_msg})
                logger.info(
                    "[LOOP:%s] iter %d: NUDGE for '%s' (nudge %d/%d) — "
                    "counter reset, continuing",
                    state.loop_id, state.iteration, _nudge_tool,
                    state.tool_nudges_given[_nudge_tool],
                    config.max_tool_nudges,
                )
                _slog(_session_log_path, {
                    "event": "tool_nudge", "loop_id": state.loop_id,
                    "iteration": state.iteration, "tool": _nudge_tool,
                    "nudge_count": state.tool_nudges_given[_nudge_tool],
                })
            else:
                _escalatable = (
                    guard_reason in ("max_iterations", "total_timeout", "tool_call_budget", "consecutive_errors")
                    or guard_reason.startswith("per_tool_retry_limit:")
                )
                if _escalatable and await _try_escalate(
                    guard_reason, state, config, copilot_queue, context,
                    slog_path=_session_log_path,
                ):
                    continue
                _final_reason = state.exit_reason if state.exit_reason == "orchestrator_terminated" else guard_reason
                logger.info("[LOOP:%s] iter %d: GUARD exit=%s", state.loop_id, state.iteration, _final_reason)
                _slog(_session_log_path, {
                    "event": "guard_exit", "loop_id": state.loop_id,
                    "iteration": state.iteration, "reason": _final_reason,
                    "total_tool_calls": state.total_tool_calls,
                    "tool_errors": dict(state.tool_errors),
                })
                state.exit_reason = _final_reason

                # ---- Auto-resume todo on budget exhaustion ----------------
                # When the loop exits because the iteration or time budget is
                # consumed but there is still an active plan with pending steps,
                # create an idle-eligible high-priority todo so the DMN picks
                # it up on the next background cycle instead of daydreaming.
                # Only needed when no delegates are live. If sub-agents are
                # still running they will escalate back to the orchestrator
                # on their own and the resume todo would be redundant.
                if (
                    _final_reason in ("max_iterations", "total_timeout")
                    and tools
                    and state.delegate_count == 0
                ):
                    try:
                        _plan_tool = tools.get("plan")
                        _todo_tool = tools.get("todo")
                        if _plan_tool is not None and _todo_tool is not None:
                            _ps = getattr(_plan_tool, "_store", None)
                            _tm = _cached_team_manager
                            _active_plan = (
                                _ps.resolve_work_plan("", _tm, reopen=True)
                                if _ps is not None
                                else None
                            )
                            from nls.agentic.plan_work import (
                                incomplete_steps,
                                plan_needs_recovery,
                                work_plan_has_open_steps,
                            )

                            _needs_resume = (
                                _active_plan is not None
                                and work_plan_has_open_steps(_active_plan)
                            )
                            if _needs_resume:
                                _open = incomplete_steps(_active_plan)
                                _pending_labels = [
                                    s.label or s.id
                                    for s in (
                                        _active_plan.pending_steps() or _open
                                    )[:3]
                                ]
                                _recovery = plan_needs_recovery(
                                    _active_plan, _tm,
                                )
                                _resume_title = (
                                    f"Resume plan: {_active_plan.title or _active_plan.id}"
                                )
                                _resume_desc = (
                                    f"Loop exited ({_final_reason}). "
                                    f"plan_id={_active_plan.id} "
                                    f"status={_active_plan.status} "
                                    f"recovery={_recovery}. "
                                    f"Open: {', '.join(_pending_labels)}. "
                                    "Use plan(action='read', plan_id='...'), "
                                    "accept_partial / delegate as needed, then continue."
                                )
                                _todo_store = getattr(_todo_tool, "get_store", None)
                                if callable(_todo_store) and config.agent_id:
                                    import uuid as _uuid
                                    import time as _time_mod
                                    _store = _todo_store(config.agent_id)
                                    # Avoid duplicate resume todos
                                    _existing = _store.list_items(status="inbox") + _store.list_items(status="queued")
                                    _already = any("Resume plan:" in (it.title or "") for it in _existing)
                                    if not _already:
                                        _store.add(
                                            id=_uuid.uuid4().hex[:12],
                                            list_id="inbox",
                                            title=_resume_title,
                                            description=_resume_desc,
                                            priority="high",
                                            status="inbox",
                                            idle_eligible=True,
                                            source="system",
                                            plan_id=_active_plan.id,
                                            created_at=_time_mod.time(),
                                        )
                                        logger.info(
                                            "[LOOP:%s] guard_exit resume todo created for plan %s (%d pending steps)",
                                            state.loop_id, _active_plan.id, len(_active_plan.pending_steps()),
                                        )
                    except Exception as _resume_exc:
                        logger.debug("[LOOP:%s] guard_exit resume todo error: %s", state.loop_id, _resume_exc)
                # -----------------------------------------------------------

                break

        # --- Plan completion wrap-up budget ---
        # After plan(action='complete') succeeds, the orchestrator gets
        # a small budget (10 iterations) to send a final notification
        # and clean up.  After that, force exit to prevent zombie cycling
        # (re-inspecting teams, re-reading files, re-sending WhatsApp).
        _POST_PLAN_BUDGET = 10
        if (
            state.plan_completed_at_iter >= 0
            and (state.iteration - state.plan_completed_at_iter) >= _POST_PLAN_BUDGET
        ):
            logger.info(
                "[LOOP:%s] iter %d: POST-PLAN BUDGET EXHAUSTED — "
                "%d iterations since plan completed at iter %d. Exiting.",
                state.loop_id, state.iteration,
                state.iteration - state.plan_completed_at_iter,
                state.plan_completed_at_iter,
            )
            state.exit_reason = "task_complete"
            break

        # --- Sub-agent budget pacing (completion-focused) ---
        from nls.agentic.evaluator import inject_subagent_pacing_nudges
        inject_subagent_pacing_nudges(state, config, context)

        if getattr(state, "prose_gate_active", False):
            context.append({
                "role": "system",
                "content": (
                    "[PROSE GATE] Your previous prose-only reply was held "
                    "(not shown to the user) because this task still needs "
                    "tool action. Continue with tools now. If you need "
                    "information or credentials from the user, call "
                    "ask_user() — do not ask in prose or assume they already "
                    "answered."
                ),
            })
            logger.info(
                "[LOOP:%s] iter %d: PROSE GATE nudge injected",
                state.loop_id, state.iteration,
            )

        # --- Stall detection: "I'm stuck" nudge ---
        from nls.agentic.evaluator import detect_stall
        _stall_msg = detect_stall(state, config)
        if _stall_msg:
            if state.stall_nudges_given >= 2:
                if await _try_escalate(
                    "stalled", state, config, copilot_queue, context,
                    slog_path=_session_log_path,
                ):
                    continue
                logger.info(
                    "[LOOP:%s] iter %d: STALL persists after %d nudges — force exit",
                    state.loop_id, state.iteration,
                    state.stall_nudges_given,
                )
                state.exit_reason = "stalled"
                break
            state.stall_nudges_given += 1
            context.append({"role": "user", "content": _stall_msg})
            from nls.agentic.skill_discovery_boost import (
                trigger_skill_discovery_boost,
            )
            trigger_skill_discovery_boost(
                hooks,
                iteration=state.iteration,
                reason="stall_detected",
                orchestration_profile=state.orchestration_profile,
            )
            logger.info(
                "[LOOP:%s] iter %d: STALL nudge #%d injected",
                state.loop_id, state.iteration,
                state.stall_nudges_given,
            )

        # Wrap-up signal: parent monitor says "finalize now"
        if wrap_up_signal and wrap_up_signal.is_set():
            wrap_up_signal.clear()
            _wu_msg = (
                f"You have used {state.iteration - state.wait_only_iterations} of "
                f"{config.max_iterations} effective iterations. "
                "If core deliverables are not on disk yet, build the next "
                "piece now — do not exit early just to save iterations. "
                "If you are blocked or need more budget, call escalate(). "
                "If the task is truly complete, verify once and finish."
            )
            context.append({"role": "system", "content": _wu_msg})
            logger.info(
                "[LOOP:%s] iter %d: WRAP-UP signal injected",
                state.loop_id, state.iteration,
            )
            _slog(_session_log_path, {
                "event": "wrap_up_injected",
                "loop_id": state.loop_id,
                "iteration": state.iteration,
            })

        # Steering from hooks
        if hooks.get_steering_messages:
            try:
                steering = await hooks.get_steering_messages()
                if steering:
                    _has_orch_hint = any(
                        "[ORCHESTRATOR HINT]" in (m.get("content") or "")
                        or "[ORCHESTRATOR REVIEW" in (m.get("content") or "")
                        or "ORCHESTRATOR DIRECTIVE" in (m.get("content") or "")
                        for m in steering
                    )
                    for msg in steering:
                        context.append(msg)
                    state.just_received_steering = True
                    if _has_orch_hint:
                        state.received_orchestrator_hint = True
                        from nls.agentic.skill_discovery_boost import (
                            trigger_skill_discovery_boost,
                        )
                        trigger_skill_discovery_boost(
                            hooks,
                            iteration=state.iteration,
                            reason="orchestrator_hint",
                            orchestration_profile=state.orchestration_profile,
                        )
                    logger.info(
                        "[LOOP:%s] iter %d: STEERING injected %d msgs "
                        "(hint=%s) — ctx_msgs now %d",
                        state.loop_id, state.iteration,
                        len(steering), _has_orch_hint, len(context),
                    )
                else:
                    state.just_received_steering = False
            except Exception:
                logger.debug(
                    "[LOOP:%s] iter %d: steering drain failed",
                    state.loop_id, state.iteration, exc_info=True,
                )
                state.just_received_steering = False

        # A2. EM nudges while a wave is executing
        if state.coordinator_mode and delegate_manager is not None:
            try:
                if delegates_running(delegate_manager):
                    if getattr(state, "must_await_delegates", False):
                        context.append({
                            "role": "system",
                            "content": (
                                "[POST-LAUNCH] Wave is executing. Debrief the "
                                "stakeholder (communicate) if needed, then "
                                "await_delegates(summary='...') — your "
                                "management turn is done until escalation, "
                                "completion, or scheduled review."
                            ),
                        })
                    elif state.coordinator_burn_iters >= 2:
                        context.append({
                            "role": "system",
                            "content": (
                                "[EM TURN COMPLETE] You are idle-polling, not "
                                "managing. The board/WM already tracks wave "
                                "state. If no escalation: await_delegates. "
                                "If someone is stuck: ONE hint, then await."
                            ),
                        })
            except Exception:
                pass

        # B-pre. Supersede stale tool results (safety net before WM transform).
        # Primary pass also runs immediately after each tool batch append.
        _apply_context_supersession_pass(
            context,
            config=config,
            state=state,
            anchor=anchor,
            is_delegate_loop=_is_delegate_loop,
            dispatch_source=state.dispatch_source or dispatch_source,
            team_manager=_cached_team_manager,
            tools=tools,
            start_index=_loop_start_idx,
            cwd=_supersession_cwd,
            plan_tool=_plan_tool,
        )

        # B. Context transform (WM injection) — runs from iteration 1 so
        # the Cryptex compose_context replaces the static bootstrap on the
        # very first LLM call, not just from the second onwards.
        if hooks.transform_context and state.iteration >= 1:
            try:
                context = hooks.transform_context(context)
            except Exception:
                logger.debug("transform_context hook failed", exc_info=True)

        # B-pre. Runtime tool schema filter (no IC tools while wave runs)
        if config.enable_delegation and not _is_delegate_loop:
            _filtered, _unlocked, _policy_changed = refresh_tool_schemas(
                state,
                _all_schemas,
                _all_unlocked,
                state.active_mode,
                delegate_manager,
                hooks,
            )
            if _policy_changed:
                _base_schemas = _filtered
                state.unlocked_tools = _unlocked
            anchor.available_tools = sorted(state.unlocked_tools)

        # C. Compaction check
        if should_compact(context, config, anchor):
            anchor.available_tools = sorted(state.unlocked_tools)
            context, anchor = await compact(
                context, anchor, config, vllm_client,
                iteration=state.iteration,
                adapter_name=adapter_name,
                is_delegate_loop=_is_delegate_loop,
            )
            if hooks.on_compaction:
                try:
                    hooks.on_compaction(anchor)
                except Exception:
                    pass

        # D-pre. Periodic thalamic re-route (every 5 turns)
        if (
            hooks.refresh_thalamic_route
            and state.iteration % 5 == 0
            and state.iteration > 0
        ):
            try:
                fresh_xargs = hooks.refresh_thalamic_route()
                if fresh_xargs is not None:
                    config.vllm_xargs = fresh_xargs
            except Exception:
                logger.debug("periodic thalamic re-route failed", exc_info=True)

        # D-pre2. Memory experts remapped to L19-23 (Zone 2/3
        # boundary) in agentic mode.  Keep prefill_bias_scale at 0.15
        # throughout all iterations for continuous behavioral grounding.
        # decode_layer_scales remain [0.0]*40 (experts silent during
        # token generation).
        # History: L6-20 (full Zone 2) → degenerate.  L37/39 + iter-1
        # silencing → clean but no drift prevention.  Now testing
        # L19-23 as a narrower, less sensitive target zone.

        # D. Generate
        await emit(on_event, AgentEvent(
            EventType.TURN_START, {"iteration": state.iteration},
        ))

        thinking = enable_thinking

        # V5 reasoning continuation: build prefill message from trajectory
        _cortisol = 0.2
        if hooks.get_cortisol:
            try:
                _cortisol = hooks.get_cortisol()
            except Exception:
                pass

        _thinking_mode = _select_thinking_mode(
            consecutive_errors=state.consecutive_errors,
            coherence_score=_last_coherence,
            cortisol=_cortisol,
            iteration=state.iteration,
            has_trajectory=bool(_reasoning_trajectory),
        )

        # Reasoning continuation (prefill) is DISABLED.
        # continue_final_message=True bypasses the Qwen3.5 template's
        # thinking flow control, causing the model to put its entire
        # response inside <think> blocks with zero visible text.
        # Without prefill, the template handles thinking properly and
        # the model produces both reasoning AND visible text.
        # TODO: revisit if vLLM adds proper thinking-aware continuation.
        _prefill: dict | None = None

        async def _gen_heartbeat() -> None:
            """Emit periodic status while generation is in progress."""
            _hb_start = time.time()
            await asyncio.sleep(15)
            while True:
                _elapsed_ms = int((time.time() - _hb_start) * 1000)
                await emit(on_event, AgentEvent(
                    EventType.STATUS,
                    {
                        "message": "Crunching data\u2026",
                        "status": "generating",
                        "elapsed_ms": _elapsed_ms,
                    },
                ))
                await asyncio.sleep(15)

        if should_compact(context, config, anchor):
            logger.info(
                "[LOOP:%s] iter %d: PRE-GENERATE compaction triggered "
                "(tool results pushed context over budget)",
                state.loop_id, state.iteration,
            )
            anchor.available_tools = sorted(state.unlocked_tools)
            context, anchor = await compact(
                context, anchor, config, vllm_client,
                iteration=state.iteration,
                adapter_name=adapter_name,
                is_delegate_loop=_is_delegate_loop,
            )
            if hooks.on_compaction:
                try:
                    hooks.on_compaction(anchor)
                except Exception:
                    pass

        _journal_write(_journal, state.iteration, context)

        _hb_task = asyncio.create_task(_gen_heartbeat())
        try:
            response = await generate(
                context, tools, config, vllm_client,
                thinking=thinking,
                adapter_name=adapter_name,
                on_event=on_event,
                abort_signal=abort_signal,
                iteration=state.iteration,
                base_schemas=_base_schemas,
                unlocked_tools=state.unlocked_tools,
                prefill_msg=_prefill,
                loop_id=state.loop_id,
            )
        finally:
            _hb_task.cancel()
            try:
                await _hb_task
            except asyncio.CancelledError:
                pass

        logger.info(
            "[LOOP:%s] iter %d: GENERATE done — tool_calls=%d text_len=%d "
            "thinking_len=%d error=%s raw_len=%d mode=%s "
            "tokens=%d/%d/%d cumul=%d/%d/%d",
            state.loop_id, state.iteration,
            len(response.tool_calls),
            len(response.text),
            len(response.thinking),
            bool(response.error),
            len(response.raw_text) if response.raw_text else 0,
            _thinking_mode,
            response.prompt_tokens, response.completion_tokens, response.total_tokens,
            state.total_prompt_tokens, state.total_completion_tokens, state.total_tokens,
        )
        # ── Token accounting ──
        state.total_prompt_tokens += response.prompt_tokens
        state.total_completion_tokens += response.completion_tokens
        state.total_tokens += response.total_tokens
        if (
            config.enable_delegation
            and (
                is_orchestration_dispatch_source(dispatch_source)
                or delegates_running(delegate_manager)
            )
        ):
            state.coordinator_wake_prompt_tokens += response.prompt_tokens
        state.iter_token_log.append({
            "iter": state.iteration,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "total_tokens": response.total_tokens,
        })

        from nls.agentic.generation_budget import (
            analyze_generation_budget,
            build_file_tool_recovery_nudge,
            build_thinking_length_nudge,
            clear_truncated_write_attempt,
            extract_file_tool_target,
            record_truncated_file_events,
            should_suppress_error_recovery,
        )
        _budget = analyze_generation_budget(response, config)

        _gen_log: dict[str, Any] = {
            "event": "generation", "loop_id": state.loop_id,
            "iteration": state.iteration,
            "tool_calls_count": len(response.tool_calls),
            "text_len": len(response.text),
            "thinking_len": len(response.thinking),
            "error": response.error or None,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "total_tokens": response.total_tokens,
            "cumulative_prompt_tokens": state.total_prompt_tokens,
            "cumulative_completion_tokens": state.total_completion_tokens,
            "cumulative_total_tokens": state.total_tokens,
        }
        if response.finish_reason:
            _gen_log["finish_reason"] = response.finish_reason
        if _budget.output_budget_exhausted:
            _gen_log["output_budget_exhausted"] = True
        if _budget.truncated_file_tools:
            _gen_log["truncated_file_tools"] = _budget.truncated_file_tools
        if _budget.truncated_file_events:
            _gen_log["truncated_file_events"] = [
                {
                    "tool": e.tool_name,
                    "path": e.target_path[:120],
                    "kind": e.kind,
                }
                for e in _budget.truncated_file_events
            ]
        if _budget.thinking_budget_exhausted:
            _gen_log["thinking_budget_exhausted"] = True
        if response.tool_calls:
            _gen_log["tool_calls"] = [
                {
                    "name": tc.get("function", {}).get("name", "?"),
                    "args_preview": tc.get("function", {}).get("arguments", "")[:200],
                }
                for tc in response.tool_calls
            ]
            for _tci, _tc in enumerate(response.tool_calls):
                _tcn = _tc.get("function", {}).get("name", "?")
                _tca = _tc.get("function", {}).get("arguments", "")[:80]
                logger.info(
                    "[LOOP:%s] iter %d: tool_call[%d] name=%s args=%.80s",
                    state.loop_id, state.iteration, _tci, _tcn, _tca,
                )
        elif response.text:
            _gen_log["text_preview"] = response.text[:500]
            logger.info(
                "[LOOP:%s] iter %d: TEXT-ONLY preview=%.200s",
                state.loop_id, state.iteration, response.text[:200],
            )
        _slog(_session_log_path, _gen_log)

        if response.error:
            logger.warning(
                "[LOOP:%s] iter %d: GENERATION ERROR ctx_msgs=%d: %.400s",
                state.loop_id, state.iteration, len(context), (response.error or "")[:400],
            )
            _reasoning_trajectory = ""

            # Preserve partial content as tombstone for diagnostic/cleanup
            if response.message and response.message.get("_tombstoned"):
                context.append(response.message)

            await emit(on_event, AgentEvent(
                EventType.TURN_END, {"iteration": state.iteration, "error": True},
            ))
            if is_context_overflow(response.error):
                anchor.available_tools = sorted(state.unlocked_tools)
                context, anchor = await compact(
                    context, anchor, config, vllm_client,
                    force=True, iteration=state.iteration,
                    adapter_name=adapter_name,
                    is_delegate_loop=_is_delegate_loop,
                )
                if hooks.on_compaction:
                    try:
                        hooks.on_compaction(anchor)
                    except Exception:
                        pass
                state.overflow_retries += 1
                if state.overflow_retries > 2:
                    state.exit_reason = "context_overflow"
                    break
                continue
            if is_transient(response.error) and state.transient_retries < 3:
                state.transient_retries += 1
                state.iteration -= 1  # don't burn an iteration on a transient failure
                logger.info(
                    "[LOOP:%s] transient retry %d/3 — rolled back iteration to %d",
                    state.loop_id, state.transient_retries, state.iteration,
                )
                await asyncio.sleep(2 ** state.transient_retries)
                continue
            state.last_generation_error = response.error or ""
            state.exit_reason = "generation_error"
            break

        state.overflow_retries = 0
        state.transient_retries = 0

        # E. Post-generation — guard against empty/bare dict from failed generation
        if response.message and response.message.get("role"):
            context.append(response.message)

        if response.thinking and hooks.on_thinking:
            try:
                hooks.on_thinking(response.thinking)
            except Exception:
                pass

        # Hint acknowledgment: when a delegate responds after receiving
        # an orchestrator hint, extract the response and push it back
        # so the orchestrator can see it on next inspect.
        if state.received_orchestrator_hint and hooks.on_hint_ack:
            _ack_text = (response.text or "")[:150].strip()
            if _ack_text:
                try:
                    hooks.on_hint_ack(_ack_text)
                except Exception:
                    pass
            state.received_orchestrator_hint = False

        # V5: update reasoning trajectory for continuation
        if response.thinking:
            _last_coherence = assess_coherence(response.thinking, response.text)
            _reasoning_trajectory = extract_trajectory(
                response.thinking, max_chars=600,
            )
            if _last_coherence < 0.3:
                _reasoning_trajectory = ""
        else:
            _last_coherence = 1.0
            _reasoning_trajectory = ""

        # F. Route: tool calls or text-only
        _iter_tool_calls: list[dict] = []
        _iter_tool_results: list[dict] = []

        if response.tool_calls:
            state.consecutive_text_only = 0
            _iter_tool_names = [
                tc.get("function", {}).get("name", "")
                for tc in response.tool_calls
            ]
            if "ask_user" in _iter_tool_names:
                state.prose_gate_active = False

            _truncated_file_nudge: str | None = None
            _file_recovery_injected = False

            # Auto-focus VC on browser during browser tool calls.
            # Note: the push/pop window is brief (< 2s) so the VC background
            # loop (1–60s interval) may not fire during it.  The actual
            # per-tool visual feedback is handled below by _take_snapshot()
            # rather than relying on the VC buffer.  The push/pop is kept for
            # the rare case where look_now() is called explicitly.
            _pushed_vc_browser_focus = False
            if visual_cortex is not None:
                _has_browser_call = any(
                    tc.get("function", {}).get("name", "") == "browser"
                    or tc.get("function", {}).get("name", "").startswith("browser_")
                    for tc in response.tool_calls
                )
                if _has_browser_call:
                    try:
                        from nls.tools.visual_cortex import FocusTarget as _FocusTarget
                        visual_cortex.push_focus(_FocusTarget.browser())
                        _pushed_vc_browser_focus = True
                    except Exception:
                        pass

            results, digest_count = await execute_tools(
                response.tool_calls, tools, config, state,
                abort_signal=abort_signal,
                on_event=on_event,
                hooks=hooks,
                vllm_client=vllm_client,
                user_task=user_input,
                digest_count=digest_count,
                delegate_manager=delegate_manager,
                response_has_text=bool(response.text and len(response.text.strip()) > 50),
            )

            if state.prose_gate_active and any(
                not getattr(r, "is_error", False) for r in results
            ):
                state.prose_gate_active = False

            if _pushed_vc_browser_focus:
                try:
                    visual_cortex.pop_focus()
                except Exception:
                    pass

            for tc, result in zip(response.tool_calls, results):
                context.append(make_tool_message(tc, result))
                _tool_name = tc.get("function", {}).get("name", "unknown")
                _args_raw = tc.get("function", {}).get("arguments", "")
                _args_fp = _args_raw[:200] if isinstance(_args_raw, str) else str(_args_raw)[:200]
                state.total_tool_calls += 1
                state.record_tool(_tool_name, result, args_fingerprint=_args_fp)
                _gr = getattr(hooks, "guardrails_registry", None)
                if _gr is not None and getattr(result, "is_error", False):
                    from nls.tools.agent_tools.guardrails_registry import (
                        record_tool_contract_guardrail,
                    )
                    _cryptex_gr = getattr(
                        hooks, "_accumulator_wm_target", None,
                    )
                    record_tool_contract_guardrail(
                        _gr,
                        tool_name=_tool_name,
                        content=result.content or "",
                        delegate_number=0,
                        cryptex=_cryptex_gr,
                    )
                _register_appended_tool_outcome(
                    state, context, _tool_name, result, _args_raw,
                )
                _status_tag = "OK" if not result.is_error else "FAIL"
                _action_hint = ""
                try:
                    _pa = json.loads(_args_raw) if isinstance(_args_raw, str) else _args_raw
                    if isinstance(_pa, dict):
                        if _tool_name == "bash":
                            _action_hint = f" `{str(_pa.get('command', ''))[:80]}`"
                        elif _tool_name in ("write", "edit", "read"):
                            _action_hint = f" {str(_pa.get('path', ''))[:60]}"
                        elif _tool_name in ("team", "plan", "todo"):
                            _action_hint = f"({_pa.get('action', '')})"
                        elif _tool_name == "delegate":
                            _action_hint = f" {str(_pa.get('task', ''))[:50]}"
                except Exception:
                    pass
                state.cumulative_actions.append(
                    f"{_tool_name}{_action_hint}: {_status_tag}"
                )

                if (
                    _tool_name == "write"
                    and not getattr(result, "is_error", False)
                ):
                    _ok_path = extract_file_tool_target(_tool_name, _args_raw)
                    if _ok_path:
                        clear_truncated_write_attempt(
                            state.truncated_write_attempts, _ok_path,
                        )

                # Update loop state ref for Cryptex ring priority
                if _lstate_ref is not None:
                    _tool_action = ""
                    try:
                        _parsed_args = json.loads(_args_raw) if isinstance(_args_raw, str) else _args_raw
                        if isinstance(_parsed_args, dict):
                            _tool_action = _parsed_args.get("action", "")
                    except Exception:
                        pass
                    _lstate_ref["last_tool"] = _tool_name
                    _lstate_ref["last_tool_action"] = _tool_action
                    _lstate_ref["iteration"] = state.iteration
                    _lstate_ref["coordinator_mode"] = state.coordinator_mode
                    _lstate_ref["active_mode"] = state.active_mode.value
                    _lstate_ref["delegate_count"] = state.delegate_count
                    _lstate_ref["orchestration_profile"] = (
                        state.orchestration_profile or "solo_structured"
                    )
                    if hooks and hooks.has_active_plan:
                        try:
                            _lstate_ref["has_active_plan"] = bool(
                                hooks.has_active_plan(),
                            )
                        except Exception:
                            pass
                    _recent = _lstate_ref.get("recent_tools", [])
                    _recent.append(_tool_name)
                    if len(_recent) > 10:
                        _recent[:] = _recent[-10:]

                if _tool_name in ("write", "edit") and not result.is_error:
                    try:
                        _parsed = json.loads(_args_raw) if isinstance(_args_raw, str) else _args_raw
                        _fp = _parsed.get("path", "")
                        if _fp and _fp not in state.files_written:
                            state.files_written.append(_fp)
                    except Exception:
                        pass
                _iter_args: dict[str, Any] = {}
                try:
                    _parsed_iter = (
                        json.loads(_args_raw)
                        if isinstance(_args_raw, str)
                        else _args_raw
                    )
                    if isinstance(_parsed_iter, dict):
                        _iter_args = _parsed_iter
                except Exception:
                    pass
                _iter_tool_calls.append({
                    "name": _tool_name,
                    "call_id": tc.get("id", ""),
                    "arguments": _iter_args,
                })
                _iter_tool_results.append({
                    "success": not result.is_error,
                })
                from nls.security.secret_redact import redact_secrets

                _preview = redact_secrets((result.content or "")[:300])[0]
                _slog(_session_log_path, {
                    "event": "tool_result",
                    "loop_id": state.loop_id,
                    "iteration": state.iteration,
                    "tool": _tool_name,
                    "success": not result.is_error,
                    "content_len": len(result.content or ""),
                    "content_preview": _preview,
                })

                # --- Context-aware breadcrumb hints ---
                _bc_ctx = _build_bc_ctx(
                    _tool_name, result, state, _deferred_actions, anchor,
                )
                if not result.is_error or (
                    _tool_name == "team"
                    and _bc_ctx.result_details.get("wave_needs_advance")
                ) or bool(result.details.get("rewrite_blocked")):
                    _bc_hint = _breadcrumb_engine.evaluate(_bc_ctx)
                else:
                    _bc_hint = None
                if _bc_hint:
                    context.append({"role": "system", "content": _bc_hint})
                    _slog(_session_log_path, {
                        "event": "breadcrumb",
                        "loop_id": state.loop_id,
                        "iteration": state.iteration,
                        "trigger_tool": _tool_name,
                        "hint_preview": _bc_hint[:200],
                    })

                if _tool_name == "read" and not result.is_error:
                    _read_path = str(_iter_args.get("path", "") or "")
                    if _read_path.lower().endswith("skill.md"):
                        try:
                            from nls.skills_setup_policy import (
                                instruction_skill_post_read_nudge,
                            )

                            _skill_nudge = instruction_skill_post_read_nudge(
                                _read_path,
                            )
                            if _skill_nudge:
                                context.append({
                                    "role": "system",
                                    "content": _skill_nudge,
                                })
                        except Exception:
                            pass

                from nls.agentic.profile_depth_policy import (
                    evaluate_after_tool,
                    evaluate_wm_profile_mismatch,
                    journal_depth_event,
                )

                _depth_nudge = evaluate_after_tool(
                    state,
                    _tool_name,
                    _iter_args,
                    result,
                    mode=state.active_mode,
                    enable_delegation=config.enable_delegation,
                )
                if _depth_nudge:
                    context.append({
                        "role": "system",
                        "content": _depth_nudge.message,
                    })
                    _slog(_session_log_path, journal_depth_event(
                        "depth_nudge",
                        loop_id=state.loop_id,
                        trigger_id=_depth_nudge.trigger_id,
                        profile_from=state.orchestration_profile or "",
                        profile_to=_depth_nudge.suggested_profile,
                    ))

                if _tool_name == "adopt_orchestration_profile" and not result.is_error:
                    _adopt_details = getattr(result, "details", None) or {}
                    if _adopt_details.get("adopted_profile"):
                        invalidate_tool_policy_cache(state)
                        state._mode_schemas_applied = False
                        if state.pending_profile_anchor:
                            context.append({
                                "role": "system",
                                "content": state.pending_profile_anchor,
                            })
                            state.pending_profile_anchor = ""
                        if not _is_delegate_loop:
                            _base_schemas, state.unlocked_tools, _ = refresh_tool_schemas(
                                state,
                                _all_schemas,
                                _all_unlocked,
                                state.active_mode,
                                delegate_manager,
                                hooks,
                                force=True,
                            )
                        if _lstate_ref is not None:
                            _lstate_ref["orchestration_profile"] = (
                                _adopt_details["adopted_profile"]
                            )
                        _slog(_session_log_path, journal_depth_event(
                            "profile_adopted",
                            loop_id=state.loop_id,
                            profile_from=_adopt_details.get("previous_profile", ""),
                            profile_to=_adopt_details["adopted_profile"],
                        ))

                # --- Delegation hallucination guard ---
                if (
                    _tool_name == "await_delegates"
                    and result.is_error
                    and "no delegates" in (result.content or "").lower()
                ):
                    context.append({
                        "role": "system",
                        "content": delegation_hallucination_nudge(),
                    })
                elif _tool_name == "switch_mode" and not result.is_error:
                    try:
                        _sm_args = (
                            json.loads(_args_raw)
                            if isinstance(_args_raw, str)
                            else _args_raw or {}
                        )
                        _target_mode = str(
                            _sm_args.get("mode", "") or "",
                        ).lower()
                    except Exception:
                        _target_mode = ""
                    if _target_mode in ("monitoring", "delegating"):
                        _delegates_live = state.delegate_count > 0
                        if not _delegates_live and delegate_manager is not None:
                            try:
                                _delegates_live = (
                                    delegate_manager.has_active_delegates()
                                )
                            except Exception:
                                pass
                        if (
                            not _delegates_live
                            and state.coordinator_mode
                            and config.enable_delegation
                        ):
                            context.append({
                                "role": "system",
                                "content": delegation_hallucination_nudge(),
                            })

                # --- bash(sleep) → wait() steering ---
                # The wait() tool provides delegate status, triggers
                # self-state beat, and is exempt from stall detection.
                if (
                    _tool_name == "bash"
                    and state.coordinator_mode
                ):
                    try:
                        _bcmd = (json.loads(_args_raw) if isinstance(_args_raw, str)
                                 else _args_raw or {}).get("command", "")
                        if re.match(
                            r"^\s*(?:sleep|Start-Sleep)\b", _bcmd, re.IGNORECASE,
                        ):
                            context.append({"role": "system", "content": (
                                "USE wait() INSTEAD OF bash('sleep'). "
                                "The wait(seconds=N, reason='...') tool is "
                                "purpose-built for monitoring: it reports "
                                "delegate status after the wait and keeps "
                                "your self-state updated. Replace all "
                                "bash('sleep N') calls with wait(seconds=N)."
                            )})
                    except Exception:
                        pass

                if _tool_name == "team" and not result.is_error:
                    if state.has_pending_escalation:
                        state.has_pending_escalation = False
                        state.pending_escalation_team_id = ""
                        state.pending_escalation_member_idx = -1
                        state.pending_escalation_writes = 0
                        state.pending_escalation_paths = []
                        logger.info(
                            "[LOOP:%s] iter %d: escalation handled — "
                            "cleared has_pending_escalation (team action)",
                            state.loop_id, state.iteration,
                        )

                if _tool_name == "plan" and not result.is_error:
                    _plan_details = getattr(result, "details", None) or {}
                    if _plan_details.get("action") == "complete":
                        state.plan_completed_at_iter = state.iteration
                        logger.info(
                            "[LOOP:%s] iter %d: PLAN COMPLETED — "
                            "wrap-up budget starts",
                            state.loop_id, state.iteration,
                        )
                    if hooks and hooks.has_active_plan:
                        try:
                            if hooks.has_active_plan():
                                _wm_nudge = evaluate_wm_profile_mismatch(
                                    state,
                                    wm_has_strategic_goals=False,
                                    wm_has_plan_position=True,
                                )
                                if _wm_nudge:
                                    context.append({
                                        "role": "system",
                                        "content": _wm_nudge.message,
                                    })
                        except Exception:
                            pass

                if _tool_name == "get_tool_schema" and not result.is_error:
                    _args = tc.get("function", {}).get("arguments", "{}")
                    if isinstance(_args, str):
                        try:
                            _args = json.loads(_args)
                        except Exception:
                            _args = {}
                    _requested = _args.get("tool_name", "")
                    if _requested and _requested in tools:
                        state.unlocked_tools.add(_requested)
                        _all_unlocked.add(_requested)
                        logger.info("Unlocked tool schema: %s", _requested)

                if _tool_name == "discover_tools" and not result.is_error:
                    _newly_unlocked: list[str] = []
                    for _disc_name in tools:
                        if _disc_name in (result.content or ""):
                            if _disc_name not in state.unlocked_tools:
                                _newly_unlocked.append(_disc_name)
                            state.unlocked_tools.add(_disc_name)
                            _all_unlocked.add(_disc_name)
                    if _newly_unlocked:
                        logger.info(
                            "discover_tools unlocked: %s", _newly_unlocked,
                        )

            if _budget.truncated_file_events:
                _attempts = record_truncated_file_events(
                    state.truncated_write_attempts,
                    _budget.truncated_file_events,
                )
                _truncated_file_nudge = build_file_tool_recovery_nudge(
                    _budget.truncated_file_events,
                    config.max_new_tokens,
                    state.truncated_write_attempts,
                )
                _file_recovery_injected = True
                logger.info(
                    "[LOOP:%s] iter %d: FILE_RECOVERY nudge (events=%d, "
                    "attempts=%s, completion_tokens=%d max=%d)",
                    state.loop_id,
                    state.iteration,
                    len(_budget.truncated_file_events),
                    _attempts,
                    response.completion_tokens,
                    config.max_new_tokens,
                )

            if _truncated_file_nudge:
                context.append({
                    "role": "system",
                    "content": _truncated_file_nudge,
                })

            # Supersede immediately after tool batch — next generate() sees thin context.
            _apply_context_supersession_pass(
                context,
                config=config,
                state=state,
                anchor=anchor,
                is_delegate_loop=_is_delegate_loop,
                dispatch_source=state.dispatch_source or dispatch_source,
                team_manager=_cached_team_manager,
                tools=tools,
                start_index=_loop_start_idx,
                cwd=_supersession_cwd,
                plan_tool=_plan_tool,
            )

            if state.consecutive_errors >= 2:
                from nls.agentic.evaluator import Directive, get_directive_message
                _recovery = get_directive_message(Directive.ERROR_RECOVERY)
                if _recovery and not should_suppress_error_recovery(
                    _file_recovery_injected,
                ):
                    context.append({"role": "system", "content": _recovery})
                    from nls.agentic.skill_discovery_boost import (
                        trigger_skill_discovery_boost,
                    )
                    trigger_skill_discovery_boost(
                        hooks,
                        iteration=state.iteration,
                        reason="error_recovery",
                        orchestration_profile=state.orchestration_profile,
                    )
                    logger.info(
                        "[LOOP:%s] iter %d: ERROR_RECOVERY directive "
                        "(consecutive_errors=%d)",
                        state.loop_id, state.iteration,
                        state.consecutive_errors,
                    )
                elif _recovery and _file_recovery_injected:
                    logger.info(
                        "[LOOP:%s] iter %d: ERROR_RECOVERY suppressed "
                        "(file recovery nudge already injected, "
                        "consecutive_errors=%d)",
                        state.loop_id, state.iteration,
                        state.consecutive_errors,
                    )

            # --- Tool-driven mode transitions (Tier 1 & 2) — before schema filter ---
            _prev_mode = state.active_mode

            if state.active_mode == AgentMode.RESPONDING:
                _responding_comm = frozenset({
                    "communicate", "ask_user", "contacts",
                    "whatsapp_send", "telegram_send", "email_send",
                    "gmail_send", "gmail_reply",
                })
                _non_comm_calls = [
                    tc.get("function", {}).get("name", "")
                    for tc in (response.tool_calls or [])
                    if tc.get("function", {}).get("name", "") not in _responding_comm
                ]
                _delivered_response = bool(response.text) and not _non_comm_calls
                if _delivered_response:
                    _restore = state._pre_responding_mode or AgentMode.MONITORING
                    state.active_mode = _restore
                    state._pre_responding_mode = None
                    invalidate_tool_policy_cache(state)
                    state.mode_override_count = 0
                    logger.info(
                        "[LOOP:%s] RESPONDING → %s (response delivered)",
                        state.loop_id, _restore.value,
                    )
            else:
                _tool_mt = compute_tool_mode_transition(
                    state,
                    response.tool_calls or [],
                    results,
                    enable_delegation=config.enable_delegation,
                    plan_tool=_plan_tool,
                )
                if _tool_mt is not None:
                    apply_tool_mode_transition(state, _tool_mt)
                    if _tool_mt.hint:
                        context.append({
                            "role": "system",
                            "content": _tool_mt.hint,
                        })

            if state.active_mode != _prev_mode:
                _rmode = getattr(hooks, "_render_mode_ref", None)
                if _rmode:
                    _rmode[0] = state.active_mode.value
                logger.info(
                    "[LOOP:%s] AUTO-TRANSITION: %s → %s (iter %d)",
                    state.loop_id, _prev_mode.value,
                    state.active_mode.value, state.iteration,
                )
                _mt_acc = getattr(hooks, "_accumulator", None)
                if _mt_acc is not None:
                    _mt_acc.ingest("MODE_TRANSITION", {
                        "from_mode": _prev_mode.value,
                        "to_mode": state.active_mode.value,
                        "reason": "tool_mode_policy",
                    })

            # --- Mode schema restriction after mid-loop mode switch ---
            if not state._mode_schemas_applied:
                state._mode_schemas_applied = True
                _mode_allowed = get_allowed_tools(state.active_mode)
                if state.active_mode != AgentMode.EXECUTING and _mode_allowed:
                    _base_schemas = [
                        s for s in _all_schemas
                        if s.get("function", {}).get("name", "") in _mode_allowed
                    ]
                    state.unlocked_tools = {
                        t for t in _all_unlocked if t in _mode_allowed
                    }
                else:
                    # EXECUTING mode: expand to the FULL registered tool set.
                    # The initial snapshot (_all_schemas / _all_unlocked) was
                    # taken with the active_tool_names filter (predict_tools),
                    # so it may be smaller than the complete tool dict.  When
                    # the agent explicitly switches to EXECUTING we must give
                    # it every tool that was registered for this agent, not
                    # just the pre-filtered subset.
                    _exec_schemas: list[dict] = virtual_tool_schemas_for_loop(
                        enable_delegation=config.enable_delegation,
                        enable_detached_delegates=config.enable_detached_delegates,
                        delegate_manager=delegate_manager,
                    )
                    _exec_unlocked: set[str] = set(
                        virtual_tool_names_for_loop(
                            enable_delegation=config.enable_delegation,
                            enable_detached_delegates=config.enable_detached_delegates,
                            delegate_manager=delegate_manager,
                        )
                    )
                    for _s in _all_schemas:
                        _sn = (_s.get("function") or {}).get("name", "")
                        if _sn:
                            _exec_unlocked.add(_sn)
                    for _en, _eo in tools.items():
                        if isinstance(_eo, AgentTool):
                            try:
                                _exec_schemas.append(tool_to_openai_schema(_eo))
                                _exec_unlocked.add(_en)
                            except Exception:
                                pass
                    _base_schemas = _exec_schemas
                    state.unlocked_tools = _exec_unlocked
                    # Update snapshot so subsequent mode switches (e.g.
                    # EXECUTING → MONITORING) can filter from the full set.
                    _all_schemas = list(_exec_schemas)
                    _all_unlocked = set(_exec_unlocked)
                if config.enable_delegation and not _is_delegate_loop:
                    _base_schemas, state.unlocked_tools, _ = refresh_tool_schemas(
                        state,
                        _all_schemas,
                        _all_unlocked,
                        state.active_mode,
                        delegate_manager,
                        hooks,
                        force=True,
                    )
                _rmode = getattr(hooks, "_render_mode_ref", None)
                if _rmode:
                    _rmode[0] = state.active_mode.value
                logger.info(
                    "[LOOP:%s] MODE %s schemas applied mid-loop — %d tools",
                    state.loop_id, state.active_mode.value, len(_base_schemas),
                )

            # --- Communicate deduplication ---
            # When the model's last tool call was communicate(), it already
            # delivered the message.  A text-only follow-up often repeats
            # the same content.  Inject a steering hint to avoid that.
            if response.tool_calls:
                _last_tc_name = response.tool_calls[-1].get(
                    "function", {}).get("name", "")
                if _last_tc_name == "communicate" and response.text:
                    _comm_len = len(response.text.strip())
                    if _comm_len > 60:
                        context.append({"role": "system", "content": (
                            "Your communicate() call already delivered that "
                            "message. Do NOT repeat or rephrase the same "
                            "content as a text response. Either continue with "
                            "the next action, or end your turn."
                        )})

            # --- Granular coordinator tool control ---
            # While delegates run: block overlapping implementation.
            # Before any team launch: block orchestrator self-build (plan+team first).
            if state.coordinator_mode:
                _has_running = False
                _running: list[Any] = []
                if delegate_manager is not None:
                    try:
                        _running = [
                            ds for ds in delegate_manager.list_all()
                            if ds.state == "running"
                        ]
                        _has_running = bool(_running)
                    except Exception:
                        pass

                _pre_delegate = state.must_delegate_before_impl
                if _pre_delegate and not _has_running:
                    _impl_blocked = False
                    for tc in (response.tool_calls or []):
                        _tn = tc.get("function", {}).get("name", "")
                        _ta = _parse_tool_args_safe(
                            tc.get("function", {}).get("arguments", "{}"))
                        if pre_delegate_block_message(
                            _tn, _ta,
                            active_mode=state.active_mode,
                            block_reason=state.pre_delegate_reason or None,
                            orchestrator_recovery=state.orchestrator_recovery,
                            orchestration_profile=state.orchestration_profile,
                        ):
                            _impl_blocked = True
                            break
                    if _impl_blocked:
                        context.append({
                            "role": "system",
                            "content": coordinator_nudge_pre_delegate(
                                state.pre_delegate_reason or None,
                            ),
                        })
                        logger.warning(
                            "[LOOP:%s] iter %d: PRE-DELEGATE block — "
                            "orchestrator tried to implement without plan/team",
                            state.loop_id, state.iteration,
                        )

                if _has_running or _pre_delegate:
                    _HEAVY_WRITE_THRESHOLD = 500
                    _HEAVY_WRITE_LIMIT = 3
                    _COORD_TOOLS = {"team", "plan", "todo", "wait",
                                    "delegate_status", "communicate"}
                    _BASH_CREATE_RE = re.compile(
                        r"\b(mkdir|New-Item|touch|cp\b|copy\b|mv\b|move\b"
                        r"|Out-File|Set-Content|Add-Content|>>|> "
                        r"|git\s+init|git\s+push|git\s+commit"
                        r"|git\s+add\b|git\s+config|git\s+remote\s+add"
                        r"|gh\s+repo\s+create|npm\s+init|pip\s+install)",
                        re.IGNORECASE,
                    )
                    _had_heavy = False
                    _had_coord = False
                    _had_bash_create = False
                    _write_paths: list[str] = []
                    for tc in response.tool_calls:
                        _tn = tc.get("function", {}).get("name", "")
                        if _tn in _COORD_TOOLS:
                            _had_coord = True
                        elif _tn in ("write", "edit"):
                            _raw_args = tc.get("function", {}).get("arguments", "")
                            if len(_raw_args) > _HEAVY_WRITE_THRESHOLD:
                                _had_heavy = True
                            try:
                                import json as _j
                                _wp = _j.loads(_raw_args).get("path", "")
                                if _wp:
                                    _write_paths.append(_wp)
                            except Exception:
                                pass
                        elif _tn == "bash":
                            _raw_args = tc.get("function", {}).get("arguments", "")
                            try:
                                import json as _j
                                _cmd = _j.loads(_raw_args).get("command", "") if isinstance(_raw_args, str) else (_raw_args or {}).get("command", "")
                            except Exception:
                                _cmd = str(_raw_args)
                            if _BASH_CREATE_RE.search(_cmd):
                                _had_bash_create = True
                                _had_heavy = True

                    if _had_coord:
                        state.consecutive_heavy_writes = 0
                    elif _had_heavy:
                        state.consecutive_heavy_writes += 1
                    if state.consecutive_heavy_writes >= _HEAVY_WRITE_LIMIT:
                        context.append({
                            "role": "system",
                            "content": (
                                f"You have made {state.consecutive_heavy_writes} "
                                f"consecutive implementation actions while sub-agents "
                                f"are still running. Wait for them to finish first.\n"
                                f"Use: team(action='inspect') or wait(seconds=60)."
                            ),
                        })
                        logger.warning(
                            "[LOOP:%s] iter %d: COORDINATOR NUDGE — "
                            "%d consecutive heavy writes while delegates active",
                            state.loop_id, state.iteration,
                            state.consecutive_heavy_writes,
                        )
                        state.consecutive_heavy_writes = 0

                    if _had_bash_create:
                        context.append({
                            "role": "system",
                            "content": (
                                "STOP — You are creating files/directories via "
                                "bash while your sub-agents are still running. "
                                "This is the DELEGATE's job, not yours.\n"
                                "Wait for your team to finish, THEN review and "
                                "fix anything that's wrong. Right now:\n"
                                "  - team(action='inspect') to check progress\n"
                                "  - wait(seconds=60) to let them work\n"
                                "  - team(action='intervene', team_id=..., member=N, decision='...', message='...') if they need help"
                            ),
                        })
                        logger.warning(
                            "[LOOP:%s] iter %d: COORDINATOR bash-create while "
                            "delegates running",
                            state.loop_id, state.iteration,
                        )

                    if _write_paths:
                        try:
                            _overlap_warnings: list[str] = []
                            for _wp in _write_paths:
                                _wp_lower = _wp.replace("\\", "/").lower()
                                for _rd in _running:
                                    _task_lower = (_rd.task or "").lower()
                                    for _seg in _wp_lower.split("/"):
                                        if (
                                            len(_seg) > 3
                                            and _seg in _task_lower
                                            and _seg not in (
                                                "src", "app", "index", "main",
                                                "config", "test", "docs",
                                            )
                                        ):
                                            _overlap_warnings.append(
                                                f"'{_wp}' overlaps with running "
                                                f"delegate #{_rd.delegate_number} "
                                                f"(iter {_rd.iteration}/"
                                                f"{_rd.max_iterations})"
                                            )
                                            break
                            if _overlap_warnings:
                                context.append({
                                    "role": "system",
                                    "content": (
                                        "WARNING: You are writing files in areas "
                                        "where sub-agents are still working:\n"
                                        + "\n".join(f"  - {w}" for w in _overlap_warnings)
                                        + "\nThis risks file conflicts. Wait for "
                                        "the delegate to finish first, or use "
                                        "team(action='inspect') to check progress."
                                    ),
                                })
                                logger.warning(
                                    "[LOOP:%s] iter %d: COORDINATOR OVERLAP — "
                                    "writing to active delegate areas: %s",
                                    state.loop_id, state.iteration,
                                    _overlap_warnings,
                                )
                        except Exception:
                            pass
                else:
                    # No delegates running — orchestrator is free to
                    # review, polish, and fix.  Reset the counter.
                    state.consecutive_heavy_writes = 0

            # --- Orchestrator discipline ---
            _REPAIR_BUDGET_LIMIT = 5
            if state.coordinator_mode and delegate_manager is not None:
                try:
                    _any_delegate_running = any(
                        ds.state == "running"
                        for ds in delegate_manager.list_all()
                    )

                    # Git mutation guard — ALWAYS active in coordinator mode
                    _GIT_MUTATION_RE = re.compile(
                        r"\b(git\s+push|git\s+init|git\s+commit"
                        r"|git\s+add\s|git\s+config|git\s+remote"
                        r"|gh\s+repo\s+create)",
                        re.IGNORECASE,
                    )
                    _had_git_mutation = False
                    if response.tool_calls:
                        for tc in response.tool_calls:
                            _tn = tc.get("function", {}).get("name", "")
                            if _tn == "bash":
                                _raw = tc.get("function", {}).get("arguments", "")
                                try:
                                    import json as _j
                                    _cmd = _j.loads(_raw).get("command", "") if isinstance(_raw, str) else (_raw or {}).get("command", "")
                                except Exception:
                                    _cmd = str(_raw)
                                if _GIT_MUTATION_RE.search(_cmd):
                                    _had_git_mutation = True
                                    break
                    if _had_git_mutation:
                        context.append({
                            "role": "system",
                            "content": (
                                "BLOCKED: Git/repo operations (push, init, "
                                "commit, remote, gh repo create) are NOT the "
                                "orchestrator's job. Delegate this to a "
                                "sub-agent via team(action='hint') or "
                                "team(action='rewake').\n"
                                "NEVER run git push, git init, or gh repo "
                                "create yourself."
                            ),
                        })

                    if _any_delegate_running and response.tool_calls:
                        # STRICT MODE: delegates are actively working.
                        # Only coordination + read-only monitoring allowed.
                        _MONITOR_OK = {"team", "plan", "todo", "wait",
                                       "delegate_status", "communicate",
                                       "switch_mode", "ask_user",
                                       "list_dir", "read", "grep", "glob"}
                        _impl_tools = [
                            tc.get("function", {}).get("name", "")
                            for tc in response.tool_calls
                            if tc.get("function", {}).get("name", "") not in _MONITOR_OK
                        ]
                        if _impl_tools:
                            state.post_wave_direct_iterations += 1
                            context.append({
                                "role": "system",
                                "content": (
                                    f"ORCHESTRATOR DISCIPLINE: Your delegate is "
                                    f"STILL RUNNING. You used [{', '.join(_impl_tools)}] "
                                    f"which is implementation work. While delegates "
                                    f"are running you may ONLY: team(inspect/hint/"
                                    f"intervene), wait, read, list_dir.\n"
                                    f"STOP doing the delegate's job. Use "
                                    f"team(action='inspect') to check progress, "
                                    f"or team(action='hint') to guide them."
                                ),
                            })
                            logger.warning(
                                "[LOOP:%s] iter %d: orchestrator used %s "
                                "while delegate running",
                                state.loop_id, state.iteration, _impl_tools,
                            )

                    elif not _any_delegate_running and response.tool_calls:
                        # POST-WAVE EVALUATION: repair budget applies
                        _EVAL_COORD = {"team", "plan", "todo", "wait",
                                       "delegate_status", "communicate",
                                       "switch_mode"}
                        _had_coord_tool = any(
                            tc.get("function", {}).get("name", "") in _EVAL_COORD
                            for tc in response.tool_calls
                        )
                        if _had_coord_tool:
                            state.post_wave_direct_iterations = 0
                        else:
                            state.post_wave_direct_iterations += 1

                    if state.post_wave_direct_iterations >= _REPAIR_BUDGET_LIMIT:
                        _queued_hint = ""
                        try:
                            _queued_teams = [
                                t for t in team_manager.list_teams(include_terminal=False)
                                if t.status == "created"
                            ]
                            if _queued_teams:
                                _qt = _queued_teams[0]
                                _queued_hint = (
                                    f"\n\n⚠ QUEUED TEAM FOUND: {_qt.name} "
                                    f"[{_qt.id}] is waiting to be launched.\n"
                                    f"Run: team(action='launch', "
                                    f"team_id='{_qt.id}')"
                                )
                        except Exception:
                            pass
                        context.append({
                            "role": "system",
                            "content": (
                                f"REPAIR BUDGET EXCEEDED: You have spent "
                                f"{state.post_wave_direct_iterations} iterations "
                                f"doing direct work. "
                                f"You are the MANAGER, not the individual "
                                f"contributor. STOP coding and do ONE of:\n"
                                f"  (a) advance the team to launch the next "
                                f"wave of delegates\n"
                                f"  (b) rewake a failed delegate with "
                                f"corrective instructions\n"
                                f"  (c) plan(add_step) or plan(sub_plan) on the "
                                f"existing plan — never plan(create) for remainder\n"
                                f"  (d) report results to the user\n"
                                f"Do NOT keep writing code manually."
                                + _queued_hint
                            ),
                        })
                        logger.warning(
                            "[LOOP:%s] iter %d: REPAIR BUDGET EXCEEDED — "
                            "%d direct iterations",
                            state.loop_id, state.iteration,
                            state.post_wave_direct_iterations,
                        )
                except Exception:
                    pass

            # --- Post-tool steering drain ---
            # Drain steering immediately after tool execution so both:
            # (a) orchestrator escalations from sub-agents, and
            # (b) orchestrator hints to this delegate
            # are seen within 1 iteration instead of waiting for the
            # next full loop cycle.
            if hooks.get_steering_messages:
                try:
                    _post_steering = await hooks.get_steering_messages()
                    if _post_steering:
                        _esc_msgs = [
                            m for m in _post_steering
                            if any(
                                kw in (m.get("content") or "")
                                for kw in (
                                    "[TEAM MEMBER HELP REQUEST",
                                    "[TEAM COMPLETED",
                                    "ESCALATION",
                                    "escalat",
                                    "timed out",
                                )
                            )
                        ] if state.coordinator_mode else []
                        _hint_msgs = [
                            m for m in _post_steering
                            if any(
                                kw in (m.get("content") or "")
                                for kw in (
                                    "[ORCHESTRATOR HINT]",
                                    "[ORCHESTRATOR REVIEW",
                                    "ORCHESTRATOR DIRECTIVE",
                                )
                            )
                        ]
                        for msg in _post_steering:
                            context.append(msg)
                        if _esc_msgs:
                            state.has_pending_escalation = True
                            from .orchestration_policy import (
                                parse_escalation_steering,
                            )
                            _meta = parse_escalation_steering(
                                (_esc_msgs[0].get("content") or ""),
                            )
                            if _meta.get("team_id"):
                                state.pending_escalation_team_id = str(
                                    _meta["team_id"],
                                )
                            if _meta.get("member_idx") is not None:
                                state.pending_escalation_member_idx = int(
                                    _meta["member_idx"],
                                )
                            if _meta.get("writes") is not None:
                                state.pending_escalation_writes = int(
                                    _meta["writes"],
                                )
                            if _meta.get("paths"):
                                state.pending_escalation_paths = list(
                                    _meta["paths"],
                                )
                            _member_hint = (
                                f"member={state.pending_escalation_member_idx}"
                                if state.pending_escalation_member_idx >= 0
                                else "member=<index from message>"
                            )
                            _team_hint = (
                                state.pending_escalation_team_id
                                or "team_id from message"
                            )
                            _paths_hint = ""
                            if state.pending_escalation_paths:
                                _paths_hint = (
                                    f"\nRequested paths: "
                                    f"{', '.join(state.pending_escalation_paths[:6])}"
                                    f"\nUse team(action='grant_paths', team_id='{_team_hint}', "
                                    f"{_member_hint}, paths=[...]) to approve file access."
                                )
                            context.append({
                                "role": "system",
                                "content": (
                                    "PRIORITY: You have pending team "
                                    "escalation(s). Handle them NOW with "
                                    f"team(action='intervene', team_id='{_team_hint}', "
                                    f"{_member_hint}, decision='extend' or 'hint', "
                                    "message='specific next actions')"
                                    + (
                                        " OR team(action='grant_paths', paths=[...]) "
                                        "for file_access requests."
                                        if state.pending_escalation_paths
                                        else ""
                                    )
                                    + ".\n"
                                    "Do NOT terminate while writes>0 or the "
                                    "member listed a bounded finish list.\n"
                                    "Do NOT continue other work until this is resolved."
                                    + _paths_hint
                                ),
                            })
                            logger.warning(
                                "[LOOP:%s] iter %d: ESCALATION PRIORITY "
                                "— %d escalation msgs detected post-tool",
                                state.loop_id, state.iteration,
                                len(_esc_msgs),
                            )
                        if _hint_msgs:
                            state.received_orchestrator_hint = True
                            from nls.agentic.skill_discovery_boost import (
                                trigger_skill_discovery_boost,
                            )
                            trigger_skill_discovery_boost(
                                hooks,
                                iteration=state.iteration,
                                reason="orchestrator_hint_post_tool",
                                orchestration_profile=state.orchestration_profile,
                            )
                            logger.info(
                                "[LOOP:%s] iter %d: ORCHESTRATOR HINT "
                                "received post-tool — delegate will "
                                "reassess next iteration",
                                state.loop_id, state.iteration,
                            )
                        if not _esc_msgs and not _hint_msgs and _post_steering:
                            logger.info(
                                "[LOOP:%s] iter %d: post-tool steering "
                                "injected %d msgs",
                                state.loop_id, state.iteration,
                                len(_post_steering),
                            )
                except Exception:
                    logger.debug(
                        "[LOOP:%s] iter %d: post-tool steering drain failed",
                        state.loop_id, state.iteration, exc_info=True,
                    )

            # Browser post-tool feedback: after navigate/click inject a live screenshot
            # AND the DOM snapshot so the model sees both the visual state and the
            # interactive element refs.
            #
            # The screenshot is sent directly as image_url in the message content —
            # the same vLLM model that runs the agentic loop also handles vision, so
            # there is no need for a separate VLM→text translation step.  This
            # eliminates the 16-22 s /vision/describe roundtrip entirely.
            #
            # The DOM snapshot (element refs + values) is appended as plain text so
            # the model has structured ref numbers to use in the next tool call.
            _browser_tcs = [
                tc for tc in response.tool_calls
                if tc.get("function", {}).get("name", "") == "browser"
                or tc.get("function", {}).get("name", "").startswith("browser_")
            ]
            if _browser_tcs:
                import asyncio as _asyncio
                _browser_actions = []
                for _btc in _browser_tcs:
                    try:
                        _bargs = json.loads(
                            _btc.get("function", {}).get("arguments", "{}") or "{}"
                        )
                    except Exception:
                        _bargs = {}
                    _browser_actions.append(_bargs.get("action", "browser"))

                # Inject after actions that change page state
                _SNAP_ACTIONS = {"navigate", "click", "submit", "press"}
                if any(a in _SNAP_ACTIONS for a in _browser_actions):
                    await _asyncio.sleep(0.8)  # SPA hydration settle
                    _browser_tool = tools.get("browser")
                    _snapped = False
                    if _browser_tool is not None:
                        try:
                            _action_label = f"[PAGE after {', '.join(_browser_actions)}]"

                            # 1. Try to get a live screenshot and inject it as an image.
                            #    The main vLLM model supports vision natively via image_url,
                            #    same as the GPU worker's _vllm_describe — no extra roundtrip.
                            _screenshot_b64: str | None = None
                            _async_cap = getattr(_browser_tool, "_async_capture_frame", None)
                            if _async_cap is not None:
                                try:
                                    _pil_img = await _async_cap()
                                    if _pil_img is not None:
                                        import io as _io
                                        import base64 as _b64
                                        _buf = _io.BytesIO()
                                        _pil_img.save(_buf, format="JPEG", quality=60)
                                        _screenshot_b64 = _b64.b64encode(
                                            _buf.getvalue()
                                        ).decode()
                                except Exception as _cap_err:
                                    logger.debug(
                                        "browser screenshot capture failed: %s", _cap_err
                                    )

                            # 2. Always get the DOM snapshot for structured ref data.
                            _snap_text: str = ""
                            if hasattr(_browser_tool, "_take_snapshot"):
                                try:
                                    _snap_text = await _browser_tool._take_snapshot()
                                except Exception:
                                    pass

                            # 3. Build and inject the context message.
                            if _screenshot_b64:
                                context.append({
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": f"data:image/jpeg;base64,{_screenshot_b64}"
                                            },
                                        },
                                        {
                                            "type": "text",
                                            "text": (
                                                f"{_action_label}\n{_snap_text}"
                                                if _snap_text else _action_label
                                            ),
                                        },
                                    ],
                                })
                                logger.info(
                                    "[LOOP] browser screenshot+snapshot injected after %s "
                                    "(img=%d b64 chars, snap=%d chars)",
                                    _browser_actions,
                                    len(_screenshot_b64),
                                    len(_snap_text),
                                )
                                _snapped = True
                            elif _snap_text:
                                context.append({
                                    "role": "user",
                                    "content": f"{_action_label}\n{_snap_text}",
                                })
                                logger.info(
                                    "[LOOP] page snapshot injected after %s (%d chars)",
                                    _browser_actions, len(_snap_text),
                                )
                                _snapped = True
                        except Exception as _snap_err:
                            logger.debug("browser feedback injection failed: %s", _snap_err)
                    # Fall back to VC buffer if browser tool not accessible
                    if (
                        not _snapped
                        and visual_cortex is not None
                        and not state.coordinator_mode
                    ):
                        try:
                            _fb = visual_cortex.get_tool_visual_feedback(
                                tool_start=time.time() - 3,
                            )
                            if _fb:
                                context.append({
                                    "role": "user",
                                    "content": (
                                        f"[VISUAL FEEDBACK — after {', '.join(_browser_actions)}]\n{_fb}"
                                    ),
                                })
                                logger.debug(
                                    "[LOOP] VC fallback feedback injected after %s (%d chars)",
                                    _browser_actions, len(_fb),
                                )
                        except Exception:
                            pass

            if hooks.on_turn_end:
                try:
                    hooks.on_turn_end(response, results)
                except Exception:
                    pass

            # Plan event bridge: master/sub-plan JSON (plan tool) → WM + UI.
            # Orchestrator updates plan; sub-plans pair with delegate.
            # Also fires after team tool calls because team(advance)
            # modifies the plan on disk without a plan tool call.
            if _plan_tool and hasattr(_plan_tool, "get_store"):
                _any_plan_call = any(
                    tc.get("function", {}).get("name") in ("plan", "team")
                    for tc in response.tool_calls
                )
                if _any_plan_call:
                    try:
                        _store = _plan_tool.get_store()
                        _refreshed = _store.find_active()
                        if _refreshed:
                            _new_steps = [s.label for s in _refreshed.steps]
                            _new_statuses = [s.status for s in _refreshed.steps]
                            if _new_steps != _plan_steps:
                                _plan_steps = _new_steps
                                _plan_statuses = _new_statuses
                                _rich = [
                                    {"id": s.id, "label": s.label, "status": s.status}
                                    for s in _refreshed.steps
                                ]
                                await emit(on_event, AgentEvent(
                                    EventType.PLAN_UPDATE, {
                                        "type": "agentic_plan",
                                        "steps": _rich,
                                        "plan_id": _refreshed.id,
                                        "title": _refreshed.title,
                                        "todo_id": _refreshed.todo_id or "",
                                        "project_dir": _refreshed.project_dir or "",
                                        "iteration": state.iteration,
                                    },
                                ))
                            else:
                                for _si, (_old, _new) in enumerate(
                                    zip(_plan_statuses, _new_statuses)
                                ):
                                    if _old != _new:
                                        _lbl = _refreshed.steps[_si].label if _si < len(_refreshed.steps) else ""
                                        _step_id = (
                                            _refreshed.steps[_si].id
                                            if _si < len(_refreshed.steps)
                                            else ""
                                        )
                                        await emit(on_event, AgentEvent(
                                            EventType.PLAN_UPDATE, {
                                                "type": "plan_step_update",
                                                "step_index": _si,
                                                "step_id": _step_id,
                                                "status": _new,
                                                "label": _lbl,
                                                "plan_id": _refreshed.id,
                                                "todo_id": _refreshed.todo_id or "",
                                                "iteration": state.iteration,
                                            },
                                        ))
                                _plan_statuses = _new_statuses

                            # Update WM plan position
                            _pos = _refreshed.to_position_string()
                            if _pos and hooks.wm_set_plan_position:
                                hooks.wm_set_plan_position(_pos)
                    except Exception:
                        logger.debug("Plan event bridge failed", exc_info=True)

            # Refresh todo board in WM after any todo/plan tool call
            if hooks.wm_refresh_todo_board and any(
                tc.get("function", {}).get("name", "") in ("todo", "plan")
                for tc in response.tool_calls
            ):
                try:
                    hooks.wm_refresh_todo_board()
                except Exception:
                    pass

            # Recovery / delegation lifecycle flags from tool results.
            _recovery_note_needed = False
            for tc, r in zip(response.tool_calls, results):
                _fn = tc.get("function", {}) or {}
                _tname = _fn.get("name", "")
                _targs = _parse_tool_args_safe(_fn.get("arguments", "{}"))
                _details = getattr(r, "details", None) or {}
                _action = _targs.get("action", "")
                if (
                    _tname == "plan"
                    and _action == "delete"
                    and not r.is_error
                ):
                    state.orchestrator_recovery = True
                    _recovery_note_needed = True
                elif _tname == "plan" and _action == "create" and not r.is_error:
                    state.orchestrator_recovery = False
                elif _tname == "team" and _action in ("create", "launch") and not r.is_error:
                    state.orchestrator_recovery = False
                elif (
                    _tname == "plan"
                    and _action == "accept_partial"
                    and not r.is_error
                ):
                    state.orchestrator_recovery = True
                    _recovery_note_needed = True
                elif (
                    _tname == "team"
                    and _action == "disband"
                    and not r.is_error
                ):
                    state.orchestrator_recovery = True
                    _recovery_note_needed = True
                elif _details.get("orchestrator_recovery"):
                    state.orchestrator_recovery = True
                    _recovery_note_needed = True
                elif (
                    _tname == "team"
                    and _action == "inspect"
                    and not r.is_error
                ):
                    record_team_inspect(
                        state, str(_targs.get("team_id", "")),
                    )
                elif (
                    _tname == "team"
                    and _action == "advance"
                    and _details.get("outcome") == "failed"
                ):
                    state.orchestrator_recovery = True
                    _recovery_note_needed = True
            if _recovery_note_needed and state.orchestrator_recovery:
                _delegates_live = state.delegate_count > 0
                if delegate_manager is not None:
                    try:
                        _delegates_live = delegate_manager.has_active_delegates()
                    except Exception:
                        pass
                if not _delegates_live:
                    context.append({
                        "role": "system",
                        "content": recovery_mode_system_note(),
                    })

            if any(r.stop_loop for r in results):
                _stop_result = next(
                    (r for r in results if r.stop_loop), None)
                _stop_details = (
                    getattr(_stop_result, "details", {}) or {}
                ) if _stop_result else {}
                if _stop_details.get("type") == "task_complete":
                    if (
                        _is_delegate_loop
                        and config.escalate_on_limit
                        and not getattr(state, "_completion_reviewed", False)
                    ):
                        state._completion_reviewed = True
                        _review = await _await_completion_review(
                            state, config, copilot_queue, context,
                            slog_path=_session_log_path,
                        )
                        if _review == "rejected":
                            state.exit_reason = ""
                            await emit(on_event, AgentEvent(
                                EventType.TURN_END, {
                                    "iteration": state.iteration,
                                    "has_tool_calls": True,
                                    "tool_calls": _iter_tool_calls,
                                    "tool_results": _iter_tool_results,
                                },
                            ))
                            continue
                    state.exit_reason = "task_complete"
                    state.final_response = (
                        _stop_details.get("summary", "")
                        or getattr(_stop_result, "content", "")
                    )
                elif _stop_details.get("type") == "awaiting_delegates":
                    state.exit_reason = "awaiting_delegates"
                    state.final_response = (
                        _stop_details.get("summary", "")
                        or getattr(_stop_result, "content", "")
                    )
                else:
                    state.exit_reason = "tool_requested_stop"
                    if _stop_result and _stop_result.content:
                        state.final_response = _stop_result.content
                await emit(on_event, AgentEvent(
                    EventType.TURN_END, {
                        "iteration": state.iteration,
                        "has_tool_calls": True,
                        "tool_calls": _iter_tool_calls,
                        "tool_results": _iter_tool_results,
                    },
                ))
                break

            if state.exit_reason == "orchestrator_terminated":
                _term_msg = next(
                    (r.content for r in results if r.content), "",
                )
                if _term_msg:
                    state.final_response = _term_msg
                await emit(on_event, AgentEvent(
                    EventType.TURN_END, {
                        "iteration": state.iteration,
                        "has_tool_calls": True,
                        "tool_calls": _iter_tool_calls,
                        "tool_results": _iter_tool_results,
                    },
                ))
                break

            # --- EM idle-polling detection (not a substitute for management) ---
            _iter_tool_names = iter_tool_names(response.tool_calls)
            if state.coordinator_mode and delegate_manager is not None:
                update_coordinator_counters(
                    state,
                    _iter_tool_names,
                    response.tool_calls,
                    delegate_manager,
                )
                _tm_yield = getattr(hooks, "_cached_team_manager", None)
                _force_yield, _yield_reason = should_force_coordinator_yield(
                    state, delegate_manager,
                    dispatch_source=dispatch_source,
                    has_pending_completion_reviews=(
                        _tm_yield.has_pending_completion_reviews()
                        if _tm_yield is not None
                        else False
                    ),
                )
                if (
                    _force_yield
                    and _tm_yield is not None
                    and _tm_yield.has_pending_completion_reviews()
                ):
                    _cr_block = _tm_yield.completion_review_yield_block_message()
                    if _cr_block:
                        logger.info(
                            "[LOOP:%s] iter %d: coordinator yield blocked — "
                            "pending completion review(s)",
                            state.loop_id, state.iteration,
                        )
                        context.append({
                            "role": "system",
                            "content": f"[EM TURN — COMPLETION REVIEW REQUIRED]\n{_cr_block}",
                        })
                        _force_yield = False
                if _force_yield:
                    logger.info(
                        "[LOOP:%s] iter %d: coordinator yield — %s "
                        "(monitor=%d burn=%d idle=%d)",
                        state.loop_id, state.iteration, _yield_reason,
                        state.coordinator_monitor_iters,
                        state.coordinator_burn_iters,
                        state.idle_monitor_cycles,
                    )
                    state.exit_reason = (
                        _yield_reason
                        if _yield_reason != "idle_monitor"
                        else "idle_monitor_yield"
                    )
                    state.final_response = ""
                    _slog(_session_log_path, {
                        "event": "coordinator_yield",
                        "loop_id": state.loop_id,
                        "iteration": state.iteration,
                        "reason": _yield_reason,
                        "monitor_iters": state.coordinator_monitor_iters,
                        "burn_iters": state.coordinator_burn_iters,
                        "idle_cycles": state.idle_monitor_cycles,
                    })
                    break
                if state.idle_monitor_cycles >= 2:
                    _idle_nudge = (
                        "[EM TURN] Repeated inspect/wait without a "
                        "management decision. Hint if stuck, evaluate "
                        "if wave landed, otherwise await_delegates — "
                        "do not idle-poll the board."
                    )
                    if (
                        _tm_yield is not None
                        and _tm_yield.has_pending_completion_reviews()
                    ):
                        _idle_nudge = (
                            "[EM TURN] A delegate is waiting for your "
                            "completion review. Call team(intervene, "
                            "decision='approve' or 'hint') NOW — do not "
                            "await_delegates or keep inspecting."
                        )
                    context.append({
                        "role": "system",
                        "content": _idle_nudge,
                    })

            # Iterations whose ONLY tool call is wait() do not count against
            # the iteration budget — the agent is just polling, not working.
            _wait_only = (
                _iter_tool_names
                and all(n == "wait" for n in _iter_tool_names)
            )
            if _wait_only:
                state.wait_only_iterations += 1

        else:
            state.consecutive_text_only += 1
            state._last_iter_text = response.text

            # Sub-agent loops: fail fast when the model never emits tool_calls
            # (common on OpenRouter with reasoning-only models).
            if (
                _is_delegate_loop
                and state.total_tool_calls == 0
                and state.iteration >= 6
            ):
                logger.warning(
                    "[LOOP:%s] delegate exit at iter %d — zero tool_calls "
                    "after %d iterations (upstream likely not tool-calling)",
                    state.loop_id,
                    state.iteration,
                    state.iteration,
                )
                state.exit_reason = "delegate_no_tool_calls"
                await emit(on_event, AgentEvent(
                    EventType.TURN_END,
                    {
                        "iteration": state.iteration,
                        "error": True,
                        "reason": "delegate_no_tool_calls",
                    },
                ))
                break

            if response.text and response.text.strip():
                _last_text_response = response.text

            if hooks.on_turn_end:
                try:
                    hooks.on_turn_end(response, [])
                except Exception:
                    pass

            # RESPONDING → prior coordinator mode on text-only delivery.
            # The auto-transition block is inside `if response.tool_calls:`
            # above, so it never fires for pure text responses.  We must
            # handle it here: if the agent just delivered a text response
            # with no tool calls, that IS the delivered response, so restore
            # the mode immediately so the evaluator sees MONITORING (not
            # RESPONDING) and can apply the _monitoring_wrap_up shortcut.
            if state.active_mode == AgentMode.RESPONDING:
                _resp_comm_tools = frozenset({
                    "communicate", "ask_user", "contacts",
                    "whatsapp_send", "telegram_send", "email_send",
                    "gmail_send", "gmail_reply",
                })
                # No tool calls at all → definitely delivered a text response
                if response.text:
                    _restore = state._pre_responding_mode or AgentMode.MONITORING
                    state.active_mode = _restore
                    state._pre_responding_mode = None
                    invalidate_tool_policy_cache(state)
                    state.mode_override_count = 0
                    _rmode = getattr(hooks, "_render_mode_ref", None)
                    if _rmode:
                        _rmode[0] = _restore.value
                    logger.info(
                        "[LOOP:%s] iter %d: RESPONDING → %s "
                        "(text-only delivery, no tool calls)",
                        state.loop_id, state.iteration, _restore.value,
                    )
            # The model sometimes puts its final answer entirely inside
            # <think> blocks with empty visible text.  When thinking is
            # substantive (>150 chars) and we already have tool results,
            # prompt the model to surface its conclusion as visible text
            # instead of wasting iterations on stall nudges.
            if _budget.thinking_budget_exhausted:
                logger.info(
                    "[LOOP:%s] iter %d: THINKING_BUDGET_EXhausted nudge "
                    "(finish_reason=%s thinking_len=%d completion_tokens=%d)",
                    state.loop_id,
                    state.iteration,
                    response.finish_reason,
                    len(response.thinking),
                    response.completion_tokens,
                )
                _reasoning_trajectory = ""
                context.append({
                    "role": "system",
                    "content": build_thinking_length_nudge(
                        len(response.thinking),
                        config.max_new_tokens,
                    ),
                })
                state.consecutive_text_only = 0
                await emit(on_event, AgentEvent(
                    EventType.TURN_END, {"iteration": state.iteration},
                ))
                continue

            _thinking_has_answer = (
                not response.text
                and response.thinking
                and len(response.thinking) > 150
                and state.total_tool_calls > 0
            )
            if _thinking_has_answer:
                logger.info(
                    "[LOOP:%s] iter %d: thinking-as-response detected "
                    "(thinking_len=%d, total_tc=%d) — prompting "
                    "model to surface conclusion",
                    state.loop_id, state.iteration, len(response.thinking),
                    state.total_tool_calls,
                )
                _reasoning_trajectory = ""
                context.append({
                    "role": "system",
                    "content": (
                        "You completed your analysis in your reasoning. "
                        "Now communicate your findings to the user as "
                        "a direct, concise response. Summarize what you "
                        "found and any results."
                    ),
                })
                state.consecutive_text_only = 0
                await emit(on_event, AgentEvent(
                    EventType.TURN_END, {"iteration": state.iteration},
                ))
                continue

            # --- Stall injection (before completion check) ---
            from nls.agentic.evaluator import (
                prose_stream_text,
                refresh_prose_verdict,
            )

            await refresh_prose_verdict(
                state, vllm_client, adapter_name=adapter_name,
            )
            _prose_exit = getattr(state, "last_prose_verdict", "") in (
                "awaiting_user_input", "duplicate",
            )

            _had_errors = any(
                v > 0 for v in state.tool_errors.values()
            )
            # When the model delivers a substantive text response after
            # using tools, it's likely reporting results — let the
            # evaluator decide rather than injecting a stall nudge.
            _substantive_delivery = (
                state.consecutive_text_only == 1
                and state.total_tool_calls > 0
                and len(response.text or "") > 100
            )

            # Coordinator override: if the orchestrator produces a
            # text-only response but there is still work to do (active
            # plan or queued/running teams), inject a nudge instead of
            # silently continuing.  Without this, the agent drifts into
            # asking the user "What next?" when it should advance the
            # plan autonomously.
            _coordinator_has_work = False
            _has_active_plan_work = False
            _active_teams: list = []
            if hooks.has_active_plan:
                try:
                    _has_active_plan_work = bool(hooks.has_active_plan())
                except Exception:
                    pass
            if _substantive_delivery and (
                state.coordinator_mode or _has_active_plan_work
            ):
                _coordinator_has_work = _has_active_plan_work
                try:
                    _tm_ref = _cached_team_manager
                    if _tm_ref:
                        _active_teams = [
                            t for t in _tm_ref.list_teams(
                                include_terminal=False)
                            if t.status in (
                                "created", "active", "running",
                            )
                        ]
                        if not _coordinator_has_work:
                            _coordinator_has_work = bool(_active_teams)
                except Exception:
                    pass

            # MONITORING coordinators with in-flight delegates: suppress
            # ALL stall nudges and let should_complete decide.  The
            # agent already launched teams — nudging it to "take action"
            # just causes pointless wait/inspect/text cycles instead of
            # a clean exit to background.
            _bg_delegates = state.delegate_count > 0
            if not _bg_delegates and delegate_manager is not None:
                try:
                    _bg_delegates = delegate_manager.has_active_delegates()
                except Exception:
                    pass
            _monitoring_should_yield = (
                state.active_mode == AgentMode.MONITORING
                and state.coordinator_mode
                and _bg_delegates
            )
            _evaluating_text_spiral = (
                state.active_mode == AgentMode.EVALUATING
                and state.coordinator_mode
                and state.consecutive_text_only >= 2
                and not _substantive_delivery
            )

            # Only inject stall nudges BELOW the hard limit.
            # At the limit, let should_complete / check_guards handle exit.
            # Do NOT stall-nudge after a substantive coordinator status turn —
            # that forced a second model reply after the user already saw text
            # (e.g. "What would you like me to do first?"). Yield via
            # should_complete instead.
            if _substantive_delivery and _coordinator_has_work:
                stall_msg = None
            elif _substantive_delivery:
                stall_msg = None
            elif _monitoring_should_yield:
                stall_msg = None
            elif _evaluating_text_spiral:
                stall_msg = build_evaluating_action_breadcrumb(
                    _plan_tool,
                    dispatch_source=dispatch_source
                    or getattr(state, "dispatch_source", ""),
                )
            elif _prose_exit:
                stall_msg = None
            elif state.consecutive_text_only >= config.consecutive_text_only_limit:
                stall_msg = (
                    "You have responded with text "
                    f"{state.consecutive_text_only} times without "
                    "calling any tool. If you are truly finished, call "
                    "task_complete(summary='...') to end. Otherwise, "
                    "take action with a tool call NOW."
                )
            elif state.consecutive_text_only >= 2:
                if _monitoring_should_yield:
                    stall_msg = (
                        "[LOOP CONTROL] Your delegates are running in the "
                        "background. Do NOT keep posting status updates in "
                        "this loop.\n"
                        "Call await_delegates(summary='...') NOW to exit "
                        "cleanly. You will be re-activated automatically "
                        "when waves complete or milestones occur."
                    )
                else:
                    stall_msg = (
                        "Your last responses were text, not tool calls. "
                        "The user's request requires ACTION. Their request: "
                        f"\"{user_input[:200]}\"\n\n"
                        "If the previous tool call failed, try a different "
                        "approach or fix the command syntax and retry. "
                        "Call a tool NOW. If you are done, call "
                        "task_complete(summary='...')."
                    )
            elif state.consecutive_text_only == 1 and (
                state.total_tool_calls == 0 or _had_errors
            ):
                if (
                    not _had_errors
                    and state.total_tool_calls == 0
                    and is_conversational_user_turn(user_input)
                ):
                    stall_msg = None
                else:
                    stall_msg = (
                        "You responded with text but the task requires "
                        "action via tools. "
                        + (
                            "The previous tool call had an error — fix "
                            "the issue and retry with corrected arguments. "
                            if _had_errors else ""
                        )
                        + f"Request: \"{user_input[:200]}\""
                    )
            else:
                stall_msg = None

            if stall_msg:
                logger.info(
                    "[LOOP:%s] iter %d: STALL injected (consec_text=%d "
                    "total_tc=%d had_errors=%s)",
                    state.loop_id, state.iteration, state.consecutive_text_only,
                    state.total_tool_calls, _had_errors,
                )
                context.append({
                    "role": "system",
                    "content": f"[LOOP CONTROL] {stall_msg}",
                })
                await emit(on_event, AgentEvent(
                    EventType.TURN_END, {"iteration": state.iteration},
                ))
            elif await should_complete(
                state, config, hooks, vllm_client, delegate_manager,
                adapter_name=adapter_name,
            ):
                # Delegate completion review: before accepting, ask the
                # orchestrator to verify the work.  Uses the same escalation
                # path (hint_queue) so the orchestrator can reject with a
                # hint or let the timeout expire (= implicit approval).
                # Only one review per delegate to avoid infinite loops.
                if (
                    _is_delegate_loop
                    and config.escalate_on_limit
                    and not getattr(state, "_completion_reviewed", False)
                ):
                    state._completion_reviewed = True
                    _review = await _await_completion_review(
                        state, config, copilot_queue, context,
                        slog_path=_session_log_path,
                    )
                    if _review == "rejected":
                        await emit(on_event, AgentEvent(
                            EventType.TURN_END, {
                                "iteration": state.iteration,
                            },
                        ))
                        continue

                logger.info(
                    "[LOOP:%s] iter %d: COMPLETE — task_complete "
                    "(total_tc=%d consec_text=%d)",
                    state.loop_id, state.iteration, state.total_tool_calls,
                    state.consecutive_text_only,
                )
                state.exit_reason = "task_complete"
                _streamed = prose_stream_text(state, response.text or "")
                if _streamed.strip():
                    state.final_response = _streamed
                elif getattr(state, "last_prose_verdict", "") != "duplicate":
                    state.final_response = response.text or ""
                await emit(on_event, AgentEvent(
                    EventType.TURN_END, {
                        "iteration": state.iteration,
                        "response_text": _streamed,
                    },
                ))
                break
            else:
                await emit(on_event, AgentEvent(
                    EventType.TURN_END, {
                        "iteration": state.iteration,
                        "response_text": prose_stream_text(
                            state, response.text or "",
                        ),
                    },
                ))

        # Hypothalamus tick (wall-clock delta since last tick, not cumulative)
        if hooks.tick_hypothalamus:
            try:
                _now = time.time()
                _last = state._last_hypo_tick_ts or state.start_time
                _delta = max(0.1, _now - _last)
                state._last_hypo_tick_ts = _now
                hooks.tick_hypothalamus(_delta)
            except Exception:
                pass

        # Brain event bus emission (Phase 4 — unified signal distribution)
        # When the bus is wired, it handles ANS + Narrative + ToM dispatch,
        # so we skip the legacy hook to avoid double-calling on_response.
        _bus = getattr(hooks, "brain_event_bus", None)
        if _bus is not None and response.text:
            try:
                from nls.engine.brain_events import BrainSignal, BrainSignalType
                _bus.emit(BrainSignal(
                    type=BrainSignalType.RESPONSE,
                    source="agentic:v5",
                    user_input=user_input,
                    response_text=response.text or "",
                    is_agentic=True,
                    iteration=state.iteration,
                ))
                _bus.emit(BrainSignal(
                    type=BrainSignalType.TURN_END,
                    source="agentic:v5",
                    is_agentic=True,
                    iteration=state.iteration,
                    response_text=(response.text or "")[:200],
                    metadata={"tool_calls": list(state.tool_successes.keys())},
                ))
            except Exception:
                pass
        elif hooks.ans_on_response:
            try:
                hooks.ans_on_response(user_input, response.text)
            except Exception:
                pass

        # Shared context update for crash resilience
        if config.shared_context is not None:
            config.shared_context.clear()
            config.shared_context.extend(context)

        # Checkpoint callback
        if (
            config.checkpoint_callback
            and state.iteration % config.checkpoint_interval == 0
        ):
            try:
                config.checkpoint_callback(
                    list(context), [], [], state.iteration,
                )
            except Exception:
                pass

        # TURN_END for tool-call path (text-only path emits above)
        if _iter_tool_calls:
            _turn_end_data: dict[str, Any] = {
                "iteration": state.iteration,
                "has_tool_calls": True,
                "tool_calls": _iter_tool_calls,
                "tool_results": _iter_tool_results,
                "duration_ms": round(
                    (time.time() - state.start_time) * 1000, 1,
                ),
            }
            await emit(on_event, AgentEvent(
                EventType.TURN_END, _turn_end_data,
            ))

    # --- Post-loop ---
    _loop_duration = time.time() - state.start_time
    logger.info(
        "[LOOP:%s] === END === exit=%s iterations=%d total_tc=%d "
        "consec_text=%d ctx_msgs=%d tool_successes=%s tool_errors=%s "
        "duration=%.1fs",
        state.loop_id,
        state.exit_reason, state.iteration, state.total_tool_calls,
        state.consecutive_text_only,
        len(context),
        dict(state.tool_successes), dict(state.tool_errors),
        _loop_duration,
    )
    _slog(_session_log_path, {
        "event": "loop_end",
        "loop_id": state.loop_id,
        "exit_reason": state.exit_reason,
        "iterations": state.iteration,
        "total_tool_calls": state.total_tool_calls,
        "tool_successes": dict(state.tool_successes),
        "tool_errors": dict(state.tool_errors),
        "tool_nudges_given": dict(state.tool_nudges_given),
        "profile_depth_nudges_given": sorted(state.profile_depth_nudges_given),
        "profile_depth_adopted": state.profile_depth_adopted_this_loop,
        "duration_s": round(_loop_duration, 1),
        "final_response_len": len(state.final_response or ""),
        "final_response_preview": (state.final_response or "")[:500],
        "delegate_count": state.delegate_count,
        "cumulative_actions": state.cumulative_actions[-20:],
        "total_prompt_tokens": state.total_prompt_tokens,
        "total_completion_tokens": state.total_completion_tokens,
        "total_tokens": state.total_tokens,
        "iter_token_log": state.iter_token_log,
        "supersession_stubs_applied": state.supersession_stubs_applied,
        "supersession_tokens_saved": state.supersession_tokens_saved,
        "read_cache_hits": state.read_cache_hits,
    })

    # Aggregate delegate token usage into parent totals
    _delegate_tokens: dict[str, int] = {}
    if delegate_manager is not None:
        try:
            _delegate_tokens = delegate_manager.aggregate_token_usage()
            state.total_prompt_tokens += _delegate_tokens.get("prompt_tokens", 0)
            state.total_completion_tokens += _delegate_tokens.get("completion_tokens", 0)
            state.total_tokens += _delegate_tokens.get("total_tokens", 0)
        except Exception:
            pass

    _slog(_session_log_path, {
        "event": "token_summary",
        "loop_id": state.loop_id,
        "orchestrator_prompt_tokens": state.total_prompt_tokens - _delegate_tokens.get("prompt_tokens", 0),
        "orchestrator_completion_tokens": state.total_completion_tokens - _delegate_tokens.get("completion_tokens", 0),
        "orchestrator_total_tokens": state.total_tokens - _delegate_tokens.get("total_tokens", 0),
        "delegate_prompt_tokens": _delegate_tokens.get("prompt_tokens", 0),
        "delegate_completion_tokens": _delegate_tokens.get("completion_tokens", 0),
        "delegate_total_tokens": _delegate_tokens.get("total_tokens", 0),
        "combined_prompt_tokens": state.total_prompt_tokens,
        "combined_completion_tokens": state.total_completion_tokens,
        "combined_total_tokens": state.total_tokens,
        "delegate_count": state.delegate_count,
    })
    logger.info(
        "[LOOP:%s] TOKEN SUMMARY — orchestrator: prompt=%d completion=%d total=%d | "
        "delegates: prompt=%d completion=%d total=%d | "
        "combined: prompt=%d completion=%d total=%d | %d iterations",
        state.loop_id,
        state.total_prompt_tokens - _delegate_tokens.get("prompt_tokens", 0),
        state.total_completion_tokens - _delegate_tokens.get("completion_tokens", 0),
        state.total_tokens - _delegate_tokens.get("total_tokens", 0),
        _delegate_tokens.get("prompt_tokens", 0),
        _delegate_tokens.get("completion_tokens", 0),
        _delegate_tokens.get("total_tokens", 0),
        state.total_prompt_tokens,
        state.total_completion_tokens,
        state.total_tokens,
        state.iteration,
    )

    apply_final_response_backfill(state, _last_text_response)

    if dispatch_source.startswith("team_wave_complete:") and _cached_team_manager is not None:
        _wave_team_id = dispatch_source.split(":", 1)[1]
        try:
            await _cached_team_manager.handle_wave_review_loop_end(
                _wave_team_id,
                tool_calls=state.total_tool_calls,
            )
        except Exception:
            logger.debug("wave review loop end handler failed", exc_info=True)
        if config.enable_delegation and delegate_manager is not None:
            try:
                from nls.agentic.executor import try_auto_launch_pending_wave

                await try_auto_launch_pending_wave(
                    _cached_team_manager,
                    delegate_manager,
                    tools,
                    config,
                    state,
                    hooks,
                    vllm_client,
                    on_event,
                    abort_signal,
                    user_input,
                )
            except Exception:
                logger.debug(
                    "pending wave auto-launch after wave review failed",
                    exc_info=True,
                )

    # Auto-complete active plan + linked todo when loop exits successfully.
    # The model sometimes delivers a final answer without explicitly
    # calling plan(action='complete'), leaving Kanban items stuck.
    # Skip for delegate loops and when there are active teams/delegates.
    _has_running_team = False
    if not _is_delegate_loop:
        _tm = _cached_team_manager
        if _tm is not None:
            try:
                _has_running_team = _tm.has_orchestrator_blocking_team()
            except Exception:
                pass

    _PLAN_AUTO_COMPLETE_EXITS = frozenset({
        "task_complete",
        "complete",
    })

    if _is_delegate_loop:
        logger.debug("[LOOP] skipped plan auto-complete — delegate loop")
    elif (
        state.exit_reason in _PLAN_AUTO_COMPLETE_EXITS
        and _plan_tool is not None
        and not _has_running_team
    ):
        try:
            from nls.agentic.plan_work import auto_complete_active_plan_if_ready

            _completed_id = await auto_complete_active_plan_if_ready(
                _plan_tool, _tm,
            )
            if _completed_id:
                logger.info(
                    "[LOOP] auto-completed plan %s on exit (%s)",
                    _completed_id, state.exit_reason,
                )
            elif _plan_tool is not None:
                _store = (
                    _plan_tool.get_store()
                    if hasattr(_plan_tool, "get_store")
                    else None
                )
                _active = _store.find_active() if _store else None
                if _active and _active.status not in ("done", "archived"):
                    logger.info(
                        "[LOOP] skipped plan auto-complete for %s — "
                        "completion gate not met (exit=%s)",
                        _active.id, state.exit_reason,
                    )
        except Exception:
            logger.debug("Post-loop plan auto-complete failed", exc_info=True)
    elif _has_running_team:
        logger.info(
            "[LOOP] skipped plan auto-complete — active team(s) running"
        )

    # WM consolidation — accumulator-driven when available, legacy fallback otherwise.
    # The LLM compression + compounding can take several seconds (vLLM calls);
    # running it inline blocks the loop return and keeps _foreground_processing
    # elevated, causing the next user message to be misrouted as "copilot".
    # Fix: fire-and-forget the heavy path so the loop returns immediately.
    _wm_save_deferred = False
    if not _is_delegate_loop:
        _consol = _build_consolidation_summary(user_input, state)
        _acc = getattr(hooks, "_accumulator", None)
        _acc_wm = getattr(hooks, "_accumulator_wm_target", None)
        if _acc is not None and _acc_wm is not None:
            for _line in (_consol or "").splitlines():
                _ll = _line.strip().lower()
                if _ll.startswith("[progress]"):
                    _acc._buffers["progress"].append(_line)
                elif _ll.startswith("[knowledge]"):
                    _acc._buffers["knowledge"].append(_line)
                elif _ll.startswith("[context]"):
                    _acc._buffers["context"].append(_line)

            _wm_save_fn = hooks.wm_save
            _wm_consolidate_fn = hooks.wm_consolidate
            _wm_save_deferred = True

            async def _bg_consolidation() -> None:
                try:
                    await _acc.compress_and_flush(_acc_wm, reason="loop-end")
                except Exception:
                    logger.debug("Accumulator loop-end flush failed", exc_info=True)
                    if _wm_consolidate_fn and _consol:
                        try:
                            _wm_consolidate_fn(_consol)
                        except Exception:
                            pass
                if _wm_save_fn:
                    try:
                        _wm_save_fn()
                    except Exception:
                        pass

            asyncio.get_running_loop().create_task(_bg_consolidation())
        elif hooks.wm_consolidate and _consol:
            try:
                hooks.wm_consolidate(_consol)
            except Exception:
                pass
    if hooks.wm_save and not _wm_save_deferred:
        try:
            hooks.wm_save()
        except Exception:
            pass

    # ANS record task complete
    if hooks.ans_record_task_complete:
        try:
            hooks.ans_record_task_complete(
                user_input,
                state.final_response,
                sorted(set(state.tool_successes) | set(state.tool_errors)),
                state.exit_reason == "task_complete",
                time.time() - state.start_time,
            )
        except Exception:
            pass

    # Structured learnings export
    if hooks.extract_session_learnings:
        try:
            hooks.extract_session_learnings()
        except Exception:
            pass

    if hooks.on_loop_end:
        try:
            hooks.on_loop_end(state)
        except Exception:
            pass

    # Clean loop completion — remove the crash-recovery journal
    _journal_delete(_journal)

    if (
        not _is_delegate_loop
        and _plan_tool is not None
        and hasattr(_plan_tool, "_store")
    ):
        try:
            from nls.agentic.plan_work import (
                plan_closure_blocked_summary,
                should_emit_closure_blocked_communicate,
            )

            from nls.agentic.plan_work import resolve_work_plan

            _store_comm = (
                _plan_tool.get_store()
                if hasattr(_plan_tool, "get_store")
                else getattr(_plan_tool, "_store", None)
            )
            _active_for_comm = (
                resolve_work_plan(
                    _store_comm, "", _cached_team_manager, reopen=False,
                )
                if _store_comm is not None
                else _plan_tool._store.find_active()
            )
            if should_emit_closure_blocked_communicate(
                _active_for_comm,
                exit_reason=state.exit_reason,
                tool_successes=dict(state.tool_successes),
                is_delegate_loop=_is_delegate_loop,
            ) and _active_for_comm is not None:
                _comm_msg = plan_closure_blocked_summary(_active_for_comm)
                from nls.agentic.executor import _handle_communicate

                await _handle_communicate(
                    {"message": _comm_msg},
                    on_event,
                    state.iteration,
                )
                state.tool_successes["communicate"] = (
                    state.tool_successes.get("communicate", 0) + 1
                )
        except Exception:
            logger.debug("closure-blocked communicate failed", exc_info=True)

    result = state.to_result()
    result.total_duration_ms = (time.time() - state.start_time) * 1000
    result.context_messages = context
    result.loop_start_idx = _loop_start_idx
    result.deferred_actions = _deferred_actions

    await emit(on_event, AgentEvent(
        EventType.AGENT_END,
        {
            "result": result.exit_reason,
            "exit_reason": result.exit_reason,
            "iterations": result.iterations,
            "total_tool_calls": result.total_tool_calls,
            "aborted": result.aborted,
            "abort_reason": result.abort_reason,
            "duration_ms": round(result.total_duration_ms, 1),
            "final_response": result.final_response,
        },
    ))

    return result
