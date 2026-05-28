"""Tests for outbound notification gate (final_summary flag)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from nls.agentic.outbound_notify import (
    OutboundNotifyGate,
    OutboundNotifyLedger,
    is_final_summary_requested,
    strip_outbound_control_args,
)


def test_is_final_summary_default_false():
    assert not is_final_summary_requested({})
    assert not is_final_summary_requested({"text": "hello"})


def test_is_final_summary_true():
    assert is_final_summary_requested({"final_summary": True})
    assert is_final_summary_requested({"final_summary": "true"})


def test_strip_control_args():
    args = {"phone": "+1", "text": "hi", "final_summary": True}
    assert strip_outbound_control_args(args) == {"phone": "+1", "text": "hi"}


def test_ledger_skips_duplicate_hash():
    with tempfile.TemporaryDirectory() as tmp:
        ledger = OutboundNotifyLedger(Path(tmp) / "outbound_notify.json")
        ledger.record("progress", "team_1", "whatsapp", "abc123")
        assert ledger.should_skip("progress", "team_1", "whatsapp", "abc123")
        assert not ledger.should_skip("progress", "team_1", "whatsapp", "different")


def test_milestone_passes_without_final_summary():
    """Any language, any wording — default sends are free."""
    with tempfile.TemporaryDirectory() as tmp:
        active_plan = MagicMock()
        active_plan.id = "plan_1"
        active_plan.status = "in_progress"
        active_plan.steps = []
        active_plan.pending_steps.return_value = [MagicMock(label="step-2")]

        team = MagicMock()
        team.id = "team_abc"
        team.status = "active"
        team.plan_id = "plan_1"
        team.wave_index = 1
        team.is_terminal = False
        team.members = []
        team.completed_at = 0
        team.created_at = 0

        ps = MagicMock()
        ps.find_active.return_value = active_plan

        tm = MagicMock()
        tm.list_teams.side_effect = lambda include_terminal=False: (
            [team] if not include_terminal else [team]
        )

        gate = OutboundNotifyGate(tmp, team_manager=tm, plan_store=ps)
        msg = gate.check(
            "whatsapp_send",
            {
                "text": (
                    "Welle 0 abgeschlossen! Starte Welle 1 — "
                    "Datenbankschema als Nächstes."
                ),
            },
        )
        assert msg is None


def test_final_summary_blocked_while_plan_active():
    with tempfile.TemporaryDirectory() as tmp:
        pending = MagicMock()
        pending.label = "Deploy to Railway"
        pending.id = "step-12"

        active_plan = MagicMock()
        active_plan.id = "plan_1"
        active_plan.status = "in_progress"
        active_plan.steps = [pending]
        active_plan.pending_steps.return_value = [pending]

        ps = MagicMock()
        ps.find_active.return_value = active_plan

        gate = OutboundNotifyGate(tmp, team_manager=None, plan_store=ps)
        msg = gate.check(
            "whatsapp_send",
            {
                "text": "Das Projekt ist fertig.",
                "final_summary": True,
            },
        )
        assert msg is not None
        assert "Blocked" in msg
        assert "pending step" in msg


def test_final_summary_allowed_after_cleanup():
    with tempfile.TemporaryDirectory() as tmp:
        ps = MagicMock()
        ps.find_active.return_value = None

        tm = MagicMock()
        tm.list_teams.return_value = []

        dm = MagicMock()
        dm.has_active_delegates.return_value = False

        gate = OutboundNotifyGate(
            tmp, team_manager=tm, plan_store=ps, delegate_manager=dm,
        )
        msg = gate.check(
            "whatsapp_send",
            {
                "text": "Alles erledigt — hier ist die Zusammenfassung.",
                "final_summary": True,
            },
        )
        assert msg is None


def test_final_summary_blocked_while_delegates_running():
    with tempfile.TemporaryDirectory() as tmp:
        ps = MagicMock()
        ps.find_active.return_value = None

        dm = MagicMock()
        dm.has_active_delegates.return_value = True

        gate = OutboundNotifyGate(
            tmp, team_manager=None, plan_store=ps, delegate_manager=dm,
        )
        msg = gate.check(
            "whatsapp_send",
            {"text": "Done!", "final_summary": True},
        )
        assert msg is not None
        assert "delegates are still running" in msg
