"""Dream job dataclass used by agent runtime DMN ticks."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DreamJob:
    """A pending daydream generation job."""

    agent_id: str
    prompt: str
    facts: list[dict] = field(default_factory=list)
    mode: str = "replay"
    queued_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    result: dict[str, Any] = field(default_factory=dict)
