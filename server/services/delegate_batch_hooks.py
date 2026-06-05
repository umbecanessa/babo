"""Shared DelegateManager batch-complete wiring (runtime + WS)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def chain_batch_complete_callback(
    delegate_manager: Any,
    new_cb: Callable[..., Any],
) -> None:
    """Install *new_cb*, preserving any existing handler (executor cancel, etc.)."""
    prev = getattr(delegate_manager, "_on_batch_complete", None)
    if prev is not None and prev is not new_cb:
        async def _chained(
            batch_id: str,
            results: list,
            _new=new_cb,
            _prev=prev,
        ) -> None:
            await _new(batch_id, results)
            prev_result = _prev(batch_id, results)
            if asyncio.iscoroutine(prev_result):
                await prev_result

        delegate_manager._on_batch_complete = _chained
    else:
        delegate_manager._on_batch_complete = new_cb


def build_batch_complete_handler(
    agent_id: str,
    *,
    copilot_queue: Any | None = None,
    get_copilot_queue: Callable[[], Any | None] | None = None,
) -> Callable[..., Any]:
    """Return async on_batch_complete(batch_id, results) for DelegateManager."""

    async def _on_batch_complete(batch_id: str, results: list) -> None:
        summaries: list[str] = []
        for r in results:
            status = "done" if getattr(r, "success", False) else getattr(r, "exit_reason", "error")
            summary = getattr(r, "summary", "") or ""
            summaries.append(
                f"#{getattr(r, 'delegate_number', '?')} [{status}]: {summary[:200]}"
            )

        payload = {
            "type": "delegate_batch_complete",
            "batch_id": batch_id,
            "count": len(results),
            "results_summary": "\n".join(summaries),
        }

        cm = _resolve_connection_manager()
        if cm is not None:
            try:
                await cm.broadcast(agent_id, payload)
            except Exception:
                logger.debug(
                    "Agent %s: batch complete broadcast failed",
                    agent_id,
                    exc_info=True,
                )

        sched_mgr = _resolve_scheduler_manager()
        if sched_mgr is not None:
            job_name = f"delegate_checkback_{batch_id}"
            if sched_mgr.remove_job(job_name):
                logger.info(
                    "Agent %s: removed delegate check-back job '%s' (batch complete)",
                    agent_id,
                    job_name,
                )

        compile_prompt = (
            f"[DELEGATE_RESULTS] All {len(results)} sub-agents completed "
            f"(batch {batch_id}).\n\nResults:\n"
            + "\n".join(summaries)
            + "\n\nCompile these results into a full report and DELIVER to the "
            "user via their preferred channel (see [CHANNEL ROUTING] if present). "
            "Do NOT just post in chat if the user requested delivery elsewhere."
        )

        queue = copilot_queue
        if queue is None and get_copilot_queue is not None:
            queue = get_copilot_queue()
        if queue is not None:
            try:
                queue.put_nowait(compile_prompt)
            except Exception:
                logger.debug(
                    "Agent %s: copilot_queue inject failed for batch %s",
                    agent_id,
                    batch_id,
                    exc_info=True,
                )

        il = _resolve_inner_loop(agent_id)
        if il is not None:
            try:
                il.enqueue_autonomous_dispatch(
                    compile_prompt,
                    source="delegate_batch_complete",
                )
                logger.info(
                    "Agent %s: delegate batch %s — autonomous dispatch enqueued",
                    agent_id,
                    batch_id,
                )
            except Exception:
                logger.warning(
                    "Agent %s: enqueue_autonomous_dispatch failed for batch %s",
                    agent_id,
                    batch_id,
                    exc_info=True,
                )
        else:
            logger.info(
                "Agent %s: delegate batch %s complete — %d results "
                "(no inner loop for auto-dispatch)",
                agent_id,
                batch_id,
                len(results),
            )

    return _on_batch_complete


def wire_runtime_batch_complete(
    delegate_manager: Any,
    agent_id: str,
    *,
    get_copilot_queue: Callable[[], Any | None] | None = None,
) -> None:
    """Ensure runtime-level batch-complete handler is installed (idempotent)."""
    if getattr(delegate_manager, "_batch_complete_wired", False):
        return
    handler = build_batch_complete_handler(
        agent_id,
        get_copilot_queue=get_copilot_queue,
    )
    chain_batch_complete_callback(delegate_manager, handler)
    delegate_manager._batch_complete_wired = True


def _resolve_connection_manager() -> Any | None:
    try:
        from server.main import app as _app

        return getattr(_app.state, "connection_manager", None)
    except Exception:
        return None


def _resolve_scheduler_manager() -> Any | None:
    try:
        from server.main import app as _app

        return getattr(_app.state, "scheduler_manager", None)
    except Exception:
        return None


def _resolve_inner_loop(agent_id: str) -> Any | None:
    try:
        from server.main import app as _app

        cs = getattr(_app.state, "consciousness_scheduler", None)
        if cs is None:
            return None
        return cs.get_inner_loop(agent_id)
    except Exception:
        return None
