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
from .delegate_manager import DelegateManager, DelegateSpec
from .events import AgentEvent, EventType, emit
from .types import (
    AgentMode, COORDINATOR_TOOLS, COORDINATOR_BASH_TIMEOUT_S,
    LoopConfig, LoopState, is_override_tool, get_allowed_tools,
    MODE_PRIMARY_TOOLS,
)

logger = logging.getLogger(__name__)

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

    # Fix double-wrapped path: LLM sometimes emits path='{"path":"real/file"}'
    # instead of path='real/file'. The braces cause WinError 123 on Windows.
    # Also repairs truncated JSON (missing closing '}').
    for k in ("path", "source", "destination", "target"):
        v = args.get(k)
        if not isinstance(v, str):
            continue
        sv = v.strip()
        if not sv.startswith("{"):
            continue
        _unwrapped = False
        for _candidate in (sv, sv + "}", sv + '"}'):
            try:
                inner = json.loads(_candidate)
                if isinstance(inner, dict) and k in inner:
                    logger.warning(
                        "_parse_tool_args: unwrapped double-JSON %s: %s -> %s",
                        k, v[:80], inner[k],
                    )
                    args[k] = inner[k]
                    _unwrapped = True
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        if not _unwrapped and sv.startswith('{"') and k in sv:
            # Last-resort regex extraction for badly truncated JSON
            import re as _re
            _m = _re.search(r'"' + _re.escape(k) + r'"\s*:\s*"([^"]+)"', sv)
            if _m:
                logger.warning(
                    "_parse_tool_args: regex-extracted double-JSON %s: %s -> %s",
                    k, v[:80], _m.group(1),
                )
                args[k] = _m.group(1)

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

_DIGEST_TOOLS = frozenset({"read", "web_fetch", "bash"})
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
        max_steps = min(int(args.get("max_steps", 15)), 50)
    except (ValueError, TypeError):
        max_steps = 15

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
    )

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
        _pd_abs = str(_Path(_workspace_root) / _pd)
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
                # Tag write/edit clones with delegate authorship for the ledger.
                if _tname in ("write", "edit") and hasattr(_cloned, "_ledger_meta"):
                    _cloned._ledger_meta = dict(_cloned._ledger_meta)
                    _cloned._ledger_meta.update({
                        "role": "delegate",
                        "delegate_index": delegate_number,
                        "wave": None,  # wave info not available in blocking path
                    })
                sub_tools[_tname] = _cloned
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
        _project_dir_info = (
            f"\nPROJECT DIRECTORY — CRITICAL: {_pd}/\n"
            f"Your CWD (for bash AND file tools) is ALREADY set to {_pd}/.\n"
            f"Do NOT `cd {_pd}` — you are already inside it.\n"
            f"- bash: run commands directly (e.g. `mkdir -p backend/models`). "
            f"Do NOT prefix with `cd {_pd} &&`.\n"
            f"- read/write/glob: use paths relative to {_pd}/, "
            f"e.g. write(path=\"backend/main.py\").\n"
            f"Do NOT prepend the project folder name to paths.\n"
            f"NEVER create new top-level project directories.\n"
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

    _sub_cryptex = SubCryptex.spawn_from_parent(
        parent=_parent_cryptex,
        task=f"You are a sub-agent. Complete this specific task:\n\n{task}\n\n"
             f"Parent task context: {user_task[:300]}",
        preflight_facts=_facts,
        cwd_info=_cwd_info,
        project_dir_info=_project_dir_info,
        sub_agent_supplement=_SUB_AGENT_SUPPLEMENT,
        context_window_tokens=config.context_window_tokens,
    )

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
                f"Task:\n{task}"
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


def _format_digest(digest: dict, original_size: int) -> str:
    """Format a digest as a compact context replacement."""
    lines = [f"[Digest of {digest['source']} ({original_size} chars)]"]
    lines.append(digest["summary"])
    if digest["insights"]:
        lines.append("Key points:")
        for insight in digest["insights"]:
            lines.append(f"  - {insight}")
    lines.append("[Full content available — use read tool to re-examine]")
    return "\n".join(lines)


def _fallback_digest(content: str, source: str) -> str:
    """Create a truncation-based digest when LLM extraction fails."""
    truncated = content[:500].rstrip()
    if len(content) > 500:
        truncated += "\n..."
    return (
        f"[Digest of {source} ({len(content)} chars — LLM digest unavailable)]\n"
        f"{truncated}\n"
        f"[Full content available — use read tool to re-examine]"
    )


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
    *,
    abort_signal: asyncio.Event | None = None,
    on_event: Callable | None = None,
    hooks: LoopHooks | None = None,
) -> ToolResult:
    """Execute one tool with timeout and hook integration."""
    fn = call.get("function", {})
    name = fn.get("name", "unknown")
    args_str = fn.get("arguments", "{}")
    call_id = call.get("id", "")

    args = _parse_tool_args(args_str)

    await emit(on_event, AgentEvent(
        EventType.TOOL_START,
        {"tool_name": name, "arguments": args, "call_id": call_id, "iteration": state.iteration},
    ))

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
    #   bash: agent can request a custom timeout
    _timeout = config.tool_timeout_seconds
    _PLAN_TIMEOUT = 90
    _BASH_MAX_TIMEOUT = 300
    if name == "plan":
        _timeout = _PLAN_TIMEOUT
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
            "is_error": result.is_error,
            "result_preview": result.content[:200],
            "iteration": state.iteration,
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
    """Store a WM digest of large tool results, then pass full content through.

    The cognitive digest is written to working memory (for long-term recall)
    but the tool result content is NOT replaced — the agent must see the full
    output first.  Context-window compression of old tool results is handled
    later by the compactor when context pressure builds.
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
                or args.get("command", "")[:80]
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
            # Full result content is intentionally NOT replaced here.
            # The compactor truncates stale tool results when context fills up.

    if hooks and hooks.ans_tool_learning and not result.is_error:
        try:
            hooks.ans_tool_learning(name, args, result.content, user_task)
        except Exception:
            pass

    return result


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
        + "\n\nIMPORTANT — your role is now ORCHESTRATOR, not executor:\n"
        "- DO NOT start performing the same work you just delegated.\n"
        "- Acknowledge to the user that work is underway.\n"
        "- Use team(action='inspect', team_id='" + launched.id + "') "
        "to monitor progress.\n"
        "- When all members complete, use team(action='advance', team_id='"
        + launched.id + "') to finalize and advance to next wave.\n\n"
        "CHOOSE ONE strategy:\n"
        "A) SHORT task (< 60s): wait(seconds=N), then team(action='inspect').\n"
        "B) LONG task (> 60s): END YOUR TURN NOW. You will be re-invoked "
        "via check-back scheduler when progress needs review."
    )

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
            f"{_routing}[TEAM CHECK-BACK] Team {launched.name} [{launched.id}]\n\n"
            f"Step 1: team(action='inspect', team_id='{launched.id}') "
            "to see current progress.\n\n"
            "Step 2: Based on inspect results:\n"
            "  - If members still RUNNING: send hints to stuck ones, then END YOUR TURN.\n"
            "  - If ALL members reached terminal state (done/failed):\n"
            f"    a) scheduler(command='remove', name='{_checkback_job_name}') — cancel check-back.\n"
            f"    b) team(action='advance', team_id='{launched.id}') — finalize.\n"
            "    c) Advance returns the OUTCOME:\n"
            "       COMPLETED = all succeeded → deliver results, next phase.\n"
            "       PARTIAL = mixed → retry failures yourself or new team.\n"
            "       FAILED = most/all failed → investigate root cause, retry.\n"
            "    d) Deliver ONE concise summary. Never repeat summaries.\n\n"
            "CRITICAL: If inspect shows [ALREADY REPORTED], skip silently.\n"
            "CRITICAL: Do NOT report success when the outcome is FAILED or PARTIAL."
        )
        try:
            _sched_mgr.add_job(_SJ(
                name=_checkback_job_name,
                schedule_type="interval",
                interval_seconds=120.0,
                action="agent_message",
                action_message=_checkback_msg,
                owner="team_manager",
            ))
            logger.info(
                "[EXEC] team check-back job scheduled: '%s' (every 120s)",
                _checkback_job_name,
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
        _pd_abs = str(_Path(_workspace_root) / _pd)
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
                # Tag write/edit clones with delegate authorship for the ledger.
                if _tname in ("write", "edit") and hasattr(_cloned, "_ledger_meta"):
                    _cloned._ledger_meta = dict(_cloned._ledger_meta)
                    _cloned._ledger_meta.update({
                        "role": "delegate",
                        "delegate_index": spec.delegate_number,
                        "wave": spec.wave,
                    })
                sub_tools[_tname] = _cloned
            except Exception:
                pass

    _cwd_info = ""
    if _pd and _workspace_root:
        _cwd_info = (
            f"\nIMPORTANT — Your working directory is pre-set to the project "
            f"folder. All relative paths in write/read/edit/glob resolve "
            f"inside the project directory automatically.\n"
        )

    if _pd:
        _project_dir_info = (
            f"\nPROJECT DIRECTORY — CRITICAL: {_pd}/\n"
            f"Your CWD (for bash AND file tools) is ALREADY set to {_pd}/.\n"
            f"Do NOT `cd {_pd}` — you are already inside it.\n"
            f"- bash: run commands directly (e.g. `mkdir -p backend/models`). "
            f"Do NOT prefix with `cd {_pd} &&`.\n"
            f"- read/write/glob: use paths relative to {_pd}/, "
            f"e.g. write(path=\"backend/main.py\").\n"
            f"- mkdir: use NESTED paths like `backend/models`, NOT flat lists "
            f"like `\"backend\",\"models\"` (that creates sibling dirs).\n"
            f"NEVER create new top-level project directories.\n"
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

    _sub_cryptex = _SubCryptex.spawn_from_parent(
        parent=_parent_cryptex_d,
        task=f"You are a sub-agent. Complete this specific task:\n\n{task}\n\n"
             f"Parent task context: {user_task[:300]}",
        preflight_facts=_facts,
        cwd_info=_cwd_info,
        project_dir_info=_project_dir_info,
        sub_agent_supplement=_SUB_AGENT_SUPPLEMENT,
        context_window_tokens=config.context_window_tokens,
        file_manifest=getattr(spec, "file_manifest", None) or [],
        team_briefing=getattr(spec, "team_briefing", "") or "",
    )

    # Expose SubCryptex to DelegateManager for orchestrator ring access.
    if sub_cryptex_holder_out is not None:
        sub_cryptex_holder_out.append(_sub_cryptex)

    _initial_ctx_d = _sub_cryptex.compose_context()
    sub_system = _initial_ctx_d[0]["content"] if _initial_ctx_d else (
        f"You are a sub-agent. Complete this specific task:\n\n{task}\n\n"
        + _cwd_info + _project_dir_info + _SUB_AGENT_SUPPLEMENT
    )

    _user_msg = f"Execute the task above using tools. Task:\n{task}"
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
    # Use state_holder_out directly so DelegateManager._progress_monitor
    # can read the live LoopState during execution (not just after).
    _state_holder: list = state_holder_out if state_holder_out is not None else []

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


async def _handle_wait(
    args: dict,
    on_event: Callable | None,
    iteration: int,
    abort_signal: asyncio.Event | None,
    delegate_manager: DelegateManager | None,
    copilot_queue: Any | None = None,
    mid_wait_hook: Callable | None = None,
    idle_monitor_cycles: int = 0,
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
        lines.append(
            f"Delegate #{s.delegate_number}: {s.state} | "
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

        # Mode enforcement: reject tools not in primary + override sets.
        _allowed = get_allowed_tools(state.active_mode)
        if (state.active_mode != AgentMode.EXECUTING
                and _allowed  # EXECUTING has empty set = all allowed
                and name not in _allowed
                and name not in ("get_tool_schema",)):
            ordered_results[idx] = ToolResult(
                content=(
                    f"{state.active_mode.value.upper()} MODE: tool "
                    f"'{name}' is not available. "
                    f"Switch to a mode that supports it with "
                    f"switch_mode(mode='evaluating') or "
                    f"switch_mode(mode='executing')."
                ),
                is_error=True,
            )
            continue

        if name == "ask_user":
            if not config.enable_delegation:
                # Sub-agent → escalate the question to the orchestrator
                # who can answer directly or relay from the user.
                question = args.get("question", "What do you need?")
                _esc_cb = config.on_escalation
                _cq = (hooks or LoopHooks()).copilot_queue

                if _esc_cb and _cq is not None:
                    try:
                        _r = _esc_cb(f"ask_user: {question}", state, question)
                        if asyncio.iscoroutine(_r):
                            await _r
                    except Exception:
                        logger.debug("ask_user escalation callback failed", exc_info=True)

                    try:
                        _answer = await asyncio.wait_for(_cq.get(), timeout=120)
                        if isinstance(_answer, dict) and "message" in _answer:
                            _answer_text = _answer["message"]
                        elif isinstance(_answer, dict) and "content" in _answer:
                            _answer_text = _answer["content"]
                        elif isinstance(_answer, str):
                            _answer_text = _answer
                        else:
                            _answer_text = str(_answer)
                        ordered_results[idx] = ToolResult(
                            content=f"Orchestrator answered: {_answer_text}",
                        )
                    except asyncio.TimeoutError:
                        ordered_results[idx] = ToolResult(
                            content=(
                                "Orchestrator did not respond in time. "
                                "Work with what you have or state what "
                                "you need in your final response."
                            ),
                            is_error=True,
                        )
                else:
                    ordered_results[idx] = ToolResult(
                        content=(
                            "You are a sub-agent and cannot ask the user "
                            "directly. Work with what you have or state "
                            "what you need in your final response."
                        ),
                        is_error=True,
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

            _prev_mode = state.active_mode
            state.active_mode = _target_mode
            state._mode_schemas_applied = False
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

            _MODE_HINTS = {
                AgentMode.PLANNING: (
                    "PLANNING MODE active. You are the architect.\n"
                    "Primary tools: todo, plan, read, research.\n"
                    "WORKFLOW: OODA → todo(add) → plan(create with ALL "
                    "steps) → switch_mode(mode='delegating').\n"
                    "Do NOT use bash, write, team, or delegate in this mode."
                ),
                AgentMode.DELEGATING: (
                    "DELEGATING MODE active. You are the engineering manager.\n"
                    "Primary tools: team, delegate, plan, scheduler.\n"
                    "WORKFLOW: team(create) → team(launch) → "
                    "switch_mode(mode='monitoring').\n"
                    "Do NOT create files or run bash — that is "
                    "the team's job."
                ),
                AgentMode.MONITORING: (
                    "MONITORING MODE active. You are watching progress.\n"
                    "Primary tools: team(inspect/hint/intervene), wait, "
                    "communicate.\n"
                    "When wave completes: switch_mode(mode='evaluating').\n"
                    "Sub-agents run in the background — you do NOT need "
                    "to stay in this loop. After one status update, call "
                    "task_complete(summary='...') to close cleanly. You "
                    "will be re-activated when delegates finish."
                ),
                AgentMode.EVALUATING: (
                    "EVALUATING MODE active. Full file/bash access.\n"
                    "Review output, verify quality, patch small gaps.\n"
                    "Soft budget: ~10 iterations. Then advance the wave "
                    "or switch_mode(mode='delegating') for next wave."
                ),
                AgentMode.EXECUTING: (
                    "EXECUTING MODE active. All tools available.\n"
                    "Direct execution for simple tasks."
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
        if name == "wait":
            _cq = hooks.copilot_queue if hooks else None
            _mwh = hooks.mid_wait_hook if hooks else None
            _idle_cycles = getattr(state, "idle_monitor_cycles", 0)
            r = await _handle_wait(args, on_event, state.iteration, abort_signal, delegate_manager, copilot_queue=_cq, mid_wait_hook=_mwh, idle_monitor_cycles=_idle_cycles)
            ordered_results[idx] = r
            continue

        # Team tool launch intercept: execute the tool, then if the
        # result signals needs_delegate_spawn, actually spawn the
        # delegates via the TeamManager + DelegateManager pipeline.
        if name == "team" and args.get("action") == "launch":
            team_tool = tools.get("team")
            if team_tool is not None:
                r = await _execute_single(
                    team_tool, call, config, state,
                    abort_signal=abort_signal, on_event=on_event, hooks=hooks,
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
                    _ms = min(int(_args.get("max_steps", 15)), 50)
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
                + "\n\nIMPORTANT — your role is now ORCHESTRATOR, not executor:\n"
                "- DO NOT start performing the same work you just delegated. "
                "The sub-agents are handling it.\n"
                "- Acknowledge to the user that work is underway. If the user "
                "is AFK, send the acknowledgment via their preferred channel "
                "(WhatsApp/Telegram/email) — not just the chat they aren't watching.\n"
                "- You may do OTHER useful work while waiting, but not the "
                "delegated tasks.\n\n"
                "CHOOSE ONE strategy based on how long this will take:\n\n"
                "A) SHORT task (expected < 60s): use wait(seconds=N) to pause "
                "once, then call delegate_status to check. "
                "Never poll delegate_status in a tight loop without a wait.\n\n"
                "B) LONG task (expected > 60s, or clearly substantial): "
                "END YOUR TURN NOW — reply briefly to the user and stop. "
                "You will be AUTOMATICALLY RE-INVOKED when all delegates "
                "complete, with their results injected. You do NOT need to "
                "keep your loop running to catch the results. "
                "Keeping the loop alive just wastes iterations.\n\n"
                "When results arrive (either via wait→delegate_status or "
                "via automatic re-invocation), compile and DELIVER the full "
                "report via the user's preferred channel."
                + _plan_hint
            )

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
                    f"{_routing}[DELEGATE CHECK-BACK] "
                    f"Scheduled check-in for batch {batch.batch_id} "
                    f"({len(specs)} sub-agent(s)).\n\n"
                    "Step 1: Call delegate_status(action='list') to see current progress.\n\n"
                    "Step 2 — FOR EACH STILL-RUNNING agent: if it looks stuck "
                    "(same iteration, unexpected errors, needs input), send a hint: "
                    "delegate_status(action='hint', delegate_number=N, message='...').\n\n"
                    "Step 3 — FOR EACH COMPLETED agent: "
                    "delegate_status(action='wrap_up', delegate_number=N).\n\n"
                    "Step 4 — ONCE ALL AGENTS ARE COMPLETE OR WRAPPED UP:\n"
                    f"  a) FIRST: Cancel this check-back: scheduler(command='remove', name='{_checkback_job_name}')\n"
                    "  b) Update the Kanban board — mark completed items done, failed items blocked.\n"
                    "  c) Review your working memory / project plan for the NEXT PHASE.\n"
                    "  d) Immediately spawn the next team OR execute the next task.\n"
                    "  e) Deliver ONE concise summary to the user. Do NOT repeat summaries.\n\n"
                    "CRITICAL RULES:\n"
                    "- ALWAYS cancel the check-back BEFORE reporting to the user.\n"
                    "- Do NOT generate a completion message if you already reported.\n"
                    "- A batch finishing means one PHASE is done — decide next action."
                )
                try:
                    _sched_mgr.add_job(_SJ(
                        name=_checkback_job_name,
                        schedule_type="interval",
                        interval_seconds=120.0,
                        action="agent_message",
                        action_message=_checkback_msg,
                        owner="delegate_manager",
                    ))
                    logger.info(
                        "[EXEC] delegate check-back job scheduled: '%s' "
                        "(every 120s)",
                        _checkback_job_name,
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
                r = await _execute_single(tool, call, config, state, abort_signal=abort_signal, on_event=on_event, hooks=hooks)
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
            result = await _execute_single(tool, call, config, state, abort_signal=abort_signal, on_event=on_event, hooks=hooks)
            result = await _post_process_result(name, args, result, config, hooks, vllm_client, user_task, digest_count_ref)
            ordered_results[idx] = result
    else:
        for idx, call, tool in real_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            args_str = fn.get("arguments", "{}")
            args = _parse_tool_args(args_str)
            result = await _execute_single(tool, call, config, state, abort_signal=abort_signal, on_event=on_event, hooks=hooks)
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
