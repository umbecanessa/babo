"""Consolidation cycles must not call notify_sleep_complete — scheduler owns that."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nls.brain.autonomic import AgentState
from server.services.consolidation_sleep import run_consolidation_cycle


@pytest.mark.asyncio
async def test_consolidation_cycle_skips_notify_sleep_complete():
    runtime = MagicMock()
    runtime.notify_sleep_complete = MagicMock()

    ans = MagicMock()
    ans._state = AgentState.AWAKE
    triaged = MagicMock()
    triaged.high = []
    triaged.medium = []
    triaged.low = []
    ans.triage.return_value = triaged
    ans.config.sleep_phases.triage.priority_order = ("high", "medium", "low")
    runtime.ans = ans

    await run_consolidation_cycle(
        agent_id="agent_test",
        agent_dir=Path("."),
        runtime=runtime,
    )

    runtime.notify_sleep_complete.assert_not_called()
