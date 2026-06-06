"""NLS Inner Loop -- Continuous Consciousness.

Replaces the tick-based DaydreamScheduler with a continuous,
biologically-inspired processing loop for each conscious agent.

Two rhythms:

  THE HEARTBEAT (fast, every cycle)
    Pure math: recalculate SelfState, decay hormones, update drive
    pressures.  Microseconds.  No GPU.

  THE BREATH (slow, every N heartbeats)
    Model inference: DMN activation, drive evaluation, proactive
    initiative checks.  500ms-2s.  Uses GPU via dream worker.

The heartbeat never stops while the agent is conscious.  The breath
frequency is modulated by engagement: high engagement = more breaths
(more thinking), low engagement = fewer breaths (conserve GPU).

User messages are interrupts: they pause the inner loop, get processed
by the main inference path, then the loop resumes.

Architecture::

    InnerLoop._run()
      while not interrupted:
        # HEARTBEAT (every cycle)
        period = self_state.beat()      # math, µs
        beats_since_breath += 1

        # BREATH (every N beats)
        if beats_since_breath >= breath_interval:
          → DMN activation check
          → Drive evaluation
          → Proactive initiative
          → Sleep trigger check

        await sleep(period)

The InnerLoop is instantiated per-agent and bound to a single dream
worker.  The ConsciousnessScheduler (Phase 3) manages which agents
get inner loops and on which workers.
"""

from __future__ import annotations

import asyncio
import logging
import math
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nls.engine.events import AgentEvent

logger = logging.getLogger(__name__)


# ===================================================================
# Inner Loop State
# ===================================================================


@dataclass
class InnerLoopStats:
    """Telemetry for one inner loop session."""

    total_beats: int = 0
    total_breaths: int = 0
    total_dreams: int = 0
    total_active_dreams: int = 0
    total_drive_actions: int = 0
    started_at: float = field(default_factory=time.time)
    stopped_at: float | None = None
    stop_reason: str = ""


# ===================================================================
# Inner Loop
# ===================================================================


def dispatch_priority(source: str, prompt: str = "") -> int:
    """Lower number = higher priority when draining ``_pending_dispatches``."""
    if source.startswith("team_wave_complete:"):
        if "PLAN RECOVERY" in (prompt or ""):
            return 0
        return 5
    if source.startswith("pending_wave_launch:"):
        return 10
    if source == "delegate_batch_complete":
        return 15
    if source.startswith("team_completion_review:"):
        return 20
    if source.startswith("team_member_escalation:"):
        return 25
    if source.startswith("team_checkback:"):
        return 80
    if source.startswith("scheduler:"):
        return 90
    return 50


class InnerLoop:
    """Continuous consciousness loop for one agent on one dream worker.

    Two rhythms: the heartbeat (fast, math-only) and the breath
    (slow, inference).  The loop runs until interrupted by a user
    message, preempted by the scheduler, or the agent falls asleep.

    Parameters
    ----------
    runtime : ServerRuntime
        The agent's server runtime (owns SelfState, brain components).
    worker_pool : WorkerPool
        For acquiring dream workers.
    connection_manager : Any, optional
        For broadcasting events to the frontend.
    """

    def __init__(
        self,
        runtime: Any,
        connection_manager: Any = None,
        *,
        model_a: Any | None = None,
        model_a_tokenizer: Any | None = None,
        vllm_client: Any | None = None,
    ):
        self.runtime = runtime
        self.connection_manager = connection_manager

        # vLLM client: when set, inference goes through vLLM HTTP API
        self._vllm_client = vllm_client

        # OpenAI-compatible inference client (Model A path for dreams/drives)
        self._model_a = model_a
        self._model_a_tokenizer = model_a_tokenizer
        self._use_model_a = all(
            x is not None
            for x in (model_a, model_a_tokenizer)
        )

        self._interrupted = False
        self._paused = False
        self._running = False
        self._task: asyncio.Task | None = None
        self.stats = InnerLoopStats()

        # Abort event for the currently-running autonomous dispatch.
        # Set to an asyncio.Event while a background loop is active;
        # None otherwise.  ws_handler sets it to abort the background
        # task immediately when a foreground user message arrives.
        self._autonomous_abort: asyncio.Event | None = None

        # ── Sleep inertia / Cortisol Awakening Response ──
        # In human biology, the cortisol awakening response (CAR)
        # prevents immediate re-sleep after waking.  Adenosine has
        # been cleared, cortisol rises sharply -- the brain protects
        # its fresh wakefulness.  We mirror this by skipping sleep
        # checks for the first N breaths, giving the agent time to
        # actually use what it consolidated before the persisted
        # signal buffer triggers sleep again.
        self._grace_breaths: int = 3

        # ── Drowsy negotiation (hypnagogia) ──
        # In humans, sleep onset is not a switch -- it's a gradient.
        # The hypnagogic period allows social cues to override sleep
        # pressure ("someone is talking to me, I should stay awake").
        # When the ANS wants sleep but a user is present, we enter
        # a negotiation: broadcast drowsiness, wait for consent.
        self._pending_sleep_reason: str | None = None
        self._pending_sleep_at: float | None = None

        # ── User-denied sleep cooldown ──
        # When the user explicitly says "stay awake", we suppress ALL
        # sleep requests until this timestamp.  Prevents the annoying
        # pattern of re-asking every 30 seconds.
        self._deny_sleep_until: float = 0.0

        # ── Pending autonomous dispatches ──
        # External code (e.g. DelegateManager batch completion) can
        # enqueue prompts here.  The next breath cycle picks them up
        # and runs _dispatch_autonomous_v2.
        self._pending_dispatches: list[tuple[str, str]] = []

        # ── Post-completion drive cooldown ──
        # Set by the ws_handler after a foreground task completes so the
        # inner loop suppresses drive evaluation briefly.
        self._last_foreground_completion_ts: float = 0.0
        self._last_agentic_stall_ts: float = 0.0

        # ── Event queue (Phase 0 — additive, unused by dispatch yet) ──
        # All event sources push typed AgentEvents here.  Phase 2 will
        # switch _breath to drain from this queue instead of _pending_dispatches.
        from nls.engine.events import AgentEventQueue
        self.event_queue = AgentEventQueue()

        # ── Processed event deduplication ──
        # Prevents re-dispatch of events that were already handled
        # (e.g. delegate_batch_complete that timed out but ran to completion).
        self._processed_event_ids: set[str] = set()

        # ── Active dream state ──
        # Tracks an in-progress active dream so it can be cleanly
        # aborted when a user message arrives (user always preempts).
        self._active_dream_task: asyncio.Task | None = None

    # ===================================================================
    # Lifecycle
    # ===================================================================

    def start(self) -> None:
        """Start the inner loop as a background asyncio task."""
        if self._running:
            return
        self._interrupted = False
        self._paused = False
        self._running = True
        self.stats = InnerLoopStats()
        self._task = asyncio.create_task(self._run())
        logger.info(
            "InnerLoop started for agent %s", self.runtime.agent_id,
        )

    def stop(self, reason: str = "external") -> None:
        """Stop the inner loop."""
        self._interrupted = True
        self.stats.stop_reason = reason
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._running = False
        self.stats.stopped_at = time.time()
        logger.info(
            "InnerLoop stopped for agent %s (reason=%s, beats=%d, "
            "breaths=%d)",
            self.runtime.agent_id, reason,
            self.stats.total_beats, self.stats.total_breaths,
        )

    def preempt_background(self) -> None:
        """Cancel daydream/DMN work so the agent can attend to a human.

        Does **not** pause the breath cycle — safe to call when enqueueing
        a channel event that the inner loop will dispatch on the next breath.
        Web/chat foreground paths use :meth:`interrupt` instead (pause + this).
        """
        if self._pending_sleep_reason is not None:
            logger.info(
                "InnerLoop: cancelled pending sleep for agent %s "
                "(user preempts background, reason was: %s)",
                self.runtime.agent_id, self._pending_sleep_reason,
            )
            self._pending_sleep_reason = None
            self._pending_sleep_at = None

        ans = getattr(self.runtime, "ans", None)
        if ans is not None:
            from datetime import datetime as _dt
            ans._last_interaction_at = _dt.utcnow()

        self._grace_breaths = max(self._grace_breaths, 5)

        if self._active_dream_task is not None:
            if not self._active_dream_task.done():
                self._active_dream_task.cancel()
                logger.info(
                    "InnerLoop: active dream cancelled for agent %s "
                    "(background preempt)",
                    self.runtime.agent_id,
                )
            self._active_dream_task = None

        ss = getattr(self.runtime, "self_state", None)
        if ss is not None:
            ss.turns_since_input = 0

    def interrupt(self) -> None:
        """User message arrives.  Pause the loop so the main inference
        path can handle the message.  The heartbeat effectively pauses.

        If an active dream is in progress, cancel it immediately.
        The user always preempts -- the agent drops everything to
        attend to the human, like waking from a dream.

        Call ``resume()`` after the user message is processed.
        """
        self.preempt_background()
        self._paused = True
        logger.debug(
            "InnerLoop paused for agent %s (user message)",
            self.runtime.agent_id,
        )

    def resume(self) -> None:
        """Resume after a user message has been processed."""
        self._paused = False

        # Ensure self_state idle counter is zeroed — process_message()
        # sets it deep in the response path but a second reset here
        # guarantees the breath cycle sees "user just talked."
        ss = getattr(self.runtime, "self_state", None)
        if ss is not None:
            ss.turns_since_input = 0

        logger.debug(
            "InnerLoop resumed for agent %s", self.runtime.agent_id,
        )

    @property
    def is_running(self) -> bool:
        return self._running and not self._interrupted

    # ===================================================================
    # The Loop
    # ===================================================================

    async def _run(self) -> None:
        """Main consciousness loop.

        Two rhythms:
          - Heartbeat: every cycle, pure math
          - Breath: every N heartbeats, model inference
        """
        beats_since_breath = 0
        agent_id = self.runtime.agent_id

        try:
            while not self._interrupted:
                # --- Pause handling (user message being processed) ---
                if self._paused:
                    await asyncio.sleep(0.1)
                    continue

                # --- Get self_state ---
                self_state = getattr(self.runtime, "self_state", None)
                if self_state is None:
                    logger.warning(
                        "Agent %s: no self_state, inner loop waiting...",
                        agent_id,
                    )
                    await asyncio.sleep(1.0)
                    continue

                # ── THE HEARTBEAT (every cycle, pure math, no GPU) ──
                hypothalamus = getattr(self.runtime, "hypothalamus", None)
                period = self_state.beat(hypothalamus=hypothalamus)

                # Collect from all brain subsystems
                self._collect_state()

                # Wall-clock flush for LearningAccumulator
                _acc = getattr(self.runtime, "_learning_accumulator", None)
                if _acc is not None and _acc.should_wall_clock_flush():
                    _acc_wm = getattr(self.runtime, "dual_wm", None) or getattr(self.runtime, "working_memory", None)
                    if _acc_wm is not None:
                        try:
                            _acc.flush(_acc_wm, reason="wall-clock")
                        except Exception:
                            pass

                beats_since_breath += 1
                self.stats.total_beats += 1

                # ── THE BREATH (every N beats, uses GPU) ──
                # Adaptive interval: when drives are blocked, the agent
                # naturally slows down its checking frequency -- like a
                # human who stops rattling a locked door and instead
                # sits down and waits.  The base interval comes from
                # engagement; the frustration stretch comes from blocked
                # drives.  At 20+ blocked ticks, the interval doubles.
                breath_interval = self_state.breath_interval_beats()
                if self_state.drives_blocked and self_state.frustration > 0.1:
                    # Stretch the interval: the more frustrated, the
                    # slower the checking.  Max 3x stretch.
                    stretch = 1.0 + min(2.0, self_state.frustration * 3.0)
                    breath_interval = int(breath_interval * stretch)
                if beats_since_breath >= breath_interval:
                    beats_since_breath = 0
                    self.stats.total_breaths += 1

                    # Only breathe when BYO inference is available
                    can_breathe = self._use_model_a or (
                        hasattr(self.runtime, "inference_available")
                        and self.runtime.inference_available()
                    )
                    if can_breathe:
                        try:
                            await self._breath(self_state)
                        except Exception as exc:
                            logger.warning(
                                "Agent %s: breath failed: %s",
                                agent_id, exc,
                            )

                # ── Wait for next heartbeat ──
                try:
                    await asyncio.sleep(period)
                except asyncio.CancelledError:
                    break

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(
                "Agent %s: inner loop crashed: %s",
                agent_id, exc, exc_info=True,
            )
        finally:
            self._running = False
            self.stats.stopped_at = time.time()

    # ===================================================================
    # State Collection
    # ===================================================================

    def _collect_state(self) -> None:
        """Collect readings from all brain subsystems into self_state."""
        rt = self.runtime
        ss = getattr(rt, "self_state", None)
        if ss is None:
            return

        # Hypothalamus -> hormone levels
        hypo = getattr(rt, "hypothalamus", None)
        if hypo is not None:
            hypo.contribute_to_state(ss)

        # Drives -> drive pressures + frustration state
        drives = getattr(rt, "drive_engine", None)
        if drives is not None and hypo is not None:
            drives.contribute_to_state(ss, hypo)
            # Propagate frustration state from drive engine to self_state.
            # This keeps the state consistent between heartbeats (which
            # don't evaluate drives, only collect) and breaths (which do).
            if drives.is_frustrated:
                ss.drives_blocked = True

        # ANS -> signal buffer depth + front-brain context (IR-4/IR-7)
        ans = getattr(rt, "ans", None)
        if ans is not None:
            ans.contribute_to_state(ss)
            tom = getattr(rt, "theory_of_mind", None)
            tom_interests: list[str] = []
            if tom is not None:
                try:
                    um = tom.get_user()
                    tom_interests = um.top_interests(5) if um else []
                except Exception:
                    pass
            ans.set_front_brain_context(
                prediction_error=ss.prediction_error,
                energy=ss.energy,
                cognitive_load=ss.cognitive_load,
                resonance=ss.resonance,
                episode_tag=ss.episode_arc,
                tom_interests=tom_interests,
            )

        # DMN -> (placeholder, no direct fields)
        dmn = getattr(rt, "dmn", None)
        if dmn is not None:
            dmn.contribute_to_state(ss)

        # Temporal Self -> trajectory, mood, energy, felt time
        temporal = getattr(rt, "temporal_self", None)
        if temporal is not None:
            temporal.record(ss)
            derivs = temporal.compute_derivatives()
            ss.delta_valence = derivs.get("delta_valence", 0.0)
            ss.delta_arousal = derivs.get("delta_arousal", 0.0)
            ss.delta_coherence = derivs.get("delta_coherence", 0.0)
            ss.delta_engagement = derivs.get("delta_engagement", 0.0)
            ss.mood_valence = temporal.mood_valence
            ss.mood_arousal = temporal.mood_arousal
            ss.energy = temporal.energy
            ss.mood_label = temporal.get_mood_label()
            ss.felt_idle = temporal.felt_idle_time()
            ss.momentum = temporal.momentum()

            # IR-1: Feed energy and mood context to hypothalamus
            if hypo is not None:
                hypo.set_energy_level(temporal.energy)
                hypo.set_mood_context(temporal.mood_valence)

        # Working Memory -> salience decay each heartbeat + collect (IR-3)
        wm = getattr(rt, "working_memory", None)
        if wm is not None:
            wm.decay_salience(dt=1.0)
            ss.collect_from_working_memory(wm)

        # Predictive Processing -> collect for predictive confidence (IR-3)
        pp = getattr(rt, "predictive", None)
        if pp is not None:
            ss.collect_from_predictive(pp)

        # Narrative Self -> regulation evaluation each heartbeat
        narrative = getattr(rt, "narrative_self", None)
        if narrative is not None and ss is not None:
            cortisol = ss.hormones.get("cortisol", 0.0)
            strategy = narrative.evaluate_regulation(
                cortisol=cortisol,
                valence=ss.valence,
                coherence=narrative.narrative_coherence,
            )
            if strategy:
                hypo = getattr(rt, "hypothalamus", None)
                if hypo is not None:
                    narrative.apply_regulation_to_hormones(hypo)
            # Sync regulation state: active strategy or cleared if faded
            ss.regulation_strategy = narrative._active_strategy or ""
            ss.narrative_coherence = narrative.narrative_coherence
            ss.coherence_label = narrative.coherence_label()

        # Network Dynamics -> update ECN/SN/DMN activation each heartbeat
        nd = getattr(rt, "network_dynamics", None)
        if nd is not None and ss is not None:
            wm_avg = 0.0
            wm_obj = getattr(rt, "working_memory", None)
            if wm_obj is not None and wm_obj.get_slot_count() > 0:
                wm_avg = wm_obj.get_avg_salience()
            nd.update(
                engagement=ss.engagement,
                arousal=ss.arousal,
                delta_ratio=ss.delta_ratio,
                turns_since_input=ss.turns_since_input,
                frustration=ss.frustration,
                prediction_error=ss.prediction_error,
                energy=ss.energy,
                wm_avg_salience=wm_avg,
            )
            ss.network_ecn = nd.ecn
            ss.network_sn = nd.sn
            ss.network_dmn = nd.dmn
            ss.dominant_network = nd.dominant

    # ===================================================================
    # The Breath (slow channel -- uses GPU)
    # ===================================================================

    async def _breath(self, self_state: Any) -> None:
        """One breath: model inference cycle.

        Checks DMN activation, drive evaluation, proactive initiative,
        and sleep triggers in sequence.  Each step may or may not fire.
        """
        agent_id = self.runtime.agent_id
        rt = self.runtime

        # Safety guard: if the ANS has already transitioned to sleeping
        # (e.g. user-triggered manual sleep), stop the loop immediately
        # so DMN and drives don't keep firing during sleep.
        ans = getattr(rt, "ans", None)
        if ans is not None and ans.is_sleeping:
            logger.info(
                "Agent %s: ANS is sleeping, stopping inner loop",
                agent_id,
            )
            self.stop(reason="sleep:ans_already_sleeping")
            return

        # Advance the idle counter each breath.  Reset to 0 happens in
        # ServerRuntime when user input arrives.
        self_state.turns_since_input += 1

        # --- Event-driven dispatch from priority queue (Phase 2) ---
        # Drain the event queue first (higher-priority events first).
        # Falls back to legacy _pending_dispatches for backward compat.
        _dispatched_from_eq = False
        if not self.event_queue.is_empty and self._can_dispatch_v2(rt):
            _dispatched_from_eq = await self._dispatch_from_event_queue(rt)

        # Legacy path: pending autonomous dispatches
        if (
            not _dispatched_from_eq
            and self._pending_dispatches
            and self._can_dispatch_v2(rt)
        ):
            from nls.agentic.wake_coordination import (
                should_skip_stale_orchestration_wake,
            )

            prompt, source = "", ""
            while self._pending_dispatches:
                prompt, source = self._pop_highest_priority_dispatch()
                _tm = getattr(rt, "_team_manager", None)
                if _tm is not None and should_skip_stale_orchestration_wake(
                    _tm,
                    source,
                    context=f"dispatch:{source}",
                ):
                    prompt, source = "", ""
                    continue
                break
            if prompt and source:
                logger.info(
                    "Agent %s: dispatching pending autonomous task "
                    "(source=%s, remaining=%d)",
                    agent_id, source, len(self._pending_dispatches),
                )
                try:
                    await self._dispatch_autonomous_v2(rt, prompt, source=source)
                except Exception:
                    logger.warning(
                        "Agent %s: pending autonomous dispatch failed",
                        agent_id, exc_info=True,
                    )

        # --- Proactive initiative check ---
        if self.connection_manager is not None:
            try:
                initiative = rt.check_proactive_initiative()
                if initiative:
                    await self._dispatch_reach_out(
                        agent_id, rt, initiative,
                    )
            except Exception:
                pass

        # --- Drive evaluation (continuous, per-breath) ---
        # Drives and DMN are gated by their own idle timing
        # (turns_since_input, min idle breaths, pressure thresholds).
        # The inner loop pauses during active inference (_paused flag),
        # so there is no GPU contention with user messages.

        # Cortisol gate: skip drives entirely when stress is high
        # (prevents doom-loop of spawning tasks into hostile hormonal state)
        _DRIVE_CORTISOL_GATE = 0.5
        _POST_ABORT_COOLDOWN_S = 60.0
        _cortisol_blocked = False
        _hypo = getattr(rt, "hypothalamus", None)
        if _hypo is not None:
            _cort = _hypo.hormones.get("cortisol")
            if _cort is not None and _cort.level > _DRIVE_CORTISOL_GATE:
                _cortisol_blocked = True
                logger.debug(
                    "Agent %s: drive gate — cortisol %.3f > %.2f, "
                    "skipping drive tick",
                    agent_id, _cort.level, _DRIVE_CORTISOL_GATE,
                )

        # Post-abort cooldown: don't spawn drives immediately after a
        # main loop abort — let cortisol decay first
        _abort_blocked = False
        _last_abort = max(
            getattr(self, "_last_agentic_abort_ts", None) or 0.0,
            getattr(rt, "_last_agentic_abort_ts", None) or 0.0,
        )
        if _last_abort and (time.time() - _last_abort) < _POST_ABORT_COOLDOWN_S:
            _abort_blocked = True
            logger.debug(
                "Agent %s: drive gate — post-abort cooldown "
                "(%.0fs remaining)",
                agent_id,
                _POST_ABORT_COOLDOWN_S - (time.time() - _last_abort),
            )

        # Post-completion cooldown: don't fire drives immediately after a
        # foreground task finishes — give the user a moment to follow up
        # before the agent launches background work.
        _POST_COMPLETION_COOLDOWN_S = 25.0
        _completion_blocked = False
        _last_completion = getattr(self, "_last_foreground_completion_ts", 0.0)
        if _last_completion and (time.time() - _last_completion) < _POST_COMPLETION_COOLDOWN_S:
            _completion_blocked = True
            logger.debug(
                "Agent %s: drive gate — post-completion cooldown "
                "(%.0fs remaining)",
                agent_id,
                _POST_COMPLETION_COOLDOWN_S - (time.time() - _last_completion),
            )

        # --- Todo priority: pending todos override curiosity drives ---
        _has_pending_todos = False
        try:
            _todo_tool = next(
                (t for t in getattr(rt, "_agent_tools", None) or []
                 if getattr(t, "name", "") == "todo"),
                None,
            )
            if _todo_tool is not None:
                _todo_store = getattr(_todo_tool, "_store", None)
                if _todo_store is not None:
                    _has_pending_todos = _todo_store.next_idle_task() is not None
        except Exception:
            pass

        # --- Team-active gate: suppress drives/daydreaming while a team
        # is running.  Only pending dispatches (delegate results, check-backs,
        # escalations) should proceed — those are handled above.
        _team_active = False
        _tm = getattr(rt, "_team_manager", None)
        if _tm is not None:
            try:
                _active_teams = _tm.list_teams(include_terminal=False)
                _team_active = bool(_active_teams)
            except Exception:
                pass

        _plan_work_open = False
        try:
            from nls.agentic.plan_work import runtime_has_open_plan_work

            _plan_work_open = runtime_has_open_plan_work(rt)
        except Exception:
            pass

        drive_goal = None
        if (
            not _cortisol_blocked
            and not _abort_blocked
            and not _completion_blocked
            and not _has_pending_todos
            and not _team_active
            and not _plan_work_open
        ):
            try:
                drive_goal = rt.tick_drives()
            except Exception as exc:
                logger.warning(
                    "Agent %s: drive tick failed: %s", agent_id, exc,
                )
        elif _has_pending_todos:
            logger.debug(
                "Agent %s: skipping drives — pending todos take priority",
                agent_id,
            )
        elif _team_active:
            logger.debug(
                "Agent %s: skipping drives/daydreaming — team is active",
                agent_id,
            )
        elif _plan_work_open:
            logger.debug(
                "Agent %s: skipping drives — plan/todo work in progress",
                agent_id,
            )

        if drive_goal is not None:
            self_state.drives_blocked = False
            dispatched = await self._dispatch_drive_goal(rt, drive_goal)
            if dispatched:
                return  # drive acted via v2: skip DMN
            # v2 unavailable -- fall back to legacy v1 single-tool dispatch
            await self._execute_drive_on_worker(drive_goal)
            return

        # --- Frustration detection (ACC conflict signal) ---
        drive_engine = getattr(rt, "drive_engine", None)
        drives_are_blocked = (
            _cortisol_blocked
            or _abort_blocked
            or _completion_blocked          # post-completion cooldown gates DMN too
            or (drive_engine is not None and drive_engine.is_frustrated)
        )
        self_state.drives_blocked = drives_are_blocked

        if drives_are_blocked and drive_engine is not None:
            self._emit_frustration_signal(rt, drive_engine)

        # --- WM tactical goal execution (agentic v2 bridge) ---
        _WM_GOAL_MIN_IDLE_BREATHS = 5
        if (
            drive_goal is None
            and not drives_are_blocked
            and not _team_active
            and self_state.turns_since_input >= _WM_GOAL_MIN_IDLE_BREATHS
            and not getattr(self, "_autonomous_executing", False)
        ):
            dispatched = await self._maybe_dispatch_wm_goal(rt)
            if dispatched:
                return

        # --- Job charter background (before drives/DMN; after todos/plan) ---
        _JOB_BG_MIN_IDLE_BREATHS = 5
        if (
            drive_goal is None
            and not drives_are_blocked
            and not _team_active
            and not _has_pending_todos
            and not _plan_work_open
            and self_state.turns_since_input >= _JOB_BG_MIN_IDLE_BREATHS
            and not getattr(self, "_autonomous_executing", False)
        ):
            dispatched = await self._maybe_dispatch_job_background(
                rt,
                has_pending_todos=_has_pending_todos,
                plan_work_open=_plan_work_open,
                team_active=_team_active,
            )
            if dispatched:
                return

        # --- DMN activation (network dynamics-aware) ---
        # Phase 7: DMN eligibility is now governed by the NetworkDynamics
        # module when available.  The three-network model (ECN/SN/DMN)
        # computes anti-correlated activation levels every heartbeat.
        # When the DMN activation exceeds the passive/active thresholds,
        # dreaming becomes eligible.
        #
        # Fallback to the original heuristic when network_dynamics is
        # not initialized (backwards compatibility).
        _PASSIVE_DMN_MIN_IDLE_BREATHS = 3   # ~50s at low engagement
        _ACTIVE_DMN_MIN_IDLE_BREATHS = 8    # ~2 min at low engagement
        nd = getattr(rt, "network_dynamics", None)
        if nd is not None:
            dmn_eligible = nd.is_dmn_eligible(self_state.turns_since_input)
            active_dream_eligible = nd.is_active_dream_eligible(
                self_state.turns_since_input,
            )
        else:
            dmn_eligible = (
                self_state.turns_since_input > _PASSIVE_DMN_MIN_IDLE_BREATHS
                and (
                    self_state.engagement < 0.4
                    or drives_are_blocked
                )
            )
            active_dream_eligible = (
                dmn_eligible
                and self_state.turns_since_input > _ACTIVE_DMN_MIN_IDLE_BREATHS
            )
        if not dmn_eligible and drives_are_blocked:
            logger.info(
                "Agent %s: DMN skipped (turns_since=%d, engagement=%.2f, "
                "blocked=%s)",
                agent_id, self_state.turns_since_input,
                self_state.engagement, drives_are_blocked,
            )

        # Suppress DMN when background delegates or teams are running.
        # The orchestrator should stay attentive — the scheduled check-back
        # job (set by executor.py at spawn time) will re-invoke it every 2 min.
        _dm = getattr(rt, "delegate_manager", None)
        _delegates_running = _dm is not None and _dm.has_active_delegates()
        if _delegates_running or _team_active:
            if dmn_eligible or active_dream_eligible:
                logger.info(
                    "Agent %s: DMN suppressed — active team/delegates running",
                    agent_id,
                )
            dmn_eligible = False
            active_dream_eligible = False

        # Suppress DMN while orchestration plan or in-progress todos remain.
        if _plan_work_open:
            if dmn_eligible or active_dream_eligible:
                logger.info(
                    "Agent %s: DMN suppressed — incomplete plan/todo work",
                    agent_id,
                )
            dmn_eligible = False
            active_dream_eligible = False

        # Job charter background outranks daydreaming when due.
        try:
            from nls.runtime.job_background import job_background_due_for_runtime

            if job_background_due_for_runtime(
                rt,
                has_pending_todos=_has_pending_todos,
                plan_work_open=_plan_work_open,
                team_active=_team_active,
            ):
                if dmn_eligible or active_dream_eligible:
                    logger.info(
                        "Agent %s: DMN suppressed — job background due",
                        agent_id,
                    )
                dmn_eligible = False
                active_dream_eligible = False
        except Exception:
            pass

        if dmn_eligible:
            try:
                # Check for active dream first (tool-using, foraging).
                # Active dreams need a longer idle period than passive
                # dreams -- the agent should be genuinely unoccupied
                # before it starts browsing the web on its own.
                dmn = getattr(rt, "dmn", None)
                if (
                    active_dream_eligible
                    and not _has_pending_todos
                    and dmn is not None
                    and dmn.should_active_dream()
                    and self._active_dream_task is None
                ):
                    await self._start_active_dream(dmn)
                    return  # active dream dispatched, skip passive

                # Passive dream (text-only: replay or exploration)
                dream_job = rt.tick_dmn(skip_hypo_tick=True)
                if dream_job is not None:
                    await self._execute_dream_on_worker(dream_job)
                elif drives_are_blocked:
                    logger.info(
                        "Agent %s: DMN eligible but tick returned None",
                        agent_id,
                    )
            except Exception as exc:
                logger.warning(
                    "Agent %s: DMN tick failed: %s", agent_id, exc,
                )

        # --- Sleep trigger check (circadian-aware) ---
        #
        # Guards (in order):
        # 1. Grace period (CAR): skip for first N breaths after waking
        #    (also granted on user message via interrupt())
        # 2. Pending drowsy negotiation: check timeout (120s)
        # 3. Education in progress: defer
        # 4. Active session: defer
        # 5. Busy guard: defer (agentic workflows)
        # 6. Normal check -> circadian or legacy
        #    - Bedtime/nap deferred by ANS if user active < 5 min
        #    - Drowsy negotiation if user connected
        #
        ans = getattr(rt, "ans", None)
        hypo = getattr(rt, "hypothalamus", None)
        if ans is not None:
            try:
                # ── Guard 1: Grace period (sleep inertia) ──
                if self._grace_breaths > 0:
                    self._grace_breaths -= 1
                    logger.info(
                        "Agent %s: sleep check deferred (grace period, "
                        "%d breaths remaining)",
                        agent_id, self._grace_breaths,
                    )
                # ── Guard 2: User-denied cooldown ──
                elif time.time() < self._deny_sleep_until:
                    _remaining = self._deny_sleep_until - time.time()
                    logger.debug(
                        "Agent %s: sleep suppressed (user denied, "
                        "%.0fs remaining)",
                        agent_id, _remaining,
                    )
                # ── Guard 3: Active detached delegates ──
                elif (
                    hasattr(rt, "delegate_manager")
                    and rt.delegate_manager is not None
                    and rt.delegate_manager.has_active_delegates()
                ):
                    logger.info(
                        "Agent %s: sleep check deferred "
                        "(active delegates running)",
                        agent_id,
                    )
                # ── Guard 4: Pending drowsy negotiation ──
                elif self._pending_sleep_reason is not None:
                    elapsed = time.time() - (self._pending_sleep_at or 0)
                    # Bedtime: 120s timeout (gives user time to respond).
                    # Naps: 120s timeout.
                    _drowsy_timeout = 120.0
                    if elapsed >= _drowsy_timeout:
                        logger.info(
                            "Agent %s: drowsy timeout (%.0fs), sleeping "
                            "(reason=%s)",
                            agent_id, elapsed,
                            self._pending_sleep_reason,
                        )
                        reason = self._pending_sleep_reason
                        self._pending_sleep_reason = None
                        self._pending_sleep_at = None
                        self.stop(reason=f"sleep:{reason}")
                        if self.connection_manager is not None:
                            await self.connection_manager.broadcast(
                                agent_id,
                                {"type": "sleep_triggered",
                                 "reason": reason},
                            )
                # ── Guard 5: Education in progress ──
                elif getattr(rt, "education_active", False):
                    logger.info(
                        "Agent %s: sleep check deferred "
                        "(education active)",
                        agent_id,
                    )
                # ── Guard 6: Busy guard (agentic workflows) ──
                elif getattr(rt, "is_busy", False):
                    logger.info(
                        "Agent %s: sleep check deferred "
                        "(busy guard active)",
                        agent_id,
                    )
                # ── Guard 7: Active dream in progress ──
                elif (self._active_dream_task is not None
                      and not self._active_dream_task.done()):
                    logger.info(
                        "Agent %s: sleep check deferred "
                        "(active dream in progress)",
                        agent_id,
                    )
                else:
                    # ── Normal sleep check (circadian or legacy) ──
                    should_sleep, reason = ans.check_sleep_trigger(
                        hypothalamus=hypo,
                    )
                    if should_sleep:
                        user_connected = (
                            self.connection_manager is not None
                            and self.connection_manager.is_connected(
                                agent_id,
                            )
                        )
                        if not user_connected:
                            # Nobody to negotiate with — sleep immediately
                            logger.info(
                                "Agent %s: sleep triggered by inner "
                                "loop (reason=%s)",
                                agent_id, reason,
                            )
                            self.stop(reason=f"sleep:{reason}")
                            if self.connection_manager is not None:
                                await self.connection_manager.broadcast(
                                    agent_id,
                                    {"type": "sleep_triggered",
                                     "reason": reason},
                                )
                        else:
                            # Drowsy negotiation for ALL triggers
                            # when user is connected (bedtime, nap,
                            # signal_pressure).  Bedtime uses a more
                            # assertive message + shorter timeout.
                            is_bedtime = reason.startswith("bedtime")
                            self._pending_sleep_reason = reason
                            self._pending_sleep_at = time.time()
                            content = (
                                "It's my bedtime -- I should sleep "
                                "to consolidate what I've learned. "
                                "Can I go to sleep?"
                            ) if is_bedtime else (
                                "I'm feeling a bit drowsy... "
                                "I wouldn't mind taking a nap "
                                "to consolidate what I've "
                                "learned. Is that okay?"
                            )
                            logger.info(
                                "Agent %s: drowsy negotiation started "
                                "(reason=%s, user connected)",
                                agent_id, reason,
                            )
                            await self.connection_manager.broadcast(
                                agent_id,
                                {
                                    "type": "drowsy",
                                    "content": content,
                                    "reason": reason,
                                    "actions": ["yes", "no"],
                                },
                            )
            except Exception as exc:
                logger.warning(
                    "Agent %s: sleep check failed: %s", agent_id, exc,
                )

    # ===================================================================
    # Frustration Signal (ACC -> Hypothalamus)
    # ===================================================================

    def _emit_frustration_signal(
        self, runtime: Any, drive_engine: Any,
    ) -> None:
        """Emit hormonal frustration signal when drives are blocked.

        Maps to the ACC (anterior cingulate cortex) conflict signal in
        the human brain.  When you want to act but can't, the ACC
        detects the intention-action mismatch and triggers:

          - Cortisol: mild increase (stress from blocked goal).
            Not a panic spike -- a slow simmer.  "I should be doing
            something but I can't."

          - Dopamine: slight reduction (anticipated reward that never
            comes).  The VTA predicted a reward from action, the action
            didn't happen, so dopamine drops (prediction error).

        The signal scales with frustration duration: first few blocked
        ticks are barely noticeable.  Prolonged blocking builds.

        This naturally leads to:
          - Engagement dropping (cortisol up → arousal changes)
          - BPM decreasing (lower engagement → slower heartbeat)
          - DMN activation (frustration path already enables DMN)
          - Eventually sleep (cortisol + low dopamine → sleep signature)
        """
        hypothalamus = getattr(runtime, "hypothalamus", None)
        if hypothalamus is None:
            return

        blocked_ticks = drive_engine.frustration_ticks

        # Scale the signal: barely noticeable at first, builds over time.
        # Capped at 0.3 magnitude to prevent runaway cortisol storms.
        # The function is sqrt-shaped: fast initial rise, then plateaus.
        # This mirrors human frustration: quick onset, then adaptation.
        frustration_magnitude = min(0.3, 0.05 * math.sqrt(blocked_ticks))

        if frustration_magnitude < 0.01:
            return

        # Cortisol: mild stress from blocked intention
        cortisol = hypothalamus.hormones.get("cortisol")
        if cortisol is not None:
            cortisol.produce(frustration_magnitude)

        # Dopamine: slight drop from unrewarded anticipation.
        # We don't produce dopamine -- we nudge it DOWN toward
        # a lower value.  This mimics the prediction error signal.
        dopamine = hypothalamus.hormones.get("dopamine")
        if dopamine is not None:
            dopamine.level = max(
                dopamine.definition.floor,
                dopamine.level - frustration_magnitude * 0.3,
            )

        # Log at INFO only occasionally (not every breath)
        if blocked_ticks % 10 == 0 and blocked_ticks > 0:
            logger.info(
                "Agent %s: frustration signal (blocked=%d ticks, "
                "magnitude=%.3f, cortisol=%.3f, dopamine=%.3f)",
                runtime.agent_id, blocked_ticks, frustration_magnitude,
                cortisol.level if cortisol else 0.0,
                dopamine.level if dopamine else 0.0,
            )

    # ===================================================================
    # Worker-based generation helpers
    # ===================================================================

    async def _execute_dream_on_worker(self, dream_job: Any) -> None:
        """Execute a dream job via BYO inference (vLLM / OpenAI-compatible)."""
        await self._execute_dream_on_model_a(dream_job)

    async def _execute_dream_on_model_a(self, dream_job: Any) -> None:
        """Execute a dream using vLLM inference."""
        agent_id = self.runtime.agent_id
        prompt = dream_job.prompt if hasattr(dream_job, "prompt") else str(dream_job)
        mode = getattr(dream_job, "mode", "replay")

        try:
            dream_response = await self.runtime.dream_generate_async(prompt)

            # Content-level dedup: skip near-identical dreams
            dmn = getattr(self.runtime, "dmn", None)
            if dmn is not None and dmn.is_duplicate_dream(dream_response):
                logger.info(
                    "Agent %s: dream discarded (duplicate content)",
                    agent_id,
                )
                return

            # Register output + track which facts/domains were used
            dream_facts = getattr(dream_job, "facts", None)
            if dmn is not None:
                dmn.register_dream_output(dream_response, dream_facts)

            # Process dream result outside the lock (no GPU needed)
            loop = asyncio.get_running_loop()
            dream_result = await loop.run_in_executor(
                None,
                self.runtime.process_dream_result,
                dream_response,
                mode,
            )

            self.stats.total_dreams += 1
            mode_label = {
                "replay": "replay",
                "seeded": "exploration/seeded",
                "pure": "exploration/spontaneous",
            }.get(mode, mode)
            logger.info(
                "Agent %s: dream complete via Model A (%s, signals=%d, "
                "facts=%d)",
                agent_id, mode_label,
                dream_result.get("signals_extracted", 0),
                dream_result.get("facts_stored", 0),
            )

            if self.connection_manager is not None:
                await self.connection_manager.broadcast(agent_id, {
                    "type": "daydream",
                    "content": dream_response[:300],
                    "signals": dream_result.get("signals_extracted", 0),
                    "facts_stored": dream_result.get("facts_stored", 0),
                    "mode": mode,
                })

        except Exception as exc:
            logger.warning(
                "Agent %s: dream failed (Model A): %s", agent_id, exc,
            )

    async def _dispatch_reach_out(
        self,
        agent_id: str,
        rt: Any,
        initiative: dict,
    ) -> None:
        """Route a reach-out through the best available channel.

        If external channels are connected, the agent gets a single
        inference call to choose the channel.  If no channels exist,
        falls back to WebSocket-only broadcast (legacy behavior).

        The WebSocket activity feed always receives the event so the
        frontend can display it regardless of delivery channel.
        """
        message = initiative.get("message", "")
        subtype = initiative.get("type", "suggestion")

        channel_registry = getattr(rt, "channel_registry", None)
        connected_channels: list[dict] = []
        if channel_registry is not None:
            try:
                connected_channels = await channel_registry.list_connected()
                connected_channels = [
                    c for c in connected_channels if c.get("connected")
                ]
            except Exception:
                pass

        chosen_channel = "chat"
        chosen_target = ""

        if connected_channels:
            chosen_channel, chosen_target = await self._select_reach_out_channel(
                rt, message, subtype, connected_channels,
            )

        if chosen_channel != "chat" and channel_registry is not None:
            try:
                sent = await channel_registry.send(
                    chosen_channel, chosen_target, message,
                )
                if not sent:
                    chosen_channel = "chat"
            except Exception:
                chosen_channel = "chat"

        if self.connection_manager is not None:
            await self.connection_manager.broadcast(agent_id, {
                "type": "reach_out",
                "subtype": subtype,
                "content": message,
                "channel": chosen_channel,
                "target": chosen_target,
            })

    async def _select_reach_out_channel(
        self,
        rt: Any,
        message: str,
        subtype: str,
        channels: list[dict],
    ) -> tuple[str, str]:
        """Single inference call to pick the best channel for reach-out.

        Returns (channel_name, target_id).  Falls back to "chat" if
        the model response is unparseable.
        """
        ws_connected = (
            self.connection_manager is not None
            and self.connection_manager.is_connected(rt.agent_id)
        ) if self.connection_manager else False

        channel_desc = ["- chat (web UI" + (" — user is ONLINE)" if ws_connected else " — user is OFFLINE)")]
        for ch in channels:
            name = ch.get("name", "")
            info = ch.get("channel", name)
            extra = ""
            if ch.get("bot_username"):
                extra = f", bot=@{ch['bot_username']}"
            if ch.get("phone_number_id"):
                extra = f", phone_id={ch['phone_number_id']}"
            if ch.get("from_address"):
                extra = f", from={ch['from_address']}"
            channel_desc.append(f"- {name} (connected{extra})")

        prompt = (
            f"You want to reach out with this message: \"{message}\"\n"
            f"Message type: {subtype}\n\n"
            f"Available channels:\n"
            + "\n".join(channel_desc) + "\n\n"
            "Pick the best channel. Consider: if the user is online, "
            "'chat' is best. If offline, pick the channel most likely "
            "to reach them. For long-form updates, prefer email.\n\n"
            "Reply with ONLY the channel name (e.g. 'telegram' or 'chat')."
        )

        try:
            _vllm, _adapter = rt.inference_pipeline()
            if _vllm is None:
                return ("chat", "")

            from nls.runtime.inference_compat import prepare_micro_inference

            _micro_msgs, _micro_body = prepare_micro_inference(
                [{"role": "user", "content": prompt}],
                vllm_client=_vllm,
                adapter_name=_adapter,
            )
            response = await _vllm.generate(
                adapter_name=_adapter,
                messages=_micro_msgs,
                max_tokens=20,
                temperature=0.1,
                extra_body=_micro_body,
            )
            _text = response.text if hasattr(response, "text") else str(response or "")
            choice = _text.strip().lower().split()[0] if _text else "chat"
            choice = choice.strip("'\".,")

            valid_names = {"chat"} | {ch.get("name", "") for ch in channels}
            if choice not in valid_names:
                return ("chat", "")

            if choice == "chat":
                return ("chat", "")

            for ch in channels:
                if ch.get("name") == choice:
                    target = ch.get("phone_number_id", "") or ch.get("bot_username", "")
                    return (choice, target)

            return ("chat", "")

        except Exception as exc:
            logger.debug("Channel selection inference failed: %s", exc)
            return ("chat", "")

    async def _execute_drive_on_worker(self, goal: Any) -> None:
        """Execute a drive action via BYO inference."""
        await self._execute_drive_on_model_a(goal)

    async def _execute_drive_on_model_a(self, goal: Any) -> None:
        """Generate drive query via vLLM, then execute the action."""
        agent_id = self.runtime.agent_id
        action_type = getattr(goal, "action_type", "reflect")

        try:
            needs_query = action_type in (
                "web_search", "read_page", "deep_browse",
                "self_test", "reach_out",
            )

            if needs_query and not getattr(goal, "query", ""):
                loop = asyncio.get_running_loop()
                query = await loop.run_in_executor(
                    None,
                    self.runtime.generate_drive_query,
                    goal,
                )
                goal.query = query

            # Execute the action (tool calls, no GPU needed)
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                self.runtime.execute_drive_action,
                goal,
            )

            if result is not None:
                self.stats.total_drive_actions += 1
                logger.info(
                    "Agent %s: drive action complete %s/%s (Model A)",
                    agent_id,
                    getattr(goal, "drive_name", "?"),
                    action_type,
                )

                if self.connection_manager is not None:
                    await self.connection_manager.broadcast(agent_id, {
                        "type": "drive_action",
                        "drive": getattr(goal, "drive_name", ""),
                        "action_type": action_type,
                        "domain": getattr(goal, "domain", ""),
                        "query": getattr(goal, "query", ""),
                        "success": result.get("success", False),
                    })

        except Exception as exc:
            logger.warning(
                "Agent %s: drive action failed (Model A): %s",
                agent_id, exc,
            )

    # ===================================================================
    # Event-driven Dispatch (Phase 2)
    # ===================================================================

    async def _dispatch_channel_event(
        self,
        event: Any,
        depth: Any,
        rt: Any,
    ) -> bool:
        """Handle a CHANNEL_MESSAGE event at any engagement depth.

        Reconstructs the reply path from serializable metadata in the
        event payload (channel_name, reply_target) so the same logic
        works for fresh events and events re-injected from BackgroundQueue.

        Returns True if the event was fully handled, False if the caller
        should persist it (DEFER).
        """
        from nls.engine.events import EngagementDepth

        payload = event.payload or {}
        channel_name = payload.get("channel_name", "")
        reply_target = payload.get("reply_target", "")
        session_key = payload.get("session_key", "")
        user_input = payload.get("user_input", "")
        agent_id = payload.get("agent_id", "") or rt.agent_id
        needs_thinking = payload.get("needs_thinking", True)
        history = payload.get("history") or []

        # Reconstruct reply callable via ChannelRegistry
        registry = getattr(rt, "channel_registry", None)
        _reply = None
        if registry is not None and channel_name and reply_target:
            _reply = registry.reconstruct_reply(channel_name, reply_target, agent_id)
        else:
            async def _reply(text: str) -> None:
                pass

        # ── MICRO ──
        if depth == EngagementDepth.MICRO:
            if user_input:
                try:
                    from nls.engine.micro_inference import micro_respond
                    _tm = getattr(rt, "_team_manager", None)
                    if rt.inference_available():
                        await micro_respond(
                            runtime=rt,
                            user_input=user_input,
                            team_manager=_tm,
                            history=history,
                            reply_channel=_reply,
                        )
                except Exception:
                    logger.warning(
                        "Agent %s: channel micro dispatch failed",
                        agent_id, exc_info=True,
                    )
            return True

        # ── DEFER ──
        if depth == EngagementDepth.DEFER:
            # Cross-surface defer is handled in process_channel_message; do not
            # auto-reply "I'm busy" into the channel (confuses users).
            return False  # caller persists to BackgroundQueue

        # ── DROP ──
        if depth == EngagementDepth.DROP:
            return True

        _slot_mgr = getattr(rt, "_slot_manager", None)
        _deep_locked = (
            _slot_mgr.deep.is_busy if _slot_mgr is not None else rt.is_busy
        )

        # ── FOCUS while deep slot is held: lightweight reply on the channel ──
        if depth == EngagementDepth.FOCUS and _deep_locked:
            if user_input:
                try:
                    from nls.engine.micro_inference import micro_respond
                    _tm = getattr(rt, "_team_manager", None)
                    if rt.inference_available():
                        await micro_respond(
                            runtime=rt,
                            user_input=user_input,
                            team_manager=_tm,
                            history=history,
                            reply_channel=_reply,
                        )
                except Exception:
                    logger.warning(
                        "Agent %s: channel focus/micro dispatch failed",
                        agent_id, exc_info=True,
                    )
            if session_key:
                from nls.runtime.surface_inbox import mark_session_inbox_handled

                mark_session_inbox_handled(agent_id, session_key)
            return True

        # ── FOCUS / DEEP: full agentic processing ──
        if not user_input:
            return True

        # Inject channel context so the agent knows replies are auto-routed
        if channel_name and reply_target:
            user_input = (
                f"[CHANNEL: {channel_name} | reply_to: {reply_target} | "
                f"Your text response will be automatically sent back. "
                f"Do NOT manually call {channel_name}_send.]\n\n"
                + user_input
            )

        from nls.runtime.channels import ChannelProgressReporter

        # Build copilot_queue + progress reporter
        copilot_queue = asyncio.Queue()
        on_event_cb = None

        if registry is not None and channel_name and reply_target:
            adapter = registry.get(channel_name)
            if adapter is not None:
                reporter = ChannelProgressReporter(adapter, reply_target, agent_id)
                on_event_cb = reporter.on_event

        # Wire copilot_queue into TeamManager
        _tm = getattr(rt, "_team_manager", None)
        if _tm is not None:
            _tm._copilot_queue = copilot_queue

        # Preempt daydream/DMN — channel dispatch runs inside the inner loop,
        # so cancel background work without pausing the breath cycle.
        self.preempt_background()
        for _ in range(10):
            if not rt.is_busy:
                break
            await asyncio.sleep(0.3)

        # Register pending queue for ask_user routing
        from nls.skills.channel_processing import _pending_queues
        _queue_key = None
        if session_key:
            _queue_key = (agent_id, session_key)
            _pending_queues[_queue_key] = copilot_queue

        try:
            from nls.runtime import AgentRuntime as _AgentRuntime
            if isinstance(rt, _AgentRuntime):
                result = await rt.process_message_agentic_async(
                    user_input=user_input,
                    history=history,
                    enable_thinking=needs_thinking,
                    copilot_queue=copilot_queue,
                    on_event=on_event_cb,
                    source="user:channel",
                    session_key=session_key or "",
                )
            else:
                result = await rt.process_message_agentic_v2(
                    user_input=user_input,
                    history=history,
                    enable_thinking=needs_thinking,
                    copilot_queue=copilot_queue,
                    on_event=on_event_cb,
                    source="user:channel",
                )

            # Send final response back through channel
            from nls.runtime.response_cleanup import sanitize_channel_outbound

            _raw_final = getattr(result, "final_response", "") or ""
            final_text = sanitize_channel_outbound(_raw_final)
            if final_text:
                try:
                    if registry is not None and channel_name and reply_target:
                        adapter = registry.get(channel_name)
                        if adapter is not None:
                            from nls.skills.channel_attachments import deliver_channel_reply

                            await deliver_channel_reply(
                                adapter, reply_target, final_text, _raw_final,
                                agent_id=agent_id,
                            )
                        else:
                            await _reply(final_text)
                    else:
                        await _reply(final_text)
                except Exception:
                    logger.debug("Channel final reply failed", exc_info=True)
                if session_key:
                    from nls.runtime.surface_inbox import mark_session_inbox_handled

                    mark_session_inbox_handled(agent_id, session_key)
            elif _raw_final.strip():
                logger.warning(
                    "Agent %s: blocked tool-call leak on channel final reply (%r)",
                    agent_id,
                    _raw_final[:120],
                )

        except Exception:
            logger.warning(
                "Agent %s: channel FOCUS/DEEP dispatch failed",
                agent_id, exc_info=True,
            )
        finally:
            if _queue_key:
                _pending_queues.pop(_queue_key, None)

        return True

    async def _dispatch_from_event_queue(self, rt: Any) -> bool:
        """Drain the event queue and dispatch based on thalamic routing.

        Returns True if at least one event was dispatched (ran an agentic
        loop or micro-inference).  Events that are DEFER'd go back into
        a holding list; DROP'd events are discarded.
        """
        from nls.engine.events import EngagementDepth, EventType
        from nls.engine.thalamic_router import ThalamicRouter

        _tm = getattr(rt, "_team_manager", None)
        _router = ThalamicRouter(team_manager=_tm, runtime=rt)

        events = self.event_queue.drain(max_events=8)
        if not events:
            return False

        # Deduplicate: skip events already processed in a prior dispatch
        _fresh: list = []
        for ev in events:
            if ev.event_id in self._processed_event_ids:
                logger.info(
                    "Agent %s: skipping already-processed event %s "
                    "(source=%s, type=%s)",
                    rt.agent_id, ev.event_id, ev.source, ev.type.value,
                )
                continue
            _fresh.append(ev)
        events = _fresh
        if not events:
            return False

        dispatched = False
        _deferred: list = []
        _slot_mgr = getattr(rt, "_slot_manager", None)
        _deep_busy = (
            _slot_mgr.deep.is_busy if _slot_mgr is not None else rt.is_busy
        )

        for event in events:
            depth = _router.route(
                event,
                deep_slot_busy=_deep_busy,
                focus_slot_busy=getattr(self, "_autonomous_executing", False),
            )

            logger.info(
                "Agent %s: event-queue dispatch %s (source=%s) → %s",
                rt.agent_id, event.type.value, event.source, depth.value,
            )

            if depth == EngagementDepth.DROP:
                continue

            # Channel events get dedicated dispatch with reply reconstruction
            if event.type == EventType.CHANNEL_MESSAGE:
                handled = await self._dispatch_channel_event(event, depth, rt)
                if handled:
                    dispatched = True
                    self._processed_event_ids.add(event.event_id)
                else:
                    _deferred.append(event)
                if depth in (EngagementDepth.FOCUS, EngagementDepth.DEEP):
                    break  # one full dispatch per breath cycle
                continue

            if depth == EngagementDepth.DEFER:
                _deferred.append(event)
                continue

            if depth == EngagementDepth.MICRO:
                _text = event.payload.get("user_input", "")
                if _text and event.reply_channel is not None:
                    try:
                        from nls.engine.micro_inference import micro_respond
                        _tm = getattr(rt, "_team_manager", None)
                        if rt.inference_available():
                            await micro_respond(
                                runtime=rt,
                                user_input=_text,
                                team_manager=_tm,
                                reply_channel=event.reply_channel,
                            )
                            dispatched = True
                    except Exception:
                        logger.warning(
                            "Agent %s: micro dispatch failed for %s",
                            rt.agent_id, event.type.value, exc_info=True,
                        )
                continue  # micro doesn't block; keep draining

            if depth in (EngagementDepth.FOCUS, EngagementDepth.DEEP):
                prompt = event.payload.get("prompt", "")
                if not prompt:
                    prompt = event.payload.get("user_input", "")
                if not prompt:
                    continue

                source = event.source
                from nls.agentic.wake_coordination import (
                    should_skip_stale_orchestration_wake,
                )
                _tm = getattr(rt, "_team_manager", None)
                if _tm is not None and should_skip_stale_orchestration_wake(
                    _tm,
                    source,
                    context=f"event:{source}",
                ):
                    self._processed_event_ids.add(event.event_id)
                    continue
                try:
                    await self._dispatch_autonomous_v2(
                        rt, prompt, source=source,
                    )
                    dispatched = True
                except Exception:
                    logger.warning(
                        "Agent %s: event dispatch failed for %s",
                        rt.agent_id, event.type.value, exc_info=True,
                    )
                self._processed_event_ids.add(event.event_id)
                if len(self._processed_event_ids) > 500:
                    _oldest = sorted(self._processed_event_ids)[:250]
                    self._processed_event_ids -= set(_oldest)
                break  # one full dispatch per breath cycle

        # Persist deferred events to BackgroundQueue (disk-backed)
        _slot_mgr = getattr(rt, "_slot_manager", None)
        for ev in _deferred:
            ev.defer_count += 1
            if ev.is_expired:
                logger.warning(
                    "Agent %s: dropping event %s after %d defers",
                    rt.agent_id, ev.event_id, ev.defer_count,
                )
                continue
            if _slot_mgr is not None and hasattr(_slot_mgr, "background"):
                _slot_mgr.background.push(ev.to_dict())
                logger.debug(
                    "Agent %s: DEFER'd event %s → BackgroundQueue (defer #%d)",
                    rt.agent_id, ev.event_id, ev.defer_count,
                )
            else:
                self.event_queue.push(ev)

        # Re-inject from BackgroundQueue if deep slot is free
        if _slot_mgr is not None and hasattr(_slot_mgr, "background"):
            if not _slot_mgr.deep_slot_busy and _slot_mgr.background.depth > 0:
                stored = _slot_mgr.background.pop()
                if stored is not None:
                    try:
                        from nls.engine.events import AgentEvent as _AE
                        rehydrated = _AE.from_dict(stored)
                        self.event_queue.push(rehydrated)
                        logger.info(
                            "Agent %s: re-injected deferred event %s from BackgroundQueue",
                            rt.agent_id, rehydrated.event_id,
                        )
                    except Exception:
                        logger.debug(
                            "Agent %s: failed to re-inject deferred event",
                            rt.agent_id, exc_info=True,
                        )

        return dispatched

    # ===================================================================
    # Autonomous v2 Dispatch (drives + WM goals)
    # ===================================================================

    def _can_dispatch_v2(self, rt: Any) -> bool:
        """Whether autonomous agentic dispatch is allowed (``agency.agentic_loop.use_v2``).

        The actual loop implementation is :meth:`AgentRuntime.process_message_agentic_async`
        — same entry point as chat WebSocket — which reads
        ``agency.agentic_loop_version`` (default ``v5``).
        """
        if getattr(self, "_autonomous_executing", False):
            return False
        if getattr(rt, "is_user_busy", False):
            return False
        use_v2 = rt.config.get("agency", {}).get(
            "agentic_loop", {},
        ).get("use_v2", False)
        if not use_v2 or not rt.is_agentic_enabled():
            return False
        if not (
            self._use_model_a
            or (
                hasattr(rt, "inference_available")
                and rt.inference_available()
            )
        ):
            return False
        return True

    def _build_mission_context(self, rt: Any, source: str) -> str:
        """Build a structured preamble for autonomous dispatches (§1.1).

        Loads the last few user messages, WM summary, and active team
        state so the orchestrator knows *what it's working toward*.
        """
        parts: list[str] = []
        parts.append("[SYSTEM — Mission Context]")

        # Recent user directives from conversation history
        # Filter out pure naming/onboarding messages (e.g. "Your name is Babo!")
        # — they are not task directives and confuse the model into re-introducing
        # itself when the Mission Context is injected in later loops.
        _NAMING_RE = re.compile(
            r"^\s*(your\s+name\s+is|call\s+(me|yourself|you)|name\s+me|"
            r"you\s+are\s+called|i('m| am) naming you)\s+\w+[!.]?\s*$",
            re.IGNORECASE,
        )
        try:
            _conv_load = getattr(rt, "load_conversation_history", None)
            if _conv_load:
                recent = _conv_load(max_turns=6)
                user_msgs = [
                    m for m in recent
                    if m.get("role") == "user"
                    and isinstance(m.get("content"), str)
                    and not m.get("metadata", {}).get("autonomous")
                    and not _NAMING_RE.match(m.get("content", ""))
                    and not any(
                        m.get("content", "").startswith(p)
                        for p in InnerLoop._STALL_NUDGE_PREFIXES
                    )
                ][-3:]
                if user_msgs:
                    parts.append("Your current user directives (most recent conversation):")
                    for um in user_msgs:
                        parts.append(f'  "{um["content"][:300]}"')
        except Exception:
            pass

        # Working memory summary
        wm = getattr(rt, "working_memory", None)
        if wm is not None:
            try:
                wm_ctx = wm.to_context_string()
                if wm_ctx and len(wm_ctx) > 20:
                    parts.append("\nWorking Memory Summary:")
                    parts.append(wm_ctx[:1500])
            except Exception:
                pass

        # Project directory from active plan — tells autonomous loops
        # where ALL file operations should go.
        _project_dir = ""
        _tools = getattr(rt, "_agent_tools", None) or []
        for _tool in _tools:
            _ps = getattr(_tool, "_store", None)
            if _ps is not None and hasattr(_ps, "find_active"):
                try:
                    _ap = _ps.find_active()
                    if _ap and getattr(_ap, "project_dir", ""):
                        _project_dir = _ap.project_dir
                except Exception:
                    pass
                break
        if not _project_dir:
            for _tool in _tools:
                _ps = getattr(_tool, "_store", None)
                if _ps is not None and hasattr(_ps, "find_any_project_dir"):
                    try:
                        _project_dir = _ps.find_any_project_dir() or ""
                    except Exception:
                        pass
                    break

        if _project_dir:
            parts.append(
                f"\n[PROJECT DIRECTORY — CRITICAL]\n"
                f"All file writes MUST go inside {_project_dir}/. "
                f"Use paths like {_project_dir}/backend/... or {_project_dir}/frontend/...\n"
                f"Do NOT create files at the workspace root."
            )

        # Live plan progress (avoid stale WM step counts)
        for _tool in _tools:
            _ps = getattr(_tool, "_store", None)
            if _ps is not None and hasattr(_ps, "find_active"):
                try:
                    _ap = _ps.find_active()
                    if _ap is not None:
                        _done = sum(
                            1 for s in _ap.steps
                            if s.status in ("done", "skipped")
                        )
                        _total = len(_ap.steps)
                        parts.append(
                            f"\n[PLAN POSITION — {_done}/{_total} steps done]"
                        )
                        parts.append(
                            f"Active plan: {_ap.title} [{_ap.id}] "
                            f"(status={_ap.status})"
                        )
                except Exception:
                    pass
                break

        # Active teams summary
        _has_teams = False
        _tm = getattr(rt, "_team_manager", None)
        if _tm is not None:
            try:
                team_summary = _tm.get_active_summary()
                if team_summary:
                    parts.append(f"\n{team_summary}")
                    _has_teams = True
                _active_teams = [
                    t for t in _tm._teams.values()
                    if t.status == "active"
                ]
                if _active_teams:
                    _focus = max(_active_teams, key=lambda t: t.wave_index)
                    parts.append(
                        f"\n[ORCHESTRATION FOCUS] Primary active team: "
                        f"{_focus.id} ({_focus.name}). "
                        f"Use team(action='inspect', team_id='{_focus.id}') "
                        f"then team(action='advance') when all members are done. "
                        f"Do NOT advance terminal or older wave teams."
                    )
                _created = [
                    t for t in _tm._teams.values()
                    if t.status == "created"
                ]
                if _created:
                    _c = _created[0]
                    parts.append(
                        f"\n[PENDING LAUNCH] Team {_c.id} ({_c.name}) is created "
                        f"but not launched — call team(action='launch', "
                        f"team_id='{_c.id}') when no other wave is running."
                    )
            except Exception:
                pass

        # Active delegates (only if no team summary was added)
        if not _has_teams:
            _dm = getattr(rt, "delegate_manager", None)
            if _dm is not None:
                try:
                    all_status = _dm.list_all()
                    running = [s for s in all_status if s.state == "running"]
                    if running:
                        parts.append("\nActive Delegates:")
                        for ds in running[:5]:
                            parts.append(
                                f"  #{ds.delegate_number}: {ds.task[:80]} "
                                f"(iter {ds.iteration}, {ds.state})"
                            )
                except Exception:
                    pass

        parts.append("[END Mission Context]")
        if len(parts) <= 2:
            return ""
        return "\n".join(parts)

    async def _dispatch_autonomous_v2(
        self,
        rt: Any,
        prompt: str,
        source: str = "system",
    ) -> str:
        """Run :meth:`AgentRuntime.process_message_agentic_async` autonomously (same as chat).

        Loop version (v5 / v4 / v3 / v2) comes from ``agency.agentic_loop_version``
        on the runtime config, not from this method name.  Handles abort-on-interrupt,
        event streaming, and cleanup.  Returns the final response text (empty on
        failure/abort).
        """
        agent_id = rt.agent_id
        from nls.agentic.wake_coordination import (
            should_skip_stale_orchestration_wake,
        )
        _tm = getattr(rt, "_team_manager", None)
        if _tm is not None and should_skip_stale_orchestration_wake(
            _tm,
            source,
            context=f"run:{source}",
        ):
            return ""
        if self._pending_dispatches and any(
            (src or "").startswith("job_background:")
            or (src or "").startswith("squad_member_checkback:")
            for _, src in self._pending_dispatches
        ):
            return ""
        self._autonomous_executing = True
        _cm = self.connection_manager
        _t0 = time.time()

        _FORWARDED = frozenset((
            "activity_status", "tool_execution_start",
            "tool_execution_end", "agentic_plan", "plan_step_update",
            "turn_thinking",
        ))

        _communicated_texts: set[str] = set()

        async def _on_autonomous_event(event):
            """Forward key agentic-loop events to connected frontends."""
            if _cm is None:
                return
            try:
                data = event.to_dict() if hasattr(event, "to_dict") else {}
                etype = data.get("type", "")

                if etype == "turn_end":
                    live_hormones = {}
                    if rt.hypothalamus is not None:
                        live_hormones = {
                            n: round(h.level, 3)
                            for n, h in rt.hypothalamus.hormones.items()
                        }
                    wm_snap = None
                    if rt.working_memory is not None:
                        try:
                            wm_snap = rt.working_memory.get_summary()
                        except Exception:
                            pass
                    await _cm.broadcast(agent_id, {
                        "type": "agentic_iteration",
                        "step": data.get("iteration", 0),
                        "max_steps": data.get("max_iterations", 25),
                        "tool_calls": data.get("tool_calls", []),
                        "tool_results": data.get("tool_results", []),
                        "duration_ms": round(data.get("duration_ms", 0), 1),
                        "hormones": live_hormones,
                        "working_memory": wm_snap,
                        "autonomous": True,
                    })
                    _resp_text = data.get("response_text", "").strip()
                    if _resp_text:
                        from nls.runtime.dispatch_sources import (
                            is_orchestration_dispatch_source,
                        )
                        if not is_orchestration_dispatch_source(source):
                            _sig = _resp_text[:200]
                            if _sig not in _communicated_texts:
                                _communicated_texts.add(_sig)
                                await _cm.broadcast(agent_id, {
                                    "type": "communicate",
                                    "message": _resp_text,
                                    "iteration": data.get("iteration", 0),
                                    "autonomous": True,
                                    "mid_loop": True,
                                    "source": source,
                                })
                elif etype == "communicate":
                    _comm_msg = data.get("message", "").strip()
                    _comm_sig = _comm_msg[:200]
                    if _comm_sig and _comm_sig not in _communicated_texts:
                        _communicated_texts.add(_comm_sig)
                        await _cm.broadcast(agent_id, {
                            "type": "communicate",
                            "message": _comm_msg,
                            "iteration": data.get("iteration", 0),
                            "autonomous": True,
                            "user_facing": True,
                            "source": source,
                        })
                elif etype == "ask_user":
                    await _cm.broadcast(agent_id, {
                        "type": "ask_user",
                        "question": data.get("question", ""),
                        "request_id": (
                            data.get("request_id")
                            or data.get("tool_call_id")
                            or ""
                        ),
                        "iteration": data.get("iteration", 0),
                        "autonomous": True,
                        "source": source,
                    })
                elif etype == "user_answer":
                    await _cm.broadcast(agent_id, {
                        "type": "user_answer",
                        "content": data.get("answer", ""),
                        "autonomous": True,
                        "source": source,
                    })
                elif etype in _FORWARDED:
                    await _cm.broadcast(agent_id, {
                        **data, "autonomous": True,
                    })
            except Exception:
                pass

        _bg_slot_id = f"_bg_activity_{id(self)}_{int(_t0)}"
        _wm = getattr(rt, "working_memory", None)

        try:
            # Write background activity to Cryptex so foreground turns
            # can see what the agent is working on concurrently.
            if _wm is not None:
                try:
                    from nls.brain.working_memory import WMSlot
                    _wm.add(WMSlot(
                        slot_type="perception",
                        domain="background_activity",
                        content=(
                            f"Background task in progress: [{source}] "
                            f"{prompt[:200]}"
                        ),
                        salience=0.9,
                        source="system",
                        metadata={"bg_slot_id": _bg_slot_id},
                    ))
                except Exception:
                    logger.debug(
                        "Agent %s: failed to write background activity slot",
                        agent_id, exc_info=True,
                    )

            if _cm is not None:
                await _cm.broadcast(agent_id, {
                    "type": "agentic_start",
                    "max_steps": 25,
                    "autonomous": True,
                    "source": source,
                    "task_preview": prompt[:200],
                })

            abort = asyncio.Event()

            # Expose the abort event so ws_handler can interrupt immediately
            # when a foreground user message arrives.
            self._autonomous_abort = abort

            async def _watch_interrupt():
                while not abort.is_set():
                    if self._interrupted or self._paused:
                        abort.set()
                        break
                    # Abort as soon as a foreground user turn starts so we
                    # don't block vLLM for minutes while the user is waiting.
                    if getattr(rt, "is_user_busy", getattr(rt, "is_busy", False)):
                        logger.info(
                            "Agent %s: background dispatch aborted — "
                            "foreground user turn started",
                            agent_id,
                        )
                        abort.set()
                        break
                    await asyncio.sleep(0.5)

            watch_task = asyncio.create_task(_watch_interrupt())

            _auto_history = None
            _load_fn = getattr(rt, "load_autonomous_history", None)
            if _load_fn:
                try:
                    _auto_history = _load_fn(max_turns=10)
                except Exception:
                    _auto_history = None

            # §1.1 — Inject mission context so autonomous dispatches know
            # what the user asked for and where the project stands.
            _original_prompt = prompt
            _mission_preamble = self._build_mission_context(rt, source)
            if _mission_preamble:
                prompt = _mission_preamble + "\n\n" + prompt

            # Wire a copilot_queue so ask_user / wait can receive user input.
            # Mirror ws_handler: create a queue, set it on TeamManager, register
            # for WS user_answer routing, and pass it to the loop.
            from nls.skills.channel_processing import (
                register_autonomous_copilot_queue,
                unregister_autonomous_copilot_queue,
            )

            _auto_copilot_queue = asyncio.Queue()
            register_autonomous_copilot_queue(agent_id, _auto_copilot_queue)
            _tm = getattr(rt, "_team_manager", None)
            if _tm is not None:
                _tm._copilot_queue = _auto_copilot_queue

            try:
                result = await rt.process_message_agentic_async(
                    user_input=prompt,
                    history=_auto_history,
                    enable_thinking=True,
                    abort_signal=abort,
                    on_event=_on_autonomous_event,
                    source=source,
                    copilot_queue=_auto_copilot_queue,
                )
            finally:
                unregister_autonomous_copilot_queue(agent_id)
                abort.set()
                watch_task.cancel()
                try:
                    await watch_task
                except asyncio.CancelledError:
                    pass

            final = getattr(result, "final_response", "") or ""
            _dur = round((time.time() - _t0) * 1000, 1)
            _iters = getattr(result, "iterations", 0)
            _tc = getattr(result, "total_tool_calls", 0)
            _aborted = getattr(result, "aborted", False)

            if _aborted:
                self._last_agentic_abort_ts = time.time()

            logger.info(
                "Agent %s: autonomous dispatch completed (%s, %d chars, %d iters)",
                agent_id, source, len(final), _iters,
            )

            _noop_abort = (
                _aborted and _iters <= 2 and _tc == 0
                and (
                    len(final.strip()) < 20
                    or source.startswith("scheduler")
                )
            )
            if _cm is not None and not _noop_abort:
                _abort_reason = getattr(result, "abort_reason", "") or ""
                if _aborted and not _abort_reason:
                    from nls.runtime.dispatch_sources import (
                        is_orchestration_dispatch_source,
                    )
                    if is_orchestration_dispatch_source(source):
                        _abort_reason = "orchestration_preempted"
                    else:
                        _abort_reason = "user_abort"
                await _cm.broadcast(agent_id, {
                    "type": "agentic_complete",
                    "autonomous": True,
                    "source": source,
                    "total_steps": _iters,
                    "total_tool_calls": _tc,
                    "aborted": _aborted,
                    "abort_reason": _abort_reason,
                    "exit_reason": getattr(result, "exit_reason", "") or "",
                    "duration_ms": _dur,
                    "final_response": final[:500] if final else "",
                })

            # Persist autonomous task result in AUTONOMOUS history
            # (separate from user conversation to prevent context pollution)
            try:
                _save_fn = getattr(rt, "save_autonomous_history", None)
                _load_fn = getattr(rt, "load_autonomous_history", None)
                if _save_fn and _load_fn:
                    _history = _load_fn(max_turns=20)
                    _history.append({
                        "role": "user",
                        "content": f"[Autonomous task — {source}] {_original_prompt[:300]}",
                        "metadata": {"autonomous": True, "source": source},
                    })
                    _assistant_content = final or (
                        f"[Task {'aborted' if _aborted else 'completed'} "
                        f"after {_iters} steps with no output]"
                    )
                    _history.append({
                        "role": "assistant",
                        "content": _assistant_content,
                        "metadata": {
                            "agentic": True,
                            "autonomous": True,
                            "source": source,
                            "iterations": _iters,
                            "tool_calls": _tc,
                            "aborted": _aborted,
                            "abort_reason": getattr(result, "abort_reason", ""),
                        },
                    })
                    _save_fn(_history, max_turns=20)
            except Exception as _he:
                logger.debug("Failed to save autonomous history: %s", _he)

            if source.startswith("job_background:") or source.startswith(
                "squad_member_checkback:",
            ):
                try:
                    from nls.runtime.job_background import record_background_wake

                    record_background_wake(pathlib.Path(rt.agent_dir))
                except Exception:
                    pass

            return final

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Agent %s: autonomous dispatch failed (%s): %s",
                agent_id, source, exc, exc_info=True,
            )
            if _cm is not None:
                try:
                    await _cm.broadcast(agent_id, {
                        "type": "agentic_complete",
                        "autonomous": True,
                        "source": source,
                        "total_steps": 0,
                        "total_tool_calls": 0,
                        "aborted": True,
                        "abort_reason": str(exc)[:200],
                        "duration_ms": round((time.time() - _t0) * 1000, 1),
                    })
                except Exception:
                    pass
            return ""
        finally:
            self._autonomous_executing = False
            self._autonomous_abort = None
            # Clear the background activity slot from Cryptex
            if _wm is not None:
                try:
                    _wm.remove_by_metadata("bg_slot_id", _bg_slot_id)
                except Exception:
                    pass

    # ===================================================================
    # Drive Goal → v2 Dispatch
    # ===================================================================

    @staticmethod
    def _drive_goal_to_prompt(goal: Any, rt: Any) -> str:
        """Convert a DriveGoal into a natural-language v2 prompt."""
        action = getattr(goal, "action_type", "reflect")
        domain = getattr(goal, "domain", "")
        human_domain = domain.replace(".", " ").replace(
            "User ", "their ",
        ).replace("Agent ", "my ").strip()

        # Gather existing knowledge so the agent doesn't repeat itself
        context = ""
        domain_db = getattr(rt, "domain_db", None)
        if domain_db is not None:
            fact = domain_db.get_fact(domain)
            if fact and fact.current_value:
                val = fact.current_value
                if "\n[context:" in val:
                    val = val.split("\n[context:")[0].strip()
                context = f' I already know: "{val[:200]}".'

        if action == "web_search":
            return (
                f"I'm curious about {human_domain}.{context} "
                f"Research this topic: search the web, read the most "
                f"relevant pages, and write a concise summary of what "
                f"I learned. Save any important findings."
            )
        elif action == "deep_browse":
            return (
                f"I want to deeply explore {human_domain}.{context} "
                f"Browse the web, follow interesting links, read "
                f"multiple sources, and compile a thorough analysis."
            )
        elif action == "disconfirm":
            return (
                f"I want to challenge my assumption about "
                f"{human_domain}.{context} Search for contradicting "
                f"evidence or alternative viewpoints and evaluate "
                f"whether my current understanding holds up."
            )
        elif action == "self_test":
            return (
                f"I want to test my knowledge of {human_domain}."
                f"{context} Think of a challenging question about this "
                f"topic, try to answer it, then verify my answer "
                f"with a web search."
            )
        elif action == "reach_out":
            return (
                f"I've been thinking about {human_domain} and want "
                f"to share something interesting with the user."
                f"{context} Draft a short, warm message about this."
            )
        else:
            return (
                f"Reflect on {human_domain}.{context} "
                f"Think about what I know, what gaps remain, and what "
                f"would be most valuable to explore next."
            )

    _AUTONOMOUS_DRIVE_NAMES = frozenset({
        "homeostasis", "curiosity", "competence", "social",
        "self_direction", "disconfirmation",
        # legacy / alias names still seen in older state
        "epistemic", "wonder", "dmn",
    })

    _STALL_NUDGE_PREFIXES = (
        "You appear to be stuck",
        "[Loop stopped: stalled",
    )

    _POST_STALL_DRIVE_SUPPRESS_S = 1800.0

    def _should_skip_autonomous_drive(
        self,
        rt: Any,
        drive_name: str,
        action_type: str,
    ) -> bool:
        """Pause background drives while the user has an active task."""
        if drive_name not in self._AUTONOMOUS_DRIVE_NAMES:
            return False
        if action_type in ("notify", "deliver"):
            return False
        _stall_ts = max(
            float(getattr(rt, "_last_agentic_stall_ts", 0.0) or 0.0),
            float(self._last_agentic_stall_ts or 0.0),
        )
        if _stall_ts and (time.time() - _stall_ts) < self._POST_STALL_DRIVE_SUPPRESS_S:
            return True
        _user_busy = getattr(rt, "is_user_busy", getattr(rt, "is_busy", False))
        if _user_busy:
            return True
        _wm = getattr(rt, "dual_wm", None) or getattr(rt, "working_memory", None)
        if _wm is None:
            return False
        try:
            return any(
                getattr(g, "level", "") == "tactical"
                and getattr(g, "source", "") in (
                    "task_extract", "todo-list", "user",
                )
                for g in _wm.get_goals()
            )
        except Exception:
            return False

    async def _dispatch_drive_goal(self, rt: Any, goal: Any) -> bool:
        """Dispatch a drive goal through the v2 agentic loop.

        Returns True if dispatched via v2, False if caller should
        fall back to legacy v1 single-tool dispatch.
        """
        if not self._can_dispatch_v2(rt):
            return False

        drive_name = getattr(goal, "drive_name", "unknown")
        action_type = getattr(goal, "action_type", "reflect")
        if self._should_skip_autonomous_drive(rt, drive_name, action_type):
            logger.info(
                "Agent %s: skip drive %s/%s — user task active",
                rt.agent_id, drive_name, action_type,
            )
            return True

        prompt = self._drive_goal_to_prompt(goal, rt)

        logger.info(
            "Agent %s: drive %s/%s → agentic dispatch",
            rt.agent_id, drive_name, action_type,
        )

        final = await self._dispatch_autonomous_v2(
            rt, prompt, source=f"drive:{drive_name}",
        )

        success = bool(final)
        if success:
            self.stats.total_drive_actions += 1

        domain = getattr(goal, "domain", "")
        drive_engine = getattr(rt, "drive_engine", None)
        if drive_engine is not None and domain:
            drive_engine.experience.record_outcome(domain, success)
            is_search = action_type in (
                "web_search", "read_page", "deep_browse", "disconfirm",
            )
            if is_search:
                drive_engine.experience.mark_searched(domain)
            try:
                drive_engine.save_state(rt.agent_dir)
            except Exception:
                pass

        return True

    # ===================================================================
    # Job charter background → v2 dispatch
    # ===================================================================

    async def _maybe_dispatch_job_background(
        self,
        rt: Any,
        *,
        has_pending_todos: bool = False,
        plan_work_open: bool = False,
        team_active: bool = False,
    ) -> bool:
        """Wake agent from Job charter when background_enabled and idle."""
        if getattr(rt, "is_user_busy", getattr(rt, "is_busy", False)):
            return False
        if not self._can_dispatch_v2(rt):
            return False

        # Squad members: scheduler owns job-background wakes.
        try:
            from server.main import app

            sm = getattr(app.state, "squad_manager", None)
            if sm is not None:
                squad = sm.get_squad_for_agent(rt.agent_id)
                if (
                    squad is not None
                    and not squad.is_lead(rt.agent_id)
                    and getattr(squad, "member_checkback_enabled", True)
                ):
                    return False
                if (
                    squad is not None
                    and squad.is_lead(rt.agent_id)
                    and getattr(squad, "checkback_enabled", True)
                ):
                    from nls.runtime.job_background import MIN_BACKGROUND_INTERVAL_SECONDS

                    last_sq = float(getattr(squad, "last_checkback_at", 0) or 0)
                    if last_sq > 0 and (time.time() - last_sq) < MIN_BACKGROUND_INTERVAL_SECONDS:
                        return False
        except Exception:
            pass

        if self._pending_dispatches and any(
            (src or "").startswith("job_background:")
            or (src or "").startswith("squad_member_checkback:")
            for _, src in self._pending_dispatches
        ):
            return False

        from nls.runtime.job_background import (
            background_wake_due,
            build_job_background_wake_prompt,
            job_allows_background_work,
            job_background_blocked,
        )
        from nls.runtime.job_trust import load_job

        if job_background_blocked(
            has_pending_todos=has_pending_todos,
            plan_work_open=plan_work_open,
            team_active=team_active,
            user_busy=False,
        ):
            return False

        agent_dir = pathlib.Path(getattr(rt, "agent_dir", "") or "")
        if not agent_dir.is_dir():
            return False
        job = load_job(agent_dir)
        if not job_allows_background_work(job, agent_dir):
            return False
        if not background_wake_due(job):
            return False

        prompt = build_job_background_wake_prompt(job)
        logger.info(
            "Agent %s: Job background dispatch (title=%s)",
            rt.agent_id, job.display_title,
        )
        try:
            await self._dispatch_autonomous_v2(
                rt, prompt, source=f"job_background:{rt.agent_id}",
            )
        except Exception:
            logger.warning(
                "Agent %s: job background dispatch failed",
                rt.agent_id, exc_info=True,
            )
            return False
        return True

    # ===================================================================
    # WM Tactical Goal → v2 Dispatch
    # ===================================================================

    _TODO_ID_RE = re.compile(r"todo\s*\[([a-f0-9]+)\]", re.IGNORECASE)

    _DISPATCHABLE_SOURCES = frozenset(("todo-list", "user", "system"))

    async def _maybe_dispatch_wm_goal(self, rt: Any) -> bool:
        """Check WM for tactical goals and dispatch via agentic v2.

        Only dispatches top-level goals (from todo-list, user, or
        system).  Plan sub-steps (source="plan") are NEVER dispatched
        independently — they must execute within their parent agentic
        run.  Dispatching them separately would lose conversation
        context and lead to destructive overwrites.
        """
        if getattr(rt, "is_user_busy", getattr(rt, "is_busy", False)):
            return False

        wm = getattr(rt, "working_memory", None)
        if wm is None:
            return False

        goals = wm.get_goals()
        if not goals:
            return False

        # Only dispatch top-level goals, never plan sub-steps
        tactical = [
            g for g in goals
            if g.level == "tactical"
            and getattr(g, "source", "") in self._DISPATCHABLE_SOURCES
        ]
        if not tactical:
            return False

        target = max(tactical, key=lambda g: g.salience)

        if not self._can_dispatch_v2(rt):
            return False

        logger.info(
            "Agent %s: WM tactical goal → agentic dispatch: %s",
            rt.agent_id, target.content[:120],
        )

        # Extract linked todo ID (pattern: "todo [<hex>]")
        todo_id = None
        _m = self._TODO_ID_RE.search(target.content)
        if _m:
            todo_id = _m.group(1)

        # Move linked todo to in_progress before executing
        if todo_id:
            await self._update_todo_status(
                rt, todo_id, "in_progress",
            )

        # Enrich prompt with plan context if available
        dispatch_prompt = target.content
        try:
            _tools = getattr(rt, "_agent_tools", None) or []
            for _t in _tools:
                if getattr(_t, "name", "") == "plan" and hasattr(_t, "get_store"):
                    _store = _t.get_store()
                    _active_plan = None

                    # Try matching by todo_id first
                    if todo_id:
                        _active_plan = _store.find_by_todo(todo_id)
                    if _active_plan is None:
                        _active_plan = _store.find_active()

                    if _active_plan:
                        dispatch_prompt = (
                            f"{target.content}\n\n"
                            f"You have an existing plan for this task. "
                            f"Start by calling plan(action='read') to "
                            f"load your plan, then continue from the "
                            f"next pending step.\n\n"
                            f"{_active_plan.to_context_string()}"
                        )
                    break
        except Exception as _pe:
            logger.debug("Plan enrichment skipped: %s", _pe)

        final = await self._dispatch_autonomous_v2(
            rt, dispatch_prompt, source=target.source,
        )

        # Clean up WM: remove the dispatched goal that triggered this
        # run.  Plan-sourced goals are handled by on_agent_end (success
        # → removed, failure → kept and marked as FAILED for learning).
        _content = target.content
        wm.remove_goals_where(lambda g: g.content == _content)
        wm.remove_intentions_where(lambda p: p.content == _content)

        wm_path = getattr(rt, "agent_dir", None)
        if wm_path is not None:
            try:
                dual = getattr(rt, "dual_wm", None)
                if dual is not None:
                    dual.save(wm_path)
                else:
                    wm.save(wm_path / "working_memory_state.json")
            except Exception:
                pass

        if final and todo_id:
            await self._update_todo_status(rt, todo_id, "done")

        return True

    async def _update_todo_status(
        self,
        rt: Any,
        todo_id: str,
        status: str,
    ) -> None:
        """Update a todo item's status and broadcast the change."""
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

            store = _todo_mgr.get_store(rt.agent_id)
            item = store.update(todo_id, status=status)
            if item is None:
                return

            if status in ("in_progress", "done"):
                _todo_mgr.sync_idle_intention(rt.agent_id)

            await _todo_mgr.broadcast(rt.agent_id, "updated", item)

            logger.info(
                "Agent %s: todo [%s] → %s",
                rt.agent_id, todo_id, status,
            )
        except Exception as exc:
            logger.debug(
                "Agent %s: todo status update failed: %s",
                rt.agent_id, exc,
            )

    # ===================================================================
    # Active Dreaming (tool-using, foraging)
    # ===================================================================

    async def _start_active_dream(self, dmn: Any) -> None:
        """Dispatch an active dream as a background task.

        Active dreams run as a separate asyncio task so the heartbeat
        continues.  If a user message arrives, interrupt() cancels the
        task -- the user always preempts.

        Three-phase cycle:
          1. WONDER (adapter ON)  -- generate research intention
          2. ACT    (adapter OFF) -- execute tools (read-only, safe)
          3. REFLECT (adapter ON) -- extract LEARN signals, score
        """
        agent_id = self.runtime.agent_id
        rt = self.runtime

        # Build the active dream plan from DMN
        # Use autonomous history (not user conversation) to avoid
        # polluting DMN context with user task details.
        # The WM already provides consolidated user context.
        conversation_history = None
        _load_auto = getattr(rt, "load_autonomous_history", None)
        if _load_auto:
            try:
                conversation_history = _load_auto(max_turns=5)
            except Exception:
                pass

        recent_files = getattr(rt, "_recent_files", [])
        recent_errors = getattr(rt, "_recent_errors", [])

        dream_plan = dmn.build_active_dream(
            conversation_history=conversation_history,
            recent_files=recent_files,
            recent_errors=recent_errors,
            self_state=getattr(rt, "self_state", None),
            theory_of_mind=getattr(rt, "theory_of_mind", None),
            working_memory=getattr(rt, "working_memory", None),
            predictive=getattr(rt, "predictive", None),
        )
        if dream_plan is None:
            logger.debug(
                "Agent %s: active dream build returned None", agent_id,
            )
            return

        wonder_prompt, dream_type, type_config = dream_plan

        logger.info(
            "Agent %s: starting active dream (type=%s)",
            agent_id, dream_type,
        )

        # Launch as background task
        self._active_dream_task = asyncio.create_task(
            self._execute_active_dream(
                wonder_prompt, dream_type, type_config, dmn,
            ),
        )

    async def _execute_active_dream(
        self,
        wonder_prompt: str,
        dream_type: str,
        type_config: dict,
        dmn: Any,
    ) -> None:
        """Execute a three-phase active dream.

        Phase 1 (WONDER): Generate research intention with adapter ON.
        Phase 2 (ACT): Execute tools with adapter OFF (base model).
        Phase 3 (REFLECT): Score findings with adapter ON.

        The entire execution is cancellable -- if the user sends a
        message, interrupt() cancels this task immediately.
        """
        from nls.brain.dream_findings import DreamFinding

        agent_id = self.runtime.agent_id
        rt = self.runtime
        start_time = time.time()
        time_budget = type_config.get(
            "time_budget_seconds", dmn._active_time_budget,
        )
        max_iterations = type_config.get(
            "max_iterations", dmn._active_max_iterations,
        )
        allowed_tools = type_config.get("allowed_tools", [])
        safety = type_config.get("safety", {})

        tool_outputs: list[str] = []
        sources: list[str] = []
        research_question = ""

        # Ensure workspace directory exists for autonomous dreams
        workspace_dir = safety.get("workspace_dir")
        if workspace_dir and dream_type == "autonomous":
            ws_path = pathlib.Path(workspace_dir)
            if not ws_path.is_absolute():
                base = getattr(rt, "workspace_path", None) or "."
                ws_path = pathlib.Path(base) / ws_path
            ws_path.mkdir(parents=True, exist_ok=True)
            logger.info(
                "Agent %s: autonomous workspace ensured at %s",
                agent_id, ws_path,
            )

        try:
            # ==============================================================
            # PHASE 1: WONDER (adapter ON -- personality drives curiosity)
            # ==============================================================
            wonder_response = await self._dream_generate_async(
                wonder_prompt, use_adapter=True,
            )
            if not wonder_response:
                logger.warning(
                    "Agent %s: active dream WONDER phase empty", agent_id,
                )
                return

            research_question = wonder_response.strip()
            # Trim to first paragraph if the model rambles
            if "\n\n" in research_question:
                research_question = research_question.split("\n\n")[0]
            if len(research_question) > 500:
                research_question = research_question[:500]

            logger.info(
                "Agent %s: active dream WONDER: '%s'",
                agent_id, research_question[:120],
            )

            # ==============================================================
            # PHASE 2: ACT (adapter OFF -- clean tool execution)
            # ==============================================================
            # Build a mini system prompt for tool use
            act_prompt = (
                "You are researching a question during your idle time. "
                "Use your tools to find the answer. Be efficient -- you "
                "have a limited number of tool calls.\n\n"
                f"Research question: {research_question}\n\n"
                f"Available tools: {', '.join(allowed_tools)}\n"
                "When you have enough information, stop and summarize "
                "what you found."
            )

            # Get the agent's v2 tools, filtered to allowed set
            agent_tools = getattr(rt, "_agent_tools", None)
            if not agent_tools:
                logger.warning(
                    "Agent %s: no agent tools for active dream", agent_id,
                )
                return

            dream_tools = [
                t for t in agent_tools if t.name in allowed_tools
            ]
            if not dream_tools:
                logger.warning(
                    "Agent %s: no matching tools for dream type %s",
                    agent_id, dream_type,
                )
                return

            # Build OpenAI tool schemas for filtered tools
            openai_tools = []
            for t in dream_tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                })

            # Run mini agentic loop via vLLM
            messages: list[dict] = [
                {"role": "system", "content": act_prompt},
                {"role": "user", "content": research_question},
            ]

            for iteration in range(max_iterations):
                # Check time budget
                if time.time() - start_time > time_budget:
                    logger.info(
                        "Agent %s: active dream time budget exhausted",
                        agent_id,
                    )
                    break

                # Check if we've been cancelled (user preempt)
                if self._paused or self._interrupted:
                    logger.info(
                        "Agent %s: active dream aborted (user preempt)",
                        agent_id,
                    )
                    return

                # Generate next step (adapter ON for trained tool judgment)
                response = await self._dream_vllm_generate(
                    messages=messages,
                    tools=openai_tools,
                    use_adapter=True,
                )

                if response is None:
                    break

                assistant_content = response.get("content") or ""
                tool_calls = response.get("tool_calls") or []

                if not tool_calls:
                    # Model is done -- it produced a summary
                    if assistant_content:
                        tool_outputs.append(
                            f"[Summary]: {assistant_content}"
                        )
                    break

                # Add assistant message to context
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                })

                # Execute each tool call
                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    try:
                        raw_args = func.get("arguments", "{}")
                        if isinstance(raw_args, str):
                            import json
                            tool_args = json.loads(raw_args)
                        else:
                            tool_args = raw_args
                    except Exception:
                        tool_args = {}

                    # Safety enforcement
                    if not self._check_dream_tool_safety(
                        tool_name, tool_args, safety, dream_type,
                    ):
                        result_text = (
                            f"Blocked: {tool_name} not allowed in "
                            f"{dream_type} dream (safety policy)."
                        )
                    else:
                        # Find and execute the tool
                        tool_obj = next(
                            (t for t in dream_tools if t.name == tool_name),
                            None,
                        )
                        if tool_obj is None:
                            result_text = f"Error: tool '{tool_name}' not available."
                        else:
                            try:
                                result = await tool_obj.execute(
                                    tool_args, signal=None,
                                )
                                result_text = result.content or ""
                                if len(result_text) > 3000:
                                    result_text = result_text[:3000] + "\n[truncated]"
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                result_text = f"Error: {exc}"

                    tool_outputs.append(
                        f"[{tool_name}]: {result_text[:500]}"
                    )

                    # Track URLs as sources
                    url = tool_args.get("url") or tool_args.get("query")
                    if url and tool_name in ("web_search", "web_fetch", "browser"):
                        sources.append(str(url)[:200])

                    # Add tool result to context
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", "unknown"),
                        "content": result_text,
                    })

                logger.debug(
                    "Agent %s: active dream iteration %d/%d complete",
                    agent_id, iteration + 1, max_iterations,
                )

            # ==============================================================
            # PHASE 3: REFLECT (adapter ON -- score findings)
            # ==============================================================
            if not tool_outputs:
                logger.info(
                    "Agent %s: active dream produced no outputs", agent_id,
                )
                dmn.record_activation(mode=f"active_{dream_type}")
                return

            findings_text = "\n\n".join(tool_outputs[-5:])
            reflect_prompt = dmn.build_active_reflect_prompt(findings_text)

            reflect_response = await self._dream_generate_async(
                reflect_prompt, use_adapter=True,
            )

            # Parse relevance score from reflection
            relevance = 0.0
            reflection = reflect_response or ""
            if reflect_response:
                import re
                match = re.search(
                    r"RELEVANCE:\s*([\d.]+)", reflect_response,
                )
                if match:
                    try:
                        relevance = float(match.group(1))
                        relevance = max(0.0, min(1.0, relevance))
                    except ValueError:
                        pass

            elapsed = time.time() - start_time
            logger.info(
                "Agent %s: active dream complete (type=%s, "
                "iterations=%d, outputs=%d, relevance=%.2f, %.1fs)",
                agent_id, dream_type, min(iteration + 1, max_iterations),
                len(tool_outputs), relevance, elapsed,
            )

            # Record activation (uses active cooldown)
            dmn.record_activation(mode=f"active_{dream_type}")
            self.stats.total_dreams += 1

            # Build finding
            finding = DreamFinding(
                agent_id=agent_id,
                dream_type=dream_type,
                research_question=research_question,
                summary=findings_text[:2000],
                relevance_score=relevance,
                sources=sources,
                raw_tool_outputs=tool_outputs,
                learn_signals_extracted=0,
                reflection=reflection[:1000],
            )

            # Process through runtime for LEARN signal extraction
            if hasattr(rt, "process_dream_result") and reflect_response:
                try:
                    loop = asyncio.get_running_loop()
                    dream_result = await loop.run_in_executor(
                        None, rt.process_dream_result,
                        reflect_response, f"active_{dream_type}",
                    )
                    finding.learn_signals_extracted = dream_result.get(
                        "signals_extracted", 0,
                    )
                    finding.facts_stored = dream_result.get(
                        "facts_stored", 0,
                    )
                except Exception as exc:
                    logger.warning(
                        "Agent %s: dream result processing failed: %s",
                        agent_id, exc,
                    )

            # Queue finding for user delivery if above threshold
            if relevance >= dmn._active_relevance_threshold:
                rt.add_dream_finding(finding)

            # Broadcast to connected clients
            if self.connection_manager is not None:
                await self.connection_manager.broadcast(agent_id, {
                    "type": "daydream",
                    "mode": f"active_{dream_type}",
                    "content": research_question[:200],
                    "findings_preview": findings_text[:300],
                    "relevance": relevance,
                    "signals": finding.learn_signals_extracted,
                    "facts_stored": finding.facts_stored,
                })

        except asyncio.CancelledError:
            logger.info(
                "Agent %s: active dream cancelled (%.1fs elapsed)",
                agent_id, time.time() - start_time,
            )
        except Exception as exc:
            logger.error(
                "Agent %s: active dream failed: %s",
                agent_id, exc, exc_info=True,
            )
        finally:
            self._active_dream_task = None

    def _check_dream_tool_safety(
        self,
        tool_name: str,
        tool_args: dict,
        safety: dict,
        dream_type: str,
    ) -> bool:
        """Enforce safety rules for active dream tool calls.

        Returns True if the tool call is allowed, False if blocked.
        """
        # ── Autonomous type: workspace-scoped writes, blocklist bash ──
        if dream_type == "autonomous":
            workspace_dir = safety.get(
                "workspace_dir", "autonomous_workspace",
            )
            if tool_name in ("write", "edit"):
                path = tool_args.get("path", "")
                if workspace_dir not in path:
                    logger.warning(
                        "Dream safety: autonomous blocked %s "
                        "outside workspace (%s not in %s)",
                        tool_name, path, workspace_dir,
                    )
                    return False

            if tool_name == "bash":
                command = tool_args.get("command", "")
                blocked = safety.get("blocked_commands", [])
                for blocked_cmd in blocked:
                    if blocked_cmd.lower() in command.lower():
                        logger.warning(
                            "Dream safety: autonomous blocked bash "
                            "'%s' (contains '%s')",
                            command[:100], blocked_cmd,
                        )
                        return False

            return True

        # ── Sandbox enforcement for practice dreams ──
        if safety.get("sandbox_only"):
            sandbox_dir = safety.get("sandbox_dir", "dream_workspace")
            if tool_name in ("write", "edit"):
                path = tool_args.get("path", "")
                if sandbox_dir not in path:
                    logger.warning(
                        "Dream safety: blocked %s outside sandbox (%s)",
                        tool_name, path,
                    )
                    return False

        # ── Bash command filtering (read-only types) ──
        if tool_name == "bash" and safety.get("read_only"):
            command = tool_args.get("command", "")
            blocked = safety.get("blocked_commands", [])
            for blocked_cmd in blocked:
                if blocked_cmd.lower() in command.lower():
                    logger.warning(
                        "Dream safety: blocked bash command '%s' "
                        "(contains '%s')",
                        command[:100], blocked_cmd,
                    )
                    return False

            allowed_prefixes = safety.get("allowed_command_prefixes")
            if allowed_prefixes:
                cmd_lower = command.strip().lower()
                if not any(
                    cmd_lower.startswith(p.lower())
                    for p in allowed_prefixes
                ):
                    logger.warning(
                        "Dream safety: blocked bash command '%s' "
                        "(not in allowed prefixes)",
                        command[:100],
                    )
                    return False

        return True

    async def _dream_generate_async(
        self,
        prompt: str,
        use_adapter: bool = True,
    ) -> str:
        """Generate text for a dream phase (WONDER or REFLECT).

        Uses the agent's orchestrator inference pipeline (session model).
        """
        agent_id = self.runtime.agent_id
        rt = self.runtime

        try:
            _vllm, _adapter = rt.inference_pipeline()
        except Exception:
            _vllm, _adapter = None, None

        if _vllm is not None:
            try:
                from nls.runtime.inference_compat import micro_inference_extra_body

                _upstream = getattr(_vllm, "base_url", "") or ""
                result = await _vllm.generate(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1024,
                    temperature=0.7,
                    adapter_name=_adapter if use_adapter else None,
                    extra_body=micro_inference_extra_body(_upstream, thinking=False),
                )
                return result.text if result else ""
            except Exception as exc:
                logger.warning(
                    "Agent %s: dream generate via vLLM failed: %s",
                    agent_id, exc,
                )
                return ""

        return ""

    async def _dream_vllm_generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        use_adapter: bool = False,
    ) -> dict | None:
        """Generate a structured response (with tool calls) for ACT phase.

        Returns dict with 'content' and optional 'tool_calls', or None.
        Adapts GenerateResult from vLLM client into the dict format the
        mini agentic loop expects.
        """
        rt = self.runtime
        try:
            _vllm, _adapter = rt.inference_pipeline()
        except Exception:
            _vllm, _adapter = None, None

        if _vllm is None:
            logger.warning(
                "Agent %s: no vLLM client for active dream ACT phase",
                rt.agent_id,
            )
            return None

        try:
            from nls.runtime.inference_compat import micro_inference_extra_body

            _upstream = getattr(_vllm, "base_url", "") or ""
            result = await _vllm.generate(
                messages=messages,
                tools=tools,
                max_tokens=2048,
                temperature=0.3,
                adapter_name=_adapter if use_adapter else None,
                extra_body=micro_inference_extra_body(_upstream, thinking=False),
            )
            # GenerateResult has .text and .tool_calls attributes
            return {
                "content": result.text or "",
                "tool_calls": result.tool_calls or [],
            }
        except Exception as exc:
            logger.warning(
                "Agent %s: dream vLLM generate failed: %s",
                rt.agent_id, exc,
            )
            return None

    # ===================================================================
    # Drowsy Negotiation (Hypnagogia)
    # ===================================================================

    def confirm_sleep(self) -> None:
        """User confirms drowsy request -- proceed with sleep.

        The user said "yes, go ahead and nap."  Like a parent telling
        a child "okay, you can rest now."  The social gate opens and
        sleep proceeds immediately.
        """
        reason = self._pending_sleep_reason or "user_confirmed"
        self._pending_sleep_reason = None
        self._pending_sleep_at = None
        logger.info(
            "Agent %s: sleep confirmed by user (reason=%s)",
            self.runtime.agent_id, reason,
        )
        self.stop(reason=f"sleep:{reason}")

    def deny_sleep(self) -> None:
        """User denies drowsy request -- stay awake.

        The user said "no, stay awake."  We clear the pending request
        and set a **1-hour cooldown** so the agent does not re-ask.
        Bedtime denials get a 30-minute cooldown (higher pressure).
        """
        was_bedtime = (
            self._pending_sleep_reason is not None
            and self._pending_sleep_reason.startswith("bedtime")
        )
        self._pending_sleep_reason = None
        self._pending_sleep_at = None
        _cooldown = 1800.0 if was_bedtime else 3600.0
        self._deny_sleep_until = time.time() + _cooldown
        self._grace_breaths = 3
        logger.info(
            "Agent %s: sleep denied by user — cooldown %.0fs (until %s)",
            self.runtime.agent_id, _cooldown,
            time.strftime("%H:%M:%S", time.localtime(self._deny_sleep_until)),
        )

    @property
    def is_drowsy(self) -> bool:
        """Whether the agent is in drowsy negotiation."""
        return self._pending_sleep_reason is not None

    def enqueue_autonomous_dispatch(
        self, prompt: str, source: str = "system",
    ) -> None:
        """Schedule an autonomous agentic loop to run on the next breath.

        Thread-safe: can be called from DelegateManager callbacks or
        any async context.  The breath cycle picks it up and runs
        ``_dispatch_autonomous_v2``.

        Priority rules
        --------------
        * **User / channel turn running** → skip entirely.  The real
          foreground orchestrator will call delegate_status when its tool
          loop completes.
        * **Autonomous / DMN loop running** → do NOT skip.  The DMN is
          preemptable by higher-priority dispatches (scheduler check-backs,
          delegate completions).  Cancel any active dream task immediately
          so the lock is released sooner, then queue the dispatch.
        """
        rt = self.runtime
        if rt is None:
            return

        # Use is_user_busy when available (distinguishes user turns from DMN).
        # Fall back to is_busy for backwards compatibility with older runtimes.
        user_busy = getattr(rt, "is_user_busy", getattr(rt, "is_busy", False))
        _critical_wake = (
            source.startswith("team_completion_review:")
            or source.startswith("team_wave_complete:")
            or source.startswith("team_member_escalation:")
        )
        if user_busy and not _critical_wake:
            logger.info(
                "Agent %s: autonomous dispatch skipped — user/channel "
                "foreground turn active (source=%s). Orchestrator will "
                "handle via delegate_status.",
                getattr(rt, "agent_id", "?"), source,
            )
            return

        from nls.agentic.wake_coordination import (
            should_skip_stale_orchestration_wake,
        )
        _tm = getattr(rt, "_team_manager", None)
        if _tm is not None and should_skip_stale_orchestration_wake(
            _tm,
            source,
            context=f"enqueue:{source}",
        ):
            return

        # If an active dream is running, cancel it so the agentic lock is
        # released faster and the queued check-back runs promptly.
        if (
            self._active_dream_task is not None
            and not self._active_dream_task.done()
        ):
            self._active_dream_task.cancel()
            self._active_dream_task = None
            logger.info(
                "Agent %s: active dream cancelled — preempted by %s dispatch",
                getattr(rt, "agent_id", "?"), source,
            )

        # Dedup: if there's already a pending dispatch with the same
        # source (e.g. "team_checkback:team_abc"), don't enqueue another.
        # Completion reviews coalesce per team (batched source).
        from nls.agentic.wake_coordination import (
            completion_review_source,
            is_completion_review_source,
            parse_completion_review_team_id,
        )
        _cr_team = parse_completion_review_team_id(source) if is_completion_review_source(source) else ""
        for _i, (_existing_prompt, _existing_source) in enumerate(
            list(self._pending_dispatches),
        ):
            if _existing_source == source:
                logger.info(
                    "Agent %s: autonomous dispatch DEDUPED — "
                    "source=%s already in queue (depth=%d)",
                    getattr(rt, "agent_id", "?"), source,
                    len(self._pending_dispatches),
                )
                return
            if _cr_team and is_completion_review_source(_existing_source):
                if parse_completion_review_team_id(_existing_source) == _cr_team:
                    _batched = completion_review_source(_cr_team)
                    self._pending_dispatches[_i] = (prompt, _batched)
                    logger.info(
                        "Agent %s: completion-review wake COALESCED for %s",
                        getattr(rt, "agent_id", "?"), _cr_team,
                    )
                    return

        if source == "delegate_batch_complete":
            self.drain_pending_dispatches(source_prefix="team_checkback:")

        self._pending_dispatches.append((prompt, source))
        logger.info(
            "Agent %s: autonomous dispatch enqueued (source=%s, "
            "prompt=%.100s, queue_depth=%d, priority=%d)",
            self.runtime.agent_id, source,
            prompt, len(self._pending_dispatches),
            dispatch_priority(source, prompt),
        )

        # Phase 0: mirror into the typed event queue for future use
        self._mirror_dispatch_to_event_queue(prompt, source)

    def _pop_highest_priority_dispatch(self) -> tuple[str, str]:
        """Pop the highest-priority pending dispatch (not strict FIFO)."""
        if not self._pending_dispatches:
            return "", ""
        best_i = min(
            range(len(self._pending_dispatches)),
            key=lambda i: dispatch_priority(
                self._pending_dispatches[i][1],
                self._pending_dispatches[i][0],
            ),
        )
        return self._pending_dispatches.pop(best_i)

    def drain_pending_dispatches(
        self,
        *,
        source_exact: str = "",
        source_prefix: str = "",
    ) -> int:
        """Remove queued autonomous dispatches matching source filter."""
        if not source_exact and not source_prefix:
            return 0
        before = len(self._pending_dispatches)
        if source_exact:
            self._pending_dispatches = [
                (p, s) for p, s in self._pending_dispatches
                if s != source_exact
            ]
        else:
            self._pending_dispatches = [
                (p, s) for p, s in self._pending_dispatches
                if not s.startswith(source_prefix)
            ]
        removed = before - len(self._pending_dispatches)
        if removed:
            logger.info(
                "Agent %s: drained %d pending dispatch(es) "
                "(exact=%r prefix=%r)",
                getattr(self.runtime, "agent_id", "?"),
                removed, source_exact, source_prefix,
            )
        return removed

    # ───────────────────────────────────────────────────────────────
    # Event queue helpers (Phase 0 — additive, no behavior change)
    # ───────────────────────────────────────────────────────────────

    def push_event(self, event: AgentEvent) -> None:
        """Push a typed event into the agent's event queue."""
        self.event_queue.push(event)

    def _mirror_dispatch_to_event_queue(
        self, prompt: str, source: str,
    ) -> None:
        """Mirror a legacy _pending_dispatches entry into the event queue."""
        from nls.engine.events import AgentEvent, EventType, EventPriority

        from nls.engine.events import _DEFAULT_PRIORITIES

        _source_to_type = {
            "delegate_batch_complete": EventType.BATCH_COMPLETE,
            "delegate": EventType.DELEGATE_COMPLETE,
            "scheduler": EventType.TIMER_FIRE,
            "check_back": EventType.TIMER_FIRE,
        }

        etype = EventType.DRIVE_SIGNAL
        priority = EventPriority.DRIVE
        for prefix, et in _source_to_type.items():
            if source.startswith(prefix):
                etype = et
                priority = _DEFAULT_PRIORITIES.get(et, priority)
                break

        self.event_queue.push(AgentEvent(
            type=etype,
            source=source,
            payload={"prompt": prompt},
            priority=priority,
        ))

    # ===================================================================
    # Status
    # ===================================================================

    def get_status(self) -> dict[str, Any]:
        """Return inner loop status for diagnostics."""
        ss = getattr(self.runtime, "self_state", None)
        active_dreaming = (
            self._active_dream_task is not None
            and not self._active_dream_task.done()
        )
        return {
            "running": self._running,
            "paused": self._paused,
            "interrupted": self._interrupted,
            "drowsy": self.is_drowsy,
            "active_dreaming": active_dreaming,
            "pending_sleep_reason": self._pending_sleep_reason,
            "grace_breaths_remaining": self._grace_breaths,
            "agent_id": self.runtime.agent_id,
            "bpm": ss.bpm if ss else 0,
            "beat_count": ss.beat_count if ss else 0,
            "total_beats": self.stats.total_beats,
            "total_breaths": self.stats.total_breaths,
            "total_dreams": self.stats.total_dreams,
            "total_active_dreams": self.stats.total_active_dreams,
            "total_drive_actions": self.stats.total_drive_actions,
            "started_at": self.stats.started_at,
            "stopped_at": self.stats.stopped_at,
            "stop_reason": self.stats.stop_reason,
            "event_queue": self.event_queue.get_stats(),
        }
