"""v4 tool execution module.

Handles sequential and parallel tool execution, virtual tools
(ask_user, communicate), cognitive digest, and per-tool retry tracking.
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

from nls.tools.agent_tools.base import ToolResult

from .bridge import LoopHooks
from .delegate_manager import DelegateManager, DelegateSpec, DELEGATE_DEFAULT_MAX_STEPS
from .coordinator_guard import (
    block_em_executing_during_review,
    block_executing_mode_escape,
    monitoring_advance_block_message,
    pre_delegate_block_message,
)
from .orchestration_policy import (
    block_tool_call,
    checkback_interval_seconds,
    invalidate_tool_policy_cache,
    on_await_delegates,
    on_evaluating_wave,
    on_team_launched,
)
from .orchestration_profile_spec import normalize_profile
from .outbound_notify import OUTBOUND_TOOLS, strip_outbound_control_args
from .events import AgentEvent, EventType, emit
from .types import (
    AgentMode, COORDINATOR_TOOLS, COORDINATOR_BASH_TIMEOUT_S,
    LoopConfig, LoopState, is_override_tool, get_allowed_tools,
    MODE_PRIMARY_TOOLS,
)

logger = logging.getLogger(__name__)


def _resolve_delegate_project_abs(workspace_root: str, project_dir: str) -> str:
    """Resolve delegate CWD without double-nesting when already inside project."""
    from pathlib import Path

    if not project_dir:
        return workspace_root
    ws = Path(workspace_root)
    if ws.name == project_dir:
        return str(ws)
    ws_norm = str(ws).replace("\\", "/").rstrip("/")
    if ws_norm.endswith(f"/{project_dir}"):
        return str(ws)
    return str(ws / project_dir)


def _merge_recipe_preflight(facts: str, task: str, user_task: str = "") -> str:
    try:
        from nls.agentic.recipe_hints import match_recipe_hints
        hint = match_recipe_hints(f"{task}\n{user_task[:500]}")
        if hint:
            return f"{facts}\n\n{hint}".strip() if facts else hint
    except Exception:
        pass
    return facts


_DELEGATE_EXCLUDED = frozenset({
    "plan", "todo", "delegate", "delegate_status", "delegate_ring",
    # Communication tools are orchestrator-only.  Delegates must not
    # independently message the user — all user-facing comms go through
    # the orchestrator.  Virtual tool "communicate" is also gated: its
    # schema is excluded from delegate loops and the executor rejects it.
    "whatsapp_send", "telegram_send", "email_send",
    "ask_user",
})

_XML_LEAK_RE = re.compile(
    r"</?(?:parameter|description|function)[^>]*>.*",
    re.DOTALL | re.IGNORECASE,
)


def _parse_tool_args(args_str: str | dict) -> dict:
    """Parse tool call arguments, unwrapping vLLM's ``input`` envelope.

    vLLM / Qwen3 sometimes wraps the real tool arguments in an
    ``{"input": "{...}"}`` envelope.  Detect and unwrap so tools receive
    the parameters they expect.
    """
    if isinstance(args_str, dict):
        args = args_str
    elif isinstance(args_str, str):
        try:
            args = json.loads(args_str)
        except (json.JSONDecodeError, ValueError):
            return {}
    else:
        return {}

    if isinstance(args, dict) and "input" in args and len(args) == 1:
        inner = args["input"]
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except (json.JSONDecodeError, ValueError):
                pass
        if isinstance(inner, dict):
            args = inner

    # Sanitize XML tag leakage in string values.
    for k, v in list(args.items()):
        if isinstance(v, str) and "<" in v:
            cleaned = _XML_LEAK_RE.sub("", v).rstrip()
            if cleaned != v:
                args[k] = cleaned

    # Fix double-wrapped path and other path keys (shared normalizer).
    from nls.tools.agent_tools.tool_path_args import (
        PATH_ARG_KEYS,
        normalize_path_fields_in_args,
        unwrap_embedded_json_path,
    )

    for k in PATH_ARG_KEYS:
        v = args.get(k)
        if not isinstance(v, str):
            continue
        sv = v.strip()
        if not sv.startswith("{"):
            continue
        embedded = unwrap_embedded_json_path(sv, k)
        if embedded is not None:
            logger.warning(
                "_parse_tool_args: unwrapped double-JSON %s: %s -> %s",
                k, v[:80], embedded,
            )
            args[k] = embedded

    normalize_path_fields_in_args(args)

    # Fix JSON-encoded arrays/objects passed as strings.
    # LLM sometimes emits steps='[{"label":"..."}]' or
    # acceptance_criteria='["item1","item2"]' as strings instead of
    # parsed JSON.  Also repairs truncated arrays missing closing ']'.
    for k in ("steps", "acceptance_criteria", "depends_on", "output_files"):
        v = args.get(k)
        if not isinstance(v, str):
            continue
        sv = v.strip()
        if not sv or sv[0] not in ("[", "{"):
            continue
        try:
            parsed = json.loads(sv)
            if isinstance(parsed, (list, dict)):
                args[k] = parsed
                continue
        except (json.JSONDecodeError, TypeError):
            pass
        # Repair truncated JSON array (LLM dropped closing bracket)
        if sv.startswith("["):
            for suffix in ("]", "}]", "\"}]", "\"}]}", "\"]}]"):
                try:
                    parsed = json.loads(sv + suffix)
                    if isinstance(parsed, list) and len(parsed) > 0:
                        logger.warning(
                            "_parse_tool_args: repaired truncated %s "
                            "(appended '%s', %d items)",
                            k, suffix, len(parsed),
                        )
                        args[k] = parsed
                        break
                except (json.JSONDecodeError, TypeError):
                    continue

    return args


def _delegate_log(config: LoopConfig, entry: dict) -> None:
    """Append to the session log for delegate lifecycle events."""
    base = config.session_log_dir
    if not base:
        import tempfile
        base = os.path.join(tempfile.gettempdir(), "nls_agentic_sessions")
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, "delegates.jsonl")
    try:
        entry["_ts"] = datetime.now(timezone.utc).isoformat()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
    except Exception:
        pass

_DIGEST_TOOLS = frozenset({"read", "web_fetch", "semantic_search"})
_DIGEST_MIN_CHARS = 2000
_DIGEST_MAX_PER_LOOP = 8

_COGNITIVE_DIGEST_SYSTEM = (
    "You are a reading comprehension assistant. Given a task context and "
    "a tool output (file content, command output, or web page), extract "
    "a concise digest of what was read.\n\n"
    "Return a JSON object with exactly these fields:\n"
    '  "summary": "<1-line description of what this content is>",\n'
    '  "insights": ["<key fact 1>", "<key fact 2>", ...],\n'
    '  "source": "<file path, URL, or command that produced this>"\n\n'
    "Rules:\n"
    "- Focus insights on what is RELEVANT to the user's task.\n"
    "- Max 5 insights, each under 80 chars.\n"
    "- For code files: note exports, dependencies, main purpose.\n"
    "- For docs: note key specs, requirements, decisions.\n"
    "- For command output: note results, status, errors.\n"
    "- Return ONLY the JSON object. No explanation."
)


# -------------------------------------------------------------------
# Virtual tool handling
# -------------------------------------------------------------------

async def _handle_ask_user(
    args: dict,
    on_event: Callable | None,
    hooks: LoopHooks,
    iteration: int,
    tool_call_id: str,
) -> ToolResult:
    """Handle ask_user virtual tool — blocks on copilot_queue."""
    question = args.get("question", "What do you need?")
    await emit(on_event, AgentEvent(
        EventType.ASK_USER,
        {"question": question, "tool_call_id": tool_call_id, "iteration": iteration},
    ))

    if hooks.copilot_queue is None:
        return ToolResult(
            content="[No communication channel — cannot ask user]",
            is_error=True,
        )

    try:
        answer = await asyncio.wait_for(hooks.copilot_queue.get(), timeout=300)
        answer_text = str(answer) if answer else "(no answer)"
    except asyncio.TimeoutError:
        answer_text = "(user did not respond within 5 minutes)"

    await emit(on_event, AgentEvent(
        EventType.USER_ANSWER,
        {"answer": answer_text, "iteration": iteration},
    ))

    if hooks.ans_extract_user_answer:
        try:
            hooks.ans_extract_user_answer(question, answer_text)
        except Exception:
            pass

    return ToolResult(content=f"User answered: {answer_text}")


def _escalation_context_summary(
    state: LoopState,
    message: str = "",
    max_iterations: int | None = None,
    max_total_iterations: int | None = None,
) -> str:
    """Compact status block for proactive sub-agent escalation."""
    _write_count = (
        state.tool_successes.get("write", 0)
        + state.tool_successes.get("edit", 0)
    )
    _soft = max_iterations if max_iterations is not None else "?"
    _hard = (
        max_total_iterations
        if max_total_iterations is not None
        else max_iterations
    )
    _budget_line = f"iteration: {state.iteration}/{_soft}"
    if _hard is not None and _hard != _soft:
        _budget_line += f" (extension cap {_hard})"
    lines = [
        message,
        "",
        _budget_line,
        f"tool_calls: {state.total_tool_calls}",
        f"writes: {_write_count}",
    ]
    if state.files_written:
        lines.append("files_written:")
        for path in state.files_written[-10:]:
            lines.append(f"- {path}")
    if state.cumulative_actions:
        lines.append("recent_actions: " + ", ".join(state.cumulative_actions[-8:]))
    return "\n".join(line for line in lines if line is not None)


async def _await_orchestrator_escalation(
    *,
    reason: str,
    state: LoopState | None,
    context_summary: str,
    config: LoopConfig,
    copilot_queue: asyncio.Queue | None,
    esc_cb: Callable | None,
    wait_seconds: float = 120,
) -> tuple[str, bool]:
    """Notify orchestrator and block until intervene or timeout.

    Returns ``(message_for_agent, terminate_loop)``.
    """
    if not esc_cb or copilot_queue is None:
        return (
            "No orchestrator escalation channel available. "
            "Work with what you have or use escalate() if you can.",
            False,
        )

    try:
        _r = esc_cb(reason, state, context_summary)
        if asyncio.iscoroutine(_r):
            await _r
    except Exception:
        logger.debug("orchestrator escalation callback failed", exc_info=True)

    try:
        decision = await asyncio.wait_for(copilot_queue.get(), timeout=wait_seconds)
    except asyncio.TimeoutError:
        return (
            "Orchestrator did not respond in time. "
            "Work with what you have or call escalate() with your progress.",
            False,
        )

    if isinstance(decision, dict) and "action" in decision:
        action = decision.get("action", "terminate")
        msg = decision.get("message", "")
        extra_iters = int(decision.get("extra_iterations", 10) or 10)
        if action in ("extend", "hint", "approve"):
            if state is not None:
                config.max_iterations += extra_iters
                config.max_total_iterations += extra_iters
                config.total_timeout_seconds += max(extra_iters * 30.0, 300.0)
                state.consecutive_errors = 0
                state.stall_nudges_given = 0
            if action == "hint":
                body = msg or "Try a different approach."
                return (f"[ORCHESTRATOR HINT] {body}", False)
            body = msg or f"You have been granted {extra_iters} more iterations."
            return (f"[ORCHESTRATOR] {body}", False)
        if state is not None:
            state.exit_reason = "orchestrator_terminated"
        body = msg or "The orchestrator ended this task."
        return (f"[ORCHESTRATOR TERMINATE] {body}", True)

    if isinstance(decision, dict) and "role" in decision:
        return (decision.get("content", str(decision)), False)
    if isinstance(decision, str):
        return (f"Orchestrator answered: {decision}", False)
    return (f"Orchestrator answered: {decision}", False)


async def _handle_sub_agent_escalation(
    reason: str,
    message: str,
    state: LoopState,
    config: LoopConfig,
    hooks: LoopHooks | None,
) -> ToolResult:
    """Proactive escalation from a worker sub-agent to the orchestrator."""
    context_summary = _escalation_context_summary(
        state,
        message,
        config.max_iterations,
        getattr(config, "max_total_iterations", None),
    )
    body, terminate = await _await_orchestrator_escalation(
        reason=reason,
        state=state,
        context_summary=context_summary,
        config=config,
        copilot_queue=(hooks or LoopHooks()).copilot_queue,
        esc_cb=config.on_escalation,
    )
    return ToolResult(content=body, is_error=terminate)


async def _handle_communicate(
    args: dict,
    on_event: Callable | None,
    iteration: int,
) -> ToolResult:
    """Handle communicate virtual tool — non-blocking message to user."""
    message = args.get("message", "")
    await emit(on_event, AgentEvent(
        EventType.COMMUNICATE,
        {"message": message, "iteration": iteration},
    ))
    return ToolResult(content="Message delivered to user.")


_MAX_DELEGATES = 5


async def _handle_delegate(
    args: dict,
    tools: dict[str, Any],
    config: "LoopConfig",
    state: "LoopState",
    hooks: "LoopHooks",
    vllm_client: Any,
    on_event: Callable | None,
    abort_signal: asyncio.Event | None,
    iteration: int,
    user_task: str,
    *,
    delegate_number: int | None = None,
) -> ToolResult:
    """Handle delegate virtual tool — spawn a sub-agent loop.

    When ``delegate_number`` is pre-assigned (parallel fan-out path), the
    caller has already incremented ``state.delegate_count`` and verified the
    cap.  When it is None (sequential / single-call path), we do it here.
    """
    task = args.get("task", "").strip()
    if not task:
        return ToolResult(content="Error: 'task' parameter is required.", is_error=True)

    from .orchestration_policy import (
        build_tool_policy_inputs,
        resolve_allowed_tools,
    )

    _policy_inputs = build_tool_policy_inputs(
        state.active_mode,
        state,
        None,
        set(tools.keys()),
        hooks,
    )
    if "delegate" not in resolve_allowed_tools(_policy_inputs):
        return ToolResult(
            content=(
                "BLOCKED: Active plan has delegatable steps — raw delegate() "
                "is disabled. Use team(action='create', plan_id=..., wave=N) "
                "then team(action='launch', team_id=...)."
            ),
            is_error=True,
            details={"blocked": True, "use_team": True},
        )

    if delegate_number is None:
        # Sequential path: check cap and assign number here.
        if state.delegate_count >= _MAX_DELEGATES:
            return ToolResult(
                content=f"BLOCKED: Maximum {_MAX_DELEGATES} delegate calls per task. "
                        "Complete remaining work yourself.",
                is_error=True,
            )
        state.delegate_count += 1
        delegate_number = state.delegate_count

    try:
        max_steps = min(int(args.get("max_steps", DELEGATE_DEFAULT_MAX_STEPS)), 50)
    except (ValueError, TypeError):
        max_steps = DELEGATE_DEFAULT_MAX_STEPS

    logger.info(
        "[DELEGATE:%d] SPAWN — task=%.200s max_steps=%d parent_iter=%d",
        delegate_number, task, max_steps, iteration,
    )
    _delegate_log(config, {
        "event": "delegate_spawn",
        "delegate_number": delegate_number,
        "task": task[:500],
        "max_steps": max_steps,
        "parent_iteration": iteration,
        "parent_user_task": user_task[:300],
    })

    await emit(on_event, AgentEvent(EventType.DELEGATE_SPAWN, {
        "delegate_number": delegate_number,
        "delegate_task": task[:200],
        "max_steps": max_steps,
        "iteration": iteration,
        "step_id": str(args.get("step_id") or "").strip(),
    }))

    # Wall-clock timeout scales with the step budget (approx 30s/step including
    # possible extension), capped at 900s.
    _sub_timeout = min(30 * (max_steps + max(10, max_steps // 3)), 900)

    from .types import LoopConfig as _LC
    sub_config = _LC(
        max_iterations=max_steps,
        max_iterations_extension=max(10, max_steps // 3),  # ~33% extension if plan has pending steps
        max_total_iterations=max_steps + max(10, max_steps // 3),
        tool_timeout_seconds=config.tool_timeout_seconds,
        total_timeout_seconds=float(_sub_timeout - 30),  # inner guard fires before outer asyncio.wait_for
        max_timeout_extensions=0,  # sub-agents never extend their timeout
        context_window_tokens=config.context_window_tokens,
        reserve_tokens=config.reserve_tokens,
        keep_recent_tokens=min(config.keep_recent_tokens, config.context_window_tokens // 2),
        result_max_chars=config.result_max_chars,
        temperature=config.temperature,
        top_p=config.top_p,
        top_k=config.top_k,
        min_p=config.min_p,
        presence_penalty=config.presence_penalty,
        repetition_penalty=config.repetition_penalty,
        enable_parallel_tools=config.enable_parallel_tools,
        enable_delegation=False,
        session_log_dir=config.session_log_dir,
        delegate_adapter_name=config.delegate_adapter_name,
    )
    _delegate_model = config.delegate_adapter_name

    # Build sub-agent tools: delegates get execution tools only.
    # Excluded: plan (read-only substitute), todo (read-only substitute),
    # delegate/delegate_status (no sub-delegation).
    from nls.tools.agent_tools.plan import PlanReadOnlyTool
    sub_tools = {k: v for k, v in tools.items() if k not in _DELEGATE_EXCLUDED}
    plan_tool = tools.get("plan")
    if plan_tool and hasattr(plan_tool, "_workspace"):
        sub_tools["plan"] = PlanReadOnlyTool(plan_tool._workspace)
    todo_tool = tools.get("todo")
    if todo_tool and hasattr(todo_tool, "_store"):
        import importlib
        _todo_mod = importlib.import_module("nls.skills.bundled.todo-list.tool")
        sub_tools["todo"] = _todo_mod.TodoReadOnlyTool(todo_tool._store)

    # Build sub-agent context with task instructions
    _facts = ""
    if hooks.get_preflight_knowledge:
        try:
            _facts = hooks.get_preflight_knowledge(task) or ""
        except Exception:
            pass
    _facts = _merge_recipe_preflight(_facts, task, user_task)

    # Resolve project directory and clone tool CWDs (same mechanism as
    # run_delegate_detached) so file tools resolve inside the project dir.
    _pd = ""
    _plan_tool = tools.get("plan")
    if _plan_tool and hasattr(_plan_tool, "_store"):
        try:
            _active_plan = _plan_tool._store.find_active()
            if _active_plan and _active_plan.project_dir:
                _pd = _active_plan.project_dir
            elif hasattr(_plan_tool._store, "find_any_project_dir"):
                _pd = _plan_tool._store.find_any_project_dir()
        except Exception:
            pass

    _workspace_root = ""
    for _t in tools.values():
        _workspace_root = getattr(_t, "_cwd", "") or getattr(_t, "_workspace_root", "")
        if _workspace_root:
            break

    if _pd and _workspace_root:
        import copy as _copy
        from pathlib import Path as _Path
        from nls.tools.agent_tools import SharedCWD as _SharedCWD
        _pd_abs = _resolve_delegate_project_abs(_workspace_root, _pd)
        _pd_path = _Path(_pd_abs)
        if not _pd_path.exists():
            try:
                _pd_path.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        _delegate_cwd = _SharedCWD(_pd_abs)
        _FILE_TOOLS = {"read", "write", "edit", "grep", "glob", "list_dir",
                       "delete_file", "move_file", "semantic_search", "bash"}
        for _tname in _FILE_TOOLS:
            _orig = sub_tools.get(_tname)
            if _orig is None:
                continue
            try:
                _cloned = _copy.copy(_orig)
                _cloned._shared_cwd = _delegate_cwd
                if _tname == "bash" and hasattr(_cloned, "_cwd"):
                    _cloned._cwd = _pd_abs
                    _cloned._project_venv_bin = None
                    if hasattr(_cloned, "_isolated_env"):
                        _cloned._isolated_env = dict(_cloned._isolated_env)
                    # Share detached-process registry + UI callback with orchestrator bash.
                    _cloned._detached_records = _orig._detached_records
                    _cloned._on_processes_changed = getattr(
                        _orig, "_on_processes_changed", None,
                    )
                    _cloned._on_output = getattr(_orig, "_on_output", None)
                # Tag write/edit clones with delegate authorship for the ledger.
                if _tname in ("write", "edit") and hasattr(_cloned, "_ledger_meta"):
                    _cloned._ledger_meta = dict(_cloned._ledger_meta)
                    _cloned._ledger_meta.update({
                        "role": "delegate",
                        "delegate_index": delegate_number,
                        "wave": None,  # wave info not available in blocking path
                    })
                if _tname == "write":
                    _cloned._write_counts = {}
                    _cloned._block_full_rewrite_after_first = True
                if _tname == "read":
                    _cloned._reader_label = f"delegate #{delegate_number}"
                sub_tools[_tname] = _cloned
            except Exception:
                pass
        _pi = sub_tools.get("project_install")
        if _pi is not None:
            try:
                _cloned_pi = _copy.copy(_pi)
                _cloned_pi._shared_cwd = _delegate_cwd
                if _pd_abs:
                    _cloned_pi._cwd = _pd_abs
                sub_tools["project_install"] = _cloned_pi
            except Exception:
                pass
    elif _pd or not _workspace_root:
        logger.warning(
            "[DELEGATE] CWD not scoped to project dir — "
            "_pd=%r, _workspace_root=%r. Files will resolve "
            "from workspace root.",
            _pd, _workspace_root,
        )

    _cwd_info = ""
    if _pd and _workspace_root:
        _cwd_info = (
            f"\nIMPORTANT — Your working directory is pre-set to the project "
            f"folder. All relative paths in write/read/edit/glob resolve "
            f"inside the project directory automatically.\n"
        )

    _project_dir_info = ""
    if _pd:
        from nls.agentic.delegate_verification import (
            format_delegate_verification_block,
            format_project_directory_block,
        )
        _project_dir_info = (
            "\n"
            + format_project_directory_block(_pd)
            + "\n"
            + format_delegate_verification_block()
            + "\n"
        )

    # Build SubCryptex for this delegate — replaces the old static
    # _compose_delegate_cryptex_preset with a live ring-based memory.
    from nls.brain.sub_cryptex import SubCryptex
    from .types import _SUB_AGENT_SUPPLEMENT
    _parent_cryptex = None
    try:
        _wm_tool = tools.get("wm")
        if _wm_tool is not None:
            _parent_cryptex = getattr(_wm_tool, "_cryptex", None)
    except Exception:
        pass

    _budget_info = (
        f"\nITERATION BUDGET: {max_steps} tool-call rounds.\n"
        "PRIMARY GOAL: Deliver the full task — required artifacts on disk, "
        "verified once. Do not rush to exit early to save iterations.\n"
        "If stuck, blocked, or running low on budget, call escalate().\n"
    )

    _tech_stack_block = ""
    _file_ownership_block = ""
    if _plan_tool and hasattr(_plan_tool, "_store"):
        try:
            _active_plan = _plan_tool._store.find_active()
            if _active_plan is not None:
                from nls.agentic.wave_coordination import (
                    build_file_ownership_block,
                    build_tech_stack_block,
                    resolve_step_owned_paths,
                )
                _tech_stack_block = build_tech_stack_block(plan=_active_plan)
                _owned = resolve_step_owned_paths(None, _pd or "")
                _file_ownership_block = build_file_ownership_block(
                    delegate_number=delegate_number or 0,
                    owned_patterns=_owned,
                    peer_lines=[],
                )
        except Exception:
            pass

    _write_tool = sub_tools.get("write")
    _file_ledger_ref = getattr(_write_tool, "_ledger", None) if _write_tool else None

    _sub_cryptex = SubCryptex.spawn_from_parent(
        parent=_parent_cryptex,
        task=(
            f"You are a sub-agent. Complete this specific task:\n\n{task}\n\n"
            f"{_budget_info}"
            f"Parent task context: {user_task[:300]}"
        ),
        preflight_facts=_facts,
        cwd_info=_cwd_info,
        project_dir_info=_project_dir_info,
        sub_agent_supplement=_SUB_AGENT_SUPPLEMENT,
        context_window_tokens=config.context_window_tokens,
        tech_stack_block=_tech_stack_block,
        file_ownership_block=_file_ownership_block,
        file_ledger=_file_ledger_ref,
    )
    _gr = getattr(hooks, "guardrails_registry", None)
    if _gr is not None:
        from nls.tools.agent_tools.guardrails_registry import (
            inject_guardrails_into_cryptex,
            inject_guardrails_into_sub_cryptex,
        )
        inject_guardrails_into_sub_cryptex(_sub_cryptex, _gr)
        if _parent_cryptex is not None:
            _parent_cryptex._guardrails_registry = _gr  # type: ignore[attr-defined]
            inject_guardrails_into_cryptex(_parent_cryptex, _gr)
    _sub_cryptex._guardrails_registry = _gr  # type: ignore[attr-defined]
    if delegate_number is not None:
        _sub_cryptex._delegate_number = delegate_number  # type: ignore[attr-defined]

    # The initial system message is composed by SubCryptex; it guarantees
    # the task is always pinned at the top and never lost to overflow.
    _initial_ctx = _sub_cryptex.compose_context()
    sub_system = _initial_ctx[0]["content"] if _initial_ctx else (
        f"You are a sub-agent. Complete this specific task:\n\n{task}\n\n"
        + _cwd_info + _project_dir_info + _SUB_AGENT_SUPPLEMENT
    )

    # vLLM/NLS requires at least one user-role message ("No user query found"
    # if only system is present). Mirror the main loop: system + user task.
    sub_context = [
        {"role": "system", "content": sub_system},
        {
            "role": "user",
            "content": (
                "Execute the task above using tools. "
                "Prefer structured tool calls (not XML). "
                f"Task:\n{task}\n"
                f"{_budget_info}"
            ),
        },
    ]

    from .loop import run_loop
    sub_result = None
    _dlg_num = delegate_number
    _state_holder: list = []

    # Wrap on_event to tag every sub-agent event with sub_agent=True and
    # delegate_number so the frontend can route them to the delegate card
    # rather than rendering them as orchestrator events.
    async def _sub_on_event(event: "AgentEvent") -> None:
        if on_event is None:
            return
        tagged = AgentEvent(
            event.type,
            {**(event.data or {}), "sub_agent": True, "delegate_number": _dlg_num},
        )
        await on_event(tagged)

    _wrap_up = asyncio.Event()

    async def _wrap_up_monitor() -> None:
        """Set wrap_up when the sub-agent approaches its iteration limit."""
        _threshold = max(int(max_steps * 0.8), max_steps - 3)
        while True:
            await asyncio.sleep(15)
            _sh = _state_holder[0] if _state_holder else None
            if _sh and _sh.iteration >= _threshold:
                _wrap_up.set()
                logger.info(
                    "[DELEGATE:%d] wrap-up signal sent at iter %d/%d",
                    delegate_number, _sh.iteration, max_steps,
                )
                return

    _monitor_task = asyncio.create_task(_wrap_up_monitor())

    # Isolated hooks for delegate — prevents the parent's
    # transform_context from overwriting the sub-agent's task
    # instructions with the orchestrator's Cryptex output.
    from .bridge import LoopHooks as _LH
    _sub_hooks = _LH(
        get_preflight_knowledge=hooks.get_preflight_knowledge if hooks else None,
        on_tool_success=hooks.on_tool_success if hooks else None,
        on_tool_error=hooks.on_tool_error if hooks else None,
        log_event=hooks.log_event if hooks else None,
        transform_context=_sub_cryptex.make_transform_hook(),
        on_after_tool=_sub_cryptex.make_after_tool_hook(
            parent_hook=hooks.on_after_tool if hooks else None,
        ),
        on_compaction=_sub_cryptex.make_compaction_hook(),
    )
    # Attach mutable refs so the loop can update them (sub-agent's own,
    # not the parent's).
    _sub_hooks._render_mode_ref = ["executing"]  # type: ignore[attr-defined]
    _sub_hooks._loop_state_ref = {}  # type: ignore[attr-defined]
    _sub_hooks._sub_cryptex = _sub_cryptex  # type: ignore[attr-defined]

    try:
        _delegate_start = time.time()
        sub_result = await asyncio.wait_for(
            run_loop(
                context=sub_context,
                tools=sub_tools,
                config=sub_config,
                hooks=_sub_hooks,
                vllm_client=vllm_client,
                on_event=_sub_on_event,
                abort_signal=abort_signal,
                user_input=task,
                adapter_name=_delegate_model,
                enable_thinking=True,
                state_holder=_state_holder,
                wrap_up_signal=_wrap_up,
            ),
            timeout=_sub_timeout,
        )
        _delegate_elapsed = time.time() - _delegate_start
        summary = sub_result.final_response or "(no output)"

        # When the sub-agent hit a limit without producing a substantive
        # summary (just the generic "[Loop stopped: ...]" stub), enrich
        # the result with its cumulative actions so the orchestrator
        # knows what was actually accomplished.
        _sub_state = _state_holder[0] if _state_holder else None
        _is_stub = summary.startswith("[Loop stopped:") or summary == "(no output)"
        if _is_stub and _sub_state and _sub_state.cumulative_actions:
            _actions = _sub_state.cumulative_actions[-20:]
            summary += "\n\nWork performed before stopping:\n" + "\n".join(
                f"  - {a}" for a in _actions
            )
        _sub_loop_id = _state_holder[0].loop_id if _state_holder else "?"
        logger.info(
            "[DELEGATE:%d] COMPLETED — loop_id=%s exit=%s iters=%d "
            "tc=%d duration=%.1fs resp_len=%d resp_preview=%.300s",
            delegate_number, _sub_loop_id,
            sub_result.exit_reason, sub_result.iterations,
            sub_result.total_tool_calls, _delegate_elapsed,
            len(summary), summary[:300],
        )
        _delegate_log(config, {
            "event": "delegate_complete",
            "delegate_number": delegate_number,
            "sub_loop_id": _sub_loop_id,
            "exit_reason": sub_result.exit_reason,
            "iterations": sub_result.iterations,
            "total_tool_calls": sub_result.total_tool_calls,
            "duration_s": round(_delegate_elapsed, 1),
            "aborted": sub_result.aborted,
            "tools_used": sub_result.tools_used,
            "final_response_len": len(summary),
            "final_response_preview": summary[:1000],
        })
    except asyncio.TimeoutError:
        _partial = _state_holder[0] if _state_holder else None
        _p_iters = _partial.iteration if _partial else 0
        _p_tc = _partial.total_tool_calls if _partial else 0
        _p_resp = (_partial.final_response or "") if _partial else ""
        _p_loop_id = _partial.loop_id if _partial else "?"
        _p_actions = _partial.cumulative_actions[-10:] if _partial else []
        _p_errors = dict(_partial.tool_errors) if _partial else {}
        summary = (
            f"(sub-agent timed out after {_sub_timeout:.0f}s — "
            f"completed {_p_iters} iterations, {_p_tc} tool calls)"
        )
        if _p_resp:
            summary += f"\nPartial output:\n{_p_resp[:500]}"
        logger.warning(
            "[DELEGATE:%d] TIMEOUT — loop_id=%s timeout=%ds iters=%d "
            "tc=%d partial_resp_len=%d errors=%s last_actions=%s",
            delegate_number, _p_loop_id, _sub_timeout,
            _p_iters, _p_tc, len(_p_resp), _p_errors, _p_actions[-5:],
        )
        _delegate_log(config, {
            "event": "delegate_timeout",
            "delegate_number": delegate_number,
            "sub_loop_id": _p_loop_id,
            "timeout_seconds": _sub_timeout,
            "iterations": _p_iters,
            "total_tool_calls": _p_tc,
            "tool_errors": _p_errors,
            "last_actions": _p_actions,
            "partial_response_len": len(_p_resp),
            "partial_response_preview": _p_resp[:500],
        })
        await emit(on_event, AgentEvent(EventType.TOOL_END, {
            "tool_name": "delegate",
            "is_error": True,
            "result_preview": summary[:200],
            "iteration": iteration,
            "sub_agent": True,
            "delegate_number": delegate_number,
        }))
    except Exception as exc:
        _p_loop_id = _state_holder[0].loop_id if _state_holder else "?"
        summary = f"(sub-agent error: {exc})"
        logger.error(
            "[DELEGATE:%d] ERROR — loop_id=%s exc=%s",
            delegate_number, _p_loop_id, exc, exc_info=True,
        )
        _delegate_log(config, {
            "event": "delegate_error",
            "delegate_number": delegate_number,
            "sub_loop_id": _p_loop_id,
            "error": str(exc),
        })
        await emit(on_event, AgentEvent(EventType.TOOL_END, {
            "tool_name": "delegate",
            "is_error": True,
            "result_preview": summary[:200],
            "iteration": iteration,
            "sub_agent": True,
            "delegate_number": delegate_number,
        }))

    _monitor_task.cancel()
    try:
        await _monitor_task
    except asyncio.CancelledError:
        pass

    # Prefer sub_result counts; fall back to live LoopState on timeout/error.
    if sub_result:
        _dlg_iters = sub_result.iterations
        _dlg_tc = sub_result.total_tool_calls
    elif _state_holder:
        _dlg_iters = _state_holder[0].iteration
        _dlg_tc = _state_holder[0].total_tool_calls
    else:
        _dlg_iters = _dlg_tc = 0

    _dlg_aborted = (
        sub_result is None or bool(getattr(sub_result, "aborted", False))
    )
    await emit(on_event, AgentEvent(EventType.DELEGATE_COMPLETE, {
        "delegate_number": delegate_number,
        "delegate_task": task[:200],
        "iterations": _dlg_iters,
        "tool_calls": _dlg_tc,
        "summary": summary[:200],
        "aborted": _dlg_aborted,
        "iteration": iteration,
    }))

    # Compress SubCryptex into a knowledge digest for the orchestrator
    _digest_text = ""
    try:
        import json as _json_d
        _digest = await _sub_cryptex.compress_to_digest(vllm_client)
        if _digest:
            _digest_text = (
                "\n\n[DELEGATE KNOWLEDGE DIGEST]\n"
                + _json_d.dumps(_digest, indent=2)
                + "\n[END DIGEST]"
            )
    except Exception as _dex:
        logger.warning("[DELEGATE:%d] digest compression failed: %s", delegate_number, _dex)

    if len(summary) > config.result_max_chars:
        summary = summary[:config.result_max_chars] + "\n... (truncated)"

    _exit = getattr(sub_result, "exit_reason", "") if sub_result else "no_result"
    if _dlg_aborted:
        _status_label = f"Sub-agent FAILED (exit: {_exit}, {_dlg_iters} steps, {_dlg_tc} tool calls)"
    else:
        _status_label = f"Sub-agent completed ({_dlg_iters} steps, {_dlg_tc} tool calls)"
    _tool_result_content = f"{_status_label}:\n\n{summary}{_digest_text}"
    logger.info(
        "[DELEGATE:%d] RESULT→ORCHESTRATOR — iters=%d tc=%d aborted=%s exit=%s "
        "content_len=%d content_preview=%.300s",
        delegate_number, _dlg_iters, _dlg_tc, _dlg_aborted, _exit,
        len(_tool_result_content), _tool_result_content[:300],
    )
    _delegate_log(config, {
        "event": "delegate_result_to_orchestrator",
        "delegate_number": delegate_number,
        "iterations": _dlg_iters,
        "tool_calls": _dlg_tc,
        "aborted": _dlg_aborted,
        "exit_reason": _exit,
        "result_content_len": len(_tool_result_content),
        "result_content_preview": _tool_result_content[:1000],
    })
    return ToolResult(content=_tool_result_content, is_error=_dlg_aborted)


# -------------------------------------------------------------------
# Cognitive digest
# -------------------------------------------------------------------

async def _extract_digest(
    vllm_client: Any,
    tool_name: str,
    tool_args: dict,
    tool_output: str,
    user_task: str,
) -> dict | None:
    """Extract a task-scoped digest from a large tool result."""
    source = (
        tool_args.get("path", "")
        or tool_args.get("url", "")
        or tool_args.get("command", "")[:80]
        or tool_name
    )
    prompt = (
        f"USER TASK: {user_task[:300]}\n\n"
        f"TOOL: {tool_name}\n"
        f"SOURCE: {source}\n\n"
        f"CONTENT ({len(tool_output)} chars):\n"
        f"{tool_output[:3000]}"
    )
    for attempt in range(2):
        try:
            result = await asyncio.wait_for(
                vllm_client.generate(
                    messages=[
                        {"role": "system", "content": _COGNITIVE_DIGEST_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    adapter_name=None,
                    max_tokens=256,
                    temperature=0.1,
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                ),
                timeout=45,
            )
            text = (result.text or "").strip()
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                parsed = json.loads(text[start : end + 1])
                summary = parsed.get("summary", "")
                insights = parsed.get("insights", [])
                if isinstance(insights, list) and summary:
                    return {
                        "summary": str(summary)[:400],
                        "insights": [str(i)[:180] for i in insights[:5]],
                        "source": str(parsed.get("source", source))[:200],
                    }
            break
        except TimeoutError:
            if attempt == 0:
                continue
            logger.warning("Cognitive digest failed after retry", exc_info=True)
        except Exception:
            logger.warning("Cognitive digest failed", exc_info=True)
            break
    return None


# -------------------------------------------------------------------
# Independence classification
# -------------------------------------------------------------------

def classify_independence(
    tool_calls: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Split tool calls into independent (parallelizable) and dependent (sequential).

    Heuristic: all calls from the same LLM turn that don't reference each
    other's call IDs or share state are independent.  For NLS typical usage
    (multiple reads, multiple bash) most will be independent.
    """
    if len(tool_calls) <= 1:
        return tool_calls, []

    all_ids = {tc.get("id", "") for tc in tool_calls}
    independent = []
    dependent = []

    for tc in tool_calls:
        fn = tc.get("function", {})
        args_str = fn.get("arguments", "")
        refs_other = any(
            oid in args_str
            for oid in all_ids
            if oid and oid != tc.get("id", "")
        )
        if refs_other:
            dependent.append(tc)
        else:
            independent.append(tc)

    return independent, dependent


# -------------------------------------------------------------------
# Single tool execution
# -------------------------------------------------------------------

async def _execute_single(
    tool: Any,
    call: dict,
    config: LoopConfig,
    state: LoopState,
    tools: dict[str, Any],
    *,
    abort_signal: asyncio.Event | None = None,
    on_event: Callable | None = None,
    hooks: LoopHooks | None = None,
    delegate_manager: Any | None = None,
) -> ToolResult:
    """Execute one tool with timeout and hook integration."""
    fn = call.get("function", {})
    name = fn.get("name", "unknown")
    args_str = fn.get("arguments", "{}")
    call_id = call.get("id", "")

    args = _parse_tool_args(args_str)

    if name == "task_complete" and config.enable_delegation:
        _bg_running = state.delegate_count > 0
        if delegate_manager is not None:
            try:
                _bg_running = delegate_manager.has_active_delegates()
            except Exception:
                pass
        if (
            _bg_running
            and state.active_mode in (AgentMode.MONITORING, AgentMode.DELEGATING)
        ):
            return ToolResult(
                content=(
                    "BLOCKED: Delegates are still running. Use "
                    "await_delegates(summary='...') to exit monitoring — "
                    "NOT task_complete (that means ALL work is finished)."
                ),
                is_error=True,
                details={"blocked": True, "prefer_await_delegates": True},
            )

    if getattr(state, "must_delegate_before_impl", False):
        _pre_block = pre_delegate_block_message(
            name,
            args,
            active_mode=state.active_mode,
            block_reason=getattr(state, "pre_delegate_reason", None) or None,
            orchestrator_recovery=getattr(
                state, "orchestrator_recovery", False,
            ),
            orchestration_profile=getattr(state, "orchestration_profile", None),
        )
        if _pre_block:
            result = ToolResult(
                content=_pre_block,
                is_error=True,
                details={"blocked": True, "pre_delegate": True},
            )
            await emit(on_event, AgentEvent(
                EventType.TOOL_END,
                {
                    "tool_name": name,
                    "call_id": call_id,
                    "result": result.content,
                    "blocked": True,
                    "is_error": True,
                },
            ))
            return result

    _orch_block = block_tool_call(
        name,
        args,
        state,
        state.active_mode,
        delegate_manager,
        has_pending_escalation=getattr(state, "has_pending_escalation", False),
        hooks=hooks,
        all_unlocked=set(tools.keys()) | set(state.unlocked_tools),
    )
    if _orch_block:
        from nls.agentic.profile_depth_policy import enrich_profile_blocked_message

        _orch_block = enrich_profile_blocked_message(
            name,
            _orch_block,
            state,
            mode=state.active_mode,
            all_unlocked=frozenset(set(tools.keys()) | set(state.unlocked_tools)),
        )
        result = ToolResult(
            content=_orch_block,
            is_error=True,
            details={"blocked": True, "orchestration_policy": True},
        )
        await emit(on_event, AgentEvent(
            EventType.TOOL_END,
            {
                "tool_name": name,
                "call_id": call_id,
                "result": result.content,
                "blocked": True,
                "is_error": True,
            },
        ))
        return result

    await emit(on_event, AgentEvent(
        EventType.TOOL_START,
        {"tool_name": name, "arguments": args, "call_id": call_id, "iteration": state.iteration},
    ))

    # Outbound notification gate (ledger + lifecycle)
    if hooks and hooks.outbound_check:
        try:
            skip_msg = hooks.outbound_check(name, args)
            if skip_msg:
                result = ToolResult(
                    content=skip_msg,
                    is_error=False,
                    details={"skipped": True, "outbound_gate": True},
                )
                await emit(on_event, AgentEvent(
                    EventType.TOOL_END,
                    {
                        "tool_name": name,
                        "call_id": call_id,
                        "result": result.content,
                        "skipped": True,
                        "is_error": False,
                    },
                ))
                return result
        except Exception:
            logger.debug("outbound_check failed", exc_info=True)

    if name in OUTBOUND_TOOLS:
        args = strip_outbound_control_args(args)

    # Hook: on_before_tool (can block)
    if hooks and hooks.on_before_tool:
        try:
            allowed = hooks.on_before_tool(name, args)
            if allowed is False:
                result = ToolResult(
                    content=f"Tool '{name}' blocked by permission hook.",
                    is_error=True,
                    blocked_by_hook=True,
                )
                await emit(on_event, AgentEvent(
                    EventType.TOOL_END,
                    {"tool_name": name, "call_id": call_id, "result": result.content, "blocked": True, "is_error": True},
                ))
                return result
        except Exception:
            pass

    # Determine the outer timeout for asyncio.wait_for.
    # Some tools need more time than the default 30s:
    #   plan: dependency micro-inference calls vLLM (can take 60-90s for big plans)
    #   project_install / server_install: pip/npm can take minutes on cold venv
    #   bash: agent can request a custom timeout
    _timeout = config.tool_timeout_seconds
    _PLAN_TIMEOUT = 90
    _INSTALL_TIMEOUT = 305  # project_install pip -r uses 300s internally
    _SERVER_INSTALL_TIMEOUT = 185  # server_install pip uses 180s internally
    _BASH_MAX_TIMEOUT = 300
    if name == "plan":
        _timeout = _PLAN_TIMEOUT
    elif name == "project_install":
        _timeout = _INSTALL_TIMEOUT
    elif name == "server_install":
        _timeout = _SERVER_INSTALL_TIMEOUT
    elif name == "bash":
        _agent_requested = None
        try:
            _agent_requested = int(args.get("timeout", 0)) or None
        except (ValueError, TypeError):
            pass

        if state.coordinator_mode:
            # Coordinator mode: cap to COORDINATOR_BASH_TIMEOUT_S
            _effective = COORDINATOR_BASH_TIMEOUT_S
            args["timeout"] = COORDINATOR_BASH_TIMEOUT_S
        elif _agent_requested:
            # Agent explicitly requested a timeout — honour it (capped)
            _effective = min(_agent_requested, _BASH_MAX_TIMEOUT)
        else:
            # No explicit timeout — use a reasonable default for bash
            _effective = max(config.tool_timeout_seconds, 120)

        _timeout = _effective + 5  # small buffer over internal timeout

    try:
        result = await asyncio.wait_for(
            tool.execute(args, signal=abort_signal),
            timeout=_timeout,
        )
    except asyncio.TimeoutError:
        _coord_hint = ""
        if state.coordinator_mode and name == "bash":
            _coord_hint = (
                f" Coordinator bash is capped at "
                f"{COORDINATOR_BASH_TIMEOUT_S}s — delegate "
                "long-running commands to a sub-agent."
            )
        result = ToolResult(
            content=f"Tool '{name}' timed out after {_timeout:.0f}s.{_coord_hint}",
            is_error=True,
        )
    except Exception as exc:
        result = ToolResult(
            content=f"Tool '{name}' failed: {str(exc)[:500]}",
            is_error=True,
        )

    # Truncate large results.  Tools can set details["requested_max_chars"]
    # to override the default (e.g. read tool's max_chars parameter).
    _max = config.result_max_chars
    _per_result = (result.details or {}).get("requested_max_chars")
    if _per_result and isinstance(_per_result, (int, float)):
        _max = min(int(_per_result), 100_000)
    if len(result.content) > _max:
        result.content = (
            result.content[: _max]
            + f"\n\n[...truncated {len(result.content) - _max} chars...]"
        )

    # Hook: on_after_tool
    if hooks and hooks.on_after_tool:
        try:
            hooks.on_after_tool(name, args, result)
        except Exception:
            pass

    # Hook: tool success/error for cognitive tracking
    if hooks:
        if result.is_error and hooks.on_tool_error:
            try:
                hooks.on_tool_error(name, args, result)
            except Exception:
                pass
        elif not result.is_error and hooks.on_tool_success:
            try:
                hooks.on_tool_success(name, args, result)
            except Exception:
                pass

    await emit(on_event, AgentEvent(
        EventType.TOOL_END,
        {
            "tool_name": name,
            "call_id": call_id,
            "arguments": args,
            "is_error": result.is_error,
            "result_preview": result.content[:200],
            "iteration": state.iteration,
            **({"details": result.details} if result.details else {}),
        },
    ))

    return result


# -------------------------------------------------------------------
# Main execution function
# -------------------------------------------------------------------

async def _post_process_result(
    name: str,
    args: dict,
    result: ToolResult,
    config: LoopConfig,
    hooks: LoopHooks | None,
    vllm_client: Any | None,
    user_task: str,
    digest_count_ref: list[int],
) -> ToolResult:
    """Store cognitive digests in WM without mutating tool output.

    Large read/fetch/search results are summarized into working memory /
    Cryptex for long-term recall.  The tool result returned to the model
    is always the original content (subject to each tool's own truncation).
    Bash/list_dir output is never digested here.
    """
    if (
        config.enable_cognitive_digest
        and not result.is_error
        and name in _DIGEST_TOOLS
        and digest_count_ref[0] < _DIGEST_MAX_PER_LOOP
        and vllm_client is not None
        and hooks is not None
        and hooks.wm_upsert_digest is not None
    ):
        digest_threshold = 800 if name == "read" else _DIGEST_MIN_CHARS
        if len(result.content) > digest_threshold:
            source = (
                args.get("path", "")
                or args.get("url", "")
                or args.get("query", "")[:80]
                or name
            )
            digest = await _extract_digest(
                vllm_client, name, args, result.content, user_task,
            )
            if digest:
                try:
                    hooks.wm_upsert_digest(
                        f"Digest.{source}",
                        digest["summary"],
                        ", ".join(digest["insights"]),
                        source,
                    )
                except Exception:
                    pass
                digest_count_ref[0] += 1
                if result.details is None:
                    result.details = {}
                result.details["digest_stored"] = True
                result.details["digest_source"] = source

    if hooks and hooks.ans_tool_learning and not result.is_error:
        try:
            hooks.ans_tool_learning(name, args, result.content, user_task)
        except Exception:
            pass

    if (
        hooks
        and hooks.outbound_record
        and not result.is_error
        and name in OUTBOUND_TOOLS
    ):
        try:
            hooks.outbound_record(name, args)
        except Exception:
            logger.debug("outbound_record failed", exc_info=True)

    return result


def _can_auto_launch_team(
    team_manager: Any,
    delegate_manager: DelegateManager,
    next_team_id: str,
) -> bool:
    """True when the next-wave team can launch without conflicting work."""
    from .orchestration_policy import should_auto_launch_next_wave

    ok, _reason = should_auto_launch_next_wave(
        team_manager, delegate_manager, next_team_id,
    )
    return ok


async def _launch_team_delegates(
    team_manager: Any,
    team_id: str,
    delegate_manager: DelegateManager,
    tools: dict[str, Any],
    config: "LoopConfig",
    state: "LoopState",
    hooks: "LoopHooks",
    vllm_client: Any,
    on_event: Callable | None,
    abort_signal: asyncio.Event | None,
    user_task: str,
) -> ToolResult:
    """Spawn delegates for a Team via TeamManager.launch_team_async.

    Mirrors the detached delegate spawn path but sources specs from
    the Team's member list instead of raw delegate tool-calls.
    Also wires up the check-back scheduler job and auto-cancel callback.
    """
    team = team_manager.load(team_id)
    if team is None:
        return ToolResult(content=f"Team '{team_id}' not found.", is_error=True)

    try:
        launched = await team_manager.launch_team_async(
            team_id,
            run_delegate_fn=run_delegate_detached,
            fn_kwargs=dict(
                tools=tools,
                config=config,
                hooks=hooks,
                vllm_client=vllm_client,
                on_event=on_event,
                abort_signal=abort_signal,
                iteration=state.iteration,
                user_task=user_task,
            ),
        )
    except Exception as exc:
        logger.exception("[EXEC] team launch failed for %s", team_id)
        return ToolResult(content=f"Team launch failed: {exc}", is_error=True)

    if launched is None:
        _team_status = team.status if team else "unknown"
        _has_dm = team_manager._delegate_manager is not None
        reasons = []
        if _team_status not in ("created", "paused"):
            reasons.append(f"team status is '{_team_status}' (must be 'created' or 'paused')")
        if not _has_dm:
            reasons.append("no delegate manager available (delegation may be disabled)")
        _why = "; ".join(reasons) if reasons else "unknown reason"
        return ToolResult(
            content=f"Team '{team_id}' could not be launched: {_why}.",
            is_error=True,
        )

    _running = [m for m in launched.members if m.status == "running"]
    _queued = [m for m in launched.members if m.status == "pending"]

    for m in _running:
        await emit(on_event, AgentEvent(EventType.DELEGATE_SPAWN, {
            "delegate_number": m.delegate_number,
            "delegate_task": m.task[:200],
            "max_steps": 25,
            "iteration": state.iteration,
            "team_id": launched.id,
            "wave_attempt": launched.wave_attempt,
            "team_name": launched.name,
            "step_id": m.step_id,
            "member_idx": launched.members.index(m),
        }))

    member_labels = [
        f"  #{m.delegate_number}: {m.task[:80]} [RUNNING]" for m in _running
    ] + [
        f"  #{m.delegate_number}: {m.task[:80]} [QUEUED]" for m in _queued
    ]
    _queue_note = ""
    if _queued:
        _queue_note = (
            f"\n\n({len(_queued)} member(s) queued — they will auto-spawn "
            f"as running delegates complete.)"
        )
    _spawn_msg = (
        f"Team {launched.name} [{launched.id}] launched! "
        f"{len(_running)} sub-agent(s) spawned now"
        + (f", {len(_queued)} queued" if _queued else "")
        + f" (batch {launched.batch_id}):\n"
        + "\n".join(member_labels)
        + _queue_note
        + "\n\nYou are the engineering manager — wave is now executing:\n"
        "- Optional: communicate(status) — one stakeholder update.\n"
        "- Required: await_delegates(summary='Wave "
        + str(getattr(launched, "wave_index", 0))
        + " executing — return on escalation/completion').\n"
        "- Your job now: end this turn. You wake to steer stuck members, "
        "review deliverables, and advance the Kanban — not to IC or poll.\n"
        "- Do NOT wait(60+), inspect loops, write files, or plan(update)."
    )

    _record_phase = None
    if hooks and hooks.wm_orch_set_coordinator_phase:
        _record_phase = hooks.wm_orch_set_coordinator_phase
    on_team_launched(state, launched.id, record_phase=_record_phase)
    team_manager.clear_pending_auto_launch(launched.id)
    if hooks and hooks.wm_orch_record_decision:
        try:
            hooks.wm_orch_record_decision(
                "team_launch",
                f"Launched {launched.name} [{launched.id}]",
                team_id=launched.id,
            )
        except Exception:
            pass
    state.active_mode = AgentMode.MONITORING
    invalidate_tool_policy_cache(state)

    # Schedule periodic check-back (same pattern as direct delegate spawn)
    _checkback_job_name = f"team_checkback_{launched.id}"
    _scheduler_tool = tools.get("scheduler")
    _sched_mgr = getattr(_scheduler_tool, "_manager", None)
    if _sched_mgr is not None:
        from nls.tools.agent_tools.scheduler import ScheduledJob as _SJ
        _agent_id = getattr(config, "agent_id", "")
        _routing = (
            f"[AGENT_MSG|agent_id={_agent_id}|batch={launched.batch_id}] "
            if _agent_id else ""
        )
        _checkback_msg = (
            f"{_routing}[TEAM CHECK-BACK — EM REVIEW] "
            f"Team {launched.name} [{launched.id}]\n\n"
            "Scheduled management check-in. Cryptex WM holds board state.\n\n"
            f"1) team(inspect, team_id='{launched.id}') — holistic view\n"
            "2) If member stuck → team(hint) with ONE concrete next step\n"
            "3) If wave terminal → switch_mode(evaluating), review outputs, "
            "update plan/Kanban, team(advance)\n"
            "4) If wave running cleanly → await_delegates(summary='...')\n\n"
            "Do NOT idle-poll. If nothing changed since last check-in, "
            "await_delegates immediately.\n"
            f"If advancing: scheduler(remove, name='{_checkback_job_name}')."
        )
        _checkback_secs = checkback_interval_seconds(delegates_active=True)
        try:
            _sched_mgr.add_job(_SJ(
                name=_checkback_job_name,
                schedule_type="interval",
                interval_seconds=_checkback_secs,
                action="agent_message",
                action_message=_checkback_msg,
                owner="team_manager",
                owner_agent_id=_agent_id,
            ))
            logger.info(
                "[EXEC] team check-back job scheduled: '%s' (every %.0fs)",
                _checkback_job_name, _checkback_secs,
            )
        except Exception as _sce:
            logger.warning("[EXEC] team check-back schedule failed: %s", _sce)

        # Auto-cancel check-back when batch finishes
        _captured_sched_mgr = _sched_mgr
        _captured_job_name = _checkback_job_name
        _captured_tm = team_manager

        def _auto_cancel_team_checkback(
            bid: str,
            results: list,
            _sm=_captured_sched_mgr,
            _jn=_captured_job_name,
        ) -> None:
            cancelled = _sm.remove_job(_jn)
            if cancelled:
                logger.info(
                    "[EXEC] auto-cancelled team check-back '%s' (batch %s done)",
                    _jn, bid,
                )

        _existing_cb = delegate_manager._on_batch_complete
        if _existing_cb is not None:
            async def _chained_cb(
                bid: str, results: list,
                _orig=_existing_cb, _new=_auto_cancel_team_checkback,
            ) -> None:
                _new(bid, results)
                cb_result = _orig(bid, results)
                if asyncio.iscoroutine(cb_result):
                    await cb_result
            delegate_manager._on_batch_complete = _chained_cb
        else:
            delegate_manager._on_batch_complete = _auto_cancel_team_checkback

    state.delegate_count += len(launched.members)
    return ToolResult(content=_spawn_msg)


async def try_auto_launch_pending_wave(
    team_manager: Any,
    delegate_manager: DelegateManager,
    tools: dict[str, Any],
    config: "LoopConfig",
    state: "LoopState",
    hooks: "LoopHooks",
    vllm_client: Any,
    on_event: Callable | None,
    abort_signal: asyncio.Event | None,
    user_task: str,
) -> bool:
    """Launch a pending next-wave team after auto-reconcile (policy-guarded).

    Returns True if delegates were spawned. On block/failure, re-queues pending
    and schedules a compact EM wake with a launch breadcrumb.
    """
    from .orchestration_policy import should_auto_launch_next_wave

    pending = team_manager.pop_pending_auto_launch()
    if pending is None:
        return False

    ok, block_reason = should_auto_launch_next_wave(
        team_manager, delegate_manager, pending.team_id,
    )
    if not ok:
        team = team_manager.load(pending.team_id)
        if team is not None:
            team_manager.offer_pending_auto_launch(team, pending.reason)
        team_manager.schedule_pending_launch_wake(
            pending.team_id, block_reason, reconcile_reason=pending.reason,
        )
        return False

    result = await _launch_team_delegates(
        team_manager,
        pending.team_id,
        delegate_manager,
        tools,
        config,
        state,
        hooks,
        vllm_client,
        on_event,
        abort_signal,
        user_task,
    )
    if result.is_error:
        team = team_manager.load(pending.team_id)
        if team is not None:
            team_manager.offer_pending_auto_launch(team, pending.reason)
        team_manager.schedule_pending_launch_wake(
            pending.team_id,
            (result.content or "launch failed")[:300],
            reconcile_reason=pending.reason,
        )
        return False

    logger.info(
        "[EXEC] auto-launched pending wave %s (reason=%s)",
        pending.team_id, pending.reason,
    )
    return True


async def run_delegate_detached(
    *,
    spec: DelegateSpec,
    wrap_up_signal: asyncio.Event,
    tools: dict[str, Any],
    config: "LoopConfig",
    hooks: "LoopHooks",
    vllm_client: Any,
    on_event: Callable | None,
    abort_signal: asyncio.Event | None,
    iteration: int,
    user_task: str,
    state_holder_out: list | None = None,
    hint_queue: asyncio.Queue | None = None,
    on_escalation: Callable | None = None,
    sub_cryptex_holder_out: list | None = None,
    hint_ack_holder_out: list | None = None,
) -> tuple[str, Any]:
    """Run a sub-agent loop for the DelegateManager.

    Returns ``(summary, loop_result_or_none)``.  Used as the
    ``run_delegate_fn`` passed to ``DelegateManager.spawn_batch()``.
    """
    delegate_number = spec.delegate_number
    task = spec.task
    max_steps = spec.max_steps

    _sub_timeout = min(30 * (max_steps + max(10, max_steps // 3)), 900)
    if on_escalation is not None:
        # Escalation can extend iterations (and inner timeout) multiple
        # times.  The outer asyncio.wait_for must never be the binding
        # constraint — inner loop guards (iteration limit, stall detection,
        # inner timeout + extensions) handle proper termination.  This is
        # purely a safety net for truly hung delegates.
        _sub_timeout = 3600

    # Wrap escalation callback to bind the delegate number
    _escalation_cb = None
    if on_escalation is not None:
        _dlg = delegate_number
        async def _escalation_cb(reason, _state, context_summary):
            await on_escalation(_dlg, reason, context_summary)

    from .types import LoopConfig as _LC
    _has_escalation = _escalation_cb is not None
    _ext_budget = max(10, max_steps // 3)
    # Inner timeout: scales with base iteration budget.  For escalation-
    # enabled delegates, extensions bump this dynamically in _try_escalate.
    _inner_timeout = float(min(30 * (max_steps + _ext_budget), 900) - 30)
    sub_config = _LC(
        max_iterations=max_steps,
        max_iterations_extension=_ext_budget,
        max_total_iterations=max_steps + _ext_budget * 3 if _has_escalation else max_steps + _ext_budget,
        tool_timeout_seconds=config.tool_timeout_seconds,
        total_timeout_seconds=_inner_timeout,
        max_timeout_extensions=3 if _has_escalation else 0,
        consecutive_error_limit=3 if _has_escalation else 5,
        context_window_tokens=config.context_window_tokens,
        reserve_tokens=config.reserve_tokens,
        keep_recent_tokens=min(config.keep_recent_tokens, config.context_window_tokens // 2),
        result_max_chars=config.result_max_chars,
        temperature=config.temperature,
        top_p=config.top_p,
        top_k=config.top_k,
        min_p=config.min_p,
        presence_penalty=config.presence_penalty,
        repetition_penalty=config.repetition_penalty,
        enable_parallel_tools=config.enable_parallel_tools,
        enable_delegation=False,
        session_log_dir=config.session_log_dir,
        escalate_on_limit=_has_escalation,
        on_escalation=_escalation_cb,
        delegate_adapter_name=config.delegate_adapter_name,
    )
    _delegate_model = config.delegate_adapter_name

    _state_holder: list = (
        state_holder_out if state_holder_out is not None else []
    )

    async def _notify_repeated_write(path_str: str, count: int) -> tuple[str, bool]:
        if _escalation_cb is None:
            return (
                "Third full rewrite — no orchestrator channel. "
                "Use edit() or escalate().",
                False,
            )
        _ls = _state_holder[0] if _state_holder else None
        _summary = f"Full rewrite #{count} of {path_str}"
        if _ls is not None:
            _summary += (
                f"\niteration: {_ls.iteration}/{sub_config.max_iterations}"
            )
            if _ls.files_written:
                _summary += "\nfiles_written:\n" + "\n".join(
                    f"- {p}" for p in _ls.files_written[-8:]
                )
        return await _await_orchestrator_escalation(
            reason=f"repeated_write:{path_str}",
            state=_ls,
            context_summary=_summary,
            config=sub_config,
            copilot_queue=hint_queue,
            esc_cb=_escalation_cb,
        )

    from nls.tools.agent_tools.plan import PlanReadOnlyTool
    sub_tools = {k: v for k, v in tools.items() if k not in _DELEGATE_EXCLUDED}
    plan_tool = tools.get("plan")
    if plan_tool and hasattr(plan_tool, "_workspace"):
        sub_tools["plan"] = PlanReadOnlyTool(plan_tool._workspace)
    todo_tool = tools.get("todo")
    if todo_tool and hasattr(todo_tool, "_store"):
        import importlib
        _todo_mod = importlib.import_module("nls.skills.bundled.todo-list.tool")
        sub_tools["todo"] = _todo_mod.TodoReadOnlyTool(todo_tool._store)

    _facts = ""
    if hooks.get_preflight_knowledge:
        try:
            _facts = hooks.get_preflight_knowledge(task) or ""
        except Exception:
            pass
    _facts = _merge_recipe_preflight(_facts, task, user_task)

    # Resolve project directory from active plan (or any prior plan)
    _project_dir_info = ""
    _pd = ""
    _plan_tool = tools.get("plan")
    if _plan_tool and hasattr(_plan_tool, "_store"):
        try:
            _active_plan = _plan_tool._store.find_active()
            if _active_plan and _active_plan.project_dir:
                _pd = _active_plan.project_dir
            elif hasattr(_plan_tool._store, "find_any_project_dir"):
                _pd = _plan_tool._store.find_any_project_dir()
        except Exception:
            pass

    # Pre-set delegate's working directory to the project folder so that
    # ALL file tools (write, read, edit, glob, etc.) resolve relative
    # paths inside it — even before the sub-agent runs `cd` in bash.
    _workspace_root = ""
    for _t in tools.values():
        _workspace_root = getattr(_t, "_cwd", "") or getattr(_t, "_workspace_root", "")
        if _workspace_root:
            break
    if _pd and _workspace_root:
        import copy as _copy
        from pathlib import Path as _Path
        from nls.tools.agent_tools import SharedCWD as _SharedCWD
        _pd_abs = _resolve_delegate_project_abs(_workspace_root, _pd)
        # Ensure the project directory exists before setting it as CWD.
        # Wave-0 delegates whose task is "create project scaffolding" would
        # otherwise fail on every bash call because the CWD doesn't exist yet.
        _pd_path = _Path(_pd_abs)
        if not _pd_path.exists():
            try:
                _pd_path.mkdir(parents=True, exist_ok=True)
                logger.info("Created project directory for delegate CWD: %s", _pd_abs)
            except OSError as _e:
                logger.warning("Could not create delegate CWD %s: %s", _pd_abs, _e)
        _delegate_cwd = _SharedCWD(_pd_abs)
        _FILE_TOOLS = {"read", "write", "edit", "grep", "glob", "list_dir",
                       "delete_file", "move_file", "semantic_search", "bash"}
        for _tname in _FILE_TOOLS:
            _orig = sub_tools.get(_tname)
            if _orig is None:
                continue
            try:
                _cloned = _copy.copy(_orig)
                _cloned._shared_cwd = _delegate_cwd
                if _tname == "bash" and hasattr(_cloned, "_cwd"):
                    _cloned._cwd = _pd_abs
                    # Reset venv cache so the delegate re-resolves with
                    # its own _cwd (project dir, not workspace root).
                    _cloned._project_venv_bin = None
                    # Deep-copy the env dict so delegate PATH mutations
                    # don't bleed into the orchestrator's env.
                    if hasattr(_cloned, "_isolated_env"):
                        _cloned._isolated_env = dict(_cloned._isolated_env)
                    # Share detached-process registry + UI callback with orchestrator bash.
                    _cloned._detached_records = _orig._detached_records
                    _cloned._on_processes_changed = getattr(
                        _orig, "_on_processes_changed", None,
                    )
                    _cloned._on_output = getattr(_orig, "_on_output", None)
                # Tag write/edit clones with delegate authorship for the ledger.
                if _tname in ("write", "edit") and hasattr(_cloned, "_ledger_meta"):
                    _cloned._ledger_meta = dict(_cloned._ledger_meta)
                    _cloned._ledger_meta.update({
                        "role": "delegate",
                        "delegate_index": spec.delegate_number,
                        "wave": spec.wave,
                    })
                if _tname == "write":
                    _cloned._write_counts = {}
                if _tname == "write" and _escalation_cb is not None:
                    _cloned._on_repeated_write_escalation = _notify_repeated_write
                if _tname == "write":
                    _cloned._block_full_rewrite_after_first = True
                if _tname == "read":
                    _cloned._reader_label = f"delegate #{spec.delegate_number}"
                sub_tools[_tname] = _cloned
            except Exception:
                pass
        _pi = sub_tools.get("project_install")
        if _pi is not None:
            try:
                _cloned_pi = _copy.copy(_pi)
                _cloned_pi._shared_cwd = _delegate_cwd
                if _pd_abs:
                    _cloned_pi._cwd = _pd_abs
                sub_tools["project_install"] = _cloned_pi
            except Exception:
                pass
        _parent_browser = tools.get("browser")
        if _parent_browser is not None:
            sub_tools["browser"] = _parent_browser

    _cwd_info = ""
    if _pd and _workspace_root:
        _cwd_info = (
            f"\nIMPORTANT — Your working directory is pre-set to the project "
            f"folder. All relative paths in write/read/edit/glob resolve "
            f"inside the project directory automatically.\n"
        )

    if _pd:
        from nls.agentic.delegate_verification import (
            format_delegate_verification_block,
            format_project_directory_block,
        )
        _project_dir_info = (
            "\n"
            + format_project_directory_block(_pd)
            + "\n"
            + format_delegate_verification_block()
            + "\n"
        )

    # Build SubCryptex for this delegate — replaces old static preset.
    from nls.brain.sub_cryptex import SubCryptex as _SubCryptex
    from .types import _SUB_AGENT_SUPPLEMENT
    _parent_cryptex_d = None
    try:
        _wm_tool_d = tools.get("wm")
        if _wm_tool_d is not None:
            _parent_cryptex_d = getattr(_wm_tool_d, "_cryptex", None)
    except Exception:
        pass

    _budget_info = (
        f"\nITERATION BUDGET: {max_steps} tool-call rounds "
        f"(passive limit hit grants +10 more on first escalation).\n"
        "PRIMARY GOAL: Ship-quality deliverables — runnable code wired end-to-end, "
        "not placeholders. Verify once (read + smoke test) before task_complete.\n"
        "Do not exit early to save iterations; do not call task_complete with "
        "only package.json, stubs, or client APIs without backend routes.\n"
        "If stuck, blocked, or running low on budget, call escalate().\n"
    )

    _write_tool = sub_tools.get("write")
    _file_ledger_ref = getattr(_write_tool, "_ledger", None)

    _sub_cryptex = _SubCryptex.spawn_from_parent(
        parent=_parent_cryptex_d,
        task=(
            f"You are a sub-agent. Complete this specific task:\n\n{task}\n\n"
            f"{_budget_info}"
            f"Parent task context: {user_task[:300]}"
        ),
        preflight_facts=_facts,
        cwd_info=_cwd_info,
        project_dir_info=_project_dir_info,
        sub_agent_supplement=_SUB_AGENT_SUPPLEMENT,
        context_window_tokens=config.context_window_tokens,
        file_manifest=getattr(spec, "file_manifest", None) or [],
        team_briefing=getattr(spec, "team_briefing", "") or "",
        tech_stack_block=getattr(spec, "tech_stack_block", "") or "",
        file_ownership_block=getattr(spec, "file_ownership_block", "") or "",
        file_ledger=_file_ledger_ref,
    )
    _gr_d = getattr(hooks, "guardrails_registry", None)
    if _gr_d is not None:
        from nls.tools.agent_tools.guardrails_registry import (
            inject_guardrails_into_cryptex,
            inject_guardrails_into_sub_cryptex,
        )
        inject_guardrails_into_sub_cryptex(_sub_cryptex, _gr_d)
        if _parent_cryptex_d is not None:
            _parent_cryptex_d._guardrails_registry = _gr_d  # type: ignore[attr-defined]
            inject_guardrails_into_cryptex(_parent_cryptex_d, _gr_d)
    _sub_cryptex._guardrails_registry = _gr_d  # type: ignore[attr-defined]
    _sub_cryptex._delegate_number = getattr(  # type: ignore[attr-defined]
        spec, "delegate_number", 0,
    )

    # Expose SubCryptex to DelegateManager for orchestrator ring access.
    if sub_cryptex_holder_out is not None:
        sub_cryptex_holder_out.append(_sub_cryptex)

    _initial_ctx_d = _sub_cryptex.compose_context()
    sub_system = _initial_ctx_d[0]["content"] if _initial_ctx_d else (
        f"You are a sub-agent. Complete this specific task:\n\n{task}\n\n"
        + _cwd_info + _project_dir_info + _SUB_AGENT_SUPPLEMENT
    )

    _user_msg = (
        f"Execute the task above using tools. Task:\n{task}\n"
        f"{_budget_info}"
    )
    if _project_dir_info:
        _user_msg += (
            "\n\nIMPORTANT: Your bash CWD is ALREADY set to the project "
            "directory — do NOT cd into it. Just run commands directly. "
            "Do NOT create a new project folder."
        )

    sub_context = [
        {"role": "system", "content": sub_system},
        {"role": "user", "content": _user_msg},
    ]

    _dlg_num = delegate_number
    # state_holder is wired above for progress monitor + rewrite escalation

    async def _sub_on_event(event: "AgentEvent") -> None:
        if on_event is None:
            return
        tagged = AgentEvent(
            event.type,
            {**(event.data or {}), "sub_agent": True, "delegate_number": _dlg_num},
        )
        await on_event(tagged)

    _hint_ack_target = hint_ack_holder_out

    def _on_hint_ack(ack_text: str) -> None:
        if _hint_ack_target is not None:
            _hint_ack_target.clear()
            _hint_ack_target.append(ack_text)

    # Create isolated LoopHooks with SubCryptex transform/after_tool.
    from .bridge import LoopHooks as _LH
    sub_hooks = _LH(
        get_preflight_knowledge=hooks.get_preflight_knowledge if hooks else None,
        on_tool_success=hooks.on_tool_success if hooks else None,
        on_tool_error=hooks.on_tool_error if hooks else None,
        log_event=hooks.log_event if hooks else None,
        transform_context=_sub_cryptex.make_transform_hook(),
        on_after_tool=_sub_cryptex.make_after_tool_hook(
            parent_hook=hooks.on_after_tool if hooks else None,
        ),
        on_compaction=_sub_cryptex.make_compaction_hook(),
        on_hint_ack=_on_hint_ack,
    )
    sub_hooks._render_mode_ref = ["executing"]  # type: ignore[attr-defined]
    sub_hooks._loop_state_ref = {}  # type: ignore[attr-defined]
    sub_hooks._sub_cryptex = _sub_cryptex  # type: ignore[attr-defined]

    # Delegates must NOT share the orchestrator's abort_signal.  The
    # orchestrator session's finally block always fires abort.set(),
    # which would cascade-kill every running delegate the moment the
    # orchestrator loop ends (even for benign reasons like total_timeout
    # or task_complete).  Delegates have their own guard system
    # (max_iterations, total_timeout, escalation) — they don't need an
    # external kill switch.
    sub_result = None
    try:
        from .loop import run_loop
        sub_result = await asyncio.wait_for(
            run_loop(
                context=sub_context,
                tools=sub_tools,
                config=sub_config,
                hooks=sub_hooks,
                vllm_client=vllm_client,
                on_event=_sub_on_event,
                abort_signal=None,
                user_input=task,
                adapter_name=_delegate_model,
                enable_thinking=True,
                state_holder=_state_holder,
                wrap_up_signal=wrap_up_signal,
                copilot_queue=hint_queue,
            ),
            timeout=_sub_timeout,
        )
        summary = sub_result.final_response or "(no output)"
        _sub_state = _state_holder[0] if _state_holder else None
        _is_stub = summary.startswith("[Loop stopped:") or summary == "(no output)"
        if _is_stub and _sub_state and _sub_state.cumulative_actions:
            _actions = _sub_state.cumulative_actions[-20:]
            summary += "\n\nWork performed before stopping:\n" + "\n".join(
                f"  - {a}" for a in _actions
            )
    except asyncio.TimeoutError:
        _partial = _state_holder[0] if _state_holder else None
        _p_resp = (_partial.final_response or "") if _partial else ""
        summary = f"(sub-agent timed out after {_sub_timeout:.0f}s)"
        if _p_resp:
            summary += f"\nPartial output:\n{_p_resp[:500]}"
    except Exception as exc:
        summary = f"(sub-agent error: {exc})"
        logger.error("[DELEGATE:%d] detached error: %s", delegate_number, exc, exc_info=True)

    # Compress SubCryptex into a knowledge digest for the orchestrator
    try:
        import json as _json_dd
        _digest_d = await _sub_cryptex.compress_to_digest(vllm_client)
        if _digest_d:
            summary += (
                "\n\n[DELEGATE KNOWLEDGE DIGEST]\n"
                + _json_dd.dumps(_digest_d, indent=2)
                + "\n[END DIGEST]"
            )
    except Exception as _dex:
        logger.warning("[DELEGATE:%d] detached digest failed: %s", delegate_number, _dex)

    _delegate_log(config, {
        "event": "detached_delegate_complete",
        "delegate_number": delegate_number,
        "summary_len": len(summary),
        "summary_preview": summary[:500],
    })

    await emit(on_event, AgentEvent(EventType.DELEGATE_COMPLETE, {
        "delegate_number": delegate_number,
        "delegate_task": task[:200],
        "iterations": sub_result.iterations if sub_result else 0,
        "tool_calls": sub_result.total_tool_calls if sub_result else 0,
        "summary": summary[:200],
        "aborted": sub_result is None or getattr(sub_result, "aborted", False),
        "iteration": iteration,
    }))

    return summary, sub_result


async def _handle_await_delegates(
    args: dict,
    state: LoopState,
    delegate_manager: DelegateManager | None,
    hooks: LoopHooks | None = None,
) -> ToolResult:
    """Exit the orchestrator loop while background delegates keep running."""
    summary = (args.get("summary") or "").strip()
    if not summary:
        return ToolResult(
            content=(
                "Error: 'summary' is required — describe what wave/delegates "
                "you are waiting on (shown in logs; optional user handoff)."
            ),
            is_error=True,
        )
    team_id = (args.get("team_id") or "").strip()
    if delegate_manager is not None:
        try:
            if not delegate_manager.has_active_delegates():
                return ToolResult(
                    content=(
                        "No delegates are running. Use team(action='inspect') "
                        "or delegate_status. If all work is finished, call "
                        "task_complete(summary='...') instead."
                    ),
                    is_error=True,
                )
        except Exception:
            pass

    _tm = getattr(hooks, "_cached_team_manager", None) if hooks else None
    if _tm is not None:
        _block = _tm.completion_review_yield_block_message()
        if _block:
            logger.info(
                "[AWAIT_DELEGATES] blocked — %d pending completion review(s)",
                len(getattr(_tm, "_pending_completion_reviews", {})),
            )
            return ToolResult(
                content=_block,
                is_error=True,
                details={"blocked": True, "pending_completion_review": True},
            )

    suffix = (
        "\n\n[Orchestrator loop ending — background work continues. "
        "You will be re-invoked on completion review, wave completion, "
        "escalation, or check-back.]"
    )
    if team_id:
        suffix += f"\nMonitoring team: {team_id}"
    logger.info(
        "[AWAIT_DELEGATES] yielding loop (mode=%s): %s",
        state.active_mode.value, summary[:200],
    )
    _record = None
    if hooks and hooks.wm_orch_set_coordinator_phase:
        _record = hooks.wm_orch_set_coordinator_phase
    on_await_delegates(state, record_phase=_record)
    if hooks and hooks.wm_orch_record_decision:
        try:
            hooks.wm_orch_record_decision(
                "await_delegates", summary[:200], team_id=team_id,
            )
        except Exception:
            pass
    return ToolResult(
        content=summary + suffix,
        stop_loop=True,
        details={
            "type": "awaiting_delegates",
            "summary": summary,
            "team_id": team_id or None,
        },
    )


async def _handle_wait(
    args: dict,
    on_event: Callable | None,
    iteration: int,
    abort_signal: asyncio.Event | None,
    delegate_manager: DelegateManager | None,
    copilot_queue: Any | None = None,
    mid_wait_hook: Callable | None = None,
    idle_monitor_cycles: int = 0,
    *,
    state: LoopState | None = None,
) -> ToolResult:
    """Handle the ``wait`` virtual tool — sleep then return delegate status.

    Wakes early if abort_signal fires OR if copilot_queue receives a
    message (escalation / user message).  Priority: user > escalation > wait.
    """
    raw = args.get("seconds", 30)
    try:
        seconds = min(max(int(raw), 1), 300)
    except (TypeError, ValueError):
        seconds = 30
    reason = args.get("reason", "")

    _bg_delegates = False
    if delegate_manager is not None:
        try:
            _bg_delegates = delegate_manager.has_active_delegates()
        except Exception:
            pass
    _monitoring = (
        state is not None
        and state.active_mode in (AgentMode.MONITORING, AgentMode.DELEGATING)
    )
    if _bg_delegates and _monitoring and seconds > 45:
        return ToolResult(
            content=(
                f"Do NOT wait({seconds}s) while delegates run in the background — "
                "that keeps this loop alive and wastes tokens.\n"
                "Use await_delegates(summary='...') to exit cleanly after a "
                "brief status (or communicate first). You will be re-invoked "
                "automatically when the wave completes or escalates.\n"
                "For a quick poll only, use wait(seconds=15) then team(inspect)."
            ),
            is_error=True,
            details={"blocked": True, "prefer_await_delegates": True},
        )

    # Enforce increasing floor when the orchestrator is in a monitor loop
    # to reduce context churn from rapid wait/inspect cycles.
    if idle_monitor_cycles > 0 and delegate_manager is not None:
        _floor = min(30 * (2 ** min(idle_monitor_cycles - 1, 3)), 300)
        if seconds < _floor:
            seconds = _floor

    # Sub-agents (no delegate_manager) should not idle-wait for long.
    # Cap at 10s and warn — they have no delegates to monitor.
    _is_subagent = delegate_manager is None
    if _is_subagent and seconds > 10:
        seconds = 10

    await emit(on_event, AgentEvent(
        EventType.TOOL_START,
        {"tool_name": "wait", "arguments": {"seconds": seconds, "reason": reason},
         "call_id": "", "iteration": iteration},
    ))

    _poll_interval = 3.0
    _loop = asyncio.get_running_loop()
    _deadline = _loop.time() + seconds
    _interrupted = False

    _pre_done: set[int] = set()
    if delegate_manager:
        for _s in delegate_manager.get_status():
            if _s.state != "running":
                _pre_done.add(_s.delegate_number)

    while _loop.time() < _deadline:
        _remaining = _deadline - _loop.time()
        _sleep_for = min(_poll_interval, _remaining)
        if _sleep_for <= 0:
            break

        if abort_signal and not abort_signal.is_set():
            try:
                await asyncio.wait_for(abort_signal.wait(), timeout=_sleep_for)
                break
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(_sleep_for)

        if mid_wait_hook is not None:
            try:
                mid_wait_hook()
            except Exception:
                pass

        if copilot_queue is not None and not copilot_queue.empty():
            _interrupted = True
            _waited = seconds - max(0, _deadline - _loop.time())
            logger.info(
                "[WAIT] interrupted after %.0fs (copilot_queue has %d messages)",
                _waited, copilot_queue.qsize(),
            )
            break

    _actual_wait = seconds - max(0, _deadline - _loop.time())

    status_suffix = ""
    if _interrupted:
        status_suffix = (
            f"\n⚠ WAIT INTERRUPTED after {_actual_wait:.0f}s — "
            f"urgent message in queue (escalation or user). "
            f"Check and respond before resuming wait."
        )
    if delegate_manager:
        statuses = delegate_manager.get_status()
        if statuses:
            visible = [
                s for s in statuses
                if s.state == "running" or s.delegate_number not in _pre_done
            ]
            if visible:
                lines = [f"\nDelegate status after waiting {_actual_wait:.0f}s:"]
                for s in visible:
                    lines.append(
                        f"  Delegate #{s.delegate_number}: {s.state} | "
                        f"iter {s.iteration}/{s.max_iterations} | "
                        f"{s.elapsed_seconds:.0f}s elapsed | {s.task[:80]}"
                    )
                all_done = all(s.state != "running" for s in visible)
                if all_done:
                    lines.append("  All delegates have finished.")
                status_suffix += "\n".join(lines)

    result_text = f"Waited {_actual_wait:.0f}s." + status_suffix
    if _is_subagent:
        result_text += (
            "\n⚠ You are a SUB-AGENT, not the orchestrator. "
            "Do NOT wait for other team members — you cannot monitor them. "
            "Focus on completing YOUR assigned task, then finish."
        )

    await emit(on_event, AgentEvent(
        EventType.TOOL_END,
        {"tool_name": "wait", "call_id": "", "is_error": False,
         "result_preview": result_text[:200], "iteration": iteration},
    ))

    return ToolResult(content=result_text)


async def _handle_delegate_status(
    args: dict,
    delegate_manager: DelegateManager | None,
) -> ToolResult:
    """Handle the ``delegate_status`` virtual tool call."""
    if delegate_manager is None:
        return ToolResult(content="No delegate manager available.", is_error=True)

    action = args.get("action", "list")

    if action == "wrap_up":
        num = args.get("delegate_number")
        if num is None:
            return ToolResult(content="delegate_number required for wrap_up.", is_error=True)
        ok = await delegate_manager.wrap_up(int(num))
        return ToolResult(content=f"Wrap-up signal {'sent' if ok else 'failed (not running?)'}.")

    if action == "cancel":
        num = args.get("delegate_number")
        if num is None:
            return ToolResult(content="delegate_number required for cancel.", is_error=True)
        ok = await delegate_manager.cancel(int(num))
        return ToolResult(content=f"Cancel {'sent' if ok else 'failed (not running?)'}.")

    if action == "hint":
        num = args.get("delegate_number")
        msg = args.get("message", "").strip()
        if num is None:
            return ToolResult(content="delegate_number required for hint.", is_error=True)
        if not msg:
            return ToolResult(content="message required for hint.", is_error=True)
        ok = await delegate_manager.hint(int(num), msg)
        return ToolResult(
            content=f"Hint {'delivered — delegate will see it next iteration' if ok else 'failed (not running?)'}.",
        )

    statuses = delegate_manager.get_status()
    if not statuses:
        return ToolResult(content="No delegates tracked.")

    if action == "detail":
        num = args.get("delegate_number")
        if num is not None:
            statuses = [s for s in statuses if s.delegate_number == int(num)]

    lines = []
    for s in statuses:
        state_label = s.state
        if s.state == "running" and delegate_manager is not None:
            if not delegate_manager.is_delegate_live(s.delegate_number):
                state_label = "interrupted (no live task — not running)"
        elif s.state == "interrupted":
            state_label = "interrupted (runtime stopped — not running)"
        lines.append(
            f"Delegate #{s.delegate_number}: {state_label} | "
            f"iter {s.iteration}/{s.max_iterations} | "
            f"tc={s.total_tool_calls} | "
            f"{s.elapsed_seconds:.0f}s elapsed | "
            f"task: {s.task[:100]}"
        )
        if s.last_actions:
            for a in s.last_actions:
                lines.append(f"  - {a}")
        if s.summary_preview:
            lines.append(f"  summary: {s.summary_preview[:200]}")
    return ToolResult(content="\n".join(lines))


async def execute_tools(
    tool_calls: list[dict],
    tools: dict[str, Any],
    config: LoopConfig,
    state: LoopState,
    *,
    abort_signal: asyncio.Event | None = None,
    on_event: Callable | None = None,
    hooks: LoopHooks | None = None,
    vllm_client: Any | None = None,
    user_task: str = "",
    digest_count: int = 0,
    delegate_manager: DelegateManager | None = None,
    response_has_text: bool = False,
) -> tuple[list[ToolResult], int]:
    """Execute tool calls — parallel when independent, sequential otherwise.

    Returns (results, updated_digest_count).
    """
    if not tool_calls:
        return [], digest_count

    _tc_names = [
        tc.get("function", {}).get("name", "?") for tc in tool_calls
    ]
    logger.info(
        "[EXEC] executing %d tool_calls: %s",
        len(tool_calls), _tc_names,
    )

    # Mutable ref so parallel tasks can safely increment
    digest_count_ref = [digest_count]

    # Separate virtual tools, unknowns, and real tools
    ordered_results: dict[int, ToolResult] = {}
    real_calls: list[tuple[int, dict, Any]] = []
    pending_delegates: list[tuple[int, dict]] = []  # (idx, raw_call)

    for idx, call in enumerate(tool_calls):
        fn = call.get("function", {})
        name = fn.get("name", "")
        args_str = fn.get("arguments", "{}")
        call_id = call.get("id", "")

        if name and name not in tools:
            import re as _re
            # camelCase -> snake_case, then spaces/dashes -> underscores
            normalized = _re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
            normalized = normalized.replace(" ", "_").replace("-", "_").lower()
            if normalized in tools:
                logger.info("[EXEC] normalized tool name %r -> %r", name, normalized)
                name = normalized
                fn["name"] = normalized

        args = _parse_tool_args(args_str)

        # Mode + runtime policy enforcement (same allowlist as schema filter).
        from .orchestration_policy import (
            build_tool_policy_inputs,
            resolve_allowed_tools,
            tool_not_allowed_message,
        )

        _policy_inputs = build_tool_policy_inputs(
            state.active_mode,
            state,
            delegate_manager,
            set(tools.keys()) | set(state.unlocked_tools),
            hooks,
        )
        _resolved_allowed = resolve_allowed_tools(_policy_inputs)
        if name not in _resolved_allowed and name not in (
            "get_tool_schema",
            "adopt_orchestration_profile",
        ):
            _block_msg = tool_not_allowed_message(
                name,
                state.active_mode,
                _resolved_allowed,
                orchestration_profile=getattr(
                    state, "orchestration_profile", "",
                ) or "",
            )
            from nls.agentic.profile_depth_policy import enrich_profile_blocked_message

            _block_msg = enrich_profile_blocked_message(
                name,
                _block_msg,
                state,
                mode=state.active_mode,
                all_unlocked=frozenset(
                    set(tools.keys()) | set(state.unlocked_tools),
                ),
            )
            ordered_results[idx] = ToolResult(
                content=_block_msg,
                is_error=True,
            )
            continue

        if name == "adopt_orchestration_profile":
            if not config.enable_delegation:
                ordered_results[idx] = ToolResult(
                    content="Sub-agents cannot change orchestration profile.",
                    is_error=True,
                )
                continue
            from nls.agentic.profile_depth_policy import (
                apply_orchestration_profile_adoption,
            )

            _ok, _msg, _details = apply_orchestration_profile_adoption(
                state,
                str(args.get("profile", "") or ""),
                reason=str(args.get("reason", "") or ""),
                enable_delegation=config.enable_delegation,
                hooks=hooks,
            )
            ordered_results[idx] = ToolResult(
                content=_msg,
                is_error=not _ok,
                details=_details,
            )
            continue

        if name == "escalate":
            if config.enable_delegation:
                ordered_results[idx] = ToolResult(
                    content=(
                        "escalate() is only for worker sub-agents. "
                        "Use ask_user() to ask the user directly."
                    ),
                    is_error=True,
                )
                continue
            _reason = args.get("reason", "question")
            _message = args.get("message", "")
            if not _message:
                ordered_results[idx] = ToolResult(
                    content="message is required for escalate().",
                    is_error=True,
                )
                continue
            _paths = args.get("paths") or []
            if _reason == "file_access":
                if not isinstance(_paths, list) or not _paths:
                    ordered_results[idx] = ToolResult(
                        content=(
                            "paths is required for escalate(reason='file_access'). "
                            "Example: paths=['.gitignore']"
                        ),
                        is_error=True,
                    )
                    continue
                _path_line = "paths_requested: " + ", ".join(
                    str(p).strip() for p in _paths if str(p).strip()
                )
                _message = f"{_message}\n\n{_path_line}"
            ordered_results[idx] = await _handle_sub_agent_escalation(
                f"escalate:{_reason}: {_message}",
                _message,
                state,
                config,
                hooks,
            )
            continue
        if name == "ask_user":
            if not config.enable_delegation:
                question = (
                    args.get("question")
                    or args.get("message")
                    or "What do you need?"
                )
                ordered_results[idx] = await _handle_sub_agent_escalation(
                    f"ask_user: {question}",
                    question,
                    state,
                    config,
                    hooks,
                )
                continue
            r = await _handle_ask_user(args, on_event, hooks or LoopHooks(), state.iteration, call_id)
            ordered_results[idx] = r
            continue
        if name == "communicate":
            if not config.enable_delegation:
                ordered_results[idx] = ToolResult(
                    content="Sub-agents cannot communicate directly with the user. "
                    "Focus on completing your task — the orchestrator handles user updates.",
                    is_error=True,
                )
                continue
            # Suppress the communicate() call when the model already emitted
            # visible text in the same turn — the text IS the communication.
            # Without this guard, the user sees the same message twice: once
            # as the streamed assistant text and again via the communicate tool.
            if response_has_text:
                ordered_results[idx] = ToolResult(
                    content="Message delivered as visible response text.",
                )
                continue
            r = await _handle_communicate(args, on_event, state.iteration)
            ordered_results[idx] = r
            continue
        if name in ("switch_mode", "coordinator_mode"):
            if not config.enable_delegation:
                ordered_results[idx] = ToolResult(
                    content="Sub-agents cannot switch modes.",
                    is_error=True,
                )
                continue

            # Backward compat: coordinator_mode(enabled=true) → planning
            if name == "coordinator_mode":
                _cm_enabled = args.get("enabled", False)
                _target_mode_str = "planning" if _cm_enabled else "executing"
                _reason = args.get("reasoning", "")
            else:
                _target_mode_str = args.get("mode", "")
                _reason = args.get("reason", "")

            try:
                _target_mode = AgentMode(_target_mode_str)
            except ValueError:
                ordered_results[idx] = ToolResult(
                    content=(
                        f"Unknown mode '{_target_mode_str}'. Valid modes: "
                        "planning, delegating, monitoring, evaluating, executing, responding."
                    ),
                    is_error=True,
                )
                continue

            from nls.agentic.orchestration_policy import (
                block_mode_switch_for_profile,
                delegates_running,
            )

            _profile_block = block_mode_switch_for_profile(
                _target_mode,
                getattr(state, "orchestration_profile", "") or "",
                is_coordinator=bool(state.coordinator_mode),
                delegates_active=delegates_running(delegate_manager),
                must_await_delegates=bool(
                    getattr(state, "must_await_delegates", False)
                ),
                dispatch_source=getattr(state, "dispatch_source", "") or "",
            )
            if _profile_block:
                from nls.agentic.profile_depth_policy import (
                    enrich_mode_switch_block_message,
                )

                ordered_results[idx] = ToolResult(
                    content=enrich_mode_switch_block_message(
                        _target_mode_str, _profile_block, state,
                    ),
                    is_error=True,
                    details={"blocked": True, "profile_mode": True},
                )
                continue

            _plan_req_team = False
            if hooks and hooks.plan_requires_team_delegation:
                try:
                    _plan_req_team = hooks.plan_requires_team_delegation()
                except Exception:
                    pass
            _has_team = False
            _tm = getattr(hooks, "_cached_team_manager", None) if hooks else None
            _pending_cr = False
            if _tm is not None:
                try:
                    _has_team = _tm.has_orchestrator_blocking_team()
                    _pending_cr = _tm.has_pending_completion_reviews()
                except Exception:
                    pass
            _review_block = block_em_executing_during_review(
                _target_mode,
                active_mode=state.active_mode,
                dispatch_source=getattr(state, "dispatch_source", "") or "",
                has_pending_completion_reviews=_pending_cr,
                enable_delegation=config.enable_delegation,
                is_delegate_loop=not config.enable_delegation,
            )
            if _review_block:
                ordered_results[idx] = ToolResult(
                    content=_review_block,
                    is_error=True,
                    details={"blocked": True, "em_review_mode": True},
                )
                continue
            _exec_block = block_executing_mode_escape(
                _target_mode,
                active_mode=state.active_mode,
                plan_requires_team_delegation=_plan_req_team,
                has_non_terminal_team=_has_team,
                enable_delegation=config.enable_delegation,
                is_delegate_loop=not config.enable_delegation,
                orchestrator_recovery=getattr(
                    state, "orchestrator_recovery", False,
                ),
                orchestration_profile=getattr(state, "orchestration_profile", None),
            )
            if _exec_block:
                ordered_results[idx] = ToolResult(
                    content=_exec_block,
                    is_error=True,
                    details={"blocked": True, "mode_escape": True},
                )
                continue

            _prev_mode = state.active_mode
            state.active_mode = _target_mode
            invalidate_tool_policy_cache(state)
            state.mode_override_count = 0
            # Record that the user explicitly requested this switch so
            # Trigger 3 won't immediately override it.
            state.user_mode_switch_iter = state.iteration
            # When entering RESPONDING, remember where to return.
            if _target_mode == AgentMode.RESPONDING and _prev_mode != AgentMode.RESPONDING:
                state._pre_responding_mode = _prev_mode
            # When explicitly leaving RESPONDING, clear the saved mode.
            elif _prev_mode == AgentMode.RESPONDING and _target_mode != AgentMode.RESPONDING:
                state._pre_responding_mode = None
            logger.info(
                "[EXEC] MODE SWITCH: %s → %s (%s)",
                _prev_mode.value, _target_mode.value,
                _reason[:200] if _reason else "no reason",
            )
            if _target_mode == AgentMode.EVALUATING:
                _record = None
                if hooks and hooks.wm_orch_set_coordinator_phase:
                    _record = hooks.wm_orch_set_coordinator_phase
                on_evaluating_wave(state, record_phase=_record)

            _MODE_HINTS = {
                AgentMode.PLANNING: (
                    "PLANNING MODE active. You are the architect.\n"
                    "Primary tools: todo, plan, read, research.\n"
                    + (
                        "WORKFLOW: OODA → todo(add) → plan(create with ALL "
                        "steps) → switch_mode(mode='delegating').\n"
                        "Do NOT use bash, write, team, or delegate in this mode."
                        if normalize_profile(
                            getattr(state, "orchestration_profile", None),
                        ) == "orchestrated"
                        else "WORKFLOW: todo(add) → plan(create) → execute each "
                        "step yourself (solo_structured — no team waves).\n"
                        "Do NOT use team or delegate in this mode."
                    )
                ),
                AgentMode.DELEGATING: (
                    "DELEGATING MODE — engineering manager staffing a wave.\n"
                    "Primary tools: team, plan, todo, scheduler.\n"
                    "WORKFLOW: plan/create steps → todo/Kanban linkage → "
                    "team(create) → team(launch) → switch_mode(monitoring).\n"
                    "Write the brief; your team does the IC work."
                ),
                AgentMode.MONITORING: (
                    "MONITORING MODE — engineering manager on the combat board.\n"
                    "Holistic view: team(inspect), team(hint) when stuck, "
                    "team(intervene) on escalation.\n"
                    "After launch: communicate(optional) → "
                    "await_delegates(summary='...') to end your turn.\n"
                    "Do NOT: IC work (write/bash), idle-poll wait(60+), "
                    "or repeated inspect loops.\n"
                    "When wave lands: switch_mode(evaluating) for review."
                ),
                AgentMode.EVALUATING: (
                    "EVALUATING MODE — second pair of eyes / acceptance review.\n"
                    "Read deliverables, verify against plan criteria, patch "
                    "small gaps, update Kanban.\n"
                    "If wave failed but artifacts exist: plan(accept_partial).\n"
                    "Then team(advance) or launch next wave."
                ),
                AgentMode.EXECUTING: (
                    "EXECUTING MODE active. All tools available.\n"
                    + (
                        "If a master plan exists with delegatable steps, you should "
                        "be in delegating/monitoring — not self-building wave work.\n"
                        "Direct execution for simple tasks."
                        if normalize_profile(
                            getattr(state, "orchestration_profile", None),
                        ) == "orchestrated"
                        else "SOLO EXECUTION: work through your plan steps yourself. "
                        "No team waves — use write/bash/edit per step, then "
                        "plan(action='complete') when done."
                    )
                ),
                AgentMode.RESPONDING: (
                    "RESPONDING MODE active. You can access calendar, email, "
                    "contacts, skills, and discovery tools while your teams "
                    "keep running in the background.\n"
                    "Handle the user's request, then call "
                    f"switch_mode(mode='{_prev_mode.value}') to return to "
                    "your previous coordinator mode, or just deliver your "
                    "text response and the mode will restore automatically."
                ),
            }
            ordered_results[idx] = ToolResult(
                content=_MODE_HINTS.get(
                    _target_mode,
                    f"Switched to {_target_mode.value} mode.",
                ),
            )
            continue
        if name == "delegate":
            _dlg_args = _parse_tool_args(fn.get("arguments", "{}"))
            _dlg_action = _dlg_args.get("action", "")
            if _dlg_action in ("list", "detail", "wrap_up", "cancel", "hint"):
                logger.warning(
                    "[EXEC] Model called 'delegate' with delegate_status args "
                    "(action=%s) — redirecting to delegate_status",
                    _dlg_action,
                )
                r = await _handle_delegate_status(_dlg_args, delegate_manager)
                ordered_results[idx] = r
                continue
            if config.enable_delegation:
                pending_delegates.append((idx, call))
            else:
                ordered_results[idx] = ToolResult(
                    content="BLOCKED: Sub-agents cannot delegate further.",
                    is_error=True,
                )
            continue
        if name == "delegate_status":
            r = await _handle_delegate_status(args, delegate_manager)
            ordered_results[idx] = r
            continue
        if name == "await_delegates":
            r = await _handle_await_delegates(
                args, state, delegate_manager, hooks=hooks,
            )
            ordered_results[idx] = r
            continue
        if name == "wait":
            _cq = hooks.copilot_queue if hooks else None
            _mwh = hooks.mid_wait_hook if hooks else None
            _idle_cycles = getattr(state, "idle_monitor_cycles", 0)
            r = await _handle_wait(
                args, on_event, state.iteration, abort_signal,
                delegate_manager, copilot_queue=_cq, mid_wait_hook=_mwh,
                idle_monitor_cycles=_idle_cycles, state=state,
            )
            ordered_results[idx] = r
            continue

        # Team advance intercept: after creating the next wave, auto-launch
        # when no delegates or other active teams are running.
        if name == "team" and args.get("action") == "advance":
            team_tool = tools.get("team")
            if team_tool is not None:
                _adv_block = monitoring_advance_block_message(
                    state, str(args.get("team_id") or ""),
                )
                if _adv_block:
                    ordered_results[idx] = ToolResult(
                        content=_adv_block,
                        is_error=True,
                        details={
                            "blocked": True,
                            "monitoring_advance": True,
                        },
                    )
                    continue
                r = await _execute_single(
                    team_tool, call, config, state, tools,
                    abort_signal=abort_signal, on_event=on_event, hooks=hooks,
                    delegate_manager=delegate_manager,
                )
                details = getattr(r, "details", None) or {}
                if (
                    not r.is_error
                    and details.get("next_team")
                    and config.enable_delegation
                    and delegate_manager is not None
                ):
                    _next_id = details.get("team_id", "")
                    _tm = getattr(team_tool, "_tm", None)
                    if _tm is not None and _can_auto_launch_team(
                        _tm, delegate_manager, _next_id,
                    ):
                        r = await _launch_team_delegates(
                            _tm, _next_id, delegate_manager, tools,
                            config, state, hooks or LoopHooks(),
                            vllm_client, on_event, abort_signal,
                            user_task,
                        )
                        if not r.is_error:
                            r = ToolResult(
                                content=(
                                    f"{r.content}\n\n"
                                    f"[AUTO-LAUNCH] No other active waves — "
                                    f"next wave was launched automatically."
                                ),
                                details={
                                    **details,
                                    "auto_launched": True,
                                },
                            )
                ordered_results[idx] = r
                continue

        # Team tool launch intercept: execute the tool, then if the
        # result signals needs_delegate_spawn, actually spawn the
        # delegates via the TeamManager + DelegateManager pipeline.
        if name == "team" and args.get("action") == "launch":
            team_tool = tools.get("team")
            if team_tool is not None:
                r = await _execute_single(
                    team_tool, call, config, state, tools,
                    abort_signal=abort_signal, on_event=on_event, hooks=hooks,
                    delegate_manager=delegate_manager,
                )
                details = getattr(r, "details", None) or {}
                if (
                    not r.is_error
                    and details.get("needs_delegate_spawn")
                    and config.enable_delegation
                    and delegate_manager is not None
                ):
                    _team_id = details.get("team_id", "")
                    _tm = getattr(team_tool, "_tm", None)
                    if _tm is not None:
                        r = await _launch_team_delegates(
                            _tm, _team_id, delegate_manager, tools,
                            config, state, hooks or LoopHooks(),
                            vllm_client, on_event, abort_signal,
                            user_task,
                        )
                ordered_results[idx] = r
                continue

        tool = tools.get(name)
        if tool is None:
            ordered_results[idx] = ToolResult(
                content=f"Unknown tool '{name}'. Available: {', '.join(sorted(tools.keys()))}",
                is_error=True,
            )
            continue

        if abort_signal and abort_signal.is_set():
            ordered_results[idx] = ToolResult(content="Aborted.", is_error=True)
            continue

        real_calls.append((idx, call, tool))

    # ── Delegate execution ────────────────────────────────────────────────
    # Pre-assign delegate numbers (cap check) then either:
    #   - DETACHED: fire-and-forget via DelegateManager (orchestrator free)
    #   - BLOCKING: gather concurrently and wait (legacy / sub-agent path)
    if pending_delegates:
        assigned: list[tuple[int, dict, int]] = []  # (idx, call, dlg_num)
        for idx, call in pending_delegates:
            if state.delegate_count >= _MAX_DELEGATES:
                ordered_results[idx] = ToolResult(
                    content=f"BLOCKED: Maximum {_MAX_DELEGATES} delegate calls per task. "
                            "Complete remaining work yourself.",
                    is_error=True,
                )
            else:
                state.delegate_count += 1
                assigned.append((idx, call, state.delegate_count))

        if assigned and config.enable_detached_delegates and delegate_manager is not None:
            # ── DETACHED PATH: fire-and-forget ──────────────────────────
            specs = []
            task_labels = []
            for _idx, _call, _num in assigned:
                _fn = _call.get("function", {})
                _args = _parse_tool_args(_fn.get("arguments", "{}"))
                _task = _args.get("task", "").strip() or "(unnamed)"
                try:
                    _ms = min(int(_args.get("max_steps", DELEGATE_DEFAULT_MAX_STEPS)), 50)
                except (ValueError, TypeError):
                    _ms = 15
                specs.append(DelegateSpec(
                    task=_task,
                    delegate_number=_num,
                    max_steps=_ms,
                    args=_args,
                ))
                task_labels.append(f"#{_num}: {_task[:80]}")

            try:
                batch = await delegate_manager.spawn_batch(
                    specs,
                    run_delegate_fn=run_delegate_detached,
                    fn_kwargs=dict(
                        tools=tools,
                        config=config,
                        hooks=hooks or LoopHooks(),
                        vllm_client=vllm_client,
                        on_event=on_event,
                        abort_signal=abort_signal,
                        iteration=state.iteration,
                        user_task=user_task,
                    ),
                )
            except ValueError as _de:
                for _idx, _call, _num in assigned:
                    ordered_results[_idx] = ToolResult(
                        content=f"Delegate rejected: {_de}",
                        is_error=True,
                    )
                return ordered_results, digest_count

            # Check if a plan exists — strongly nudge toward team workflow
            _plan_hint = ""
            _plan_tool = tools.get("plan")
            if _plan_tool and hasattr(_plan_tool, "_store"):
                try:
                    _plans = _plan_tool._store.list_plans()
                    _active = [p for p in _plans if p.status in ("in_progress", "pending")]
                    if _active:
                        _plan_hint = (
                            "\n\n⚠️ WRONG TOOL — You have an active plan with "
                            "delegatable steps. You MUST use "
                            "team(action='create', plan_id=..., wave=N, "
                            "name='Wave N - ...') + team(action='launch') "
                            "instead of raw delegate(). Teams provide wave "
                            "ordering, dependency tracking, escalation, and "
                            "auto-extensions. Raw delegate() bypasses all of "
                            "this. Switch to team tool for remaining waves."
                        )
                except Exception:
                    pass

            _spawn_msg = (
                f"{len(specs)} sub-agent(s) spawned in background (batch {batch.batch_id}):\n"
                + "\n".join(f"  - {lbl}" for lbl in task_labels)
                + "\n\nEngineering manager — team is executing:\n"
                "- Optional: communicate(status) once.\n"
                "- Required: await_delegates(summary='...') to end this turn.\n"
                "- You wake to steer, review, and advance the board — "
                "not to IC or idle-poll."
                + _plan_hint
            )
            _record_phase = None
            if hooks and hooks.wm_orch_set_coordinator_phase:
                _record_phase = hooks.wm_orch_set_coordinator_phase
            on_team_launched(
                state, batch.batch_id, record_phase=_record_phase,
            )
            state.active_mode = AgentMode.MONITORING
            invalidate_tool_policy_cache(state)

            # Auto-schedule a periodic check-back so the orchestrator is
            # re-invoked every 2 minutes while delegates are running.
            # This prevents the orchestrator from daydreaming and forgetting
            # about its delegates.  The job is named after the batch so it
            # can be cancelled when the batch completes.
            _checkback_job_name = f"delegate_checkback_{batch.batch_id}"
            _scheduler_tool = tools.get("scheduler")
            _sched_mgr = getattr(_scheduler_tool, "_manager", None)
            if _sched_mgr is not None:
                from nls.tools.agent_tools.scheduler import ScheduledJob as _SJ
                _agent_id = getattr(config, "agent_id", "")
                _routing = (
                    f"[AGENT_MSG|agent_id={_agent_id}|batch={batch.batch_id}] "
                    if _agent_id else ""
                )
                _checkback_msg = (
                    f"{_routing}[DELEGATE CHECK-BACK — EM REVIEW] "
                    f"Batch {batch.batch_id} ({len(specs)} sub-agent(s)).\n\n"
                    "Management check-in — review the board, not idle-poll.\n\n"
                    "1) delegate_status(list) — holistic view\n"
                    "2) hint stuck members with ONE concrete next step\n"
                    "3) wrap_up completed members\n"
                    "4) When batch done: switch_mode(evaluating), review, "
                    "update plan/Kanban, launch next wave\n"
                    "5) If still running cleanly: await_delegates(summary='...')\n\n"
                    f"Cancel when done: scheduler(remove, name='{_checkback_job_name}')"
                )
                _checkback_secs = checkback_interval_seconds(delegates_active=True)
                try:
                    _sched_mgr.add_job(_SJ(
                        name=_checkback_job_name,
                        schedule_type="interval",
                        interval_seconds=_checkback_secs,
                        action="agent_message",
                        action_message=_checkback_msg,
                        owner="delegate_manager",
                        owner_agent_id=_agent_id,
                    ))
                    logger.info(
                        "[EXEC] delegate check-back job scheduled: '%s' "
                        "(every %.0fs)",
                        _checkback_job_name, _checkback_secs,
                    )
                except Exception as _sce:
                    logger.warning(
                        "[EXEC] failed to schedule delegate check-back: %s",
                        _sce,
                    )

                # Auto-cancel the check-back job when the batch finishes on its
                # own — safety net in case the orchestrator model forgets to
                # call scheduler(command='remove').  Chain with any existing
                # on_batch_complete callback.
                _captured_sched_mgr = _sched_mgr
                _captured_job_name = _checkback_job_name

                def _auto_cancel_checkback(
                    bid: str,
                    results: list,
                    _sm=_captured_sched_mgr,
                    _jn=_captured_job_name,
                ) -> None:
                    cancelled = _sm.remove_job(_jn)
                    if cancelled:
                        logger.info(
                            "[EXEC] auto-cancelled check-back job '%s' "
                            "(batch %s: all %d delegates done)",
                            _jn, bid, len(results),
                        )

                _existing_cb = delegate_manager._on_batch_complete
                if _existing_cb is not None:
                    async def _chained_cb(
                        bid: str,
                        results: list,
                        _orig=_existing_cb,
                        _new=_auto_cancel_checkback,
                    ) -> None:
                        _new(bid, results)
                        cb_result = _orig(bid, results)
                        if asyncio.iscoroutine(cb_result):
                            await cb_result
                    delegate_manager._on_batch_complete = _chained_cb
                else:
                    delegate_manager._on_batch_complete = _auto_cancel_checkback
            else:
                logger.debug(
                    "[EXEC] no scheduler tool available — delegate check-back "
                    "not scheduled for batch %s",
                    batch.batch_id,
                )
            for _idx, _call, _num in assigned:
                ordered_results[_idx] = ToolResult(content=_spawn_msg)

            logger.info(
                "[EXEC] DETACHED: spawned batch %s with %d delegates",
                batch.batch_id, len(specs),
            )

        elif assigned:
            # ── BLOCKING PATH: legacy gather ────────────────────────────
            async def _run_delegate(
                _idx: int, _call: dict, _num: int,
            ) -> tuple[int, ToolResult]:
                _fn = _call.get("function", {})
                _args_str = _fn.get("arguments", "{}")
                _args = _parse_tool_args(_args_str)
                _r = await _handle_delegate(
                    _args, tools, config, state, hooks or LoopHooks(),
                    vllm_client, on_event, abort_signal,
                    state.iteration, user_task,
                    delegate_number=_num,
                )
                return _idx, _r

            _dlg_wall_start = time.time()

            if len(assigned) == 1:
                _idx, _r = await _run_delegate(*assigned[0])
                ordered_results[_idx] = _r
            else:
                logger.info(
                    "[EXEC] fan-out: spawning %d sub-agents in parallel",
                    len(assigned),
                )
                dlg_results = await asyncio.gather(
                    *[_run_delegate(i, c, n) for i, c, n in assigned],
                    return_exceptions=True,
                )
                for di, item in enumerate(dlg_results):
                    if isinstance(item, Exception):
                        logger.error(
                            "Parallel delegate %d crashed: %s",
                            di + 1, item, exc_info=item,
                        )
                        _fail_idx = assigned[di][0]
                        ordered_results[_fail_idx] = ToolResult(
                            content=f"Sub-agent crashed: {item}",
                            is_error=True,
                        )
                        continue
                    _idx, _r = item
                    ordered_results[_idx] = _r

            # Credit back delegate wall-clock time to the orchestrator.
            _dlg_wall_elapsed = time.time() - _dlg_wall_start
            state.start_time += _dlg_wall_elapsed
            logger.info(
                "[EXEC] delegate wall-clock: %.1fs — orchestrator timeout "
                "clock advanced by same amount (effective remaining: %.1fs)",
                _dlg_wall_elapsed,
                config.total_timeout_seconds - (time.time() - state.start_time),
            )

    # Attempt parallel execution for independent calls
    if config.enable_parallel_tools and len(real_calls) > 1:
        real_only = [c for _, c, _ in real_calls]
        independent, _ = classify_independence(real_only)
        independent_ids = {id(c) for c in independent}

        # Run independent calls in parallel
        parallel_items = [(i, c, t) for i, c, t in real_calls if id(c) in independent_ids]
        sequential_items = [(i, c, t) for i, c, t in real_calls if id(c) not in independent_ids]

        if len(parallel_items) > 1:
            async def _run_one(idx: int, call: dict, tool: Any) -> tuple[int, ToolResult]:
                fn = call.get("function", {})
                name = fn.get("name", "")
                args_str = fn.get("arguments", "{}")
                args = _parse_tool_args(args_str)
                r = await _execute_single(
                    tool, call, config, state, tools,
                    abort_signal=abort_signal, on_event=on_event, hooks=hooks,
                    delegate_manager=delegate_manager,
                )
                r = await _post_process_result(name, args, r, config, hooks, vllm_client, user_task, digest_count_ref)
                return idx, r

            par_results = await asyncio.gather(
                *[_run_one(i, c, t) for i, c, t in parallel_items],
                return_exceptions=True,
            )
            for pi, item in enumerate(par_results):
                if isinstance(item, Exception):
                    _fail_idx = parallel_items[pi][0]
                    _fail_name = parallel_items[pi][1].get("function", {}).get("name", "?")
                    logger.warning("Parallel tool execution error [%s]: %s", _fail_name, item)
                    ordered_results[_fail_idx] = ToolResult(
                        content=f"Tool '{_fail_name}' crashed during parallel execution: {item}",
                        is_error=True,
                    )
                    continue
                idx, result = item
                ordered_results[idx] = result
        else:
            sequential_items = parallel_items + sequential_items

        for idx, call, tool in sequential_items:
            fn = call.get("function", {})
            name = fn.get("name", "")
            args_str = fn.get("arguments", "{}")
            args = _parse_tool_args(args_str)
            result = await _execute_single(
                tool, call, config, state, tools,
                abort_signal=abort_signal, on_event=on_event, hooks=hooks,
                delegate_manager=delegate_manager,
            )
            result = await _post_process_result(name, args, result, config, hooks, vllm_client, user_task, digest_count_ref)
            ordered_results[idx] = result
    else:
        for idx, call, tool in real_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            args_str = fn.get("arguments", "{}")
            args = _parse_tool_args(args_str)
            result = await _execute_single(
                tool, call, config, state, tools,
                abort_signal=abort_signal, on_event=on_event, hooks=hooks,
                delegate_manager=delegate_manager,
            )
            result = await _post_process_result(name, args, result, config, hooks, vllm_client, user_task, digest_count_ref)
            ordered_results[idx] = result

    # Preserve original call order
    results = []
    for i in range(len(tool_calls)):
        if i in ordered_results:
            results.append(ordered_results[i])
        else:
            _miss_name = tool_calls[i].get("function", {}).get("name", "?")
            results.append(ToolResult(
                content=f"Tool '{_miss_name}' produced no result (internal dispatch error). Try calling it again.",
                is_error=True,
            ))

    # --- Conscious override friction injection ---
    if state.active_mode not in (AgentMode.EXECUTING, AgentMode.EVALUATING):
        for _ri, _rr in enumerate(results):
            _rn = tool_calls[_ri].get("function", {}).get("name", "?")
            if not _rr.is_error and is_override_tool(state.active_mode, _rn):
                state.mode_override_count += 1
                _mode_name = state.active_mode.value
                _MODE_NEXT_ACTION = {
                    AgentMode.PLANNING: "Return to planning: todo/plan/read.",
                    AgentMode.DELEGATING: "Return to delegation: team/delegate.",
                    AgentMode.MONITORING: "Return to monitoring: team(inspect)/wait.",
                }
                _next = _MODE_NEXT_ACTION.get(
                    state.active_mode,
                    f"Return to your {_mode_name} workflow.",
                )
                if state.mode_override_count <= 3:
                    _friction = (
                        f"\n\n[MODE FRICTION] You used '{_rn}' which is "
                        f"outside your {_mode_name} mode primary tools. "
                        f"This is allowed for brief, targeted actions. "
                        f"{_next}"
                    )
                else:
                    _friction = (
                        f"\n\n[MODE WARNING] You've used "
                        f"{state.mode_override_count} override tools in "
                        f"{_mode_name} mode. If you need sustained "
                        f"file/bash access, switch to evaluating mode: "
                        f"switch_mode(mode='evaluating')."
                    )
                results[_ri] = ToolResult(
                    content=_rr.content + _friction,
                    is_error=_rr.is_error,
                )
                logger.info(
                    "[EXEC] MODE OVERRIDE #%d: %s in %s mode",
                    state.mode_override_count, _rn, _mode_name,
                )

    for _ri, _rr in enumerate(results):
        _rn = tool_calls[_ri].get("function", {}).get("name", "?")
        logger.info(
            "[EXEC] result[%d] tool=%s error=%s content_len=%d preview=%.120s",
            _ri, _rn, _rr.is_error, len(_rr.content),
            _rr.content[:120],
        )

    return results, digest_count_ref[0]


def make_tool_message(call: dict, result: ToolResult) -> dict:
    """Create a tool message dict for the context."""
    call_id = call.get("id", "unknown")
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": result.content,
    }
