"""Brain Event Bus — standardized signal distribution to brain components.

Instead of each code path (agentic bridge, post_process, inner loop
heartbeat) wiring hooks differently, brain components subscribe to a
unified event bus.  The bus receives ``BrainSignal`` objects and fans
them out to registered listeners.

This is Phase 4 of the event-driven architecture: unifying how brain
components receive signals regardless of where the signal originated.
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


class BrainSignalType(enum.Enum):
    """Types of signals brain components can subscribe to."""
    RESPONSE = "response"           # agent produced a response (text)
    TOOL_RESULT = "tool_result"     # tool execution completed
    TURN_START = "turn_start"       # loop iteration starting
    TURN_END = "turn_end"           # loop iteration ended
    LOOP_START = "loop_start"       # agentic loop started
    LOOP_END = "loop_end"           # agentic loop ended
    HEARTBEAT = "heartbeat"         # inner loop heartbeat tick
    SLEEP_START = "sleep_start"     # sleep cycle beginning
    WAKE = "wake"                   # sleep cycle ended


@dataclass
class BrainSignal:
    """A signal distributed to brain components.

    Carries enough context for any brain component to update itself
    without needing to know the source (agentic loop, chat, channel, etc.)
    """
    type: BrainSignalType
    source: str = ""                # "agentic:v5", "chat:moe", "channel:wa"
    user_input: str = ""
    response_text: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: str = ""
    is_agentic: bool = False
    iteration: int = 0
    elapsed_seconds: float = 0.0
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# Listener signature: (signal: BrainSignal) -> None
BrainListener = Callable[[BrainSignal], None]


class BrainEventBus:
    """Fans out BrainSignals to registered listeners.

    Brain components (ANS, Narrative, ToM, Hypothalamus) register
    themselves for specific signal types.  The bus is synchronous
    (no async) since brain hooks are CPU-only, no GPU.
    """

    def __init__(self) -> None:
        self._listeners: dict[BrainSignalType, list[BrainListener]] = {}
        self._global_listeners: list[BrainListener] = []

    def subscribe(
        self,
        signal_type: BrainSignalType,
        listener: BrainListener,
    ) -> None:
        """Subscribe to a specific signal type."""
        self._listeners.setdefault(signal_type, []).append(listener)

    def subscribe_all(self, listener: BrainListener) -> None:
        """Subscribe to all signal types."""
        self._global_listeners.append(listener)

    def emit(self, signal: BrainSignal) -> None:
        """Emit a signal to all relevant listeners."""
        for listener in self._global_listeners:
            try:
                listener(signal)
            except Exception as exc:
                logger.debug(
                    "BrainEventBus: global listener failed: %s", exc,
                )

        type_listeners = self._listeners.get(signal.type, [])
        for listener in type_listeners:
            try:
                listener(signal)
            except Exception as exc:
                logger.debug(
                    "BrainEventBus: %s listener failed: %s",
                    signal.type.value, exc,
                )

    def wire_brain_components(
        self,
        ans: Any | None = None,
        narrative_self: Any | None = None,
        theory_of_mind: Any | None = None,
        hypothalamus: Any | None = None,
    ) -> None:
        """Convenience: wire standard brain components to the bus.

        Creates adapters that translate BrainSignals into the existing
        method calls each component expects.
        """
        if ans is not None:
            def _ans_on_response(signal: BrainSignal) -> None:
                if signal.user_input and signal.response_text:
                    ans.on_response(
                        signal.user_input,
                        signal.response_text,
                        hypothalamus,
                        is_agentic=signal.is_agentic,
                    )
            self.subscribe(BrainSignalType.RESPONSE, _ans_on_response)

        if narrative_self is not None:
            def _narrative_on_response(signal: BrainSignal) -> None:
                _cortisol = 0.0
                if hypothalamus is not None:
                    _hs = getattr(hypothalamus, "hormones", {})
                    if hasattr(_hs, "get"):
                        _c = _hs.get("cortisol", None)
                        if _c is not None:
                            _cortisol = getattr(_c, "level", 0.0)
                _turn = getattr(ans, "_turn_counter", 0) if ans else 0
                narrative_self.record_turn(
                    turn_number=_turn,
                    valence=0.0,
                    arousal=0.4,
                    mood_label="focused" if signal.is_agentic else "engaged",
                    cortisol=_cortisol,
                    is_user_turn=False,
                )
            self.subscribe(BrainSignalType.RESPONSE, _narrative_on_response)

        if theory_of_mind is not None:
            def _tom_on_response(signal: BrainSignal) -> None:
                if signal.user_input and signal.response_text:
                    theory_of_mind.update_from_turn(
                        user_input=signal.user_input,
                        response=signal.response_text,
                    )
            self.subscribe(BrainSignalType.RESPONSE, _tom_on_response)

        if hypothalamus is not None:
            def _hypo_on_heartbeat(signal: BrainSignal) -> None:
                if signal.elapsed_seconds > 0:
                    hypothalamus.tick(signal.elapsed_seconds)
            self.subscribe(BrainSignalType.HEARTBEAT, _hypo_on_heartbeat)
