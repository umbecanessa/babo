"""Orchestration-aware sleep — preserve squad/delegate WM through sleep cycles."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nls.brain.working_memory import WorkingMemory
from nls.runtime.orchestration_sleep_policy import should_preserve_orchestration_on_sleep


def test_should_preserve_when_team_manager_has_active_orchestration():
    rt = MagicMock()
    tm = MagicMock()
    tm.has_active_orchestration.return_value = True
    rt._team_manager = tm
    assert should_preserve_orchestration_on_sleep(rt) is True


def test_should_preserve_when_delegate_manager_has_active_delegates():
    rt = MagicMock()
    tm = MagicMock()
    tm.has_active_orchestration.return_value = False
    dm = MagicMock()
    dm.has_active_delegates.return_value = True
    rt._team_manager = tm
    rt.delegate_manager = dm
    assert should_preserve_orchestration_on_sleep(rt) is True


def test_should_not_preserve_when_idle():
    rt = MagicMock()
    tm = MagicMock()
    tm.has_active_orchestration.return_value = False
    dm = MagicMock()
    dm.has_active_delegates.return_value = False
    rt._team_manager = tm
    rt.delegate_manager = dm
    assert should_preserve_orchestration_on_sleep(rt) is False


def test_working_memory_on_sleep_preserves_orchestration_roster():
    wm = WorkingMemory()
    wm.orch_update_team(
        "team-1",
        plan_id="plan-a",
        status="running",
        members=[{"index": 0, "task_summary": "Investigate bug", "status": "running"}],
    )
    assert wm._orch_teams

    wm.on_sleep(preserve_orchestration=True)

    assert "team-1" in wm._orch_teams
    assert wm._orch_teams["team-1"].status == "running"


def test_working_memory_on_sleep_clears_orchestration_when_idle():
    wm = WorkingMemory()
    wm.orch_update_team(
        "team-1",
        plan_id="plan-a",
        status="running",
        members=[{"index": 0, "task_summary": "Investigate bug", "status": "running"}],
    )

    wm.on_sleep(preserve_orchestration=False)

    assert not wm._orch_teams


def test_cryptex_on_sleep_preserves_orchestration_ring():
    from nls.brain.cryptex import CryptexMemory, RING_ORCHESTRATION
    from nls.brain.working_memory import WMSlot

    cx = CryptexMemory()
    ring = cx._rings[RING_ORCHESTRATION]
    ring.upsert_slot(
        domain="orchestration",
        content="Lead squad wave 2 — members #1 and #2 running",
        slot_type="fact",
        salience=1.0,
        source="system",
    )
    view = cx._get_professional_view()
    view.orch_update_team(
        "team-1",
        status="running",
        members=[{"index": 0, "task_summary": "QA pass", "status": "running"}],
    )

    cx.on_sleep(preserve_orchestration=True)

    assert view._orch_teams
    slots = ring.positions.get(cx._active_project, [])
    assert any("Lead squad" in s.content for s in slots)


def test_cryptex_on_sleep_clears_orchestration_when_idle():
    from nls.brain.cryptex import CryptexMemory, RING_ORCHESTRATION

    cx = CryptexMemory()
    ring = cx._rings[RING_ORCHESTRATION]
    ring.upsert_slot(
        domain="orchestration",
        content="Temporary orchestration note",
        slot_type="fact",
        salience=1.0,
        source="system",
    )
    view = cx._get_professional_view()
    view.orch_update_team("team-1", status="running")

    cx.on_sleep(preserve_orchestration=False)

    assert not view._orch_teams
    slots = ring.positions.get(cx._active_project, [])
    assert not any("Temporary orchestration" in s.content for s in slots)
