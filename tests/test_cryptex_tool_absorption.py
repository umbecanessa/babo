"""Tests for shared Cryptex / SubCryptex tool absorption."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import MagicMock

from nls.brain.cryptex import DEFAULT_PROJECT, RING_PROJECT, RingSpec, WMRing
from nls.brain.cryptex_tool_absorption import (
    CRYPTEX_TRIGGER_SPECS,
    FOCUS_RECOVERY,
    FOCUS_VERIFICATION,
    FOCUS_WAVE,
    TOOL_CRYPTEX_TRIGGERS,
    absorb_orchestrator_tool_result,
    absorb_wake_attention_content,
    absorb_wave_review_outcome,
    absorb_file_and_exec_result,
)


def _make_test_cryptex(project: str = DEFAULT_PROJECT) -> SimpleNamespace:
    ring_ids = (
        "instructions",
        "orchestration",
        "wake_attention",
        "project_facts",
        "tactical_goals",
        "strategic_goals",
        "channels",
        "environment",
        "skills",
        "tools_mcp",
    )
    rings: dict[str, WMRing] = {}
    for rid in ring_ids:
        ring = WMRing(RingSpec(rid, RING_PROJECT, rid))
        ring.rotate(project)
        rings[rid] = ring
    return SimpleNamespace(
        _rings=rings,
        _active_project=project,
        active_project=project,
    )


def test_trigger_specs_and_map_present():
    assert len(CRYPTEX_TRIGGER_SPECS) >= 20
    assert "team" in TOOL_CRYPTEX_TRIGGERS
    assert "communicate" in TOOL_CRYPTEX_TRIGGERS
    assert "skill_install" in str(TOOL_CRYPTEX_TRIGGERS)


def test_skill_install_absorption():
    cryptex = _make_test_cryptex()
    absorb_orchestrator_tool_result(
        cryptex,
        "skill_install",
        {"source_path": "discord-channel", "name": "discord-channel"},
        "Installed and loaded NLS skill 'discord-channel'",
        False,
    )
    skills = cryptex._rings["skills"].get_active_slots()
    assert any(s.domain == "Skill:discord-channel" for s in skills)
    assert any("discord-channel" in s.content for s in skills)


def test_team_approve_rotates_verification_focus():
    cryptex = _make_test_cryptex()
    absorb_orchestrator_tool_result(
        cryptex,
        "team",
        {"action": "intervene", "decision": "approve", "team_id": "team_x"},
        "Approved delegate #7",
        False,
    )
    instr = cryptex._rings["instructions"]
    assert instr.active_position == FOCUS_VERIFICATION
    assert any(s.domain == "Action:verification" for s in instr.get_active_slots())


def test_team_launch_rotates_wave_focus():
    cryptex = _make_test_cryptex()
    absorb_orchestrator_tool_result(
        cryptex,
        "team",
        {"action": "launch", "team_id": "team_x"},
        "Launched",
        False,
    )
    assert cryptex._rings["orchestration"].active_position == FOCUS_WAVE


def test_team_advance_ok_returns_instructions_to_project():
    cryptex = _make_test_cryptex()
    cryptex._rings["instructions"].rotate(FOCUS_VERIFICATION)
    absorb_orchestrator_tool_result(
        cryptex,
        "team",
        {"action": "advance", "team_id": "team_x"},
        "Advanced",
        False,
    )
    assert cryptex._rings["instructions"].active_position == DEFAULT_PROJECT
    assert cryptex._rings["orchestration"].active_position == DEFAULT_PROJECT


def test_await_delegates_returns_to_project():
    cryptex = _make_test_cryptex()
    cryptex._rings["orchestration"].rotate(FOCUS_WAVE)
    absorb_orchestrator_tool_result(
        cryptex,
        "await_delegates",
        {"summary": "Wave 3 running"},
        "Turn ended",
        False,
    )
    assert cryptex._rings["orchestration"].active_position == DEFAULT_PROJECT


def test_plan_accept_partial_enters_recovery_focus():
    cryptex = _make_test_cryptex()
    absorb_orchestrator_tool_result(
        cryptex,
        "plan",
        {"action": "accept_partial", "plan_id": "plan_1"},
        "Accepted partial",
        False,
    )
    assert cryptex._rings["orchestration"].active_position == FOCUS_RECOVERY
    assert cryptex._rings["instructions"].active_position == FOCUS_RECOVERY


def test_grep_skipped_for_em_with_active_delegates():
    cryptex = _make_test_cryptex()
    absorb_orchestrator_tool_result(
        cryptex,
        "grep",
        {"pattern": "foo"},
        "many lines of matches",
        False,
        details={
            "coordinator_mode": True,
            "delegates_active": True,
            "active_mode": "monitoring",
        },
    )
    assert cryptex._rings["project_facts"].get_active_slots() == []


def test_grep_thin_line_when_em_evaluating():
    cryptex = _make_test_cryptex()
    absorb_orchestrator_tool_result(
        cryptex,
        "grep",
        {"pattern": "foo"},
        "match one",
        False,
        details={
            "coordinator_mode": True,
            "delegates_active": False,
            "active_mode": "evaluating",
        },
    )
    slots = cryptex._rings["project_facts"].get_active_slots()
    assert len(slots) == 1
    assert "grep" in slots[0].content


def test_communicate_updates_channels_and_stakeholder_focus():
    cryptex = _make_test_cryptex()
    absorb_orchestrator_tool_result(
        cryptex,
        "communicate",
        {"message": "Wave 2 started"},
        "sent",
        False,
    )
    ch = cryptex._rings["channels"]
    assert any("communicate" in s.content for s in ch.positions.get("in_app", []))
    assert cryptex._rings["orchestration"].active_position == "focus:stakeholder"


def test_communicate_keeps_wave_focus_while_delegates_run():
    cryptex = _make_test_cryptex()
    cryptex._rings["orchestration"].rotate(FOCUS_WAVE)
    absorb_orchestrator_tool_result(
        cryptex,
        "communicate",
        {"message": "Status"},
        "sent",
        False,
        details={
            "coordinator_mode": True,
            "delegates_active": True,
            "active_mode": "monitoring",
        },
    )
    assert cryptex._rings["orchestration"].active_position == FOCUS_WAVE


def test_plan_complete_updates_strategic_goals():
    cryptex = _make_test_cryptex()
    absorb_orchestrator_tool_result(
        cryptex,
        "plan",
        {"action": "complete", "plan_id": "plan_abc"},
        "Plan complete",
        False,
    )
    slots = cryptex._rings["strategic_goals"].get_active_slots()
    assert any("complete" in s.domain for s in slots)


@dataclass
class _Member:
    step_id: str
    status: str


@dataclass
class _Team:
    id: str = "team_1"
    plan_id: str = "plan_1"
    name: str = "Wave 1"
    members: list[_Member] = field(default_factory=list)

    def compute_outcome(self) -> str:
        if all(m.status == "done" for m in self.members):
            return "completed"
        return "partial"


def test_wave_review_healthy_focuses_verification():
    cryptex = _make_test_cryptex()
    team = _Team(members=[_Member("step-1", "done"), _Member("step-2", "done")])
    absorb_wave_review_outcome(cryptex, team)
    assert cryptex._rings["instructions"].active_position == FOCUS_VERIFICATION
    wake = cryptex._rings["wake_attention"].get_active_slots()
    assert wake and "WAVE COMPLETE" in wake[0].content


def test_wave_review_failed_focuses_recovery():
    cryptex = _make_test_cryptex()
    team = _Team(members=[_Member("step-1", "done"), _Member("step-2", "failed")])
    absorb_wave_review_outcome(cryptex, team)
    assert cryptex._rings["instructions"].active_position == FOCUS_RECOVERY
    wake = cryptex._rings["wake_attention"].get_active_slots()
    assert wake and "PLAN RECOVERY" in wake[0].content


def test_wake_attention_content_completion_review():
    cryptex = _make_test_cryptex()
    absorb_wake_attention_content(
        cryptex,
        "[COMPLETION REVIEW — BATCH — Wave QA]\nDelegate #9 waiting",
    )
    assert cryptex._rings["instructions"].active_position == FOCUS_VERIFICATION


def test_file_write_one_liner_orchestrator():
    cryptex = _make_test_cryptex()
    absorb_file_and_exec_result(
        cryptex,
        "write",
        {"path": "backend/main.py"},
        "ok",
        False,
        depth="orchestrator",
        ctx={"coordinator_mode": True, "delegates_active": False},
    )
    slots = cryptex._rings["project_facts"].get_active_slots()
    assert len(slots) == 1
    assert "backend/main.py" in slots[0].content


def test_mock_bridge_compat_team_approve():
    ring = MagicMock()
    cryptex = MagicMock()
    cryptex._rings = {"instructions": ring, "wake_attention": MagicMock()}

    absorb_orchestrator_tool_result(
        cryptex,
        "team",
        {"action": "intervene", "decision": "approve", "team_id": "team_x"},
        "Approved delegate #7",
        False,
    )

    assert ring.upsert_slot.called
    assert ring.rotate.called
