"""NLS Consciousness Scheduler -- Who Gets to Think.

Replaces DaydreamScheduler's tick-based polling with a continuous
consciousness model that manages which agents are awake, dreaming,
or frozen across the worker pool.

Three agent states::

    CONSCIOUS  — inner loop running on a DREAM worker
    SLEEPING   — training running on a TRAIN worker
    FROZEN     — state on disk, zero compute

One brain, one consciousness at a time (per worker).  The scheduler
distributes consciousness across available workers and rotates agents
in/out based on priority.

Key behaviors:

    - User messages ALWAYS preempt: if a user sends a message to a
      frozen agent, the lowest-priority conscious agent is frozen and
      the target agent is woken up immediately.
    - Sleep transitions are natural: when an agent's inner loop detects
      the hormonal sleep signature, it stops itself and the scheduler
      transitions the agent to SLEEPING (if a TRAIN worker is available)
      or FROZEN (if not).
    - Priority is computed from time since last conscious, drive
      pressure, signal buffer depth, and cortisol level.
    - The scheduler runs its own background loop to rotate frozen agents
      onto freed workers.

Architecture::

    ConsciousnessScheduler._run()
      while running:
        1. Check for user-message interrupts (highest priority)
        2. Assign free DREAM workers to highest-priority frozen agents
        3. Handle agents whose inner loops have stopped (sleep transitions)
        4. Clean up completed tasks
        await sleep(0.5)  # scheduler tick
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from nls.engine.inner_loop import InnerLoop
from nls.brain.self_state import SelfState

logger = logging.getLogger(__name__)


# ===================================================================
# Agent Consciousness State
# ===================================================================


class AgentConsciousnessState(Enum):
    """Which state of consciousness an agent is in."""

    CONSCIOUS = "conscious"   # inner loop active on a worker
    SLEEPING = "sleeping"     # training in progress
    FROZEN = "frozen"         # on disk, zero compute


@dataclass
class AgentEntry:
    """Scheduler's view of one agent."""

    agent_id: str
    state: AgentConsciousnessState = AgentConsciousnessState.FROZEN
    inner_loop: InnerLoop | None = None
    last_conscious_at: float = field(default_factory=time.time)
    last_user_message_at: float = 0.0
    self_state_cache: SelfState | None = None
    user_paused: bool = False


# ===================================================================
# Consciousness Scheduler
# ===================================================================


class ConsciousnessScheduler:
    """Manages agent consciousness across the worker pool.

    Parameters
    ----------
    agent_manager : AgentManager
        For accessing agent runtimes and IDs.
    connection_manager : Any, optional
        For broadcasting state changes to the frontend.
    scheduler_tick : float
        Seconds between scheduler rotation cycles.
    """

    def __init__(
        self,
        agent_manager: Any,
        connection_manager: Any = None,
        scheduler_tick: float = 0.5,
        *,
        model_a: Any | None = None,
        model_a_tokenizer: Any | None = None,
        vllm_client: Any | None = None,
        max_conscious: int = 5,
        sleep_scheduler: Any | None = None,
    ):
        self.agent_manager = agent_manager
        self.connection_manager = connection_manager
        self.scheduler_tick = scheduler_tick
        self._sleep_scheduler = sleep_scheduler

        # vLLM client: when set, all inference (including dreams) goes
        # through vLLM instead of local model.
        self._vllm_client = vllm_client

        self._model_a = model_a
        self._model_a_tokenizer = model_a_tokenizer
        self._use_model_a = all(
            x is not None
            for x in (model_a, model_a_tokenizer)
        )
        self._max_conscious = max_conscious

        # Agent tracking
        self._agents: dict[str, AgentEntry] = {}

        # Pending user-message wake requests
        self._wake_requests: asyncio.Queue[str] = asyncio.Queue()

        # Background task
        self._task: asyncio.Task | None = None
        self._running = False

        # Stats
        self._total_wakes = 0
        self._total_freezes = 0
        self._total_preemptions = 0

    # ===================================================================
    # Lifecycle
    # ===================================================================

    def start(self) -> None:
        """Start the consciousness scheduler."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("ConsciousnessScheduler started")

    def stop(self) -> None:
        """Stop the scheduler and freeze all agents."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

        # Stop all inner loops
        for entry in self._agents.values():
            if entry.inner_loop is not None:
                entry.inner_loop.stop(reason="scheduler_shutdown")
                entry.inner_loop = None
            entry.state = AgentConsciousnessState.FROZEN

        logger.info("ConsciousnessScheduler stopped")

    # ===================================================================
    # Public API
    # ===================================================================

    def register_agent(self, agent_id: str) -> None:
        """Register an agent with the scheduler.

        Called when an agent is loaded by the AgentManager.
        """
        if agent_id not in self._agents:
            entry = AgentEntry(agent_id=agent_id)
            self._agents[agent_id] = entry
            # Restore persisted pause flag so paused agents stay paused
            # across server restarts.
            entry.user_paused = self._load_paused_flag(agent_id)
            if entry.user_paused:
                logger.info(
                    "Agent %s registered (user_paused=True — will not auto-wake)",
                    agent_id,
                )
            else:
                logger.info(
                    "Agent %s registered with consciousness scheduler",
                    agent_id,
                )
        else:
            # Re-registration: clear stale flags so the agent is
            # eligible for consciousness again (e.g. runtime reloaded).
            entry = self._agents[agent_id]
            entry._no_runtime_warned = False  # type: ignore[attr-defined]

    def unregister_agent(self, agent_id: str) -> None:
        """Unregister an agent (being unloaded)."""
        entry = self._agents.pop(agent_id, None)
        if entry is not None and entry.inner_loop is not None:
            entry.inner_loop.stop(reason="unregistered")
        logger.info(
            "Agent %s unregistered from consciousness scheduler",
            agent_id,
        )

    async def on_user_message(self, agent_id: str) -> None:
        """A user sent a message to this agent.  Highest priority.

        If the agent is CONSCIOUS, the inner loop is interrupted
        (paused) so the main inference path can handle the message.
        If the agent is FROZEN, it gets queued for immediate wake-up
        via preemption.
        """
        entry = self._agents.get(agent_id)
        if entry is None:
            self.register_agent(agent_id)
            entry = self._agents[agent_id]

        entry.last_user_message_at = time.time()

        if entry.state == AgentConsciousnessState.CONSCIOUS:
            # Already conscious -- just pause the inner loop
            if entry.inner_loop is not None:
                entry.inner_loop.interrupt()
            return

        if entry.state == AgentConsciousnessState.SLEEPING:
            # Try to dequeue (waiting in queue, not yet training)
            if self._sleep_scheduler is not None:
                dequeued = self._sleep_scheduler.dequeue(agent_id)
                if dequeued:
                    logger.info(
                        "Agent %s: dequeued from sleep queue, waking "
                        "for user message", agent_id,
                    )
                    await self._make_conscious(agent_id)
                    if entry.inner_loop is not None:
                        entry.inner_loop.interrupt()
                    return
            # Actively training -- can't preempt GPU mid-cycle.
            # Chat route polls until training completes.
            logger.info(
                "Agent %s: user message while training, will process "
                "after sleep cycle completes", agent_id,
            )
            return

        # FROZEN -- need to wake up immediately
        await self._wake_requests.put(agent_id)

    def on_user_message_complete(self, agent_id: str) -> None:
        """User message has been fully processed.  Resume inner loop."""
        entry = self._agents.get(agent_id)
        if entry is not None and entry.inner_loop is not None:
            entry.inner_loop.resume()

    def get_inner_loop(self, agent_id: str):
        """Public accessor for an agent's InnerLoop (if conscious)."""
        entry = self._agents.get(agent_id)
        return entry.inner_loop if entry is not None else None

    def is_agent_ready(self, agent_id: str) -> bool:
        """Return True only when the agent is CONSCIOUS and ready for inference."""
        entry = self._agents.get(agent_id)
        if entry is None:
            return True
        return entry.state == AgentConsciousnessState.CONSCIOUS

    def on_sleep_complete(self, agent_id: str) -> None:
        """Training/sleep cycle complete.  Transition to FROZEN."""
        entry = self._agents.get(agent_id)
        if entry is not None:
            entry.state = AgentConsciousnessState.FROZEN
            entry.inner_loop = None
            logger.info(
                "Agent %s: sleep complete -> FROZEN", agent_id,
            )

    # ===================================================================
    # Pause / Unpause (user-initiated)
    # ===================================================================

    async def pause_agent(self, agent_id: str) -> bool:
        """Pause an agent: stop inner loop and prevent auto-wake.

        The agent stays registered but will not be rotated into
        CONSCIOUS by the scheduler.  Direct chat still works (the
        foreground inference path doesn't need the inner loop).

        Returns True if the agent was paused successfully.
        """
        entry = self._agents.get(agent_id)
        if entry is None:
            return False

        entry.user_paused = True

        if entry.state == AgentConsciousnessState.CONSCIOUS:
            await self._freeze(agent_id)

        self._persist_paused_flag(agent_id, paused=True)
        logger.info("Agent %s: USER PAUSED", agent_id)
        return True

    async def unpause_agent(self, agent_id: str) -> bool:
        """Unpause an agent: allow the scheduler to make it conscious.

        The scheduler will rotate the agent into CONSCIOUS on its next
        cycle if capacity is available.

        Returns True if the agent was unpaused successfully.
        """
        entry = self._agents.get(agent_id)
        if entry is None:
            return False

        entry.user_paused = False
        self._persist_paused_flag(agent_id, paused=False)
        logger.info("Agent %s: USER UNPAUSED", agent_id)
        return True

    def is_agent_paused(self, agent_id: str) -> bool:
        entry = self._agents.get(agent_id)
        return entry.user_paused if entry is not None else False

    def _persist_paused_flag(self, agent_id: str, *, paused: bool) -> None:
        """Write the paused flag to the agent's meta file on disk."""
        import json
        runtime = self._get_runtime(agent_id)
        if runtime is None:
            return
        agent_dir = getattr(runtime, "agent_dir", None)
        if agent_dir is None:
            return
        meta_path = agent_dir / "agent_meta.json"
        try:
            meta: dict = {}
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["user_paused"] = paused
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(
                "Agent %s: failed to persist paused flag: %s",
                agent_id, exc,
            )

    def _load_paused_flag(self, agent_id: str) -> bool:
        """Read the paused flag from disk (called at registration)."""
        import json
        runtime = self._get_runtime(agent_id)
        if runtime is None:
            return False
        agent_dir = getattr(runtime, "agent_dir", None)
        if agent_dir is None:
            return False
        meta_path = agent_dir / "agent_meta.json"
        try:
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                return bool(meta.get("user_paused", False))
        except Exception:
            pass
        return False

    # ===================================================================
    # Background Loop
    # ===================================================================

    async def _run(self) -> None:
        """Main scheduler loop."""
        # Wait for startup
        await asyncio.sleep(3.0)

        while self._running:
            try:
                # 1. Handle user-message wake requests (preemption)
                await self._handle_wake_requests()

                # 2. Assign free workers to frozen agents
                await self._assign_free_workers()

                # 3. Handle stopped inner loops (sleep transitions)
                self._handle_stopped_loops()

                # 4. Flush low-priority event buffers (IR-11.1)
                if self.connection_manager is not None:
                    try:
                        await self.connection_manager.flush_all_buffers()
                    except Exception:
                        pass

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "ConsciousnessScheduler error: %s",
                    exc, exc_info=True,
                )

            try:
                await asyncio.sleep(self.scheduler_tick)
            except asyncio.CancelledError:
                break

    # ===================================================================
    # Wake Requests (User Message Preemption)
    # ===================================================================

    def _has_capacity(self) -> bool:
        """Check if we can make another agent conscious.

        Capacity is determined by ``_max_conscious`` concurrent inner loops.
        """
        conscious_count = sum(
            1 for e in self._agents.values()
            if e.state == AgentConsciousnessState.CONSCIOUS
        )
        return conscious_count < self._max_conscious

    async def _handle_wake_requests(self) -> None:
        """Process queued wake requests.  User messages always win."""
        while not self._wake_requests.empty():
            try:
                agent_id = self._wake_requests.get_nowait()
            except asyncio.QueueEmpty:
                break

            entry = self._agents.get(agent_id)
            if entry is None or entry.state == AgentConsciousnessState.CONSCIOUS:
                continue

            # Try to get capacity (free worker or Model A slot)
            if self._has_capacity():
                await self._make_conscious(agent_id)
                continue

            # No capacity -- preempt the lowest-priority agent
            conscious = [
                e for e in self._agents.values()
                if e.state == AgentConsciousnessState.CONSCIOUS
            ]
            if not conscious:
                # No one to preempt, agent stays frozen
                logger.warning(
                    "Agent %s: wake requested but no capacity and no "
                    "agents to preempt", agent_id,
                )
                continue

            victim = min(conscious, key=lambda e: self._priority(e.agent_id))
            logger.info(
                "Agent %s: preempting %s for user message",
                agent_id, victim.agent_id,
            )
            await self._freeze(victim.agent_id)
            self._total_preemptions += 1

            # Now wake the target
            await self._make_conscious(agent_id)

    # ===================================================================
    # Worker Assignment
    # ===================================================================

    async def _assign_free_workers(self) -> None:
        """Assign free dream workers (or Model A slots) to frozen agents."""
        while self._has_capacity():
            agent_id = self._pick_next_agent()
            if agent_id is None:
                break
            await self._make_conscious(agent_id)

    def _is_agent_in_sleep_hours(self, agent_id: str) -> bool:
        """Check if this agent's circadian clock says it's sleep hours.

        During night sleep hours, don't rotate the agent into CONSCIOUS
        even if capacity is available -- let it rest.
        """
        runtime = self._get_runtime(agent_id)
        if runtime is None:
            return False
        ans = getattr(runtime, "ans", None)
        if ans is None:
            return False
        circ = getattr(ans, "circadian", None)
        if circ is None or not circ.enabled:
            return False
        return circ.is_sleep_hours()

    def _pick_next_agent(self) -> str | None:
        """Select the highest-priority frozen agent.

        Skips agents whose circadian clock says it's sleep hours,
        agents whose runtime is unavailable, and user-paused agents.
        """
        frozen = [
            e for e in self._agents.values()
            if (e.state == AgentConsciousnessState.FROZEN
                and not e.user_paused
                and not self._is_agent_in_sleep_hours(e.agent_id)
                and not getattr(e, "_no_runtime_warned", False))
        ]
        if not frozen:
            return None
        best = max(frozen, key=lambda e: self._priority(e.agent_id))
        return best.agent_id

    def _priority(self, agent_id: str) -> float:
        """Compute consciousness priority for an agent.

        Higher = more urgent need for consciousness.

        Factors:
          - Time since last conscious (40%): longer idle = higher priority
          - Max drive pressure (30%): unsatisfied needs
          - Signal buffer depth (20%): unprocessed experiences
          - Cortisol level (10%): stress signal
        """
        entry = self._agents.get(agent_id)
        if entry is None:
            return 0.0

        # Try to get live self_state from runtime
        runtime = self._get_runtime(agent_id)
        ss = getattr(runtime, "self_state", None) if runtime else None

        # Fall back to cached state
        if ss is None:
            ss = entry.self_state_cache

        # Time factor
        time_factor = min(
            (time.time() - entry.last_conscious_at) / 3600.0,
            1.0,
        )

        if ss is None:
            return time_factor * 0.4

        # Drive pressure factor
        max_pressure = max(ss.drive_pressures.values(), default=0.0)
        drive_factor = min(max_pressure, 1.0)

        # Signal buffer factor
        signal_factor = min(ss.signal_buffer_depth / 100.0, 1.0)

        # Cortisol factor
        cortisol = ss.hormones.get("cortisol", 0.0)
        cortisol_factor = 1.0 if cortisol > 0.5 else 0.0

        return (
            time_factor * 0.4
            + drive_factor * 0.3
            + signal_factor * 0.2
            + cortisol_factor * 0.1
        )

    # ===================================================================
    # State Transitions
    # ===================================================================

    async def _make_conscious(self, agent_id: str) -> None:
        """Transition an agent to CONSCIOUS: start its inner loop."""
        entry = self._agents.get(agent_id)
        if entry is None:
            return

        runtime = self._get_runtime(agent_id)
        if runtime is None:
            # Avoid log-spam: only warn once, then mark stale so
            # _pick_next_agent skips this agent until re-registered.
            if not getattr(entry, "_no_runtime_warned", False):
                logger.warning(
                    "Agent %s: no runtime available, can't make conscious",
                    agent_id,
                )
                entry._no_runtime_warned = True  # type: ignore[attr-defined]
            return

        # Ensure self_state exists on runtime
        if not hasattr(runtime, "self_state") or runtime.self_state is None:
            runtime.self_state = SelfState()
            # Try to load from disk
            state_path = getattr(runtime, "agent_dir", None)
            if state_path is not None:
                runtime.self_state.load(state_path / "self_state.json")

        # Create and start inner loop
        inner_loop = InnerLoop(
            runtime=runtime,
            connection_manager=self.connection_manager,
            model_a=self._model_a,
            model_a_tokenizer=self._model_a_tokenizer,
            vllm_client=self._vllm_client,
        )
        inner_loop.start()

        entry.state = AgentConsciousnessState.CONSCIOUS
        entry.inner_loop = inner_loop
        entry.last_conscious_at = time.time()
        self._total_wakes += 1

        logger.info("Agent %s: FROZEN -> CONSCIOUS", agent_id)

        if self.connection_manager is not None:
            await self.connection_manager.broadcast_prioritized(agent_id, {
                "type": "consciousness_state",
                "state": "conscious",
            })

    async def _freeze(self, agent_id: str) -> None:
        """Transition an agent to FROZEN: stop inner loop, save state."""
        entry = self._agents.get(agent_id)
        if entry is None:
            return

        # Stop inner loop
        if entry.inner_loop is not None:
            entry.inner_loop.stop(reason="frozen")
            entry.inner_loop = None

        # Save self_state to disk
        runtime = self._get_runtime(agent_id)
        if runtime is not None:
            ss = getattr(runtime, "self_state", None)
            if ss is not None:
                agent_dir = getattr(runtime, "agent_dir", None)
                if agent_dir is not None:
                    try:
                        ss.save(agent_dir / "self_state.json")
                        entry.self_state_cache = ss
                    except Exception as exc:
                        logger.warning(
                            "Agent %s: failed to save self_state: %s",
                            agent_id, exc,
                        )

        entry.state = AgentConsciousnessState.FROZEN
        entry.last_conscious_at = time.time()
        self._total_freezes += 1

        logger.info("Agent %s: CONSCIOUS -> FROZEN", agent_id)

        if self.connection_manager is not None:
            await self.connection_manager.broadcast_prioritized(agent_id, {
                "type": "consciousness_state",
                "state": "frozen",
            })

    def _transition_to_sleeping(self, agent_id: str) -> None:
        """Mark an agent as SLEEPING (training has started)."""
        entry = self._agents.get(agent_id)
        if entry is None:
            return

        # Stop inner loop if still running
        if entry.inner_loop is not None:
            entry.inner_loop.stop(reason="sleeping")
            entry.inner_loop = None

        entry.state = AgentConsciousnessState.SLEEPING
        logger.info("Agent %s: -> SLEEPING", agent_id)

    # ===================================================================
    # Stopped Loop Handling
    # ===================================================================

    def _handle_stopped_loops(self) -> None:
        """Check for inner loops that stopped themselves (sleep trigger)."""
        for entry in self._agents.values():
            if entry.state != AgentConsciousnessState.CONSCIOUS:
                continue
            if entry.inner_loop is None:
                continue
            if entry.inner_loop.is_running:
                continue

            # Inner loop stopped -- check why
            reason = entry.inner_loop.stats.stop_reason
            if reason.startswith("sleep:"):
                # Natural sleep transition
                self._transition_to_sleeping(entry.agent_id)
                logger.info(
                    "Agent %s: inner loop triggered sleep (%s)",
                    entry.agent_id, reason,
                )

                # Enqueue actual sleep training via SleepScheduler
                if self._sleep_scheduler is not None:
                    runtime = self._get_runtime(entry.agent_id)
                    signal_count = 0
                    if runtime and hasattr(runtime, "ans"):
                        ans = runtime.ans
                        if ans is not None:
                            signal_count = len(
                                getattr(ans, "signal_buffer", [])
                            )
                    from nls.models import SleepRequest
                    self._sleep_scheduler.enqueue_sync(SleepRequest(
                        agent_id=entry.agent_id,
                        reason=reason,
                        signal_count=signal_count,
                    ))
                    logger.info(
                        "Agent %s: sleep training enqueued (%d signals)",
                        entry.agent_id, signal_count,
                    )
                else:
                    logger.warning(
                        "Agent %s: sleep triggered but no sleep_scheduler "
                        "configured -- training skipped!",
                        entry.agent_id,
                    )
            else:
                # Loop ended for other reasons -- freeze
                entry.state = AgentConsciousnessState.FROZEN
                entry.inner_loop = None
                entry.last_conscious_at = time.time()
                logger.info(
                    "Agent %s: inner loop ended (%s) -> FROZEN",
                    entry.agent_id, reason,
                )

    # ===================================================================
    # Helpers
    # ===================================================================

    def _get_runtime(self, agent_id: str) -> Any | None:
        """Get an agent's ServerRuntime from the AgentManager."""
        runtimes = self.agent_manager.get_loaded_runtimes()
        return runtimes.get(agent_id)

    # ===================================================================
    # Status
    # ===================================================================

    def get_status(self) -> dict[str, Any]:
        """Return scheduler status for diagnostics/health endpoint."""
        agents_status = {}
        for agent_id, entry in self._agents.items():
            inner_status = None
            if entry.inner_loop is not None:
                inner_status = entry.inner_loop.get_status()
            agents_status[agent_id] = {
                "state": entry.state.value,
                "user_paused": entry.user_paused,
                "last_conscious_at": entry.last_conscious_at,
                "last_user_message_at": entry.last_user_message_at,
                "priority": self._priority(agent_id),
                "inner_loop": inner_status,
            }

        return {
            "running": self._running,
            "scheduler_tick": self.scheduler_tick,
            "total_wakes": self._total_wakes,
            "total_freezes": self._total_freezes,
            "total_preemptions": self._total_preemptions,
            "agents": agents_status,
            "conscious_count": sum(
                1 for e in self._agents.values()
                if e.state == AgentConsciousnessState.CONSCIOUS
            ),
            "sleeping_count": sum(
                1 for e in self._agents.values()
                if e.state == AgentConsciousnessState.SLEEPING
            ),
            "frozen_count": sum(
                1 for e in self._agents.values()
                if e.state == AgentConsciousnessState.FROZEN
            ),
        }
