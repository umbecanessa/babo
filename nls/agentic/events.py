"""v4 event system for the NLS agentic loop.

Streamlined from v3's 21-event EventType to 19 focused events.
Browser-specific events moved to tool-level events, probe signals removed,
plan events consolidated, and 4 delegation events added.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """v4 event types — 15 core + 4 delegation."""

    # Lifecycle
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    TURN_START = "turn_start"
    TURN_END = "turn_end"

    # Generation
    TOKEN = "agentic_token"
    TOOL_DELTA = "tool_call_delta"
    THINKING = "turn_thinking"

    # Tool execution
    TOOL_START = "tool_execution_start"
    TOOL_END = "tool_execution_end"
    TOOL_OUTPUT = "tool_output_chunk"

    # Communication
    COMMUNICATE = "communicate"
    ASK_USER = "ask_user"
    USER_ANSWER = "user_answer"
    LOOP_BUDGET_PROMPT = "loop_budget_prompt"
    BUDGET_DECISION = "budget_decision"

    # Planning
    PLAN_UPDATE = "plan_update"

    # Status
    STATUS = "activity_status"

    # Delegation
    DELEGATE_SPAWN = "delegate_spawn"
    DELEGATE_BATCH_STARTED = "delegate_batch_started"
    DELEGATE_PROGRESS = "delegate_progress"
    DELEGATE_COMPLETE = "delegate_complete"
    DELEGATE_FAILED = "delegate_failed"
    DELEGATE_BATCH_COMPLETE = "delegate_batch_complete"


@dataclass
class AgentEvent:
    """Typed event emitted by the v4 agentic loop."""

    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, **self.data}


async def emit(
    callback: Callable[[AgentEvent], Any] | None,
    event: AgentEvent,
) -> None:
    """Fire an event callback, tolerating None callbacks and errors."""
    if callback is None:
        return
    try:
        result = callback(event)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.debug("Event callback error for %s", event.type, exc_info=True)
