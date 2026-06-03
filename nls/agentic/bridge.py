"""Agentic Bridge — hooks + config for the agentic loop (M-015).

Extracted from ServerRuntime._build_agentic_hooks() and
get_agentic_config_v2(). Provides standalone ``build_hooks()`` and
``build_config()`` that any runtime can use.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CLONE_RE = re.compile(r"Cloning into ['\"]([^'\"]+)['\"]", re.IGNORECASE)
_ENV_FILE_RE = re.compile(r"([\w./-]+/\.env(?:\.example|\.local)?)", re.IGNORECASE)

_HINT_CREDENTIAL_RE = re.compile(
    r"ghp_[A-Za-z0-9]{30,}|gho_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
    r"|sk-[A-Za-z0-9\-_]{20,}|xox[bpsa]-[A-Za-z0-9\-]{20,}"
    r"|postgres(?:ql)?://\S+"
    r"|-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----",
    re.IGNORECASE,
)
_PKG_SCRIPTS_RE = re.compile(r'"([\w:_-]+)":\s*"', re.IGNORECASE)
_RUNBOOK_DOCS = frozenset({
    "readme.md", "architecture.md", "contributing.md",
    "getting_started.md", "docs.md", "setup.md", "development.md",
})


# ===================================================================
# v4 LoopHooks — formalized hook interface for the v4 agentic loop
# ===================================================================

from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass
class LoopHooks:
    """Extension points for NLS cognitive layer in the v4 loop.

    The loop calls these at defined points.  All are optional (default: no-op).
    The loop never depends on their return values for control flow except
    for `get_steering_messages` and `has_active_plan`.
    """

    # --- Control hooks (loop DOES depend on these) ---
    get_steering_messages: Callable[[], Awaitable[list[dict]]] | None = None
    has_active_plan: Callable[[], bool] | None = None
    plan_has_pending_steps: Callable[[], bool] | None = None
    plan_requires_team_delegation: Callable[[], bool] | None = None
    plan_suppresses_raw_delegate: Callable[[], bool] | None = None

    # --- Context hooks (affects LLM input) ---
    transform_context: Callable[[list[dict]], list[dict]] | None = None
    get_preflight_knowledge: Callable[[str], str | None] | None = None

    # --- Tool hooks ---
    on_before_tool: Callable[[str, dict], bool | None] | None = None
    on_after_tool: Callable[[str, dict, Any], None] | None = None
    on_tool_success: Callable[[str, dict, Any], None] | None = None
    on_tool_error: Callable[[str, dict, Any], None] | None = None

    # --- Turn hooks ---
    on_thinking: Callable[[str], None] | None = None
    on_turn_end: Callable[[Any, list], None] | None = None
    on_goals_extracted: Callable[[list[str]], None] | None = None
    on_hints_extracted: Callable[[list[str]], None] | None = None

    # --- Lifecycle hooks ---
    on_loop_start: Callable[[], None] | None = None
    on_loop_end: Callable[[Any], None] | None = None

    # --- Cognitive system ticks ---
    tick_hypothalamus: Callable[[float], None] | None = None
    get_cortisol: Callable[[], float] | None = None
    wm_save: Callable[[], None] | None = None
    wm_consolidate: Callable[[str], None] | None = None
    wm_upsert_digest: Callable[[str, str, str, str], None] | None = None
    wm_set_plan_position: Callable[[str], None] | None = None
    wm_push_instructions: Callable[[list[str]], None] | None = None
    wm_push_task_goals: Callable[[list[str]], None] | None = None
    wm_begin_task_epoch: Callable[..., None] | None = None
    """Begin a new task epoch (session WM rotation) when goals materially change."""
    wm_refresh_todo_board: Callable[[], None] | None = None
    guardrails_registry: Any | None = None
    wm_mark_task_goal_done: Callable[[str], bool] | None = None
    wm_prune_supporting_facts_for_goal: Callable[[str], int] | None = None

    # --- Compaction hook ---
    on_compaction: Callable[[Any], None] | None = None

    # --- Hint acknowledgment (delegate → orchestrator) ---
    on_hint_ack: Callable[[str], None] | None = None

    # --- Learning hooks (async, fire-and-forget) ---
    ans_tool_learning: Callable[..., None] | None = None
    ans_on_response: Callable[..., None] | None = None
    ans_record_task_complete: Callable[..., None] | None = None
    extract_session_learnings: Callable[[], list[Any] | None] | None = None

    # --- Communication ---
    copilot_queue: Any | None = None
    ans_extract_user_answer: Callable[..., None] | None = None
    outbound_check: Callable[[str, dict], str | None] | None = None
    outbound_record: Callable[[str, dict], None] | None = None
    wm_get_tactical_goals: Callable[[], list[str]] | None = None
    orchestrator_pre_delegate_check: Callable[[str, dict], str | None] | None = None

    # --- Mid-wait Cryptex refresh ---
    mid_wait_hook: Callable[[], None] | None = None

    # --- Thalamic routing ---
    refresh_thalamic_route: Callable[[], dict[str, Any] | None] | None = None
    classify_expert_needs: Callable[[str, str | None], list[str] | None] | None = None

    # --- Orchestration WM hooks ---
    wm_orch_update_team: Callable[..., None] | None = None
    wm_orch_record_decision: Callable[..., None] | None = None
    wm_orch_set_coordinator_phase: Callable[..., None] | None = None
    wm_orch_add_escalation: Callable[..., None] | None = None
    wm_orch_resolve_escalation: Callable[..., None] | None = None
    wm_sync_wake_attention_board: Callable[[Any], None] | None = None
    wm_absorb_wave_review: Callable[[Any], None] | None = None
    wm_prune_stale_tactical_goals: Callable[[Any, str], None] | None = None
    wm_get_credentials: Callable[[], list[tuple[str, str]]] | None = None

    # --- Event logging ---
    log_event: Callable[..., None] | None = None

    # --- Brain event bus (Phase 4) ---
    brain_event_bus: Any | None = None


def build_config(agent_config: dict[str, Any]) -> Any:
    """Build AgenticConfig from agent config dict."""
    from .types import AgenticConfig

    cfg = agent_config.get("agency", {}).get("agentic_loop", {})
    return AgenticConfig(
        max_iterations=cfg.get("max_iterations", 40),
        tool_timeout_seconds=cfg.get("tool_timeout_seconds", 30),
        max_context_chars=cfg.get("max_context_chars", 80_000),
        result_max_chars=cfg.get("result_max_chars", 20_000),
        max_continuation_passes=cfg.get("max_continuation_passes", 2),
        cortisol_redirect_threshold=cfg.get("cortisol_redirect_threshold", 0.55),
        cortisol_abort_threshold=cfg.get("cortisol_abort_threshold", 0.80),
    )


def build_hooks(
    *,
    agent_id: str = "",
    agent_dir: Path | None = None,
    working_memory: Any | None = None,
    dual_wm: Any | None = None,
    hypothalamus: Any | None = None,
    domain_db: Any | None = None,
    ans: Any | None = None,
    temporal_self: Any | None = None,
    vllm_client: Any | None = None,
    inference_adapter: str | None = None,
    store_learn_signals: Any | None = None,
    config: dict[str, Any] | None = None,
    # Phase 4 subsystems (for interoceptive snapshot, etc.)
    predictive: Any | None = None,
    self_state: Any | None = None,
    calibrator: Any | None = None,
    ofc: Any | None = None,
    theory_of_mind: Any | None = None,
    narrative_self: Any | None = None,
    event_logger: Any | None = None,
    agent_tools: list | None = None,
    slot_registry: Any | None = None,
    network_dynamics: Any | None = None,
) -> Any:
    """Build AgenticHooks wiring the NLS cognitive layer into the agentic loop."""
    from .types import AgenticHooks

    config = config or {}

    def _is_sensitive_path(path_str: str) -> bool:
        if not path_str:
            return False
        norm = os.path.normpath(path_str).replace("\\", "/").lower()
        return "/skills/" in norm and any(
            f in norm for f in ("__init__.py", "/adapter.py", "/webhook.py")
        )

    def _extract_operational_wm(tool_name: str, args: dict, result_str: str) -> None:
        if working_memory is None:
            return
        wm = working_memory
        result_str = result_str or ""

        if tool_name == "bash":
            cmd = args.get("command", "")
            m = _CLONE_RE.search(result_str)
            if m:
                wm.upsert_fact(domain="Project.Root",
                               content=f"Repository cloned to directory: {m.group(1)}")
            if cmd.strip().startswith("cd "):
                target = cmd.strip()[3:].strip().strip("'\"")
                wm.upsert_fact(domain="System.CWD",
                               content=f"Working directory changed to: {target}")
            if cmd.strip().startswith("ls") and result_str and len(result_str) < 2000:
                wm.upsert_fact(domain="Project.Layout",
                               content=f"Directory listing ({cmd.strip()[:60]}):\n{result_str[:500]}",
                               salience=0.85)
            env_files = _ENV_FILE_RE.findall(result_str)
            if env_files:
                wm.upsert_fact(domain="Project.EnvFiles",
                               content="Env files found: " + ", ".join(dict.fromkeys(env_files)),
                               salience=0.85)
            if "SERVER/DAEMON STARTED" in result_str:
                port_m = re.search(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0)[:\s]+(\d{2,5})", result_str)
                pid_m = re.search(r"pid:\s*(\d+)", result_str)
                wm.upsert_fact(
                    domain="Project.RunningServer",
                    content=f"Server running (port={port_m.group(1) if port_m else '?'}, "
                            f"pid={pid_m.group(1) if pid_m else '?'}, cmd={cmd[:80]})",
                    salience=0.95,
                )
        elif tool_name == "read":
            path = args.get("path", "")
            if path:
                wm.upsert_fact(domain="Project.LastRead",
                               content=f"Last file read: {path}", salience=0.7)

    def on_tool_success(name, args, result):
        if temporal_self is not None:
            temporal_self.drain_energy(temporal_self.cfg.energy_drain_per_tool_call)
        if hypothalamus is not None:
            hypothalamus.on_signal("tool_success")
            if name in ("write", "edit") and _is_sensitive_path(args.get("path", "")):
                hypothalamus.on_signal("tool_error")
        try:
            result_str = result if isinstance(result, str) else getattr(result, "content", str(result))
            _extract_operational_wm(name, args or {}, result_str)
        except Exception:
            pass

    def on_tool_error(name, args, result):
        if hypothalamus is not None:
            hypothalamus.on_signal("tool_error")
        if working_memory is not None and name == "bash":
            try:
                err = result if isinstance(result, str) else getattr(result, "content", str(result))
                if "No such file or directory" in err:
                    working_memory.upsert_fact(
                        domain="Project.PathError",
                        content=f"Path error: {err[:200]}",
                        salience=0.9,
                    )
            except Exception:
                pass

    def on_turn_end(response_text, tool_calls, tool_results):
        if ans is not None and response_text:
            _tags = ("LEARN", "EVALUATE", "UNKNOWN", "LOOKUP")
            if "[" not in response_text or not any(t in response_text for t in _tags):
                return
            try:
                combined = response_text
                for tr in tool_results[:5]:
                    preview = tr.get("result_preview", "")
                    if preview:
                        combined += f"\n{preview}"
                signals = ans.on_response("", combined, hypothalamus)
                if signals and domain_db is not None and store_learn_signals:
                    store_learn_signals(signals, "")
            except Exception:
                pass

    def on_agent_end(result):
        if hypothalamus is not None:
            if result.total_tool_calls > 0:
                hypothalamus.on_signal("task_completed")
            if result.aborted or getattr(result, "abort_reason", None):
                hypothalamus.on_signal("task_failed")
            hypothalamus.tick(10.0)
            result.hormones = {
                n: round(h.level, 3) for n, h in hypothalamus.hormones.items()
            }
            if agent_dir:
                try:
                    hypothalamus.save_state(agent_dir / "hypothalamus_state.json")
                except Exception:
                    pass

        if working_memory is not None:
            wm = working_memory
            _failed = result.aborted or getattr(result, "abort_reason", None)
            _is_plan = lambda g: getattr(g, "source", "") == "plan"
            _is_task_extract = lambda g: (
                getattr(g, "source", "") == "task_extract"
                and getattr(g, "level", "") == "tactical"
            )
            if _failed:
                wm.mutate_goals(
                    lambda g: setattr(g, "salience", max(g.salience * 0.5, 0.1)),
                    _is_plan,
                )
                wm.mutate_goals(
                    lambda g: setattr(g, "salience", max(g.salience * 0.5, 0.1)),
                    _is_task_extract,
                )
            else:
                removed = wm.remove_goals_where(_is_plan)
                if removed:
                    wm.upsert_fact(
                        domain="Task.RecentlyCompleted",
                        content=f"Just completed: {removed[0].content[:200]}",
                        source="plan", salience=0.7,
                    )
                wm.remove_goals_where(_is_task_extract)
            try:
                if dual_wm is not None:
                    dual_wm.save(agent_dir)
                else:
                    wm.save(agent_dir / "working_memory_state.json")
            except Exception:
                pass

    def should_abort():
        if hypothalamus is None:
            return False
        cortisol = hypothalamus.get_levels().get("cortisol", 0.0)
        threshold = config.get("agency", {}).get(
            "agentic_loop", {},
        ).get("cortisol_abort_threshold", 0.80)
        return cortisol >= threshold

    def get_cortisol():
        if hypothalamus is None:
            return 0.0
        return hypothalamus.get_levels().get("cortisol", 0.0)

    _OPS_PREFIXES = ("Project.", "System.", "Account.", "Repository.")

    def preflight_knowledge(user_message):
        if domain_db is None:
            return None
        seen: set[str] = set()
        ops_lines: list[str] = []
        kw_lines: list[str] = []
        for prefix in _OPS_PREFIXES:
            try:
                for fact in domain_db.get_facts_by_prefix(prefix):
                    if fact.domain_path in seen:
                        continue
                    val = fact.current_value or ""
                    if "\n[context:" in val:
                        val = val.split("\n[context:")[0].strip()
                    if val and len(val) > 3:
                        seen.add(fact.domain_path)
                        ops_lines.append(f"- {fact.domain_path}: {val}")
            except Exception:
                pass
        try:
            from nls.runtime.inference import detect_factual_domains
            candidates = detect_factual_domains(user_message, domain_db)
            for fact in candidates[:10]:
                if fact.domain_path in seen:
                    continue
                val = fact.current_value or ""
                if "\n[context:" in val:
                    val = val.split("\n[context:")[0].strip()
                if val and len(val) > 3:
                    seen.add(fact.domain_path)
                    kw_lines.append(f"- {fact.domain_path}: {val}")
        except Exception:
            pass
        _recipe = None
        try:
            from nls.agentic.recipe_hints import match_recipe_hints
            _recipe = match_recipe_hints(user_message)
        except Exception:
            pass
        if not ops_lines and not kw_lines and not _recipe:
            return None
        parts = ["--- RECALLED KNOWLEDGE (relevant to this task) ---"]
        if ops_lines:
            parts.append("Operational context:")
            parts.extend(ops_lines[:15])
        if kw_lines:
            parts.append("Related knowledge:")
            parts.extend(kw_lines[:10])
        if _recipe:
            parts.append(_recipe)
        parts.append("--- END RECALLED KNOWLEDGE ---")
        return "\n".join(parts)

    def ans_collect_tool_event(tool_name, call_id, args, output_preview, is_error):
        if ans is None:
            return
        sig_type = "EVALUATE:PFC.ToolError" if is_error else "EVALUATE:PFC.ToolSuccess"
        ans.inject_signal(
            signal_type=sig_type,
            domain_path=f"Task.Tool.{tool_name}",
            content=f"{tool_name}: {output_preview[:200]}",
            source="agentic_loop",
            hypothalamus=hypothalamus,
        )

    def ans_record_task(user_message, final_response, tools_used, success, duration_ms):
        if ans is None:
            return
        ans.record_task_complete(
            user_message=user_message,
            final_response=final_response,
            tools_used=tools_used,
            success=success,
            duration_ms=duration_ms,
            hypothalamus=hypothalamus,
        )
        if working_memory is not None:
            _m = "ok" if success else "FAILED"
            working_memory.upsert_fact(
                domain="Task.LastOutcome",
                content=f"[{_m}] {user_message[:100]} → {final_response[:150]}",
            )

    def ans_get_task_context():
        return ans.get_recent_tasks_context() if ans else None

    def ans_get_context():
        return ans.get_context_summary() if ans else None

    def wm_get_context():
        if working_memory is None:
            return None
        return working_memory.to_context_string() or None

    def wm_activate(source: str):
        if dual_wm is not None and hasattr(dual_wm, "activate"):
            return dual_wm.activate(source)
        return None

    def wm_push_task_goals(goals: list[str]):
        if working_memory is None:
            return
        working_memory.clear_goals("tactical")
        for goal in goals:
            working_memory.add_goal(
                level="tactical",
                content=goal,
                source="task_extract",
            )
            if ans is not None:
                ans.inject_signal(
                    signal_type="LEARN",
                    domain_path=f"Goal.Tactical.{goal[:40].replace(' ', '_')}",
                    content=f"Goal.Tactical: {goal}",
                    source="goal_extraction",
                    hypothalamus=hypothalamus,
                )

    def wm_mark_task_goal_done(substring) -> bool:
        if working_memory is None:
            return False
        if not isinstance(substring, str):
            logger.warning("wm_mark_task_goal_done: expected str, got %s",
                           type(substring).__name__)
            return False
        removed = working_memory.remove_goals_where(
            lambda g: (
                g.level == "tactical"
                and g.source == "task_extract"
                and isinstance(g.content, str)
                and substring.lower() in g.content.lower()
            )
        )
        return len(removed) > 0

    def wm_has_pending_task_goals() -> bool:
        if working_memory is None:
            return False
        goals = working_memory.get_goals()
        return any(
            g.level == "tactical" and g.source == "task_extract"
            for g in goals
        )

    def wm_begin_task_epoch_legacy(
        *,
        loop_id: str,
        goals: list[str],
        dispatch_source: str,
    ) -> None:
        from nls.agentic.task_epoch_hygiene import begin_task_epoch

        begin_task_epoch(
            None,
            working_memory,
            loop_id=loop_id,
            goals=list(goals or []),
            dispatch_source=dispatch_source or "user",
        )

    def wm_prune_supporting_facts_for_goal_legacy(goal: str) -> int:
        from nls.agentic.task_epoch_hygiene import prune_supporting_facts_for_goal

        return prune_supporting_facts_for_goal(None, working_memory, goal)

    def tick_hypo(elapsed: float) -> None:
        if hypothalamus is not None:
            hypothalamus.tick(elapsed)

    def ans_tool_learning(tool_name, args, result_text, user_message):
        if ans is None:
            return
        if vllm_client is not None:
            import asyncio
            _ans = ans
            _hypo = hypothalamus
            _ddb = domain_db
            _store = store_learn_signals
            _ans_path = agent_dir / "ans_state.json" if agent_dir else None

            async def _llm_extract():
                try:
                    llm_signals = await _ans.on_tool_result_async(
                        tool_name=tool_name, args=args,
                        result=result_text, user_message=user_message,
                        hypothalamus=_hypo, vllm_client=vllm_client,
                        adapter_name=inference_adapter,
                    )
                    if llm_signals and _ddb is not None and _store:
                        _store(llm_signals, user_message)
                    if (llm_signals or _ans.signal_count > 0) and _ans_path:
                        _ans.save_state(_ans_path)
                except Exception:
                    signals = _ans.on_tool_result(
                        tool_name=tool_name, args=args,
                        result=result_text, user_message=user_message,
                        hypothalamus=_hypo,
                    )
                    if signals and _ddb is not None and _store:
                        _store(signals, user_message)

            try:
                asyncio.get_running_loop().create_task(_llm_extract())
            except RuntimeError:
                pass
        else:
            signals = ans.on_tool_result(
                tool_name=tool_name, args=args,
                result=result_text, user_message=user_message,
                hypothalamus=hypothalamus,
            )
            if signals and domain_db is not None and store_learn_signals:
                store_learn_signals(signals, user_message)

    def ans_checkpoint(error_log, success_log):
        hints: list[str] = []
        if error_log:
            unique = []
            for e in error_log:
                short = e.get("error", "")[:150]
                if not any(short[:40] in p for p in unique):
                    unique.append(short)
            if unique:
                hints.append("ERRORS OBSERVED:\n" + "\n".join(f"- {p}" for p in unique[:5]))
        if hypothalamus is not None:
            levels = hypothalamus.get_levels()
            cortisol_val = levels.get("cortisol", 0)
            if cortisol_val > 0.3:
                hints.append(f"STRESS LEVEL: cortisol={cortisol_val:.2f} (elevated).")
        return ("--- ANS CHECKPOINT ---\n" + "\n\n".join(hints) + "\n--- END ---") if hints else None

    # ---------------------------------------------------------------
    # HIGH-PRIORITY hooks (plan, WM digest, context, snapshot, ANS)
    # ---------------------------------------------------------------

    def wm_push_goals(steps: list[str], user_message: str):
        if working_memory is None:
            return
        working_memory.remove_goals_where(
            lambda g: g.level == "tactical" and g.source != "task_extract"
        )
        working_memory.clear_goals("strategic")

        plan_ref = ""
        try:
            for _t in (agent_tools or []):
                if getattr(_t, "name", "") == "plan" and hasattr(_t, "get_store"):
                    _active = _t.get_store().find_active()
                    if _active:
                        plan_ref = _active.id
                        if domain_db is not None:
                            domain_db.update_fact(
                                domain_path="Project.ActivePlan",
                                new_value=plan_ref,
                                block_height=0,
                                skip_flip=True,
                            )
                    break
        except Exception:
            pass

        if plan_ref:
            _goal_text = f"Working on plan {plan_ref}: {user_message[:150]}"
            working_memory.add_goal(
                level="strategic",
                content=_goal_text,
                source="plan",
            )
        else:
            _goal_text = user_message[:200]
            working_memory.add_goal(
                level="strategic",
                content=_goal_text,
                source="user",
            )
        if ans is not None:
            ans.inject_signal(
                signal_type="LEARN",
                domain_path=f"Goal.Strategic.{_goal_text[:40].replace(' ', '_')}",
                content=f"Goal.Strategic: {_goal_text}",
                source="goal_extraction",
                hypothalamus=hypothalamus,
            )

    def wm_set_plan_position(position: str):
        if working_memory is not None:
            working_memory.set_plan_position(position)

    def wm_refresh_todo_board():
        """Build a compact Kanban snapshot and push it into WM."""
        if working_memory is None:
            return
        try:
            from server.main import app as _app
            _sl = getattr(_app.state, "skill_loader", None)
            if _sl is None:
                return
            _todo_sk = _sl.skills.get("todo-list")
            if _todo_sk is None or _todo_sk.context is None:
                return
            _todo_mgr = getattr(_todo_sk.context, "adapter", None)
            if _todo_mgr is None:
                return
            store = _todo_mgr.get_store(agent_id)
            items = store.list_items()
            if not items:
                working_memory.set_todo_board("")
                return

            active = [i for i in items if i.status not in ("done", "cancelled")]
            done_count = sum(1 for i in items if i.status == "done")

            if not active and done_count == 0:
                working_memory.set_todo_board("")
                return

            lines = [f"Todo Board ({len(active)} active, {done_count} done):"]
            for item in active:
                plan_tag = f" [plan:{item.plan_id}]" if item.plan_id else ""
                prio = f" !!{item.priority}" if item.priority != "normal" else ""
                lines.append(
                    f"  [{item.status}] {item.title} "
                    f"(id:{item.id}{prio}{plan_tag})"
                )
            working_memory.set_todo_board("\n".join(lines))
        except Exception:
            pass

    def plan_register_file(file_path: str):
        for tool in (agent_tools or []):
            if hasattr(tool, "register_output_file"):
                tool.register_output_file(file_path)
                break

    def update_todo_status(todo_id: str, status: str):
        try:
            from server.main import app as _app
            _sl = getattr(_app.state, "skill_loader", None)
            if _sl is None:
                return
            _todo_sk = _sl.skills.get("todo-list")
            if _todo_sk is None or _todo_sk.context is None:
                return
            _todo_mgr = getattr(_todo_sk.context, "adapter", None)
            if _todo_mgr is None:
                return
            store = _todo_mgr.get_store(agent_id)
            item = store.update(todo_id, status=status)
            if item is None:
                return
            if status in ("in_progress", "done"):
                _todo_mgr.sync_idle_intention(agent_id)
            logger.info(
                "Agent %s: plan→todo sync [%s] → %s",
                agent_id, todo_id, status,
            )
        except Exception as exc:
            logger.debug(
                "Agent %s: plan→todo sync failed: %s", agent_id, exc,
            )

    def wm_upsert_digest(domain: str, content: str):
        if working_memory is not None:
            working_memory.upsert_fact(
                domain=domain,
                content=content,
                source="digest",
                salience=0.85,
            )

    def wm_consolidate_session(summary: str):
        if dual_wm is None:
            return
        dual_wm.consolidate_session(summary)
        try:
            dual_wm.save(agent_dir)
        except Exception:
            pass

    def wm_save_fn():
        try:
            if dual_wm is not None:
                dual_wm.save(agent_dir)
            elif working_memory is not None and agent_dir is not None:
                working_memory.save(agent_dir / "working_memory_state.json")
        except Exception:
            pass

    def _transform_context(
        ctx: list[dict], user_input_text: str,
    ) -> list[dict]:
        if not ctx:
            return ctx
        try:
            fresh_wm = working_memory.to_context_string() if working_memory else None
        except Exception:
            fresh_wm = None
        if not fresh_wm:
            return ctx
        for msg in ctx:
            if msg.get("role") == "user" and "[WORKING MEMORY" in (msg.get("content") or ""):
                msg["content"] = re.sub(
                    r"\[WORKING MEMORY[^\]]*\].*?\[END WORKING MEMORY\]",
                    fresh_wm,
                    msg["content"],
                    flags=re.DOTALL,
                )
                break
        return ctx

    def get_interoceptive_snapshot():
        from .evaluator import InteroceptiveSnapshot

        h: dict = {}
        thal: dict = {}
        if hypothalamus is not None:
            h = hypothalamus.get_levels()
            thal = hypothalamus.get_thalamus_modifiers()

        _ss = 0
        _fs = 0
        _energy = 1.0
        if ans is not None:
            _ss = getattr(ans, "_success_streak", 0)
            _fs = getattr(ans, "_failure_streak", 0)
            _energy = getattr(ans, "_current_energy", 1.0)

        _pe = 0.0
        _unc = 0.0
        if predictive is not None:
            _pe_count = getattr(predictive, "_pe_count", 0)
            _total_pe = getattr(predictive, "_total_pe", 0.0)
            _pe = (_total_pe / _pe_count) if _pe_count > 0 else 0.0
            _unc_vals = getattr(predictive, "_uncertainty", {})
            _unc = (
                sum(_unc_vals.values()) / len(_unc_vals)
                if _unc_vals else 0.0
            )

        _cload = 0.0
        _val = 0.0
        _aro = 0.0
        if self_state is not None:
            _cload = getattr(self_state, "cognitive_load", 0.0)
            _val = getattr(self_state, "valence", 0.0)
            _aro = getattr(self_state, "arousal", 0.0)

        _srel = 0.0
        if calibrator is not None:
            try:
                _srel_dict = calibrator.domain_tracker.get_skill_relevance()
                if isinstance(_srel_dict, dict) and _srel_dict:
                    _srel = max(_srel_dict.values())
                elif isinstance(_srel_dict, (int, float)):
                    _srel = float(_srel_dict)
            except Exception:
                pass

        _sval = 0.0
        if ofc is not None:
            try:
                _uid = "default"
                if theory_of_mind is not None:
                    _uid = getattr(
                        theory_of_mind, "_active_user_id", "default",
                    )
                _sval = ofc.social_value(_uid)
            except Exception:
                pass

        _nd_ecn = 0.0
        _nd_sn = 0.0
        _nd_dmn = 0.0
        _nd_dom = ""
        if self_state is not None:
            _nd_ecn = getattr(self_state, "network_ecn", 0.0)
            _nd_sn = getattr(self_state, "network_sn", 0.0)
            _nd_dmn = getattr(self_state, "network_dmn", 0.0)
            _nd_dom = getattr(self_state, "dominant_network", "")

        return InteroceptiveSnapshot(
            cortisol=h.get("cortisol", 0.20),
            dopamine=h.get("dopamine", 0.50),
            norepinephrine=h.get("norepinephrine", 0.30),
            serotonin=h.get("serotonin", 0.50),
            oxytocin=h.get("oxytocin", 0.20),
            acetylcholine=h.get("acetylcholine", 0.30),
            suppression_shift=thal.get("suppression_shift", 0.0),
            exploration_bonus=thal.get("exploration_bonus", 0.0),
            confidence_boost=thal.get("confidence_boost", 0.0),
            trust_boost=thal.get("trust_boost", 0.0),
            meta_weight_shift=thal.get("meta_weight_shift", 0.0),
            success_streak=_ss,
            failure_streak=_fs,
            energy=_energy,
            prediction_error=_pe,
            uncertainty=_unc,
            cognitive_load=_cload,
            valence=_val,
            arousal=_aro,
            skill_relevance=_srel,
            social_value=_sval,
            network_ecn=_nd_ecn,
            network_sn=_nd_sn,
            network_dmn=_nd_dmn,
            dominant_network=_nd_dom,
        )

    def ans_iteration_extract(user_message, tool_results, errors, iteration):
        if ans is None or vllm_client is None:
            return
        if not tool_results:
            return
        combined_preview = "".join(
            tr.get("result_preview", "") for tr in tool_results
        )
        if len(combined_preview.strip()) < 50:
            return
        if all(not tr.get("success", True) for tr in tool_results):
            return

        results_text = "\n".join(
            f"- {tr['tool']}: {'FAIL' if not tr['success'] else 'OK'} "
            f"{tr.get('result_preview', '')[:400]}"
            for tr in tool_results
        )
        prompt_for_sn = f"User task: {user_message[:600]}"
        response_for_sn = (
            f"Iteration {iteration} tool results:\n{results_text}"
        )

        _ans = ans
        _hypo = hypothalamus
        _ddb = domain_db
        _store = store_learn_signals
        _ans_path = agent_dir / "ans_state.json" if agent_dir else None

        async def _iter_extract():
            try:
                sn_signals = await _ans.safety_net_extract_async(
                    vllm_client, hypothalamus=_hypo,
                    prompt_override=prompt_for_sn,
                    response_override=response_for_sn,
                    domain_db=_ddb,
                    adapter_name=inference_adapter,
                )
                if sn_signals and _ddb is not None and _store:
                    _store(sn_signals, user_message)
                if (sn_signals or _ans.signal_count > 0) and _ans_path:
                    _ans.save_state(_ans_path)
                if sn_signals:
                    logger.info(
                        "ANS iteration %d extract: +%d learnings",
                        iteration, len(sn_signals),
                    )
            except Exception as e:
                logger.debug("ANS iteration extract failed: %s", e)

        import asyncio
        try:
            asyncio.get_running_loop().create_task(_iter_extract())
        except RuntimeError:
            pass

    def ans_get_learnings():
        if ans is None:
            return None
        return ans._get_recent_learnings_summary(max_items=15)

    def _inject_signal(
        signal_type: str,
        content: str,
        source: str = "agentic_loop",
        prompt: str = "",
        response: str = "",
        domain_path: str | None = None,
    ):
        if ans is not None:
            ans.inject_signal(
                signal_type=signal_type,
                domain_path=domain_path,
                content=content,
                source=source,
                hypothalamus=hypothalamus,
                prompt=prompt,
                response=response,
            )

    def dampen_cortisol_fn(amount: float):
        if (
            hypothalamus is not None
            and hasattr(hypothalamus, "hormones")
            and "cortisol" in hypothalamus.hormones
        ):
            h = hypothalamus.hormones["cortisol"]
            h.level = max(0.0, h.level - amount)

    # ---------------------------------------------------------------
    # MEDIUM-PRIORITY hooks (instructions, user answer extraction)
    # ---------------------------------------------------------------

    def wm_push_instructions(instructions: list[str]):
        if working_memory is None:
            return
        working_memory.clear_instructions()
        for instr in instructions:
            working_memory.add_instruction(instr, source="task")

    def wm_get_instructions() -> str | None:
        if working_memory is None:
            return None
        instrs = working_memory.get_instructions()
        if not instrs:
            return None
        return "\n".join(f"- {i.content}" for i in instrs)

    def wm_clear_instructions():
        if working_memory is not None:
            working_memory.clear_instructions()

    def ans_extract_user_answer(
        question: str, answer: str, agentic_context: list[dict] | None = None,
    ):
        if ans is None or not answer or vllm_client is None:
            return

        _ans = ans
        _hypo = hypothalamus
        _ddb = domain_db
        _store = store_learn_signals
        _sn_prompt = f"Q: {question}\nA: {answer}"[:1000]
        _history = list(agentic_context) if agentic_context else []
        _ans_path = agent_dir / "ans_state.json" if agent_dir else None

        async def _extract():
            try:
                sn = await _ans.safety_net_extract_async(
                    vllm_client, hypothalamus=_hypo,
                    prompt_override=_sn_prompt,
                    response_override="",
                    history=_history,
                    domain_db=_ddb,
                    adapter_name=inference_adapter,
                )
                if sn and _ddb is not None and _store:
                    _store(sn, answer)
                if (sn or _ans.signal_count > 0) and _ans_path:
                    _ans.save_state(_ans_path)
                if sn:
                    facts = [
                        s.pipe_fact or s.content
                        for s in sn if s.signal_type == "LEARN"
                    ]
                    if facts:
                        from nls.runtime.learn_dedup import (
                            collect_known_keys_from_ans,
                            filter_new_learn_facts,
                            learning_dedup_key,
                            merge_known_from_broadcast_cache,
                            remember_broadcast_keys,
                        )

                        _known = collect_known_keys_from_ans(_ans)
                        _known.update(
                            merge_known_from_broadcast_cache(
                                _ans._ui_broadcast_learn_keys,
                            ),
                        )
                        facts = filter_new_learn_facts(facts, _known)
                        if facts:
                            remember_broadcast_keys(
                                _ans._ui_broadcast_learn_keys,
                                [learning_dedup_key(f) for f in facts],
                            )
                    if facts:
                        try:
                            from server.main import app
                            cm = getattr(app.state, "connection_manager", None)
                            if cm is not None:
                                await cm.broadcast(agent_id, {
                                    "type": "safety_net_learned",
                                    "facts": facts,
                                })
                        except Exception:
                            pass
            except Exception as e:
                logger.warning("ANS ask_user extraction failed: %s", e)

        import asyncio
        try:
            asyncio.get_running_loop().create_task(_extract())
        except RuntimeError:
            pass

    # Resolve log_event callable
    _log_event_fn = None
    if event_logger is not None and getattr(event_logger, "enabled", False):
        _log_event_fn = event_logger.log

    return AgenticHooks(
        # Core lifecycle
        on_tool_success=on_tool_success,
        on_tool_error=on_tool_error,
        on_turn_end=on_turn_end,
        on_agent_end=on_agent_end,
        should_abort=should_abort,
        get_cortisol=get_cortisol,
        tick_hypothalamus=tick_hypo,
        # ANS hooks
        preflight_knowledge=preflight_knowledge,
        ans_collect_tool_event=ans_collect_tool_event,
        ans_tool_learning=ans_tool_learning,
        ans_record_task=ans_record_task,
        ans_get_task_context=ans_get_task_context,
        ans_get_context=ans_get_context,
        ans_checkpoint=ans_checkpoint,
        ans_iteration_extract=ans_iteration_extract,
        ans_get_learnings=ans_get_learnings,
        ans_extract_user_answer=ans_extract_user_answer,
        # WM hooks
        wm_get_context=wm_get_context,
        wm_activate=wm_activate,
        wm_push_task_goals=wm_push_task_goals,
        wm_begin_task_epoch=wm_begin_task_epoch_legacy,
        wm_mark_task_goal_done=wm_mark_task_goal_done,
        wm_prune_supporting_facts_for_goal=wm_prune_supporting_facts_for_goal_legacy,
        wm_has_pending_task_goals=wm_has_pending_task_goals,
        wm_push_goals=wm_push_goals,
        wm_set_plan_position=wm_set_plan_position,
        wm_refresh_todo_board=wm_refresh_todo_board,
        wm_push_instructions=wm_push_instructions,
        wm_get_instructions=wm_get_instructions,
        wm_clear_instructions=wm_clear_instructions,
        wm_upsert_digest=wm_upsert_digest,
        wm_consolidate_session=wm_consolidate_session,
        wm_save=wm_save_fn,
        # Plan hooks
        plan_register_file=plan_register_file,
        update_todo_status=update_todo_status,
        # Context & evaluator hooks
        transform_context=_transform_context,
        get_interoceptive_snapshot=get_interoceptive_snapshot,
        # Signal & recovery hooks
        inject_signal=_inject_signal if ans is not None else None,
        dampen_cortisol=dampen_cortisol_fn,
        # Event logging
        log_event=_log_event_fn,
    )


# ===================================================================
# v4 hooks builder
# ===================================================================


def build_config_v4(agent_config: dict[str, Any]) -> Any:
    """Build LoopConfig from agent config dict."""
    from .types import LoopConfig

    cfg = agent_config.get("agency", {}).get("agentic_loop", {})
    return LoopConfig(
        max_iterations=cfg.get("max_iterations", 100),
        max_iterations_extension=cfg.get("max_iterations_extension", 50),
        max_total_iterations=cfg.get("max_total_iterations", 300),
        max_tool_calls=cfg.get("max_tool_calls", 200),
        per_tool_retry_limit=cfg.get("per_tool_retry_limit", 5),
        total_timeout_seconds=cfg.get("total_timeout_seconds", 1800.0),
        total_timeout_extension_seconds=cfg.get("total_timeout_extension_seconds", 300.0),
        max_timeout_extensions=cfg.get("max_timeout_extensions", 3),
        tool_timeout_seconds=cfg.get("tool_timeout_seconds", 30.0),
        context_window_tokens=cfg.get("context_window_tokens", 65_536),
        reserve_tokens=cfg.get("reserve_tokens", 6_144),
        keep_recent_tokens=cfg.get("keep_recent_tokens", 40_000),
        digest_threshold=cfg.get("digest_threshold", 2_000),
        result_max_chars=cfg.get("result_max_chars", 20_000),
        anchor_tool_result_min_chars=cfg.get("anchor_tool_result_min_chars", 4_000),
        relay_compact_message_chars=cfg.get("relay_compact_message_chars", 32_000),
        max_new_tokens=cfg.get("max_new_tokens", 16_000),
        compaction_timeout=cfg.get("compaction_timeout", 45.0),
        temperature=cfg.get("temperature", 1.0),
        top_p=cfg.get("top_p", 0.95),
        top_k=cfg.get("top_k", 20),
        min_p=cfg.get("min_p", 0.0),
        presence_penalty=cfg.get("presence_penalty", 1.5),
        repetition_penalty=cfg.get("repetition_penalty", 1.0),
        enable_parallel_tools=cfg.get("enable_parallel_tools", True),
        enable_cognitive_digest=cfg.get("enable_cognitive_digest", True),
        enable_delegation=cfg.get("enable_delegation", True),
    )


def build_hooks_v4(
    *,
    agent_id: str = "",
    agent_dir: Path | None = None,
    working_memory: Any | None = None,
    dual_wm: Any | None = None,
    hypothalamus: Any | None = None,
    domain_db: Any | None = None,
    ans: Any | None = None,
    vllm_client: Any | None = None,
    inference_adapter: str | None = None,
    store_learn_signals: Any | None = None,
    config: dict[str, Any] | None = None,
    event_logger: Any | None = None,
    agent_tools: list | None = None,
    thalamic_route_fn: Any | None = None,
    narrative_self: Any | None = None,
    theory_of_mind: Any | None = None,
    source: str = "",
    self_state: Any | None = None,
    network_dynamics: Any | None = None,
    outbound_gate: Any | None = None,
) -> LoopHooks:
    """Build v4 LoopHooks wiring NLS cognitive layer to the v4 loop.

    Reuses the same inner functions as build_hooks() but maps them
    to the simplified LoopHooks interface.
    """
    import asyncio as _asyncio
    config = config or {}
    _source = source

    # Snapshot pre-existing plans/todos so channel loops can distinguish
    # between inherited state and work they created themselves.
    _pre_existing_plan_ids: set[str] = set()
    _pre_existing_todo_ids: set[str] = set()
    if _source and _source.startswith("user:channel") and agent_tools:
        for _t in agent_tools:
            if hasattr(_t, "get_store") and getattr(_t, "name", "") == "plan":
                try:
                    for _p in _t.get_store().list_plans():
                        if _p.status in ("planning", "in_progress", "blocked"):
                            _pre_existing_plan_ids.add(_p.id)
                except Exception:
                    pass
            if getattr(_t, "name", "") == "todo":
                try:
                    for _it in _t._store.list_items(status="in_progress"):
                        _pre_existing_todo_ids.add(_it.id)
                except Exception:
                    pass

    # ----- Working Memory -----

    # Track render mode for compose_context (updated by loop to AgentMode.value)
    _render_mode_ref: list[str] = ["executing"]
    # Mutable ref for loop state signals → drives Cryptex ring priorities
    _loop_state_ref: dict[str, Any] = {}

    def _transform_context_v4(ctx: list[dict]) -> list[dict]:
        """Refresh WM context each iteration.

        When the working memory supports compose_context() (Cryptex),
        replaces the first 1-2 system messages entirely.  Otherwise
        falls back to regex replacement of the [WORKING MEMORY] block.
        """
        if not ctx or working_memory is None:
            return ctx

        # Advance NetworkDynamics per agentic iteration so ECN/SN/DMN
        # stay live while the inner loop is paused for heavy inference.
        if network_dynamics is not None and self_state is not None:
            try:
                _wm_avg = 0.0
                if working_memory is not None and hasattr(working_memory, "get_avg_salience"):
                    try:
                        if working_memory.get_slot_count() > 0:
                            _wm_avg = working_memory.get_avg_salience()
                    except Exception:
                        pass
                network_dynamics.update(
                    engagement=getattr(self_state, "engagement", 0.5),
                    arousal=getattr(self_state, "arousal", 0.3),
                    delta_ratio=getattr(self_state, "delta_ratio", 0.0),
                    turns_since_input=getattr(self_state, "turns_since_input", 0),
                    frustration=getattr(self_state, "frustration", 0.0),
                    prediction_error=getattr(self_state, "prediction_error", 0.0),
                    energy=getattr(self_state, "energy", 0.5),
                    wm_avg_salience=_wm_avg,
                )
                self_state.network_ecn = network_dynamics.ecn
                self_state.network_sn = network_dynamics.sn
                self_state.network_dmn = network_dynamics.dmn
                self_state.dominant_network = network_dynamics.dominant
                # Inject network state into loop_state_ref so
                # detect_cognitive_phase can read it
                _loop_state_ref["network_ecn"] = network_dynamics.ecn
                _loop_state_ref["network_sn"] = network_dynamics.sn
                _loop_state_ref["network_dmn"] = network_dynamics.dmn
                _loop_state_ref["dominant_network"] = network_dynamics.dominant
            except Exception:
                logger.debug("ND update in bridge failed", exc_info=True)

        # Inject hormone levels into loop_state_ref for phase detection
        if hypothalamus is not None:
            try:
                _levels = hypothalamus.get_levels()
                _loop_state_ref["cortisol"] = _levels.get("cortisol", 0.0)
                _loop_state_ref["oxytocin"] = _levels.get("oxytocin", 0.0)
            except Exception:
                pass

        # Absorb ANS signals into Cryptex rings before composing
        if ans is not None and hasattr(ans, "absorb_signals_to_rings"):
            try:
                ans.absorb_signals_to_rings(working_memory)
            except Exception:
                pass

        # Resolve the Cryptex compositor: dual_wm IS the CryptexMemory,
        # while working_memory may be a plain WorkingMemory view (from
        # dual_wm.active) that lacks compose_context / update_ring_priorities.
        _compositor = dual_wm if (dual_wm is not None and hasattr(dual_wm, "compose_context")) else working_memory

        # Update ring priorities based on cognitive phase before composing
        _detected_phase = ""
        if hasattr(_compositor, "update_ring_priorities"):
            try:
                from nls.agentic.skill_discovery_boost import (
                    sync_skill_discovery_boost_flag,
                )
                sync_skill_discovery_boost_flag(
                    _loop_state_ref,
                    int(_loop_state_ref.get("iteration", 0) or 0),
                )
                _compositor.update_ring_priorities(_loop_state_ref)
                _detected_phase = getattr(_compositor, "_cognitive_phase", "")
            except Exception:
                pass

        # Try compose_context path (Cryptex compositor)
        if hasattr(_compositor, "compose_context"):
            try:
                render_mode = _render_mode_ref[0] if _render_mode_ref else "executing"
                fresh = _compositor.compose_context(
                    render_mode=render_mode,
                    token_budget=55_000,
                    state=_loop_state_ref,
                )
                if fresh and len(fresh) >= 1:
                    _total_chars = sum(len(m.get("content", "")) for m in fresh)
                    _ring_order = []
                    if hasattr(_compositor, "get_priority_ordered_rings"):
                        _ring_order = _compositor.get_priority_ordered_rings()[:8]
                    logger.info(
                        "Cryptex compose_context: phase=%s msgs=%d chars=%d "
                        "top_rings=%s render_mode=%s iter=%s",
                        _detected_phase, len(fresh), _total_chars,
                        _ring_order, render_mode,
                        _loop_state_ref.get("iteration", "?"),
                    )
                    if _log_event_fn is not None:
                        try:
                            _log_event_fn(
                                "cryptex_compose",
                                phase=_detected_phase,
                                render_mode=render_mode,
                                msg_count=len(fresh),
                                total_chars=_total_chars,
                                top_rings=_ring_order,
                                iteration=_loop_state_ref.get("iteration", 0),
                                network_ecn=round(_loop_state_ref.get("network_ecn", 0), 3),
                                network_sn=round(_loop_state_ref.get("network_sn", 0), 3),
                                network_dmn=round(_loop_state_ref.get("network_dmn", 0), 3),
                                dominant_network=_loop_state_ref.get("dominant_network", ""),
                                cortisol=round(_loop_state_ref.get("cortisol", 0), 3),
                                coordinator_mode=_loop_state_ref.get("coordinator_mode", False),
                                active_mode=_loop_state_ref.get("active_mode", "executing"),
                                sys_prompt_preview=fresh[0].get("content", "")[:500],
                            )
                        except Exception as _evt_err:
                            logger.warning(
                                "cryptex_compose event logging failed: %s",
                                _evt_err, exc_info=True,
                            )
                    else:
                        logger.warning(
                            "cryptex_compose: _log_event_fn is None "
                            "(event_logger=%s, enabled=%s)",
                            event_logger is not None,
                            getattr(event_logger, "enabled", "N/A"),
                        )
                    # Replace first system message (identity+env+behavioral)
                    sys_indices = [
                        i for i, m in enumerate(ctx)
                        if m.get("role") == "system"
                    ]
                    if sys_indices:
                        ctx[sys_indices[0]] = fresh[0]
                        if len(fresh) > 1 and len(sys_indices) > 1:
                            ctx[sys_indices[1]] = fresh[1]
                        elif len(fresh) > 1:
                            ctx.insert(sys_indices[0] + 1, fresh[1])
                    return ctx
                else:
                    logger.warning(
                        "Cryptex compose_context returned empty (phase=%s)",
                        _detected_phase,
                    )
            except Exception as _cc_err:
                logger.warning(
                    "compose_context failed (type=%s): %s — fallback to legacy",
                    type(_cc_err).__name__, _cc_err, exc_info=True,
                )

        # Legacy path: regex-replace [WORKING MEMORY] block
        logger.warning(
            "Cryptex compose_context NOT used — falling through to legacy WM path "
            "(has_compose_wm=%s, has_compose_dual=%s, dual_wm_type=%s, phase=%s)",
            hasattr(working_memory, "compose_context"),
            hasattr(dual_wm, "compose_context") if dual_wm is not None else "N/A",
            type(dual_wm).__name__ if dual_wm is not None else "None",
            _detected_phase,
        )
        try:
            try:
                fresh_wm = working_memory.to_context_string(render_context=_source)
            except TypeError:
                fresh_wm = working_memory.to_context_string()
        except Exception:
            return ctx
        if not fresh_wm:
            return ctx
        for msg in ctx:
            if msg.get("role") == "system" and "[WORKING MEMORY" in (msg.get("content") or ""):
                msg["content"] = re.sub(
                    r"\[WORKING MEMORY[^\]]*\].*?\[END WORKING MEMORY\]",
                    fresh_wm,
                    msg["content"],
                    flags=re.DOTALL,
                )
                return ctx
        for msg in ctx:
            if msg.get("role") == "user" and "[WORKING MEMORY" in (msg.get("content") or ""):
                msg["content"] = re.sub(
                    r"\[WORKING MEMORY[^\]]*\].*?\[END WORKING MEMORY\]",
                    fresh_wm,
                    msg["content"],
                    flags=re.DOTALL,
                )
                break
        return ctx

    def _wm_save():
        try:
            if dual_wm is not None:
                dual_wm.save(agent_dir)
            elif working_memory is not None and agent_dir is not None:
                working_memory.save(agent_dir / "working_memory_state.json")
        except Exception:
            logger.debug("WM save failed", exc_info=True)

    def _wm_consolidate(summary: str):
        if dual_wm is not None:
            try:
                dual_wm.consolidate_session(summary)
            except Exception:
                logger.debug("WM consolidation failed (dual_wm)", exc_info=True)
        elif working_memory is not None:
            try:
                working_memory.consolidate_session(summary)
            except Exception:
                logger.debug("WM consolidation failed", exc_info=True)

    def _wm_upsert_digest(domain: str, summary: str, insights: str, source: str):
        _content = f"{summary}\n{insights}"
        if dual_wm is not None:
            try:
                dual_wm.upsert_fact(
                    domain=domain, content=_content, source="digest",
                )
            except Exception:
                pass
        elif working_memory is not None:
            try:
                working_memory.upsert_fact(
                    domain=domain, content=_content, source="digest",
                )
            except Exception:
                pass

    # Use dual_wm (CryptexMemory) for ring writes when available;
    # working_memory is a plain WM view whose writes don't reach rings.
    _ring_wm = dual_wm if dual_wm is not None else working_memory

    def _wm_set_plan_position(position: str):
        if _ring_wm is not None:
            try:
                _ring_wm.set_plan_position(position)
                if hasattr(_ring_wm, "upsert_orchestration_slot") and position:
                    _lines = position.split("\n")
                    _summary = "\n".join(_lines[:6]) if len(_lines) > 6 else position
                    _ring_wm.upsert_orchestration_slot(
                        domain="orch.plan_position",
                        content=_summary,
                        source="plan",
                    )
            except Exception:
                pass

    def _wm_push_instructions(instructions: list[str]):
        if _ring_wm is None:
            return
        _ring_wm.clear_instructions()
        for instr in instructions:
            _ring_wm.add_instruction(instr, source="task")

    def _wm_push_task_goals(goals: list[str]):
        """Replace tactical goals with the current user-task goals."""
        if _ring_wm is None or not goals:
            return
        clear_goals = getattr(_ring_wm, "clear_goals", None)
        add_goal = getattr(_ring_wm, "add_goal", None)
        if not callable(clear_goals) or not callable(add_goal):
            return
        try:
            clear_goals("tactical")
            for goal in goals[:5]:
                add_goal(level="tactical", content=goal, source="task_extract")
                if ans is not None:
                    ans.inject_signal(
                        signal_type="LEARN",
                        domain_path=f"Goal.Tactical.{goal[:40].replace(' ', '_')}",
                        content=f"Goal.Tactical: {goal}",
                        source="goal_extraction",
                        hypothalamus=hypothalamus,
                    )
        except Exception:
            pass

    def _wm_refresh_todo_board():
        """Build a compact Kanban snapshot and push it into WM."""
        if _ring_wm is None:
            return
        from nls.skills.bundled import todo_list as _tmod
        _todo_mgr = getattr(_tmod, "_todo_manager", None)
        if _todo_mgr is None:
            return
        try:
            store = _todo_mgr.get_store(agent_id)
            items = store.list_items()
            if not items:
                _ring_wm.set_todo_board("")
                return
            active = [i for i in items if i.status not in ("done", "cancelled")]
            done_count = sum(1 for i in items if i.status == "done")
            if not active and done_count == 0:
                _ring_wm.set_todo_board("")
                return
            lines = [f"Todo Board ({len(active)} active, {done_count} done):"]
            for item in active:
                prio = f" P{item.priority}" if getattr(item, "priority", 0) else ""
                plan_tag = f" plan:{item.plan_id[:8]}" if getattr(item, "plan_id", "") else ""
                lines.append(
                    f"  [{item.status}] {item.title} "
                    f"(id:{item.id}{prio}{plan_tag})"
                )
            _ring_wm.set_todo_board("\n".join(lines))
        except Exception:
            pass

    def _wm_begin_task_epoch(
        *,
        loop_id: str,
        goals: list[str],
        dispatch_source: str,
    ) -> None:
        from nls.agentic.task_epoch_hygiene import begin_task_epoch

        begin_task_epoch(
            dual_wm,
            working_memory,
            loop_id=loop_id,
            goals=list(goals or []),
            dispatch_source=dispatch_source or "user",
        )

    def _wm_prune_supporting_facts_for_goal(goal: str) -> int:
        from nls.agentic.task_epoch_hygiene import prune_supporting_facts_for_goal

        return prune_supporting_facts_for_goal(dual_wm, working_memory, goal)

    def _wm_mark_task_goal_done_cryptex(substring: str) -> bool:
        if _ring_wm is None:
            return False
        if not isinstance(substring, str):
            return False
        remover = getattr(_ring_wm, "remove_goals_where", None)
        if not callable(remover):
            return False
        removed = remover(
            lambda g: (
                getattr(g, "level", "") == "tactical"
                and getattr(g, "source", "") == "task_extract"
                and isinstance(getattr(g, "content", None), str)
                and substring.lower() in g.content.lower()
            ),
        )
        return len(removed) > 0

    # ----- Goals & Hints -----

    def _on_goals_extracted(goals: list[str]):
        if not goals:
            return
        try:
            _wm_push_task_goals(goals[:5])
        except Exception:
            pass

    def _on_hints_extracted(hints: list[str]):
        if working_memory is None or not hints:
            return
        clean_hints: list[str] = []
        for h in hints:
            if _HINT_CREDENTIAL_RE.search(h):
                _hl = h.lower()
                _domain = "Project.Credential.Detected"
                if "ghp_" in _hl or "gho_" in _hl or "github" in _hl:
                    _domain = "Project.Credential.GitHub"
                elif "sk-ant-" in _hl or "anthropic" in _hl:
                    _domain = "Project.Credential.Anthropic"
                elif "sk-" in _hl or "openai" in _hl:
                    _domain = "Project.Credential.OpenAI"
                elif "postgres" in _hl:
                    _domain = "Project.Credential.Database"
                elif "assembly" in _hl:
                    _domain = "Project.Credential.AssemblyAI"
                try:
                    working_memory.upsert_credential(
                        domain=_domain, content=h,
                        source="task_hints", salience=1.0,
                    )
                except Exception:
                    clean_hints.append(h)
            else:
                clean_hints.append(h)
        if clean_hints:
            try:
                working_memory.upsert_fact(
                    domain="Task.Hints",
                    content=" | ".join(clean_hints),
                )
            except Exception:
                pass

    # ----- ANS / Hypothalamus -----

    def _on_tool_success(name: str, args: dict, result: Any):
        _track_tool_for_narrative(name, args, result)
        if hypothalamus is not None:
            try:
                hypothalamus.on_signal("tool_success")
            except Exception:
                pass
        if ans is not None:
            try:
                ans.inject_signal(
                    signal_type="EVALUATE:PFC.ToolSuccess",
                    domain_path=f"Task.Tool.{name}",
                    content=f"{name}: {str(getattr(result, 'content', ''))[:200]}",
                    source="agentic_loop",
                    hypothalamus=hypothalamus,
                )
            except Exception:
                pass
        try:
            _brain_bus.emit(BrainSignal(
                type=BrainSignalType.TOOL_RESULT,
                source="agentic:v5",
                tool_name=name,
                tool_args=args or {},
                tool_result=str(getattr(result, "content", ""))[:300],
                is_agentic=True,
                metadata={"success": True},
            ))
        except Exception:
            pass

        # Absorb delegate knowledge digest into orchestrator's Cryptex
        _absorb_target = dual_wm if dual_wm is not None else working_memory
        if (
            name == "delegate"
            and _absorb_target is not None
            and hasattr(_absorb_target, "absorb_delegate_digest")
        ):
            try:
                _result_text = str(getattr(result, "content", ""))
                _dstart = _result_text.find("[DELEGATE KNOWLEDGE DIGEST]")
                _dend = _result_text.find("[END DIGEST]")
                if _dstart != -1 and _dend != -1:
                    import json as _json_bridge
                    _djson = _result_text[_dstart + len("[DELEGATE KNOWLEDGE DIGEST]"):_dend].strip()
                    _digest_data = _json_bridge.loads(_djson)
                    _absorb_target.absorb_delegate_digest(_digest_data)
                    logger.info("Absorbed delegate knowledge digest into orchestrator Cryptex")
            except Exception as _abs_err:
                logger.debug("Delegate digest absorption failed: %s", _abs_err)

    def _on_tool_error(name: str, args: dict, result: Any):
        _track_tool_for_narrative(name, args, result)
        if hypothalamus is not None:
            try:
                hypothalamus.on_signal("tool_error")
            except Exception:
                pass
        if ans is not None:
            try:
                ans.inject_signal(
                    signal_type="EVALUATE:PFC.ToolError",
                    domain_path=f"Task.Tool.{name}",
                    content=f"{name}: {str(getattr(result, 'content', ''))[:200]}",
                    source="agentic_loop",
                    hypothalamus=hypothalamus,
                )
            except Exception:
                pass
        try:
            _brain_bus.emit(BrainSignal(
                type=BrainSignalType.TOOL_RESULT,
                source="agentic:v5",
                tool_name=name,
                tool_args=args or {},
                tool_result=str(getattr(result, "content", ""))[:300],
                is_agentic=True,
                metadata={"success": False},
            ))
        except Exception:
            pass

    def _on_after_tool(name: str, args: dict, result: Any):
        result_str = str(getattr(result, "content", ""))
        is_error = bool(getattr(result, "is_error", False))
        details = dict(getattr(result, "details", None) or {})
        if _loop_state_ref:
            details.setdefault(
                "coordinator_mode",
                bool(_loop_state_ref.get("coordinator_mode")),
            )
            details.setdefault(
                "delegates_active",
                int(_loop_state_ref.get("delegate_count", 0) or 0) > 0,
            )
            details.setdefault(
                "active_mode",
                str(_loop_state_ref.get("active_mode") or ""),
            )
        if working_memory is not None:
            _extract_operational_wm_v4(name, args, result_str)
        _cryptex = dual_wm if (
            dual_wm is not None and hasattr(dual_wm, "absorb_tool_result")
        ) else None
        if _cryptex is None and working_memory is not None:
            _cryptex = working_memory if hasattr(
                working_memory, "absorb_tool_result",
            ) else None
        if _cryptex is not None:
            try:
                _cryptex.absorb_tool_result(
                    name,
                    args or {},
                    result_str,
                    is_error,
                    details=details,
                )
            except Exception:
                logger.debug("Cryptex absorb_tool_result failed", exc_info=True)

    def _extract_operational_wm_v4(tool_name: str, args: dict, result_str: str):
        """Regex-based operational fact extraction — zero GPU cost."""
        if working_memory is None:
            return
        wm = working_memory
        result_str = result_str or ""
        if tool_name == "bash":
            cmd = args.get("command", "")
            m = _CLONE_RE.search(result_str)
            if m:
                wm.upsert_fact(
                    domain="Project.Root",
                    content=f"Repository cloned to directory: {m.group(1)}",
                )
            if cmd.strip().startswith("cd "):
                target = cmd.strip()[3:].strip().strip("'\"")
                wm.upsert_fact(
                    domain="System.CWD",
                    content=f"Working directory changed to: {target}",
                )
        elif tool_name == "read":
            path = args.get("path", "")
            for m in _ENV_FILE_RE.finditer(result_str[:500]):
                wm.upsert_fact(
                    domain="Project.EnvFiles",
                    content=f"Environment file found: {m.group(1)}",
                )

    def _tick_hypothalamus(elapsed: float):
        if hypothalamus is not None:
            try:
                hypothalamus.tick(elapsed)
            except Exception:
                pass

    def _get_cortisol() -> float:
        if hypothalamus is None:
            return 0.2
        try:
            return hypothalamus.hormones.get("cortisol", type("H", (), {"level": 0.2})()).level
        except Exception:
            return 0.2

    _recent_tool_domain: list[str] = [""]
    _recent_tool_topic: list[str] = [""]

    def _track_tool_for_narrative(name: str, args: dict, result: Any):
        """Track last tool call for narrative episode domain/topic enrichment."""
        _recent_tool_domain[0] = f"Tool.{name}"
        _preview = ""
        if name == "bash":
            _cmd = (args.get("command", "") or "")[:80]
            _preview = f"bash: {_cmd}"
        elif name in ("plan", "team", "todo"):
            _action = args.get("action", "")
            _preview = f"{name}({_action})"
        elif name in ("read", "write", "edit"):
            _path = args.get("path", "")
            _preview = f"{name}: {_path.split('/')[-1] if '/' in _path else _path.split(chr(92))[-1] if chr(92) in _path else _path}"
        elif name in ("web_search", "web_fetch"):
            _preview = args.get("query", "") or args.get("url", "")
        elif name == "communicate":
            _preview = "user communication"
        else:
            _preview = name
        _recent_tool_topic[0] = _preview[:80] if _preview else name

    def _ans_on_response(user_input: str, response_text: str):
        if ans is not None:
            try:
                ans.on_response(
                    user_input, response_text, hypothalamus,
                    is_agentic=True,
                )
            except Exception:
                pass

        if narrative_self is not None:
            try:
                _cortisol = 0.0
                if hypothalamus is not None:
                    _hs = getattr(hypothalamus, "hormones", {})
                    if hasattr(_hs, "get"):
                        _c = _hs.get("cortisol", None)
                        if _c is not None:
                            _cortisol = getattr(_c, "level", 0.0)
                _turn = ans._turn_counter if ans is not None else 0
                # Derive mood from cortisol instead of hardcoding "focused"
                if _cortisol > 0.7:
                    _mood = "stressed"
                elif _cortisol > 0.4:
                    _mood = "tense"
                elif _cortisol > 0.2:
                    _mood = "focused"
                else:
                    _mood = "calm"
                _arousal = min(0.9, 0.3 + _cortisol)
                _valence = max(-0.3, 0.1 - _cortisol * 0.5)
                narrative_self.record_turn(
                    turn_number=_turn,
                    valence=_valence,
                    arousal=_arousal,
                    mood_label=_mood,
                    cortisol=_cortisol,
                    is_user_turn=False,
                    domain=_recent_tool_domain[0],
                    topic=_recent_tool_topic[0],
                )
            except Exception:
                pass

        if theory_of_mind is not None:
            try:
                theory_of_mind.update_from_turn(
                    user_input=user_input,
                    response=response_text,
                )
            except Exception:
                pass

    # Throttled ANS tool learning — max 1 in-flight, skip small results
    _learn_in_flight = [False]

    def _ans_tool_learning(tool_name: str, args: dict, result_text: str, user_msg: str):
        if ans is None or vllm_client is None:
            return
        if _learn_in_flight[0]:
            return
        if len(result_text) < 500:
            return
        # Set flag BEFORE create_task to prevent races
        _learn_in_flight[0] = True

        _ans = ans
        _hypo = hypothalamus
        _ddb = domain_db
        _store = store_learn_signals
        _ans_path = agent_dir / "ans_state.json" if agent_dir else None

        async def _llm_extract():
            try:
                llm_signals = await _ans.on_tool_result_async(
                    tool_name=tool_name, args=args,
                    result=result_text, user_message=user_msg,
                    hypothalamus=_hypo, vllm_client=vllm_client,
                    adapter_name=inference_adapter,
                )
                if llm_signals and _ddb is not None and _store:
                    _store(llm_signals, user_msg)
                if (llm_signals or _ans.signal_count > 0) and _ans_path:
                    _ans.save_state(_ans_path)
            except Exception:
                signals = _ans.on_tool_result(
                    tool_name=tool_name, args=args,
                    result=result_text, user_message=user_msg,
                    hypothalamus=_hypo,
                )
                if signals and _ddb is not None and _store:
                    _store(signals, user_msg)
            finally:
                _learn_in_flight[0] = False

        try:
            _asyncio.get_running_loop().create_task(_llm_extract())
        except RuntimeError:
            pass

    def _ans_record_task_complete(
        user_message: str,
        final_response: str,
        tools_used: list[str],
        success: bool,
        duration_s: float,
    ):
        if ans is None:
            return
        try:
            ans.record_task_complete(
                user_message=user_message,
                final_response=final_response,
                tools_used=tools_used,
                success=success,
                duration_ms=duration_s * 1000,
                hypothalamus=hypothalamus,
            )
        except Exception:
            pass

    def _ans_extract_user_answer_v4(question: str, answer: str):
        if ans is None or not answer or vllm_client is None:
            return
        _ans = ans
        _hypo = hypothalamus
        _ddb = domain_db
        _store = store_learn_signals
        _ans_path = agent_dir / "ans_state.json" if agent_dir else None

        async def _extract():
            try:
                sn = await _ans.safety_net_extract_async(
                    vllm_client, hypothalamus=_hypo,
                    prompt_override=f"Q: {question}\nA: {answer}"[:1000],
                    response_override="",
                    domain_db=_ddb,
                    adapter_name=inference_adapter,
                )
                if sn and _ddb is not None and _store:
                    _store(sn, answer)
                if (sn or _ans.signal_count > 0) and _ans_path:
                    _ans.save_state(_ans_path)
            except Exception:
                pass
        try:
            _asyncio.get_running_loop().create_task(_extract())
        except RuntimeError:
            pass

    # ----- Plan / Todo active-work check -----

    _is_channel_loop = bool(_source and _source.startswith("user:channel"))

    def _has_active_plan() -> bool:
        """Return True if the agent has open plan work or in-progress todos.

        Includes blocked/recovery plans (partial waves, false done) so background
        scheduling and iteration extensions stay engaged until truly finished.

        For channel-originated loops, only plans/todos created *during* this
        loop count — pre-existing orchestrator plans are ignored so the loop
        can terminate cleanly after a quick reply, while still sustaining
        itself if the channel message triggered new work.
        """
        if not agent_tools:
            return False
        from nls.agentic.plan_work import plan_needs_recovery, work_plan_has_open_steps

        _team_manager = None
        for tool in agent_tools:
            if getattr(tool, "name", "") == "plan":
                _team_manager = getattr(tool, "_team_manager", None)
                break

        for tool in agent_tools:
            if hasattr(tool, "get_store") and getattr(tool, "name", "") == "plan":
                try:
                    store = tool.get_store()
                    work = store.resolve_work_plan(
                        "", _team_manager, reopen=False,
                    )
                    if work is None:
                        continue
                    if _is_channel_loop and work.id in _pre_existing_plan_ids:
                        continue
                    if work_plan_has_open_steps(work):
                        return True
                    if plan_needs_recovery(work, _team_manager):
                        return True
                except Exception:
                    pass
            if getattr(tool, "name", "") == "todo":
                try:
                    todo_store = tool._store
                    in_progress = todo_store.list_items(status="in_progress")
                    if _is_channel_loop:
                        in_progress = [
                            i for i in in_progress
                            if i.id not in _pre_existing_todo_ids
                        ]
                    if in_progress:
                        return True
                except Exception:
                    pass
        return False

    def _plan_requires_team_delegation() -> bool:
        """True when the active plan has 2+ pending delegatable steps."""
        if not agent_tools:
            return False
        from .coordinator_guard import plan_requires_team_delegation as _needs_team
        for tool in agent_tools:
            if hasattr(tool, "get_store") and getattr(tool, "name", "") == "plan":
                try:
                    store = tool.get_store()
                    active = store.find_active()
                    if active and not active.all_steps_done():
                        if _is_channel_loop and active.id in _pre_existing_plan_ids:
                            continue
                        return _needs_team(active)
                except Exception:
                    pass
        return False

    def _plan_suppresses_raw_delegate() -> bool:
        """True when delegate() must be removed from the tool schema."""
        if not agent_tools:
            return False
        from .coordinator_guard import plan_suppresses_raw_delegate as _suppress
        for tool in agent_tools:
            if hasattr(tool, "get_store") and getattr(tool, "name", "") == "plan":
                try:
                    store = tool.get_store()
                    active = store.find_active()
                    if active and not active.all_steps_done():
                        if _is_channel_loop and active.id in _pre_existing_plan_ids:
                            continue
                        return _suppress(active)
                except Exception:
                    pass
        return False

    def _plan_has_pending_steps() -> bool:
        """True when work plan still has pending, failed, or recovery steps."""
        if not agent_tools:
            return False
        from nls.agentic.plan_work import work_plan_has_open_steps

        _team_manager = None
        for tool in agent_tools:
            if getattr(tool, "name", "") == "plan":
                _team_manager = getattr(tool, "_team_manager", None)
                break

        for tool in agent_tools:
            if hasattr(tool, "get_store") and getattr(tool, "name", "") == "plan":
                try:
                    store = tool.get_store()
                    active = store.resolve_work_plan(
                        "", _team_manager, reopen=False,
                    )
                    if active is None:
                        return False
                    if _is_channel_loop and active.id in _pre_existing_plan_ids:
                        return False
                    if work_plan_has_open_steps(active):
                        return True
                    return any(
                        s.status in ("pending", "in_progress", "failed")
                        for s in active.steps
                    )
                except Exception:
                    pass
        return False

    # ----- Preflight -----

    _OPS_PREFIXES = ("Project.", "System.", "Account.", "Repository.")

    def _get_preflight_knowledge(user_message: str) -> str | None:
        if domain_db is None:
            return None
        seen: set[str] = set()
        ops_lines: list[str] = []
        kw_lines: list[str] = []
        for prefix in _OPS_PREFIXES:
            try:
                for fact in domain_db.get_facts_by_prefix(prefix):
                    if fact.domain_path in seen:
                        continue
                    val = fact.current_value or ""
                    if "\n[context:" in val:
                        val = val.split("\n[context:")[0].strip()
                    if val and len(val) > 3:
                        seen.add(fact.domain_path)
                        ops_lines.append(f"- {fact.domain_path}: {val}")
            except Exception:
                pass
        try:
            from nls.runtime.inference import detect_factual_domains
            candidates = detect_factual_domains(user_message, domain_db)
            for fact in candidates[:10]:
                if fact.domain_path in seen:
                    continue
                val = fact.current_value or ""
                if "\n[context:" in val:
                    val = val.split("\n[context:")[0].strip()
                if val and len(val) > 3:
                    seen.add(fact.domain_path)
                    kw_lines.append(f"- {fact.domain_path}: {val}")
        except Exception:
            pass
        _recipe = None
        try:
            from nls.agentic.recipe_hints import match_recipe_hints
            _recipe = match_recipe_hints(user_message)
        except Exception:
            pass
        if not ops_lines and not kw_lines and not _recipe:
            return None
        parts = ["--- RECALLED KNOWLEDGE (relevant to this task) ---"]
        if ops_lines:
            parts.append("Operational context:")
            parts.extend(ops_lines[:15])
        if kw_lines:
            parts.append("Related knowledge:")
            parts.extend(kw_lines[:10])
        if _recipe:
            parts.append(_recipe)
        parts.append("--- END RECALLED KNOWLEDGE ---")
        return "\n".join(parts)

    # ----- Structured session learnings export -----

    def _get_wm_slots() -> list:
        """Get slots from WorkingMemory or DualWorkingMemory."""
        if working_memory is None:
            return []
        if hasattr(working_memory, "_slots"):
            return working_memory._slots
        if hasattr(working_memory, "active"):
            slots = list(working_memory.active._slots)
            if hasattr(working_memory, "common"):
                slots.extend(working_memory.common._slots)
            return slots
        return []

    def _extract_session_learnings() -> list[dict] | None:
        """Export structured learnings from the session.

        Scans WM operational facts and returns high-salience items as
        structured learning signals for the ANS buffer / sleep pipeline.
        """
        if working_memory is None:
            return None

        learnings: list[dict] = []
        _OPS_DOMAINS = ("Project.", "System.", "Account.", "Repository.")

        try:
            for slot in _get_wm_slots():
                if any(slot.domain.startswith(p) for p in _OPS_DOMAINS):
                    if slot.salience >= 0.5 and slot.content and len(slot.content) > 5:
                        learnings.append({
                            "type": "LEARNED_FACT",
                            "domain": slot.domain,
                            "content": slot.content[:500],
                            "salience": slot.salience,
                        })
        except Exception:
            logger.debug("Session learnings extraction failed", exc_info=True)

        if not learnings:
            return None

        if ans is not None:
            for item in learnings:
                try:
                    ans.inject_signal(
                        signal_type="LEARN",
                        domain_path=item["domain"],
                        content=f"{item['domain']}: {item['content']}",
                        source="wm_session_export",
                        hypothalamus=hypothalamus,
                    )
                except Exception:
                    pass

        return learnings

    # ----- Loop lifecycle -----

    def _on_loop_end(state: Any) -> None:
        """Post-loop cleanup: hypothalamus signals, WM goal cleanup, WM→ANS bridge."""
        try:
            _brain_bus.emit(BrainSignal(
                type=BrainSignalType.LOOP_END,
                source="agentic:v5",
                is_agentic=True,
                iteration=getattr(state, "iteration", 0),
                metadata={"exit_reason": getattr(state, "exit_reason", "")},
            ))
        except Exception:
            pass

        # Signal task outcome to hypothalamus (mirrors v3 on_agent_end)
        _aborted = getattr(state, "exit_reason", "") not in (
            "task_complete", "tool_requested_stop",
            "awaiting_delegates", "idle_monitor_yield", "",
        )
        if hypothalamus is not None:
            if getattr(state, "total_tool_calls", 0) > 0 and not _aborted:
                hypothalamus.on_signal("task_completed")
            if _aborted:
                hypothalamus.on_signal("task_failed")
            hypothalamus.tick(10.0)
            state._hormones_snapshot = {
                n: round(h.level, 3)
                for n, h in hypothalamus.hormones.items()
            }
            if agent_dir:
                try:
                    hypothalamus.save_state(agent_dir / "hypothalamus_state.json")
                except Exception:
                    pass

        # Clean up WM tactical goals (mirrors v3 on_agent_end logic)
        if working_memory is not None:
            wm = working_memory
            _is_plan = lambda g: getattr(g, "source", "") == "plan"
            _is_task_extract = lambda g: (
                getattr(g, "source", "") in ("task_extract", "goal_extraction")
                and getattr(g, "level", "") == "tactical"
            )
            if _aborted:
                wm.mutate_goals(
                    lambda g: setattr(g, "salience", max(g.salience * 0.5, 0.1)),
                    _is_plan,
                )
                wm.mutate_goals(
                    lambda g: setattr(g, "salience", max(g.salience * 0.5, 0.1)),
                    _is_task_extract,
                )
            else:
                removed = wm.remove_goals_where(_is_plan)
                if removed:
                    wm.upsert_fact(
                        domain="Task.RecentlyCompleted",
                        content=f"Just completed: {removed[0].content[:200]}",
                        source="plan", salience=0.7,
                    )
                wm.remove_goals_where(_is_task_extract)
            _wm_save()

        if working_memory is None or ans is None:
            return
        _OPS_DOMAINS = ("Project.", "System.", "Account.", "Repository.")
        try:
            for slot in _get_wm_slots():
                if any(slot.domain.startswith(p) for p in _OPS_DOMAINS):
                    if slot.salience >= 0.7 and slot.content:
                        if domain_db is not None:
                            existing = None
                            try:
                                existing = domain_db.get_fact(slot.domain)
                            except Exception:
                                pass
                            if existing is not None:
                                continue
                        ans.inject_signal(
                            signal_type="LEARN",
                            domain_path=slot.domain,
                            content=f"{slot.domain}: {slot.content[:300]}",
                            source="wm_bridge",
                            hypothalamus=hypothalamus,
                        )
        except Exception:
            logger.debug("WM→ANS bridge failed", exc_info=True)

    # ----- Thalamic routing -----

    _refresh_thalamic = None
    if thalamic_route_fn is not None:
        def _refresh_thalamic_route() -> dict[str, Any] | None:
            try:
                xargs, _meta_weight, _thinking = thalamic_route_fn(agentic=True)
                return xargs
            except Exception:
                logger.debug("refresh_thalamic_route failed", exc_info=True)
                return None

        _refresh_thalamic = _refresh_thalamic_route

    # ----- Steering messages (Pi-style copilot_queue drain) -----

    async def _get_steering_messages() -> list[dict]:
        """Non-blocking drain of copilot_queue for mid-loop steering.

        Each iteration the loop checks for user messages or internal
        announcements (e.g. delegate completion) that arrived while the
        agent was busy.  Mirrors Pi's getSteeringMessages() pattern.

        Orchestrator hints are detected and promoted to system-level
        priority messages so the delegate is forced to stop and reassess.
        """
        q = hooks_ref[0].copilot_queue if hooks_ref else None
        if q is None:
            logger.debug("[STEERING] copilot_queue is None (hooks_ref=%s)", bool(hooks_ref))
            return []
        msgs: list[dict] = []
        _has_hint = False
        _q_size = q.qsize()
        while not q.empty():
            try:
                item = q.get_nowait()
                if isinstance(item, str) and item.strip():
                    msgs.append({"role": "user", "content": item})
                    logger.info("[STEERING] drained str msg (len=%d): %.80s", len(item), item)
                elif isinstance(item, dict) and item.get("content"):
                    msgs.append(item)
                    _content = item.get("content", "")
                    if "[ORCHESTRATOR HINT]" in _content or "[ORCHESTRATOR REVIEW" in _content:
                        _has_hint = True
                    logger.info("[STEERING] drained dict msg: %.80s", _content[:80])
                else:
                    logger.warning("[STEERING] skipped item type=%s repr=%.100s", type(item).__name__, repr(item)[:100])
            except Exception:
                break
        if _q_size > 0:
            logger.info("[STEERING] queue had %d items, produced %d msgs", _q_size, len(msgs))
        if _has_hint:
            msgs.append({
                "role": "system",
                "content": (
                    "⚠ ORCHESTRATOR DIRECTIVE RECEIVED.\n"
                    "STOP what you are doing. Read the orchestrator's "
                    "message above carefully. REASSESS your current "
                    "approach in light of this new guidance. Then adjust "
                    "your plan and continue with the corrected approach.\n"
                    "Do NOT continue your previous chain of thought as if "
                    "nothing happened."
                ),
            })
            logger.info("[STEERING] amplified orchestrator hint with forced-reassess directive")
        return msgs

    hooks_ref: list[LoopHooks | None] = [None]

    # ----- Orchestration WM hooks -----

    # Use CryptexMemory (dual_wm) for orchestration updates so that
    # _snapshot_orch_to_ring fires and team data persists in the ring.
    _orch_wm = dual_wm if dual_wm is not None else working_memory

    def _wm_orch_update_team(
        team_id: str, plan_id: str = "", status: str = "running",
        members: list | None = None,
    ):
        if _orch_wm is not None:
            try:
                _orch_wm.orch_update_team(team_id, plan_id, status, members)
            except Exception:
                logger.warning("wm_orch_update_team failed", exc_info=True)

    def _wm_orch_record_decision(
        action: str, context: str, outcome: str = "",
        team_id: str = "", member_idx: int = -1,
    ):
        if _orch_wm is not None:
            try:
                _orch_wm.orch_record_decision(
                    action, context, outcome, team_id, member_idx,
                )
            except Exception:
                logger.debug("wm_orch_record_decision failed", exc_info=True)

    def _wm_orch_set_coordinator_phase(phase: str, detail: str = "") -> None:
        if _orch_wm is not None:
            try:
                _orch_wm.orch_set_coordinator_phase(phase, detail)
            except Exception:
                logger.debug("wm_orch_set_coordinator_phase failed", exc_info=True)

    def _wm_orch_add_escalation(
        team_id: str, member_idx: int, context: str,
    ):
        if _orch_wm is not None:
            try:
                _orch_wm.orch_add_escalation(team_id, member_idx, context)
            except Exception:
                logger.debug("wm_orch_add_escalation failed", exc_info=True)

    def _wm_orch_resolve_escalation(
        team_id: str, member_idx: int, outcome: str,
    ):
        if _orch_wm is not None:
            try:
                _orch_wm.orch_resolve_escalation(team_id, member_idx, outcome)
            except Exception:
                logger.debug("wm_orch_resolve_escalation failed", exc_info=True)

    def _wm_sync_wake_attention_board(team_manager: Any) -> None:
        compositor = dual_wm if (
            dual_wm is not None and hasattr(dual_wm, "set_wake_attention_board")
        ) else working_memory
        if compositor is None or not hasattr(compositor, "set_wake_attention_board"):
            return
        try:
            from nls.agentic.wake_coordination import build_batched_completion_review_message

            if _orch_wm is not None and hasattr(_orch_wm, "orch_prune_stale_escalations"):
                reconcile = getattr(team_manager, "reconcile_with_delegates", None)
                if reconcile is not None:
                    try:
                        reconcile(persist=False)
                    except Exception:
                        pass

                def _member_terminal(team_id: str, member_idx: int) -> bool:
                    team = team_manager._teams.get(team_id)
                    if team is None or member_idx < 0 or member_idx >= len(team.members):
                        return True
                    return team.members[member_idx].status in (
                        "done", "failed", "cancelled",
                    )

                try:
                    pruned = _orch_wm.orch_prune_stale_escalations(_member_terminal)
                    if pruned:
                        logger.debug(
                            "wm_sync: pruned %d stale orchestration escalation(s)",
                            pruned,
                        )
                except Exception:
                    logger.debug("orch_prune_stale_escalations failed", exc_info=True)

            parts: list[str] = []
            pending = getattr(team_manager, "_pending_completion_reviews", {}) or {}
            team_ids = {
                info.get("team_id", "")
                for info in pending.values()
                if info.get("team_id")
            }
            for tid in sorted(team_ids):
                parts.append(build_batched_completion_review_message(team_manager, tid))
            active = team_manager.get_active_summary()
            if active:
                parts.append(active)
            board = "\n\n".join(p for p in parts if p.strip()).strip()
            if board:
                compositor.set_wake_attention_board(board)
                if hasattr(compositor, "absorb_wake_attention_content"):
                    try:
                        compositor.absorb_wake_attention_content(board)
                    except Exception:
                        logger.debug(
                            "absorb_wake_attention_content failed", exc_info=True,
                        )
            else:
                compositor.clear_wake_attention_board()
            terminal = {
                t.id for t in team_manager._teams.values()
                if getattr(t, "completion_reported", False)
            }
            if hasattr(compositor, "prune_stale_orchestration_team_slots"):
                compositor.prune_stale_orchestration_team_slots(terminal)
        except Exception:
            logger.debug("wm_sync_wake_attention_board failed", exc_info=True)

    def _wm_absorb_wave_review(team: Any) -> None:
        compositor = dual_wm if (
            dual_wm is not None and hasattr(dual_wm, "absorb_wave_review")
        ) else working_memory
        if compositor is None or not hasattr(compositor, "absorb_wave_review"):
            return
        try:
            compositor.absorb_wave_review(team)
        except Exception:
            logger.debug("wm_absorb_wave_review failed", exc_info=True)

    def _wm_prune_stale_tactical_goals(plan_store: Any, plan_id: str) -> None:
        compositor = dual_wm if dual_wm is not None else working_memory
        if compositor is None or not plan_id:
            return
        try:
            from nls.agentic.coordinator_guard import prune_stale_tactical_goals_for_plan
            prune_stale_tactical_goals_for_plan(compositor, plan_store, plan_id)
        except Exception:
            logger.debug("wm_prune_stale_tactical_goals failed", exc_info=True)

    # ----- Event logging -----

    _log_event_fn = None
    if event_logger is not None and getattr(event_logger, "enabled", False):
        _log_event_fn = event_logger.log

    # ----- Brain Event Bus (Phase 4 — unified signal distribution) -----
    from nls.engine.brain_events import BrainEventBus, BrainSignal, BrainSignalType
    _brain_bus = BrainEventBus()
    _brain_bus.wire_brain_components(
        ans=ans,
        narrative_self=narrative_self,
        theory_of_mind=theory_of_mind,
        hypothalamus=hypothalamus,
    )

    # ----- Learning Accumulator (live consolidation) -----
    from nls.brain.learning_accumulator import LearningAccumulator
    _accumulator = LearningAccumulator(
        vllm_client=vllm_client,
        adapter_name=inference_adapter,
    )
    _brain_bus.subscribe(BrainSignalType.TOOL_RESULT, _accumulator.on_tool_result)
    _brain_bus.subscribe(BrainSignalType.TURN_END, _accumulator.on_turn_end)
    # LOOP_END is NOT subscribed here: the loop handles loop-end ingestion
    # directly (via _build_consolidation_summary → buffer injection → flush)
    # BEFORE hooks.on_loop_end fires. Subscribing would produce an orphan
    # entry in already-cleared buffers that never gets flushed.

    def _mid_wait_absorb() -> None:
        if ans is not None and working_memory is not None and hasattr(ans, "absorb_signals_to_rings"):
            ans.absorb_signals_to_rings(working_memory)
        if self_state is not None:
            self_state.beat(hypothalamus=hypothalamus)

    def _wm_get_tactical_goals() -> list[str]:
        if working_memory is None:
            return []
        try:
            return [
                str(g.content).strip()
                for g in working_memory.get_goals()
                if getattr(g, "level", "") == "tactical"
                and isinstance(getattr(g, "content", None), str)
                and str(g.content).strip()
            ]
        except Exception:
            return []

    def _wm_get_credentials() -> list[tuple[str, str]]:
        """Return (domain_hint, content) pairs from the Cryptex credentials ring."""
        _target = dual_wm if dual_wm is not None else working_memory
        if _target is None:
            return []
        try:
            creds = _target.get_credentials()
            return [
                (
                    getattr(c, "domain", "").replace("Project.Credential.", ""),
                    getattr(c, "content", ""),
                )
                for c in creds
                if getattr(c, "content", "")
            ]
        except Exception:
            return []

    from .outbound_notify import make_outbound_hooks

    _outbound_check, _outbound_record = make_outbound_hooks(outbound_gate)

    _on_compaction = None
    _cryptex = (
        dual_wm
        if dual_wm is not None and hasattr(dual_wm, "make_compaction_hook")
        else working_memory
    )
    if _cryptex is not None and hasattr(_cryptex, "make_compaction_hook"):
        _on_compaction = _cryptex.make_compaction_hook()
    elif _cryptex is not None and hasattr(_cryptex, "absorb_compaction"):
        def _on_compaction(anchor: Any) -> None:
            _cryptex.absorb_compaction(anchor)

    _hooks = LoopHooks(
        get_steering_messages=_get_steering_messages,
        has_active_plan=_has_active_plan,
        plan_has_pending_steps=_plan_has_pending_steps,
        plan_requires_team_delegation=_plan_requires_team_delegation,
        plan_suppresses_raw_delegate=_plan_suppresses_raw_delegate,
        transform_context=_transform_context_v4,
        get_preflight_knowledge=_get_preflight_knowledge,
        on_before_tool=None,
        outbound_check=_outbound_check,
        outbound_record=_outbound_record,
        on_after_tool=_on_after_tool,
        on_tool_success=_on_tool_success,
        on_tool_error=_on_tool_error,
        on_thinking=None,
        on_turn_end=None,
        on_goals_extracted=_on_goals_extracted,
        on_hints_extracted=_on_hints_extracted,
        on_loop_start=None,
        on_loop_end=_on_loop_end,
        tick_hypothalamus=_tick_hypothalamus,
        get_cortisol=_get_cortisol,
        wm_save=_wm_save,
        wm_consolidate=_wm_consolidate,
        wm_upsert_digest=_wm_upsert_digest,
        wm_set_plan_position=_wm_set_plan_position,
        wm_push_instructions=_wm_push_instructions,
        wm_push_task_goals=_wm_push_task_goals,
        wm_begin_task_epoch=_wm_begin_task_epoch,
        wm_mark_task_goal_done=_wm_mark_task_goal_done_cryptex,
        wm_prune_supporting_facts_for_goal=_wm_prune_supporting_facts_for_goal,
        wm_refresh_todo_board=_wm_refresh_todo_board,
        on_compaction=_on_compaction,
        ans_tool_learning=_ans_tool_learning,
        ans_on_response=_ans_on_response,
        ans_record_task_complete=_ans_record_task_complete,
        extract_session_learnings=_extract_session_learnings,
        copilot_queue=None,
        ans_extract_user_answer=_ans_extract_user_answer_v4,
        refresh_thalamic_route=_refresh_thalamic,
        classify_expert_needs=None,
        wm_orch_update_team=_wm_orch_update_team,
        wm_orch_record_decision=_wm_orch_record_decision,
        wm_orch_set_coordinator_phase=_wm_orch_set_coordinator_phase,
        wm_orch_add_escalation=_wm_orch_add_escalation,
        wm_orch_resolve_escalation=_wm_orch_resolve_escalation,
        wm_sync_wake_attention_board=_wm_sync_wake_attention_board,
        wm_absorb_wave_review=_wm_absorb_wave_review,
        wm_prune_stale_tactical_goals=_wm_prune_stale_tactical_goals,
        wm_get_credentials=_wm_get_credentials,
        wm_get_tactical_goals=_wm_get_tactical_goals,
        log_event=_log_event_fn,
        mid_wait_hook=_mid_wait_absorb,
    )
    _hooks.brain_event_bus = _brain_bus  # type: ignore[attr-defined]
    _hooks._render_mode_ref = _render_mode_ref  # type: ignore[attr-defined]
    _hooks._loop_state_ref = _loop_state_ref  # type: ignore[attr-defined]
    for _t in (agent_tools or []):
        if getattr(_t, "name", "") == "plan":
            if hasattr(_t, "set_orchestration_profile_fn"):
                _t.set_orchestration_profile_fn(
                    lambda: _loop_state_ref.get(
                        "orchestration_profile", "solo_structured",
                    ),
                )
            _hooks._cached_plan_tool = _t  # type: ignore[attr-defined]
            break
    _hooks._cryptex_compositor = (  # type: ignore[attr-defined]
        dual_wm if (dual_wm is not None and hasattr(dual_wm, "compose_context"))
        else working_memory if hasattr(working_memory, "compose_context")
        else None
    )
    _hooks._accumulator = _accumulator  # type: ignore[attr-defined]
    _hooks._accumulator_wm_target = dual_wm  # type: ignore[attr-defined]
    _guardrails_registry = None
    if agent_dir is not None:
        try:
            from nls.tools.agent_tools.guardrails_registry import (
                AgentGuardrailsRegistry,
            )
            _guardrails_registry = AgentGuardrailsRegistry(agent_dir)
        except Exception:
            logger.debug(
                "build_hooks_v4: guardrails registry init failed",
                exc_info=True,
            )
    _hooks.guardrails_registry = _guardrails_registry  # type: ignore[attr-defined]
    if _hooks.guardrails_registry is not None:
        _cryptex_bind = dual_wm if (
            dual_wm is not None and hasattr(dual_wm, "absorb_tool_result")
        ) else (
            working_memory
            if hasattr(working_memory, "absorb_tool_result")
            else None
        )
        if _cryptex_bind is not None:
            try:
                _cryptex_bind._guardrails_registry = _hooks.guardrails_registry  # type: ignore[attr-defined]
                from nls.tools.agent_tools.guardrails_registry import (
                    inject_guardrails_into_cryptex,
                )
                inject_guardrails_into_cryptex(
                    _cryptex_bind, _hooks.guardrails_registry,
                )
            except Exception:
                logger.debug("Cryptex guardrails bind failed", exc_info=True)
    hooks_ref[0] = _hooks
    return _hooks
