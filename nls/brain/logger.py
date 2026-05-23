"""NLS Research Event Logger -- Structured JSONL time-series logging.

Captures every brain event in append-only JSONL format for post-run
research analytics. Thread-safe, auto-flushing, with file rotation.

Output: data/agents/{agent_id}/events_{timestamp}.jsonl

Every line: {"ts": ISO8601, "event": "type", "data": {...}}

Event categories:
    turn_*      -- per-message cognitive pipeline
    drive_*     -- autonomous drive evaluations
    hormone_*   -- hormonal state changes
    signal_*    -- ANS signal collection
    sleep_*     -- sleep cycle phases
    thalamus_*  -- routing decisions
    agency_*    -- tool executions
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EventLogger:
    """Append-only JSONL event logger for NLS research.

    Thread-safe: multiple components can log concurrently.
    Auto-flush: writes are flushed every N events or M seconds.
    Rotation: new file when current exceeds max_size_mb.
    """

    def __init__(
        self,
        log_dir: Path,
        *,
        enabled: bool = True,
        max_size_mb: float = 50.0,
        flush_every: int = 10,
        flush_interval_seconds: float = 5.0,
        prefix: str = "events",
    ):
        self._enabled = enabled
        self._log_dir = Path(log_dir)
        self._max_size = int(max_size_mb * 1024 * 1024)
        self._flush_every = flush_every
        self._flush_interval = flush_interval_seconds
        self._prefix = prefix

        self._lock = threading.Lock()
        self._file = None
        self._file_path: Path | None = None
        self._event_count = 0
        self._total_events = 0
        self._last_flush = time.time()
        self._session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        if self._enabled:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._open_file()

    def _open_file(self):
        """Open a new JSONL file."""
        filename = f"{self._prefix}_{self._session_id}.jsonl"
        self._file_path = self._log_dir / filename
        self._file = open(self._file_path, "a", encoding="utf-8")
        self._event_count = 0

    def _rotate_if_needed(self):
        """Rotate to a new file if current exceeds max size."""
        if self._file is None:
            return
        try:
            size = self._file_path.stat().st_size
        except OSError:
            size = 0
        if size >= self._max_size:
            self._file.close()
            self._session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            self._open_file()

    # =======================================================================
    # Core logging
    # =======================================================================

    def log(self, event: str, **data: Any) -> None:
        """Log a single event.

        Args:
            event: Event type string (e.g., "turn_start", "drive_tick")
            **data: Arbitrary key-value pairs for the event data
        """
        if not self._enabled or self._file is None:
            return

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "data": data,
        }

        line = json.dumps(record, ensure_ascii=False, default=str)

        with self._lock:
            self._file.write(line + "\n")
            self._event_count += 1
            self._total_events += 1

            # Auto-flush
            now = time.time()
            if (
                self._event_count >= self._flush_every
                or (now - self._last_flush) >= self._flush_interval
            ):
                self._file.flush()
                self._last_flush = now
                self._rotate_if_needed()

    def flush(self) -> None:
        """Force flush to disk."""
        if not self._enabled or self._file is None:
            return
        with self._lock:
            self._file.flush()

    def close(self) -> None:
        """Close the log file."""
        if self._file is not None:
            with self._lock:
                self._file.flush()
                self._file.close()
                self._file = None

    # =======================================================================
    # Convenience methods -- structured event logging
    # =======================================================================

    # --- Turns ---

    def log_turn_start(self, turn: int, message: str, **extra: Any) -> None:
        self.log("turn_start", turn=turn, message=message, **extra)

    def log_turn_response(
        self,
        turn: int,
        response: str,
        latency_ms: float,
        meta_weight: float,
        **extra: Any,
    ) -> None:
        self.log(
            "turn_response",
            turn=turn,
            response=response,
            latency_ms=round(latency_ms, 1),
            meta_weight=round(meta_weight, 4),
            **extra,
        )

    def log_turn_quality(
        self,
        turn: int,
        *,
        delta_ratio: float = 0.0,
        delta_norm: float = 0.0,
        base_norm: float = 0.0,
        avg_entropy: float = 0.0,
        tag_emitted: str = "",
        tag_correct: bool | None = None,
        fact_recall: bool | None = None,
        **extra: Any,
    ) -> None:
        self.log(
            "turn_quality",
            turn=turn,
            delta_ratio=round(delta_ratio, 6),
            delta_norm=round(delta_norm, 6),
            base_norm=round(base_norm, 6),
            avg_entropy=round(avg_entropy, 4),
            tag_emitted=tag_emitted,
            tag_correct=tag_correct,
            fact_recall=fact_recall,
            **extra,
        )

    def log_turn_signals(
        self,
        turn: int,
        signals: list[dict[str, Any]],
        **extra: Any,
    ) -> None:
        self.log("turn_signals", turn=turn, signals=signals, **extra)

    def log_turn_hormones(
        self,
        turn: int,
        hormones: dict[str, float],
        **extra: Any,
    ) -> None:
        self.log("turn_hormones", turn=turn, **hormones, **extra)

    # --- Hormones ---

    def log_hormone_trigger(
        self,
        signal_type: str,
        fired: dict[str, float],
        **extra: Any,
    ) -> None:
        self.log(
            "hormone_trigger",
            signal_type=signal_type,
            fired=fired,
            **extra,
        )

    def log_hormone_decay(
        self,
        elapsed_seconds: float,
        levels_before: dict[str, float],
        levels_after: dict[str, float],
        **extra: Any,
    ) -> None:
        self.log(
            "hormone_decay",
            elapsed_seconds=round(elapsed_seconds, 2),
            before=levels_before,
            after=levels_after,
            **extra,
        )

    def log_hormone_interaction(
        self,
        deltas: dict[str, float],
        **extra: Any,
    ) -> None:
        self.log("hormone_interaction", deltas=deltas, **extra)

    # --- Drives ---

    def log_drive_tick(
        self,
        pressures: list[dict[str, Any]],
        idle_seconds: float = 0.0,
        **extra: Any,
    ) -> None:
        self.log(
            "drive_tick",
            pressures=pressures,
            idle_seconds=round(idle_seconds, 1),
            **extra,
        )

    def log_drive_goal(
        self,
        goal: dict[str, Any],
        **extra: Any,
    ) -> None:
        self.log("drive_goal", **goal, **extra)

    def log_drive_effort_gate(
        self,
        drive: str,
        base_effort: float,
        confidence: float,
        bias_strength: float,
        perceived_effort: float,
        will_to_act: float,
        passed: bool,
        **extra: Any,
    ) -> None:
        self.log(
            "drive_effort_gate",
            drive=drive,
            base_effort=round(base_effort, 4),
            confidence=round(confidence, 4),
            bias_strength=round(bias_strength, 4),
            perceived_effort=round(perceived_effort, 4),
            will_to_act=round(will_to_act, 4),
            passed=passed,
            **extra,
        )

    def log_drive_action(
        self,
        drive: str,
        action_type: str,
        domain: str,
        success: bool,
        result_preview: str = "",
        **extra: Any,
    ) -> None:
        self.log(
            "drive_action",
            drive=drive,
            action_type=action_type,
            domain=domain,
            success=success,
            result_preview=result_preview[:500],
            **extra,
        )

    def log_drive_experience(
        self,
        domain: str,
        success: bool,
        new_effort: float,
        attempts: int,
        **extra: Any,
    ) -> None:
        self.log(
            "drive_experience",
            domain=domain,
            success=success,
            new_effort=round(new_effort, 4),
            attempts=attempts,
            **extra,
        )

    # --- Signals (ANS) ---

    def log_signal_collected(
        self,
        signal_type: str,
        domain_path: str = "",
        content: str = "",
        turn: int = 0,
        hormonal_snapshot: dict[str, float] | None = None,
        meta_layer: str = "",
        **extra: Any,
    ) -> None:
        self.log(
            "signal_collected",
            signal_type=signal_type,
            domain_path=domain_path,
            content=content[:300],
            turn=turn,
            hormonal_snapshot=hormonal_snapshot or {},
            meta_layer=meta_layer,
            **extra,
        )

    def log_sleep_check(
        self,
        should_sleep: bool,
        reason: str = "",
        effective_threshold: float = 0.0,
        signal_count: int = 0,
        **extra: Any,
    ) -> None:
        self.log(
            "sleep_check",
            should_sleep=should_sleep,
            reason=reason,
            effective_threshold=round(effective_threshold, 4),
            signal_count=signal_count,
            **extra,
        )

    def log_sleep_phase(
        self,
        phase: str,
        **data: Any,
    ) -> None:
        self.log(f"sleep_{phase}", **data)

    # --- Thalamus ---

    def log_thalamus_sense(
        self,
        delta_ratio: float,
        delta_norm: float,
        base_norm: float,
        **extra: Any,
    ) -> None:
        self.log(
            "thalamus_sense",
            delta_ratio=round(delta_ratio, 6),
            delta_norm=round(delta_norm, 6),
            base_norm=round(base_norm, 6),
            **extra,
        )

    def log_thalamus_gate(
        self,
        delta_ratio: float,
        meta_weight: float,
        band: str = "",
        **extra: Any,
    ) -> None:
        self.log(
            "thalamus_gate",
            delta_ratio=round(delta_ratio, 6),
            meta_weight=round(meta_weight, 4),
            band=band,
            **extra,
        )

    # --- Insula (Digestion) ---

    def log_insula_digest(
        self,
        domain: str,
        query: str = "",
        signals_extracted: int = 0,
        signal_types: list[str] | None = None,
        digest_response: str = "",
        **extra: Any,
    ) -> None:
        """Log an Insula digestion event -- sensory input processed into knowledge."""
        self.log(
            "insula_digest",
            domain=domain,
            query=query,
            signals_extracted=signals_extracted,
            signal_types=signal_types or [],
            digest_response=digest_response[:500],
            **extra,
        )

    # --- Default Mode Network (Dreaming) ---

    def log_hippocampal_replay(
        self,
        facts_sampled: int = 0,
        domains_involved: list[str] | None = None,
        signals_extracted: int = 0,
        signal_types: list[str] | None = None,
        dream_response: str = "",
        **extra: Any,
    ) -> None:
        """Log a hippocampal replay (daydream) event."""
        self.log(
            "hippocampal_replay",
            facts_sampled=facts_sampled,
            domains_involved=domains_involved or [],
            signals_extracted=signals_extracted,
            signal_types=signal_types or [],
            dream_response=dream_response[:500],
            **extra,
        )

    # --- Agency ---

    def log_agency_action(
        self,
        action_type: str,
        query: str = "",
        domain: str = "",
        success: bool = False,
        result_preview: str = "",
        **extra: Any,
    ) -> None:
        self.log(
            "agency_action",
            action_type=action_type,
            query=query,
            domain=domain,
            success=success,
            result_preview=result_preview[:500],
            **extra,
        )

    # =======================================================================
    # Diagnostics
    # =======================================================================

    @property
    def total_events(self) -> int:
        return self._total_events

    @property
    def current_file(self) -> Path | None:
        return self._file_path

    @property
    def enabled(self) -> bool:
        return self._enabled

    def __repr__(self) -> str:
        return (
            f"EventLogger(enabled={self._enabled}, "
            f"events={self._total_events}, "
            f"file={self._file_path})"
        )
