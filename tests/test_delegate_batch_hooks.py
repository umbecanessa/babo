"""Runtime batch-complete hook wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

from server.services.delegate_batch_hooks import (
    chain_batch_complete_callback,
    wire_runtime_batch_complete,
)


def test_wire_runtime_batch_complete_is_idempotent():
    dm = MagicMock()
    dm._on_batch_complete = None
    dm._batch_complete_wired = False

    wire_runtime_batch_complete(dm, "agent-1")
    first_cb = dm._on_batch_complete
    assert first_cb is not None
    assert dm._batch_complete_wired is True

    wire_runtime_batch_complete(dm, "agent-1")
    assert dm._on_batch_complete is first_cb


def test_chain_batch_complete_preserves_prior_handler():
    dm = MagicMock()

    async def _prev(_bid, _results):
        return None

    async def _new(_bid, _results):
        return None

    dm._on_batch_complete = _prev
    chain_batch_complete_callback(dm, _new)
    assert dm._on_batch_complete is not _prev
    assert dm._on_batch_complete is not _new
