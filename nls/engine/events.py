"""NLS Agent Event System — Unified event model for the event-driven architecture.

All event sources (WebSocket, channels, delegates, timers, drives, DMN, sleep)
push typed events into a priority queue.  The AgentEventLoop (evolved InnerLoop)
drains the queue and routes each event through the thalamic router to decide
engagement depth: micro-inference, focused task, deep agentic loop, defer, or drop.

This module defines the event types, priority levels, and the priority queue.
It is the foundation layer (Phase 0) — purely additive, no behavior change.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, ClassVar

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────
# Event Types
# ───────────────────────────────────────────────────────────────────

class EventType(enum.Enum):
    """All possible agent event types."""

    # User / channel interaction
    USER_MESSAGE = "user_message"
    CHANNEL_MESSAGE = "channel_message"

    # Delegate lifecycle
    DELEGATE_COMPLETE = "delegate_complete"
    DELEGATE_ESCALATION = "delegate_escalation"
    DELEGATE_PROGRESS = "delegate_progress"
    COMPLETION_REVIEW = "completion_review"

    # Orchestration
    BATCH_COMPLETE = "batch_complete"

    # Timers and scheduled jobs
    TIMER_FIRE = "timer_fire"

    # Internal drives and autonomous processing
    DRIVE_SIGNAL = "drive_signal"
    DMN_INSIGHT = "dmn_insight"
    PROACTIVE_INITIATIVE = "proactive_initiative"

    # Sleep lifecycle
    SLEEP_READY = "sleep_ready"
    WAKE = "wake"

    # Control signals
    ABORT = "abort"
    INTERRUPT = "interrupt"


# ───────────────────────────────────────────────────────────────────
# Priority Levels (lower = higher priority)
# ───────────────────────────────────────────────────────────────────

class EventPriority(enum.IntEnum):
    """Event priority levels.  Lower numeric value = higher priority.

    Matches the agreed ordering: user > escalation > channel > timer > drive > DMN > sleep.
    """
    CRITICAL = 0       # ABORT, INTERRUPT
    USER = 10          # USER_MESSAGE (primary WS)
    ESCALATION = 20    # DELEGATE_ESCALATION, COMPLETION_REVIEW
    CHANNEL = 30       # CHANNEL_MESSAGE (WA/TG/Email)
    TIMER = 40         # TIMER_FIRE (scheduler callbacks)
    DRIVE = 50         # DRIVE_SIGNAL (hypothalamus-driven goals)
    DMN = 60           # DMN_INSIGHT (background ideation)
    SLEEP = 70         # SLEEP_READY (deferrable)
    BACKGROUND = 80    # Low-priority background tasks


# Default priority mapping for each event type
_DEFAULT_PRIORITIES: dict[EventType, int] = {
    EventType.ABORT: EventPriority.CRITICAL,
    EventType.INTERRUPT: EventPriority.CRITICAL,
    EventType.USER_MESSAGE: EventPriority.USER,
    EventType.DELEGATE_ESCALATION: EventPriority.ESCALATION,
    EventType.COMPLETION_REVIEW: EventPriority.ESCALATION,
    EventType.DELEGATE_COMPLETE: EventPriority.ESCALATION,
    EventType.BATCH_COMPLETE: EventPriority.ESCALATION,
    EventType.DELEGATE_PROGRESS: EventPriority.CHANNEL,
    EventType.CHANNEL_MESSAGE: EventPriority.CHANNEL,
    EventType.TIMER_FIRE: EventPriority.TIMER,
    EventType.DRIVE_SIGNAL: EventPriority.DRIVE,
    EventType.DMN_INSIGHT: EventPriority.DMN,
    EventType.PROACTIVE_INITIATIVE: EventPriority.DMN,
    EventType.SLEEP_READY: EventPriority.SLEEP,
    EventType.WAKE: EventPriority.USER,
}


# ───────────────────────────────────────────────────────────────────
# Engagement Depth
# ───────────────────────────────────────────────────────────────────

class EngagementDepth(enum.Enum):
    """How deeply the agent should process an event."""
    MICRO = "micro"    # Single LLM call, no tools, no lock
    FOCUS = "focus"    # Short loop (5-10 iters), limited tools
    DEEP = "deep"      # Full loop (40+ iters), all tools, delegation
    DEFER = "defer"    # Queue for later processing
    DROP = "drop"      # Discard — duplicate, stale, or irrelevant


# ───────────────────────────────────────────────────────────────────
# Agent Event
# ───────────────────────────────────────────────────────────────────

@dataclass(order=False)
class AgentEvent:
    """A single event in the agent's event queue.

    Parameters
    ----------
    type : EventType
        What kind of event this is.
    source : str
        Origin identifier — "ws", "whatsapp", "telegram", "delegate:3",
        "scheduler:job_id", "drive:curiosity", "dmn", etc.
    payload : dict
        Type-specific data (e.g., user_input, history, delegate_number).
    priority : int
        Numeric priority (lower = higher).  Defaults from _DEFAULT_PRIORITIES.
    reply_channel : callable or None
        Async callback to send the response back to the originator.
        Signature: ``async def reply(text: str) -> None``
    timestamp : float
        When the event was created (monotonic for ordering).
    event_id : str
        Unique identifier for dedup and tracking.
    """
    MAX_DEFERS: ClassVar[int] = 5

    type: EventType
    source: str
    payload: dict = field(default_factory=dict)
    priority: int = -1
    reply_channel: Callable[..., Awaitable[None]] | None = None
    timestamp: float = field(default_factory=time.monotonic)
    event_id: str = ""
    defer_count: int = 0

    def __post_init__(self) -> None:
        if self.priority < 0:
            self.priority = _DEFAULT_PRIORITIES.get(
                self.type, EventPriority.BACKGROUND,
            )
        if not self.event_id:
            self.event_id = f"{self.type.value}_{self.timestamp:.6f}"

    @property
    def is_expired(self) -> bool:
        return self.defer_count >= self.MAX_DEFERS

    def to_dict(self) -> dict[str, Any]:
        """Serialize for BackgroundQueue persistence (excludes reply_channel)."""
        return {
            "type": self.type.value,
            "source": self.source,
            "payload": self.payload,
            "priority": self.priority,
            "timestamp": self.timestamp,
            "event_id": self.event_id,
            "defer_count": self.defer_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentEvent":
        """Deserialize from BackgroundQueue storage."""
        ev = cls(
            type=EventType(data["type"]),
            source=data.get("source", ""),
            payload=data.get("payload", {}),
            priority=data.get("priority", -1),
            timestamp=data.get("timestamp", time.monotonic()),
            event_id=data.get("event_id", ""),
        )
        ev.defer_count = data.get("defer_count", 0)
        return ev


# ───────────────────────────────────────────────────────────────────
# Priority Event Queue
# ───────────────────────────────────────────────────────────────────

class AgentEventQueue:
    """Thread-safe priority queue for agent events.

    Events are ordered by (priority, timestamp) so higher-priority events
    are dequeued first, with FIFO ordering within the same priority level.

    The queue supports async get/put and can be drained from the InnerLoop's
    breath cycle or the future AgentEventLoop.
    """

    def __init__(self, maxsize: int = 256) -> None:
        self._queue: asyncio.PriorityQueue[tuple[int, float, AgentEvent]] = (
            asyncio.PriorityQueue(maxsize=maxsize)
        )
        self._total_pushed: int = 0
        self._total_popped: int = 0

    def push(self, event: AgentEvent) -> None:
        """Push an event (non-blocking).  Drops if queue is full."""
        try:
            self._queue.put_nowait(
                (event.priority, event.timestamp, event),
            )
            self._total_pushed += 1
            logger.debug(
                "EventQueue: pushed %s (source=%s, priority=%d, depth=%d)",
                event.type.value, event.source, event.priority,
                self._queue.qsize(),
            )
        except asyncio.QueueFull:
            logger.warning(
                "EventQueue: FULL — dropping %s from %s (priority=%d)",
                event.type.value, event.source, event.priority,
            )

    async def pop(self, timeout: float | None = None) -> AgentEvent | None:
        """Pop the highest-priority event.  Returns None on timeout."""
        try:
            if timeout is not None:
                _, _, event = await asyncio.wait_for(
                    self._queue.get(), timeout=timeout,
                )
            else:
                _, _, event = await self._queue.get()
            self._total_popped += 1
            return event
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return None

    def pop_nowait(self) -> AgentEvent | None:
        """Non-blocking pop.  Returns None if empty."""
        try:
            _, _, event = self._queue.get_nowait()
            self._total_popped += 1
            return event
        except asyncio.QueueEmpty:
            return None

    def drain(self, max_events: int = 32) -> list[AgentEvent]:
        """Drain up to ``max_events`` from the queue (non-blocking)."""
        events: list[AgentEvent] = []
        for _ in range(max_events):
            ev = self.pop_nowait()
            if ev is None:
                break
            events.append(ev)
        return events

    def peek_types(self) -> list[EventType]:
        """Return the types of events in the queue without consuming them.
        Useful for diagnostics.
        """
        items = list(self._queue._queue)  # type: ignore[attr-defined]
        return [ev.type for _, _, ev in sorted(items)]

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def is_empty(self) -> bool:
        return self._queue.empty()

    def remove_by_id(self, event_id: str) -> bool:
        """Remove a single event by its ``event_id``.

        Returns True if found and removed.  All other events are
        re-inserted with their original priority and timestamp so
        ordering is preserved.
        """
        found = False
        kept: list[tuple[int, float, AgentEvent]] = []
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            _, _, ev = item
            if ev.event_id == event_id and not found:
                found = True
                self._total_popped += 1
            else:
                kept.append(item)
        for item in kept:
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                pass
        if found:
            logger.debug(
                "EventQueue: removed event %s", event_id,
            )
        return found

    def has_priority_above(self, threshold: int) -> bool:
        """Check if any queued event has priority strictly below threshold
        (i.e., higher priority).
        """
        for prio, _, _ in list(self._queue._queue):  # type: ignore[attr-defined]
            if prio < threshold:
                return True
        return False

    def get_stats(self) -> dict[str, Any]:
        return {
            "depth": self.depth,
            "total_pushed": self._total_pushed,
            "total_popped": self._total_popped,
        }
