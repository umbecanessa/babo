"""Stale surface inbox pruning."""

from __future__ import annotations

import time

from nls.runtime.surface_inbox import (
    clear_agent_inbox,
    pending_count,
    prune_stale_surface_inbox,
    record_surface_inbound,
)


def setup_function():
    clear_agent_inbox("lead-1")


def test_prune_stale_unhandled_items(monkeypatch):
    item = record_surface_inbound(
        "lead-1",
        session_key="telegram:group:-100",
        channel="telegram",
        sender_name="Owner",
        content="Hey babo",
    )
    item.received_at = time.time() - (86400 * 3)
    assert pending_count("lead-1") == 1

    import nls.runtime.surface_inbox as si

    monkeypatch.setattr(si, "_STALE_UNHANDLED_SECONDS", 86400.0)
    pruned = prune_stale_surface_inbox("lead-1")
    assert pruned == 1
    assert pending_count("lead-1") == 0
