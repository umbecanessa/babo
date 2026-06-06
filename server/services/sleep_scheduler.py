"""Sleep Scheduler — FIFO consolidation queue (no weight training)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from nls.models import SleepRequest

logger = logging.getLogger(__name__)


@dataclass
class SleepJob:
    """A queued consolidation sleep job."""

    request: SleepRequest
    queued_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    result: dict[str, Any] = field(default_factory=dict)


class SleepScheduler:
    """FIFO sleep queue backed by consolidation-only cycles."""

    def __init__(
        self,
        model_manager: Any,
        agents_dir: Path,
        product_mode: bool = True,
    ):
        self.model_manager = model_manager
        self.agents_dir = agents_dir
        self.product_mode = product_mode

        self._queue: asyncio.Queue[SleepJob] = asyncio.Queue()
        self._current_job: SleepJob | None = None
        self._completed: list[SleepJob] = []
        self._runtimes: dict[str, Any] = {}
        self._worker_task: asyncio.Task | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connection_manager: Any | None = None
        self.on_sleep_done: Callable[[str], None] | None = None
        self._sleep_events: dict[str, asyncio.Event] = {}

    @property
    def connection_manager(self) -> Any:
        return self._connection_manager

    @connection_manager.setter
    def connection_manager(self, cm: Any) -> None:
        self._connection_manager = cm

    def register_runtime(self, agent_id: str, runtime: Any) -> None:
        self._runtimes[agent_id] = runtime

    def unregister_runtime(self, agent_id: str) -> None:
        self._runtimes.pop(agent_id, None)

    def get_sleep_event(self, agent_id: str) -> asyncio.Event:
        if agent_id not in self._sleep_events:
            self._sleep_events[agent_id] = asyncio.Event()
        return self._sleep_events[agent_id]

    async def enqueue(self, request: SleepRequest) -> None:
        if self._is_queued(request.agent_id):
            logger.info(
                "Agent %s already in sleep queue, skipping duplicate",
                request.agent_id,
            )
            return

        job = SleepJob(request=request)
        if request.agent_id in self._sleep_events:
            self._sleep_events[request.agent_id].clear()
        await self._queue.put(job)
        logger.info(
            "Agent %s queued for consolidation sleep "
            "(signals=%d, reason=%s, queue_depth=%d)",
            request.agent_id,
            request.signal_count,
            request.reason,
            self._queue.qsize(),
        )

    def enqueue_sync(self, request: SleepRequest) -> None:
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.enqueue(request), self._loop)
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.enqueue(request))
        except RuntimeError:
            logger.warning("No event loop for sleep enqueue")

    def _is_queued(self, agent_id: str) -> bool:
        if (
            self._current_job is not None
            and self._current_job.request.agent_id == agent_id
        ):
            return True
        for job in self._queue._queue:  # type: ignore[attr-defined]
            if job.request.agent_id == agent_id:
                return True
        return False

    def is_training(self, agent_id: str) -> bool:
        """True while this agent's consolidation cycle is running."""
        return (
            self._current_job is not None
            and self._current_job.request.agent_id == agent_id
        )

    def dequeue(self, agent_id: str) -> bool:
        if self.is_training(agent_id):
            return False

        removed = False
        remaining: list[SleepJob] = []
        for job in list(self._queue._queue):  # type: ignore[attr-defined]
            if job.request.agent_id == agent_id and not removed:
                removed = True
                logger.info(
                    "Agent %s: dequeued from sleep queue (wake-on-message)",
                    agent_id,
                )
            else:
                remaining.append(job)

        if removed:
            self._queue._queue.clear()  # type: ignore[attr-defined]
            for job in remaining:
                self._queue._queue.append(job)  # type: ignore[attr-defined]
        return removed

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("Sleep scheduler worker started (consolidation-only)")

    def stop(self) -> None:
        self._running = False
        if self._worker_task is not None:
            self._worker_task.cancel()
            self._worker_task = None
        logger.info("Sleep scheduler worker stopped")

    async def _worker(self) -> None:
        while self._running:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            self._current_job = job
            agent_id = job.request.agent_id
            try:
                job.started_at = time.time()
                job.result = await self._process_sleep(job)
                job.completed_at = time.time()
                elapsed = job.completed_at - job.started_at
                logger.info(
                    "Consolidation sleep complete for agent %s (%.1fs, signals=%d)",
                    agent_id,
                    elapsed,
                    job.result.get("signals_processed", 0),
                )
            except Exception as exc:
                logger.error(
                    "Sleep failed for agent %s: %s", agent_id, exc, exc_info=True,
                )
                job.result = {"error": str(exc)}
                job.completed_at = time.time()
            finally:
                self._completed.append(job)
                if len(self._completed) > 100:
                    self._completed = self._completed[-100:]
                self._current_job = None

    def _is_nightly_sleep(self, reason: str) -> bool:
        return reason.startswith("bedtime")

    def _is_nap(self, reason: str) -> bool:
        return reason.startswith("nap_window")

    def _get_max_cycles(self, agent_id: str) -> int:
        runtime = self._runtimes.get(agent_id)
        if runtime and hasattr(runtime, "ans") and runtime.ans:
            circ = getattr(runtime.ans, "circadian", None)
            if circ and hasattr(circ, "max_nightly_cycles"):
                return circ.max_nightly_cycles()
        return 5

    async def _process_sleep(self, job: SleepJob) -> dict[str, Any]:
        agent_id = job.request.agent_id
        reason = job.request.reason
        is_nightly = self._is_nightly_sleep(reason)
        is_nap = self._is_nap(reason)
        is_admin = reason in ("admin_requested", "manual")
        max_cycles = (
            self._get_max_cycles(agent_id) if is_nightly
            else (3 if is_admin else (2 if is_nap else 1))
        )
        sleep_type = "nightly" if is_nightly else ("nap" if is_nap else "sleep")

        if self._connection_manager is not None:
            try:
                await self._connection_manager.broadcast_prioritized(agent_id, {
                    "type": "sleep_start",
                    "agent_status": "sleeping",
                    "sleep_reason": reason,
                    "sleep_type": sleep_type,
                    "max_cycles": max_cycles,
                })
            except Exception:
                pass

        combined: dict[str, Any] = {
            "success": False,
            "signals_processed": 0,
            "consolidation_time": 0.0,
            "cycles": 0,
            "mode": "consolidation",
        }

        for cycle in range(1, max_cycles + 1):
            result = await self._consolidate_async(agent_id)
            combined["cycles"] = cycle
            combined["signals_processed"] += result.get("signals_processed", 0)
            combined["consolidation_time"] += result.get(
                "consolidation_time", 0.0,
            )
            if result.get("success"):
                combined["success"] = True
                if result.get("summary"):
                    combined["summary"] = result["summary"]

            runtime = self._runtimes.get(agent_id)
            remaining = 0
            if runtime and hasattr(runtime, "ans") and runtime.ans:
                remaining = runtime.ans.learnable_signal_count

            if remaining == 0:
                break

            if is_nightly and self._connection_manager is not None:
                try:
                    await self._connection_manager.broadcast(agent_id, {
                        "type": "sleep_cycle",
                        "cycle": cycle,
                        "max_cycles": max_cycles,
                        "signals_remaining": remaining,
                    })
                except Exception:
                    pass

        runtime = self._runtimes.get(agent_id)
        if runtime is not None:
            try:
                runtime.notify_sleep_complete(
                    signals_processed=combined.get("signals_processed", 0),
                    training_time=combined.get("consolidation_time", 0.0),
                    sleep_type=sleep_type,
                    consolidation_summary=combined.get("summary", "") or "",
                )
            except Exception as exc:
                logger.warning(
                    "Runtime notification failed for %s: %s", agent_id, exc,
                )

            if hasattr(runtime, "synthesize_day_narrative"):
                try:
                    narrative = await runtime.synthesize_day_narrative()
                    if narrative:
                        logger.info(
                            "Agent %s: day narrative persisted (%d chars)",
                            agent_id, len(narrative),
                        )
                except Exception as exc:
                    logger.warning(
                        "Agent %s: day narrative synthesis failed: %s",
                        agent_id, exc,
                    )

        if self._connection_manager is not None:
            try:
                from server.routes.chat.helpers import _build_nls_metadata

                status = runtime.get_status() if runtime is not None else {}
                nls = _build_nls_metadata(
                    status,
                    signals_processed=combined.get("signals_processed", 0),
                )
                wake_payload = {
                    "type": "sleep_complete",
                    "agent_status": "alive",
                    "sleep_complete": True,
                    "signals_processed": combined.get("signals_processed", 0),
                    "training_time": round(
                        combined.get("consolidation_time", 0.0), 1,
                    ),
                    "facts_in_memory": nls.get("facts_in_memory", 0),
                    "turn_count": nls.get("turn_count", 0),
                    "sleep_count": nls.get("sleep_count", 0),
                    "hormones": nls.get("hormones", {}),
                    "ans": nls.get("ans", {}),
                    "heartbeat": nls.get("heartbeat", {}),
                    "working_memory": nls.get("working_memory"),
                    "narrative": nls.get("narrative"),
                    "theory_of_mind": nls.get("theory_of_mind"),
                    "predictive_processing": nls.get("predictive_processing"),
                    "network_dynamics": nls.get("network_dynamics"),
                    "nls": nls,
                }
                await self._connection_manager.broadcast_prioritized(
                    agent_id, wake_payload,
                )
                # Chat UI listens for wake on type=status as well
                await self._connection_manager.broadcast_prioritized(agent_id, {
                    "type": "status",
                    "agent_status": "alive",
                    "sleep_complete": True,
                    "content": (
                        f"Agent is back up ({combined.get('signals_processed', 0)} "
                        f"signals consolidated)."
                    ),
                    **{k: wake_payload[k] for k in (
                        "facts_in_memory", "turn_count", "sleep_count",
                        "hormones", "ans", "heartbeat", "working_memory",
                        "narrative", "theory_of_mind", "predictive_processing",
                        "network_dynamics",
                    ) if wake_payload.get(k) is not None},
                })
            except Exception as exc:
                logger.warning(
                    "Agent %s: failed to push wake status: %s", agent_id, exc,
                )

        if self.on_sleep_done is not None:
            try:
                self.on_sleep_done(agent_id)
            except Exception as exc:
                logger.warning(
                    "Agent %s: on_sleep_done callback failed: %s", agent_id, exc,
                )

        await self._auto_sync_soul(agent_id, combined)

        event = self._sleep_events.pop(agent_id, None)
        if event is not None:
            event.set()

        return combined

    async def _consolidate_async(self, agent_id: str) -> dict[str, Any]:
        from server.services.consolidation_sleep import run_consolidation_cycle

        runtime = self._runtimes.get(agent_id)
        if runtime is None:
            return {"success": False, "signals_processed": 0, "consolidation_time": 0.0}
        agent_dir = self.agents_dir / agent_id
        return await run_consolidation_cycle(
            agent_id=agent_id,
            agent_dir=agent_dir,
            runtime=runtime,
        )

    async def _auto_sync_soul(
        self, agent_id: str, sleep_result: dict[str, Any],
    ) -> None:
        nestjs_url = os.environ.get("NESTJS_URL", "")
        shared_secret = (
            os.environ.get("RUNTIME_SHARED_SECRET", "").strip()
        )
        if not nestjs_url:
            return
        if not shared_secret:
            logger.debug(
                "Agent %s: soul auto-sync skipped (RUNTIME_SHARED_SECRET unset)",
                agent_id,
            )
            return

        try:
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(
                    f"{nestjs_url}/channels/internal/agents/{agent_id}/soul-packages",
                    json={
                        "chainHeight": sleep_result.get("block_height", 0),
                        "metadata": {
                            "source": "auto_sync_after_sleep",
                            "signals_processed": sleep_result.get(
                                "signals_processed", 0,
                            ),
                            "consolidation_time": sleep_result.get(
                                "consolidation_time", 0.0,
                            ),
                            "mode": "consolidation",
                        },
                    },
                    headers={
                        "Authorization": f"Bearer {shared_secret}",
                        "X-Runtime-Secret": shared_secret,
                        "Content-Type": "application/json",
                    },
                )
        except Exception as exc:
            logger.warning(
                "Agent %s: soul auto-sync failed (non-critical): %s",
                agent_id, exc,
            )

    def get_status(self) -> dict[str, Any]:
        current = None
        if self._current_job is not None:
            j = self._current_job
            current = {
                "agent_id": j.request.agent_id,
                "signals": j.request.signal_count,
                "started_at": j.started_at,
                "elapsed_seconds": round(
                    time.time() - j.started_at, 1,
                ) if j.started_at else 0,
            }

        pending = [
            {
                "agent_id": job.request.agent_id,
                "signals": job.request.signal_count,
                "queued_at": job.queued_at,
            }
            for job in list(self._queue._queue)  # type: ignore[attr-defined]
        ]

        return {
            "running": self._running,
            "mode": "consolidation",
            "current": current,
            "pending": pending,
            "queue_depth": self._queue.qsize(),
            "completed_total": len(self._completed),
        }
