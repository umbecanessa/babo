"""Background delegate lifecycle manager.

Manages fire-and-forget sub-agent tasks: spawning, monitoring, wrap-up
signaling, status queries, and completion announcement.  Lives on the
AgentRuntime (one per agent) so state persists across user turns.

Inspired by OpenClaw's ``sessions_spawn`` + ``subagent-registry`` pattern
where the spawn tool returns immediately, sub-agents run on a separate
lane, and completion is announced as a message injected back into the
parent's session.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

DELEGATE_DEFAULT_MAX_STEPS = 25
DELEGATE_UNASSIGNED_NUMBER = -1


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class DelegateSpec:
    """Specification for a single delegate to spawn."""
    task: str
    delegate_number: int
    max_steps: int = DELEGATE_DEFAULT_MAX_STEPS
    args: dict = field(default_factory=dict)
    file_manifest: list[str] = field(default_factory=list)
    team_briefing: str = ""
    wave: int | None = None
    tech_stack_block: str = ""
    file_ownership_block: str = ""


@dataclass
class DelegateStatus:
    """Live status snapshot of a running or completed delegate."""
    delegate_number: int
    task: str
    batch_id: str
    state: str = "running"          # running | done | error | cancelled
    iteration: int = 0
    max_iterations: int = 0
    total_tool_calls: int = 0
    elapsed_seconds: float = 0.0
    last_actions: list[str] = field(default_factory=list)
    hint_ack: str = ""
    exit_reason: str = ""
    summary_preview: str = ""


@dataclass
class DelegateResult:
    """Final result from a completed delegate."""
    delegate_number: int
    task: str
    success: bool = False
    summary: str = ""
    iterations: int = 0
    total_tool_calls: int = 0
    exit_reason: str = ""
    elapsed_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class BatchHandle:
    """Returned immediately by spawn_batch."""
    batch_id: str
    delegate_numbers: list[int]
    total: int


# ---------------------------------------------------------------------------
# DelegateManager
# ---------------------------------------------------------------------------

class DelegateManager:
    """Per-agent background delegate lifecycle manager.

    Spawns sub-agent loops as ``asyncio.Task``s that run independently of
    the orchestrator.  The orchestrator's ``delegate`` tool call returns
    immediately so the user can keep chatting.

    When all delegates in a batch complete, the manager calls the
    ``on_batch_complete`` callback (typically wired to inject results back
    into the conversation or send a WebSocket notification).

    The orchestrator is kept aware of running delegates via the
    ``SchedulerTool``: executor.py creates a periodic ``agent_message`` job
    at spawn time and removes it when the batch finishes.
    """

    MAX_CONCURRENT_DELEGATES = 5
    DEDUP_SIMILARITY_THRESHOLD = 0.8

    def __init__(
        self,
        *,
        on_batch_complete: Callable[[str, list[DelegateResult]], Any] | None = None,
        on_delegate_progress: Callable[[DelegateStatus], Any] | None = None,
        on_delegate_complete: Callable[[int, Any], Any] | None = None,
        max_concurrent_delegates: int | None = None,
        context_id: str = "primary",
    ) -> None:
        self._batches: dict[str, _BatchState] = {}
        self._delegates: dict[int, _DelegateState] = {}
        self._on_batch_complete = on_batch_complete
        self._on_delegate_progress = on_delegate_progress
        self._on_delegate_complete = on_delegate_complete
        self._context_id = context_id
        self._next_delegate_number = 0
        self._max_concurrent = (
            max_concurrent_delegates
            if max_concurrent_delegates is not None
            else self.MAX_CONCURRENT_DELEGATES
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _token_jaccard(a: str, b: str) -> float:
        """Simple token-level Jaccard similarity for dedup."""
        ta = set(a.lower().split())
        tb = set(b.lower().split())
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    async def spawn_batch(
        self,
        specs: list[DelegateSpec],
        *,
        run_delegate_fn: Callable[..., Any],
        fn_kwargs: dict[str, Any],
        skip_dedup: bool = False,
    ) -> BatchHandle:
        """Fire-and-forget: launch delegates as ``asyncio.Task``s.

        ``run_delegate_fn`` is called per delegate with the delegate's
        ``DelegateSpec`` merged into ``fn_kwargs``.  It must be an async
        function returning a tuple ``(summary: str, loop_result_or_none)``.

        ``skip_dedup``: when True, bypass the content-similarity dedup
        guard.  Used by team-spawned delegates where each member has a
        unique plan step but shares the same briefing text.
        """
        # --- Guard: concurrent delegate limit ---
        active_count = sum(
            1 for ds in self._delegates.values() if ds.state == "running"
        )
        if active_count + len(specs) > self._max_concurrent:
            raise ValueError(
                f"Delegate limit exceeded: {active_count} running + "
                f"{len(specs)} requested > max {self._max_concurrent}. "
                f"Wait for active delegates to finish or reduce the batch."
            )

        # --- Guard: content-similarity dedup ---
        # Skipped for team-spawned delegates: team members share a common
        # briefing that inflates Jaccard similarity, but each has a unique
        # plan step.  Dedup still applies to ad-hoc single delegates.
        if not skip_dedup:
            active_tasks = [
                ds.task for ds in self._delegates.values()
                if ds.state == "running"
            ]
            deduped_specs: list[DelegateSpec] = []
            for spec in specs:
                is_dup = False
                for existing_task in active_tasks:
                    if self._token_jaccard(spec.task, existing_task) > self.DEDUP_SIMILARITY_THRESHOLD:
                        logger.warning(
                            "[DelegateManager] duplicate delegate rejected: "
                            "'%s' overlaps with running '%s' (>%.0f%% similar)",
                            spec.task[:80], existing_task[:80],
                            self.DEDUP_SIMILARITY_THRESHOLD * 100,
                        )
                        is_dup = True
                        break
                if not is_dup:
                    deduped_specs.append(spec)
                    active_tasks.append(spec.task)

            if not deduped_specs:
                raise ValueError(
                    "All requested delegates are duplicates of already-running "
                    "delegates. No new delegates spawned."
                )
            specs = deduped_specs

        batch_id = uuid.uuid4().hex[:12]
        batch = _BatchState(batch_id=batch_id, total=len(specs))
        self._batches[batch_id] = batch

        delegate_numbers: list[int] = []
        for spec in specs:
            ds = _DelegateState(
                delegate_number=spec.delegate_number,
                task=spec.task,
                batch_id=batch_id,
                max_iterations=spec.max_steps,
                start_time=time.time(),
                wrap_up=asyncio.Event(),
            )
            ds._run_fn = run_delegate_fn
            ds._fn_kwargs = fn_kwargs
            ds._spec = spec
            self._delegates[spec.delegate_number] = ds
            batch.delegate_numbers.append(spec.delegate_number)
            delegate_numbers.append(spec.delegate_number)

            task_coro = self._run_one(
                ds, spec, run_delegate_fn, fn_kwargs,
            )
            ds.asyncio_task = asyncio.create_task(task_coro)

        logger.info(
            "[DelegateManager] batch %s: spawned %d delegates (%s)",
            batch_id, len(specs),
            [s.delegate_number for s in specs],
        )
        return BatchHandle(
            batch_id=batch_id,
            delegate_numbers=delegate_numbers,
            total=len(specs),
        )

    def get_status(self) -> list[DelegateStatus]:
        """Return live status of all tracked delegates (running + recent)."""
        out: list[DelegateStatus] = []
        for ds in self._delegates.values():
            elapsed = time.time() - ds.start_time if ds.state == "running" else ds.elapsed
            out.append(DelegateStatus(
                delegate_number=ds.delegate_number,
                task=ds.task,
                batch_id=ds.batch_id,
                state=ds.state,
                iteration=ds.iteration,
                max_iterations=ds.max_iterations,
                total_tool_calls=ds.total_tool_calls,
                elapsed_seconds=round(elapsed, 1),
                last_actions=list(ds.last_actions[-5:]),
                hint_ack=ds.hint_ack,
                exit_reason=ds.exit_reason,
                summary_preview=ds.summary[:300] if ds.summary else "",
            ))
        return out

    def get_batch_status(self, batch_id: str) -> list[DelegateStatus]:
        """Status for delegates in a specific batch."""
        batch = self._batches.get(batch_id)
        if not batch:
            return []
        return [
            s for s in self.get_status()
            if s.batch_id == batch_id
        ]

    def is_delegate_live(self, delegate_number: int) -> bool:
        """True only when a delegate has a running asyncio task."""
        ds = self._delegates.get(delegate_number)
        if ds is None or ds.state != "running":
            return False
        task = ds.asyncio_task
        return task is not None and not task.done()

    async def wrap_up(self, delegate_number: int) -> bool:
        """Signal a delegate to finalize its work."""
        ds = self._delegates.get(delegate_number)
        if not ds or ds.state != "running":
            return False
        ds.wrap_up.set()
        logger.info("[DelegateManager] wrap-up sent to delegate #%d", delegate_number)
        return True

    async def cancel(self, delegate_number: int) -> bool:
        """Cancel a running delegate."""
        ds = self._delegates.get(delegate_number)
        if not ds or ds.state != "running":
            return False
        if ds.asyncio_task and not ds.asyncio_task.done():
            ds.asyncio_task.cancel()
        ds.state = "cancelled"
        ds.exit_reason = "cancelled"
        logger.info("[DelegateManager] cancelled delegate #%d", delegate_number)
        return True

    def get_delegate_cryptex(self, delegate_number: int) -> Any | None:
        """Return the SubCryptex for a running or finished delegate."""
        ds = self._delegates.get(delegate_number)
        return ds.sub_cryptex if ds else None

    def _apply_ring_ops(self, sub_cryptex: Any, ring_ops: list[dict] | None) -> None:
        if not ring_ops or sub_cryptex is None:
            return
        for op in ring_ops:
            try:
                _action = op.get("action", "upsert")
                if _action == "boost_priority":
                    sub_cryptex.boost_priority(
                        op.get("ring", ""),
                        float(op.get("boost", 0.2)),
                    )
                else:
                    sub_cryptex.upsert(
                        ring_id=op.get("ring", ""),
                        domain=op.get("domain", "orchestrator"),
                        content=op.get("content", ""),
                        salience=float(op.get("salience", 0.9)),
                    )
            except Exception:
                pass

    async def hint(
        self,
        delegate_number: int,
        message: str,
        ring_ops: list[dict] | None = None,
        *,
        delivery: str = "both",
        also_chat_hint: bool | None = None,
        directive_domain: str | None = None,
    ) -> bool:
        """Inject a steering hint into a running delegate's context.

        ``delivery='both'`` (default): SubCryptex orchestrator ring plus a
        chat-turn on ``hint_queue`` (triggers ack path).  ``delivery='ring'``:
        ring only — quiet nudge without interrupting the delegate loop.
        """
        from .orchestrator_hint import (
            apply_orchestrator_directive,
            build_orchestrator_chat_hint,
            build_orchestrator_ring_ops,
            resolve_hint_delivery,
        )

        ds = self._delegates.get(delegate_number)
        if not ds or ds.state != "running":
            return False

        use_ring, use_chat, delivery_label = resolve_hint_delivery(
            delivery=delivery,
            also_chat_hint=also_chat_hint,
        )

        sc = ds.sub_cryptex
        if use_ring and sc is not None and message.strip():
            apply_orchestrator_directive(
                sc, message, domain=directive_domain,
            )
            ds.sub_cryptex.boost_priority("orchestrator", 0.15)

        if use_ring:
            _ops = list(ring_ops or [])
            if message.strip() and not any(
                o.get("ring") == "orchestrator" for o in _ops
            ):
                _ops.extend(build_orchestrator_ring_ops(
                    message, domain=directive_domain,
                ))
            self._apply_ring_ops(sc, _ops)

        if use_chat and message.strip():
            ds.hint_queue.put_nowait(build_orchestrator_chat_hint(message))
        logger.info(
            "[DelegateManager] hint sent to delegate #%d "
            "(delivery=%s ring=%s chat=%s): %.200s",
            delegate_number, delivery_label, use_ring, use_chat, message,
        )
        return True

    def get_hint_queue(self, delegate_number: int) -> asyncio.Queue | None:
        """Return the hint_queue for a delegate (used to wire copilot_queue)."""
        ds = self._delegates.get(delegate_number)
        return ds.hint_queue if ds else None

    async def intervene(
        self,
        delegate_number: int,
        action: str,
        message: str = "",
        extra_iterations: int = 10,
        ring_ops: list[dict] | None = None,
        *,
        delivery: str = "both",
    ) -> bool | str:
        """Send an orchestrator decision to a delegate waiting on escalation.

        ``action`` is one of ``extend``, ``hint``, ``terminate``.
        The decision is pushed as a structured dict onto ``hint_queue``
        so the loop's ``_try_escalate`` can parse it.

        If ``ring_ops`` is provided, each op is applied to the delegate's
        SubCryptex before the escalation decision is sent — allowing the
        orchestrator to inject knowledge, fix misunderstandings, or boost
        ring priorities as part of the intervention.

        Returns True on success, False if delegate unknown, or an error
        string describing why the intervention failed.
        """
        ds = self._delegates.get(delegate_number)
        if not ds:
            return False
        if ds.state != "running":
            return (
                f"Delegate #{delegate_number} already finished "
                f"(state={ds.state}, exit={ds.exit_reason}). "
                f"The escalation wait ({ds.exit_reason}) may have timed out "
                f"before your intervention reached it."
            )

        from .orchestrator_hint import (
            apply_orchestrator_directive,
            infer_directive_domain,
            resolve_hint_delivery,
        )

        use_ring, use_chat, delivery_label = resolve_hint_delivery(
            delivery=delivery,
        )

        sc = ds.sub_cryptex
        if use_ring and sc is not None and message.strip():
            apply_orchestrator_directive(
                sc, message,
                domain=infer_directive_domain(message, action=action),
            )
            if action in ("extend", "hint"):
                sc.boost_priority("progress", 0.1)
            elif action == "terminate":
                sc.boost_priority("orchestrator", 0.2)

        if use_ring:
            self._apply_ring_ops(sc, ring_ops)

        ds.hint_queue.put_nowait({
            "action": action,
            "message": message,
            "extra_iterations": extra_iterations,
            "delivery": delivery_label,
        })
        logger.info(
            "[DelegateManager] intervene #%d: action=%s delivery=%s "
            "extra_iters=%d ring_ops=%d chat=%s msg=%.100s",
            delegate_number, action, delivery_label, extra_iterations,
            len(ring_ops or []), use_chat, message,
        )
        return True

    async def rewake(
        self,
        delegate_number: int,
        message: str = "",
        extra_iterations: int = 15,
    ) -> bool | str:
        """Re-launch a finished/failed delegate with new instructions.

        Instead of spawning a brand-new delegate, this resets the existing
        one's state and re-runs it with the original task plus a continuation
        message from the orchestrator (like a manager telling an employee
        "you're not done yet, here's what's missing").

        Returns True on success, or an error string.
        """
        ds = self._delegates.get(delegate_number)
        if not ds:
            return f"Unknown delegate #{delegate_number}."
        if ds.state == "running":
            return (
                f"Delegate #{delegate_number} is still running "
                f"(iter {ds.iteration}/{ds.max_iterations}). "
                f"Use intervene() instead."
            )
        if ds._run_fn is None or ds._fn_kwargs is None or ds._spec is None:
            return (
                f"Delegate #{delegate_number} cannot be rewoken — "
                f"missing run function (was it spawned before rewake support?)."
            )

        _prev_summary = ds.summary[:500] if ds.summary else "(no summary)"
        _prev_exit = ds.exit_reason or "unknown"

        rewake_prefix = (
            f"[CONTINUATION — REWOKEN BY ORCHESTRATOR]\n"
            f"You previously worked on this task and exited with: {_prev_exit}\n"
            f"Previous result summary: {_prev_summary}\n\n"
        )
        if message:
            rewake_prefix += f"Orchestrator feedback: {message}\n\n"
        rewake_prefix += (
            "Continue from where you left off. Do NOT redo work that "
            "was already completed. Focus on what's missing or broken.\n"
        )

        from .executor import DelegateSpec
        new_spec = DelegateSpec(
            delegate_number=ds.delegate_number,
            task=rewake_prefix + ds.task,
            max_steps=extra_iterations,
        )

        # Adjust batch state: undo the previous completion so
        # _run_one doesn't double-count when the rewoken run finishes.
        batch = self._batches.get(ds.batch_id)
        if batch and batch.completed > 0:
            batch.completed -= 1
            batch.results = [
                r for r in batch.results
                if r.delegate_number != delegate_number
            ]

        ds.state = "running"
        ds.iteration = 0
        ds.total_tool_calls = 0
        ds.last_actions = []
        ds.hint_ack = ""
        ds.hint_ack_holder = []
        ds.exit_reason = ""
        ds.summary = ""
        ds.start_time = time.time()
        ds.elapsed = 0.0
        ds.wrap_up = asyncio.Event()
        ds.hint_queue = asyncio.Queue()
        ds.state_holder = []
        ds.max_iterations = extra_iterations
        ds._spec = new_spec

        task_coro = self._run_one(
            ds, new_spec, ds._run_fn, ds._fn_kwargs,
        )
        ds.asyncio_task = asyncio.create_task(task_coro)

        logger.info(
            "[DelegateManager] rewake #%d: extra_iters=%d msg=%.100s prev_exit=%s",
            delegate_number, extra_iterations, message, _prev_exit,
        )
        return True

    def get_results(self, batch_id: str) -> list[DelegateResult] | None:
        """Get completed results for a batch.  None if still running."""
        batch = self._batches.get(batch_id)
        if not batch:
            return None
        if batch.completed < batch.total:
            return None
        return list(batch.results)

    def running_count(self) -> int:
        """Number of delegates currently running."""
        return sum(
            1 for ds in self._delegates.values() if ds.state == "running"
        )

    def has_active_delegates(self) -> bool:
        """True if any delegate is still running."""
        return self.running_count() > 0

    def aggregate_token_usage(self) -> dict[str, int]:
        """Sum prompt/completion/total tokens across all finished delegates."""
        p = c = t = 0
        for ds in self._delegates.values():
            if ds.state != "running":
                p += ds.prompt_tokens
                c += ds.completion_tokens
                t += ds.total_tokens
        return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": t}

    def list_all(self) -> list[DelegateStatus]:
        """Return status of all tracked delegates (running + completed)."""
        out: list[DelegateStatus] = []
        for ds in self._delegates.values():
            out.append(DelegateStatus(
                delegate_number=ds.delegate_number,
                task=ds.task,
                batch_id=ds.batch_id,
                state=ds.state,
                iteration=ds.iteration,
                max_iterations=ds.max_iterations,
                total_tool_calls=ds.total_tool_calls,
                elapsed_seconds=round(ds.elapsed or (time.time() - ds.start_time), 1),
                last_actions=list(ds.last_actions[-5:]),
                hint_ack=ds.hint_ack,
                exit_reason=ds.exit_reason,
                summary_preview=ds.summary[:200] if ds.summary else "",
            ))
        return out

    def clear_completed(self) -> None:
        """Remove finished delegates from tracking (keep running ones)."""
        done_nums = [n for n, ds in self._delegates.items() if ds.state != "running"]
        for n in done_nums:
            del self._delegates[n]
        done_batches = [
            bid for bid, b in self._batches.items()
            if b.completed >= b.total
        ]
        for bid in done_batches:
            del self._batches[bid]

    # ------------------------------------------------------------------
    # §2.2 — Persistent delegate state
    # ------------------------------------------------------------------

    def save_state(self, path) -> None:
        """Persist batch/delegate snapshots to disk for restart resilience."""
        from pathlib import Path as _Path
        import json
        path = _Path(path)
        state = {
            "next_delegate_number": self._next_delegate_number,
            "batches": {},
            "delegates": {},
        }
        for bid, batch in self._batches.items():
            state["batches"][bid] = {
                "batch_id": batch.batch_id,
                "total": batch.total,
                "completed": batch.completed,
                "delegate_numbers": batch.delegate_numbers,
            }
        for num, ds in self._delegates.items():
            state["delegates"][str(num)] = {
                "delegate_number": ds.delegate_number,
                "task": ds.task,
                "batch_id": ds.batch_id,
                "max_iterations": ds.max_iterations,
                "state": ds.state,
                "iteration": ds.iteration,
                "total_tool_calls": ds.total_tool_calls,
                "exit_reason": ds.exit_reason,
                "summary": ds.summary[:500],
                "elapsed": ds.elapsed or round(time.time() - ds.start_time, 1),
                "last_actions": list(ds.last_actions[-5:]),
            }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    def load_state(self, path) -> bool:
        """Restore batch/delegate snapshots from disk.

        Only recovers metadata — running asyncio.Tasks are NOT resumed.
        Delegates that were 'running' at save time are marked 'interrupted'.
        """
        from pathlib import Path as _Path
        import json
        path = _Path(path)
        if not path.exists():
            return False
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            self._next_delegate_number = state.get("next_delegate_number", 0)
            for bid, bdata in state.get("batches", {}).items():
                self._batches[bid] = _BatchState(
                    batch_id=bdata["batch_id"],
                    total=bdata["total"],
                    completed=bdata.get("completed", 0),
                    delegate_numbers=bdata.get("delegate_numbers", []),
                )
            for num_str, ddata in state.get("delegates", {}).items():
                num = int(num_str)
                ds = _DelegateState(
                    delegate_number=ddata["delegate_number"],
                    task=ddata["task"],
                    batch_id=ddata["batch_id"],
                    max_iterations=ddata.get("max_iterations", DELEGATE_DEFAULT_MAX_STEPS),
                    start_time=time.time() - ddata.get("elapsed", 0),
                    wrap_up=asyncio.Event(),
                    state="interrupted" if ddata.get("state") == "running" else ddata.get("state", "done"),
                    iteration=ddata.get("iteration", 0),
                    total_tool_calls=ddata.get("total_tool_calls", 0),
                    exit_reason=ddata.get("exit_reason", ""),
                    summary=ddata.get("summary", ""),
                    elapsed=ddata.get("elapsed", 0),
                )
                ds.last_actions = ddata.get("last_actions", [])
                self._delegates[num] = ds
            logger.info(
                "[DelegateManager] loaded state: %d batches, %d delegates",
                len(self._batches), len(self._delegates),
            )
            return True
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            logger.warning("[DelegateManager] failed to load state: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_one(
        self,
        ds: "_DelegateState",
        spec: DelegateSpec,
        run_fn: Callable[..., Any],
        fn_kwargs: dict[str, Any],
    ) -> None:
        """Run a single delegate and handle lifecycle updates."""
        _progress_task = asyncio.create_task(self._progress_monitor(ds))
        try:
            summary, loop_result = await run_fn(
                spec=spec,
                wrap_up_signal=ds.wrap_up,
                state_holder_out=ds.state_holder,
                hint_queue=ds.hint_queue,
                sub_cryptex_holder_out=ds.sub_cryptex_holder,
                hint_ack_holder_out=ds.hint_ack_holder,
                **fn_kwargs,
            )
            ds.summary = summary or ""
            ds.state = "done"
            if loop_result:
                ds.iteration = getattr(loop_result, "iterations", 0)
                ds.total_tool_calls = getattr(loop_result, "total_tool_calls", 0)
                ds.exit_reason = getattr(loop_result, "exit_reason", "")
                ds.prompt_tokens = getattr(loop_result, "total_prompt_tokens", 0)
                ds.completion_tokens = getattr(loop_result, "total_completion_tokens", 0)
                ds.total_tokens = getattr(loop_result, "total_tokens", 0)
            ds.elapsed = time.time() - ds.start_time
            logger.info(
                "[DelegateManager] delegate #%d completed — exit=%s iters=%d tc=%d "
                "tokens=%d/%d/%d dur=%.1fs",
                ds.delegate_number, ds.exit_reason,
                ds.iteration, ds.total_tool_calls,
                ds.prompt_tokens, ds.completion_tokens, ds.total_tokens,
                ds.elapsed,
            )
        except asyncio.CancelledError:
            ds.state = "cancelled"
            ds.exit_reason = "cancelled"
            ds.elapsed = time.time() - ds.start_time
        except Exception as exc:
            ds.state = "error"
            ds.exit_reason = f"error: {exc}"
            ds.summary = f"Sub-agent crashed: {exc}"
            ds.elapsed = time.time() - ds.start_time
            logger.error(
                "[DelegateManager] delegate #%d error: %s",
                ds.delegate_number, exc, exc_info=True,
            )
        finally:
            _progress_task.cancel()
            try:
                await _progress_task
            except asyncio.CancelledError:
                pass

        # Per-delegate completion callback (e.g. update TeamManager)
        if self._on_delegate_complete is not None:
            try:
                _dc_result = self._on_delegate_complete(ds.delegate_number, ds)
                if asyncio.iscoroutine(_dc_result):
                    await _dc_result
            except Exception:
                logger.warning(
                    "[DelegateManager] on_delegate_complete callback failed for #%d",
                    ds.delegate_number, exc_info=True,
                )

        # Update batch
        batch = self._batches.get(ds.batch_id)
        if batch:
            batch.completed += 1
            batch.results.append(DelegateResult(
                delegate_number=ds.delegate_number,
                task=ds.task,
                success=(
                    ds.state == "done"
                    and (
                        ds.exit_reason in ("task_complete", "orchestrator_terminated")
                        or (
                            "timed out" in (ds.summary or "")
                            and "[DELEGATE KNOWLEDGE DIGEST]" in (ds.summary or "")
                        )
                    )
                ),
                summary=ds.summary,
                iterations=ds.iteration,
                total_tool_calls=ds.total_tool_calls,
                exit_reason=ds.exit_reason,
                elapsed_seconds=ds.elapsed,
                prompt_tokens=ds.prompt_tokens,
                completion_tokens=ds.completion_tokens,
                total_tokens=ds.total_tokens,
            ))
            if batch.completed >= batch.total:
                logger.info(
                    "[DelegateManager] batch %s ALL COMPLETE (%d delegates)",
                    ds.batch_id, batch.total,
                )
                if self._on_batch_complete:
                    try:
                        cb_result = self._on_batch_complete(
                            ds.batch_id, list(batch.results),
                        )
                        if asyncio.iscoroutine(cb_result):
                            await cb_result
                    except Exception:
                        logger.warning(
                            "[DelegateManager] on_batch_complete callback failed",
                            exc_info=True,
                        )

    async def _progress_monitor(self, ds: "_DelegateState") -> None:
        """Periodically update iteration/actions from state_holder and
        emit progress events.  Also triggers wrap-up at 80% budget."""
        _threshold = max(int(ds.max_iterations * 0.8), ds.max_iterations - 3)
        _wrap_up_sent = False
        while True:
            await asyncio.sleep(10)
            if ds.state != "running":
                return
            # Pull live data from the loop's LoopState
            if ds.state_holder:
                _ls = ds.state_holder[0]
                ds.iteration = getattr(_ls, "iteration", ds.iteration)
                ds.total_tool_calls = getattr(
                    _ls, "total_tool_calls", ds.total_tool_calls,
                )
                _actions = getattr(_ls, "cumulative_actions", None)
                if _actions:
                    ds.last_actions = list(_actions[-5:])
            if ds.hint_ack_holder:
                ds.hint_ack = ds.hint_ack_holder[-1]
            if self._on_delegate_progress:
                try:
                    status = DelegateStatus(
                        delegate_number=ds.delegate_number,
                        task=ds.task,
                        batch_id=ds.batch_id,
                        state=ds.state,
                        iteration=ds.iteration,
                        max_iterations=ds.max_iterations,
                        total_tool_calls=ds.total_tool_calls,
                        elapsed_seconds=round(time.time() - ds.start_time, 1),
                        last_actions=list(ds.last_actions[-5:]),
                        hint_ack=ds.hint_ack,
                    )
                    cb = self._on_delegate_progress(status)
                    if asyncio.iscoroutine(cb):
                        await cb
                except Exception:
                    pass
            if not _wrap_up_sent and ds.iteration >= _threshold:
                ds.wrap_up.set()
                _wrap_up_sent = True
                logger.info(
                    "[DelegateManager] auto wrap-up for delegate #%d at iter %d/%d",
                    ds.delegate_number, ds.iteration, ds.max_iterations,
                )


# ---------------------------------------------------------------------------
# Internal state tracking
# ---------------------------------------------------------------------------

@dataclass
class _DelegateState:
    delegate_number: int
    task: str
    batch_id: str
    max_iterations: int
    start_time: float
    wrap_up: asyncio.Event
    state: str = "running"
    iteration: int = 0
    total_tool_calls: int = 0
    last_actions: list[str] = field(default_factory=list)
    hint_ack: str = ""
    exit_reason: str = ""
    summary: str = ""
    elapsed: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    asyncio_task: asyncio.Task | None = None
    state_holder: list = field(default_factory=list)
    hint_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    # SubCryptex holder — populated by run_delegate_detached so the
    # DelegateManager (and the orchestrator's delegate_ring tool) can
    # inspect / manipulate the sub-agent's ring memory.
    sub_cryptex_holder: list = field(default_factory=list)
    hint_ack_holder: list[str] = field(default_factory=list)
    # Stored for rewake: allow re-launching a finished delegate
    _run_fn: Callable | None = None
    _fn_kwargs: dict | None = None
    _spec: Any | None = None

    @property
    def sub_cryptex(self) -> Any | None:
        """Return the delegate's SubCryptex if available."""
        return self.sub_cryptex_holder[0] if self.sub_cryptex_holder else None


@dataclass
class _BatchState:
    batch_id: str
    total: int
    completed: int = 0
    delegate_numbers: list[int] = field(default_factory=list)
    results: list[DelegateResult] = field(default_factory=list)
