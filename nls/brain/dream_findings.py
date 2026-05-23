"""Dream Findings -- Results from active (tool-using) dreams.

When the DMN executes an active dream (browsing, bash exploration,
practice), it may produce findings worth reporting to the user.
DreamFinding is the data structure that flows from InnerLoop through
ServerRuntime to the frontend via proactive initiative broadcasts.

The pipeline:
    DMN activation → active dream → tool execution → REFLECT phase
    → DreamFinding scored for relevance → queued on ServerRuntime
    → delivered to user via reach_out / dream_finding broadcast
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DreamFinding:
    """A single finding from an active dream session."""

    agent_id: str
    dream_type: str  # "browse", "bash_explore", "practice"
    research_question: str
    summary: str
    relevance_score: float  # 0.0 to 1.0
    sources: list[str] = field(default_factory=list)
    raw_tool_outputs: list[str] = field(default_factory=list)
    learn_signals_extracted: int = 0
    facts_stored: int = 0
    reflection: str = ""
    created_at: float = field(default_factory=time.time)
    delivered: bool = False
    delivered_at: float | None = None

    @property
    def is_reportable(self) -> bool:
        """Whether this finding is worth reporting to the user."""
        return self.relevance_score >= 0.6 and bool(self.summary)

    def to_broadcast(self) -> dict[str, Any]:
        """Format for WebSocket broadcast to frontend."""
        return {
            "type": "dream_finding",
            "dream_type": self.dream_type,
            "research_question": self.research_question,
            "summary": self.summary,
            "relevance": self.relevance_score,
            "sources": self.sources[:5],
            "signals_extracted": self.learn_signals_extracted,
            "facts_stored": self.facts_stored,
            "created_at": self.created_at,
        }

    def to_reach_out_message(self) -> str:
        """Format as a natural reach-out message for the user."""
        msg = f"While I was idle, I looked into something: {self.research_question}\n\n"
        msg += self.summary
        if self.sources:
            msg += "\n\nSources: " + ", ".join(self.sources[:3])
        return msg


@dataclass
class ActiveDreamState:
    """Tracks in-progress active dream for abort/interrupt handling.

    When a user message arrives during an active dream, the InnerLoop
    pauses.  This state allows the dream to either resume after the
    user interaction or be cleanly abandoned.
    """

    dream_type: str
    research_question: str = ""
    type_config: dict = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    iterations_completed: int = 0
    tool_outputs: list[str] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.started_at
