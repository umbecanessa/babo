"""Tests for delegate hint delivery modes (ring vs both)."""

from __future__ import annotations

import asyncio

import pytest

from nls.agentic.delegate_manager import DelegateManager
from nls.agentic.orchestrator_hint import HINT_DELIVERY_RING


class _FakeSubCryptex:
    def __init__(self) -> None:
        self.boosted: list[str] = []
        self.directives: list[str] = []

    def upsert_orchestrator_directive(self, message: str, **kwargs) -> bool:
        self.directives.append(message)
        return True

    def boost_priority(self, ring_id: str, boost: float) -> bool:
        self.boosted.append(ring_id)
        return True


@pytest.mark.asyncio
async def test_hint_delivery_both_enqueues_chat():
    dm = DelegateManager()
    sc = _FakeSubCryptex()
    ds = dm._delegates[0] = type("DS", (), {})()
    ds.state = "running"
    ds.sub_cryptex = sc
    ds.hint_queue = asyncio.Queue()

    ok = await dm.hint(0, "Use edit() not write()", delivery="both")
    assert ok is True
    assert sc.directives == ["Use edit() not write()"]
    chat = ds.hint_queue.get_nowait()
    assert chat["content"].startswith("[ORCHESTRATOR HINT]")
    assert "edit()" in chat["content"]


@pytest.mark.asyncio
async def test_hint_delivery_ring_skips_chat():
    dm = DelegateManager()
    sc = _FakeSubCryptex()
    ds = dm._delegates[0] = type("DS", (), {})()
    ds.state = "running"
    ds.sub_cryptex = sc
    ds.hint_queue = asyncio.Queue()

    ok = await dm.hint(0, "Quiet nudge", delivery=HINT_DELIVERY_RING)
    assert ok is True
    assert sc.directives == ["Quiet nudge"]
    assert ds.hint_queue.empty()


@pytest.mark.asyncio
async def test_intervene_hint_both_enqueues_decision_with_delivery():
    dm = DelegateManager()
    sc = _FakeSubCryptex()
    ds = dm._delegates[1] = type("DS", (), {})()
    ds.state = "running"
    ds.sub_cryptex = sc
    ds.hint_queue = asyncio.Queue()

    ok = await dm.intervene(
        1, action="hint", message="Try list_dir first", delivery="both",
    )
    assert ok is True
    decision = ds.hint_queue.get_nowait()
    assert decision["action"] == "hint"
    assert decision["delivery"] == "both"
    assert ds.hint_queue.empty()
