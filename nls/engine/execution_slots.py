"""Execution Slots — slot-based concurrency replacing _agentic_lock.

Three execution slots, each representing a different depth of processing:

  MicroSlot  — no lock, single LLM call (status replies, acks)
  FocusSlot  — lightweight lock, short agentic loop (5-10 iters)
  DeepSlot   — full lock, complete agentic loop with delegation (40+ iters)

The key improvement over ``_agentic_lock``: the micro slot can run
concurrently with a deep slot.  A WhatsApp status query gets answered
in ~2 seconds even while a 40-iteration orchestration loop is running.

BackgroundQueue persists deferred events to disk for processing when
the deep slot is free.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SlotStatus:
    """Snapshot of a slot's state."""
    name: str
    busy: bool
    source: str = ""
    started_at: float = 0.0
    elapsed_seconds: float = 0.0


class MicroSlot:
    """Lock-free execution slot for single LLM calls.

    Can run concurrently with FocusSlot and DeepSlot.
    No state mutation beyond ANS signal recording.
    """

    def __init__(self) -> None:
        self._active_count: int = 0
        self._total_runs: int = 0

    @property
    def is_busy(self) -> bool:
        return self._active_count > 0

    def acquire(self) -> _MicroContext:
        return _MicroContext(self)

    def get_status(self) -> SlotStatus:
        return SlotStatus(
            name="micro",
            busy=self.is_busy,
        )


class _MicroContext:
    """Async context manager for micro slot."""

    def __init__(self, slot: MicroSlot) -> None:
        self._slot = slot

    async def __aenter__(self) -> _MicroContext:
        self._slot._active_count += 1
        self._slot._total_runs += 1
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self._slot._active_count = max(0, self._slot._active_count - 1)


class FocusSlot:
    """Lightweight-locked slot for short agentic loops.

    Takes a lock but with a short timeout — can preempt DMN but not deep.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._source: str = ""
        self._started_at: float = 0.0
        self._total_runs: int = 0

    @property
    def is_busy(self) -> bool:
        return self._lock.locked()

    async def acquire(
        self, source: str = "", timeout: float = 5.0,
    ) -> bool:
        """Try to acquire the focus slot.  Returns False on timeout."""
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=timeout)
            self._source = source
            self._started_at = time.time()
            self._total_runs += 1
            return True
        except asyncio.TimeoutError:
            return False

    def release(self) -> None:
        if self._lock.locked():
            self._source = ""
            self._started_at = 0.0
            self._lock.release()

    def get_status(self) -> SlotStatus:
        elapsed = 0.0
        if self._started_at:
            elapsed = time.time() - self._started_at
        return SlotStatus(
            name="focus",
            busy=self.is_busy,
            source=self._source,
            started_at=self._started_at,
            elapsed_seconds=elapsed,
        )


class DeepSlot:
    """Full-locked slot for complete agentic loops with delegation.

    Equivalent to the current ``_agentic_lock`` but with richer metadata.
    Only one deep slot runs at a time per agent.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._source: str = ""
        self._started_at: float = 0.0
        self._total_runs: int = 0
        self._current_iteration: int = 0
        self._max_iterations: int = 0

    @property
    def is_busy(self) -> bool:
        return self._lock.locked()

    @property
    def lock(self) -> asyncio.Lock:
        """Direct access to the underlying lock for backward compat
        with ``async with self._agentic_lock:``."""
        return self._lock

    def update_progress(
        self, iteration: int, max_iterations: int = 0,
    ) -> None:
        self._current_iteration = iteration
        if max_iterations:
            self._max_iterations = max_iterations

    def release(self) -> None:
        if self._lock.locked():
            self._source = ""
            self._started_at = 0.0
            self._current_iteration = 0
            self._lock.release()

    def get_status(self) -> SlotStatus:
        elapsed = 0.0
        if self._started_at:
            elapsed = time.time() - self._started_at
        return SlotStatus(
            name="deep",
            busy=self.is_busy,
            source=self._source,
            started_at=self._started_at,
            elapsed_seconds=elapsed,
        )


class _DeepContext:
    """Async context manager that wraps the deep slot lock."""

    def __init__(self, slot: DeepSlot, source: str = "") -> None:
        self._slot = slot
        self._source = source

    async def __aenter__(self) -> DeepSlot:
        await self._slot._lock.acquire()
        self._slot._source = self._source
        self._slot._started_at = time.time()
        self._slot._total_runs += 1
        return self._slot

    async def __aexit__(self, *exc: Any) -> None:
        self._slot.release()


class BackgroundQueue:
    """Disk-persisted queue for deferred events.

    Events that can't be processed now (DEFER depth) are written to a
    JSON-lines file and loaded on startup.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._items: list[dict] = []
        if path is not None and path.exists():
            self._load()

    def push(self, event_data: dict) -> None:
        # Validate JSON-serializable before accepting
        try:
            serialized = json.dumps(event_data)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "BackgroundQueue.push: rejecting non-serializable event: %s", exc,
            )
            return
        self._items.append(event_data)
        self._append_line(serialized)

    def pop(self) -> dict | None:
        if not self._items:
            return None
        item = self._items.pop(0)
        self._rewrite()
        return item

    def drain(self, max_items: int = 10) -> list[dict]:
        items = self._items[:max_items]
        self._items = self._items[max_items:]
        self._rewrite()
        return items

    @property
    def depth(self) -> int:
        return len(self._items)

    @property
    def is_empty(self) -> bool:
        return len(self._items) == 0

    def _append_line(self, serialized: str) -> None:
        """Append a single pre-serialized JSON line (push fast-path)."""
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(serialized + "\n")
        except Exception as exc:
            logger.warning("BackgroundQueue append failed: %s", exc)

    def _rewrite(self) -> None:
        """Full rewrite of the queue file (used after pop/drain)."""
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                for item in self._items:
                    try:
                        f.write(json.dumps(item) + "\n")
                    except (TypeError, ValueError) as exc:
                        logger.warning(
                            "BackgroundQueue: skipping non-serializable item: %s", exc,
                        )
        except Exception as exc:
            logger.warning("BackgroundQueue rewrite failed: %s", exc)

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        loaded = 0
        skipped = 0
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._items.append(json.loads(line))
                        loaded += 1
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            "BackgroundQueue: skipping malformed line %d: %s",
                            line_num, exc,
                        )
                        skipped += 1
        except Exception as exc:
            logger.warning("BackgroundQueue load failed: %s", exc)
        if loaded or skipped:
            logger.info(
                "BackgroundQueue loaded %d items (%d skipped) from %s",
                loaded, skipped, self._path,
            )


# ───────────────────────────────────────────────────────────────────
# Composite Slot Manager
# ───────────────────────────────────────────────────────────────────

class ExecutionSlotManager:
    """Manages all execution slots for one agent.

    Provides a unified interface for the event loop to check availability,
    acquire slots, and get status.
    """

    def __init__(self, agent_dir: Path | None = None) -> None:
        self.micro = MicroSlot()
        self.focus = FocusSlot()
        self.deep = DeepSlot()
        self.background = BackgroundQueue(
            path=agent_dir / "background_queue.jsonl" if agent_dir else None,
        )

    @property
    def deep_slot_busy(self) -> bool:
        return self.deep.is_busy

    @property
    def focus_slot_busy(self) -> bool:
        return self.focus.is_busy

    @property
    def any_busy(self) -> bool:
        return self.deep.is_busy or self.focus.is_busy

    def acquire_deep(self, source: str = "") -> _DeepContext:
        """Return an async context manager for the deep slot."""
        return _DeepContext(self.deep, source=source)

    def get_status(self) -> dict[str, Any]:
        return {
            "micro": self.micro.get_status().__dict__,
            "focus": self.focus.get_status().__dict__,
            "deep": self.deep.get_status().__dict__,
            "background_depth": self.background.depth,
        }

    def get_deep_for_context(self, context_id: str) -> DeepSlot:
        """Get or create a deep slot for a specific orchestration context.

        The default "primary" context uses the main deep slot.
        Additional contexts get their own isolated deep slots so
        multiple orchestration sessions can run concurrently.
        """
        if context_id == "primary" or not context_id:
            return self.deep
        if not hasattr(self, "_context_deep_slots"):
            self._context_deep_slots: dict[str, DeepSlot] = {}
        if context_id not in self._context_deep_slots:
            self._context_deep_slots[context_id] = DeepSlot()
        return self._context_deep_slots[context_id]
