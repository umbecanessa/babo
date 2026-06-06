"""Tests for plan ↔ working-memory sync and pending-wave breadcrumbs."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from nls.agentic.plan_store import Plan, PlanAudit, PlanStep, PlanStore
from nls.agentic.plan_wm_sync import (
    SYNC_MODE_REFRESH,
    apply_plan_wm_sync,
    audit_issue_is_stale,
    build_pending_teams_breadcrumb,
    prune_stale_audit_issues,
    reconcile_plan_status,
)
from nls.agentic.team_manager import Team, TeamMember


class _FakeRing:
    def __init__(self) -> None:
        self.positions: dict[str, list] = {"default": []}

    def upsert_slot(self, **kwargs) -> None:
        domain = kwargs.get("domain", "")
        self.positions["default"] = [
            s for s in self.positions["default"] if s.domain != domain
        ]
        self.positions["default"].append(_FakeSlot(**kwargs))


@dataclass
class _FakeSlot:
    domain: str = ""
    content: str = ""
    slot_type: str = "fact"
    salience: float = 0.9
    source: str = ""
    level: str = ""
    access: str = "malleable"


class _FakeWM:
    def __init__(self) -> None:
        self._rings = {
            "consolidation": _FakeRing(),
            "orchestration": _FakeRing(),
            "instructions": _FakeRing(),
            "project_facts": _FakeRing(),
            "tactical_goals": _FakeRing(),
        }
        self.plan_position = ""
        self.orch_slots: dict[str, str] = {}
        self.facts: dict[str, str] = {}
        self.saved = False

    def set_plan_position(self, position: str) -> None:
        self.plan_position = position

    def upsert_orchestration_slot(
        self, domain: str, content: str, **kwargs,
    ) -> None:
        self.orch_slots[domain] = content
        self._rings["orchestration"].upsert_slot(
            domain=domain, content=content, **kwargs,
        )

    def upsert_fact(
        self, domain: str, content: str, **kwargs,
    ) -> None:
        self.facts[domain] = content

    def remove_by_domain(self, domain: str) -> int:
        n = 0
        for ring in self._rings.values():
            before = len(ring.positions["default"])
            ring.positions["default"] = [
                s for s in ring.positions["default"] if s.domain != domain
            ]
            n += before - len(ring.positions["default"])
        self.orch_slots.pop(domain, None)
        self.facts.pop(domain, None)
        return n

    def remove_goals_where(self, predicate) -> list:
        ring = self._rings["tactical_goals"]
        removed = []
        kept = []
        for slot in ring.positions["default"]:
            if slot.slot_type == "goal" and predicate(slot):
                removed.append(slot)
            else:
                kept.append(slot)
        ring.positions["default"] = kept
        return removed

    def save(self, agent_dir) -> None:
        self.saved = True


class _PlainWM:
    """Minimal WM without Cryptex rings — upsert_fact fallback."""

    def __init__(self) -> None:
        self.plan_position = ""
        self.facts: dict[str, str] = {}

    def set_plan_position(self, position: str) -> None:
        self.plan_position = position

    def upsert_fact(self, domain: str, content: str, **kwargs) -> None:
        self.facts[domain] = content

    def remove_by_domain(self, domain: str) -> int:
        if domain in self.facts:
            del self.facts[domain]
            return 1
        return 0


class _FakeTeamManager:
    def __init__(self, teams: list[Team]) -> None:
        self._teams = {t.id: t for t in teams}

    def discover_unlaunched_wave_teams(self) -> list[Team]:
        return [
            t for t in self._teams.values()
            if t.status == "created" and not t.batch_id
        ]


def test_audit_issue_is_stale_for_done_step():
    plan = Plan(
        id="plan_a",
        title="Test",
        steps=[
            PlanStep(id="step-8", label="Frontend page", status="done"),
        ],
    )
    issue = "Step not done: [step-8] Frontend page missing routes"
    assert audit_issue_is_stale(issue, plan)


def test_prune_stale_audit_issues():
    plan = Plan(
        id="plan_a",
        title="Test",
        status="blocked",
        audit=PlanAudit(
            issues=[
                "Step not done: [step-8] Frontend page",
                "Real blocker: acceptance criteria #3",
            ],
            all_criteria_met=False,
        ),
        steps=[
            PlanStep(id="step-8", label="Frontend page", status="done"),
            PlanStep(id="step-9", label="Deploy", status="pending"),
        ],
    )
    removed = prune_stale_audit_issues(plan)
    assert removed == 1
    assert len(plan.audit.issues) == 1
    assert "Real blocker" in plan.audit.issues[0]


def test_reconcile_plan_status_unblocks_when_recovery_cleared():
    plan = Plan(
        id="plan_a",
        title="Test",
        status="blocked",
        steps=[
            PlanStep(id="step-1", label="A", status="done"),
            PlanStep(id="step-2", label="B", status="pending"),
        ],
    )
    assert reconcile_plan_status(plan, team_manager=None) is True
    assert plan.status == "in_progress"


def test_build_pending_teams_breadcrumb():
    team = Team(
        id="team_abc",
        name="Wave 5",
        plan_id="plan_x",
        wave_index=4,
        status="created",
        members=[
            TeamMember(step_id="step-9", task="Completion page in Polish"),
        ],
    )
    tm = _FakeTeamManager([team])
    bc = build_pending_teams_breadcrumb(tm, plan_id="plan_x")
    assert "[WAVES READY — LAUNCH REQUIRED]" in bc
    assert "team_abc" in bc
    assert "team(action='launch'" in bc
    assert "Completion page" in bc


def test_apply_plan_wm_sync_updates_position_and_breadcrumb(tmp_path):
    store = PlanStore(tmp_path / ".plans")
    plan = Plan(
        id="plan_sync",
        title="ICF Platform",
        status="blocked",
        audit=PlanAudit(
            issues=["Step not done: [step-8] partial"],
            all_criteria_met=False,
        ),
        steps=[
            PlanStep(id="step-8", label="Frontend", status="done"),
            PlanStep(id="step-9", label="Completion page", status="pending"),
        ],
    )
    store.save(plan)

    team = Team(
        id="team_9332",
        name="Wave 5",
        plan_id="plan_sync",
        wave_index=4,
        status="created",
        members=[TeamMember(step_id="step-9", task="Completion page")],
    )
    wm = _FakeWM()

    apply_plan_wm_sync(
        plan,
        wm=wm,
        team_manager=_FakeTeamManager([team]),
        plan_store=store,
        agent_dir=tmp_path,
    )

    assert plan.status == "in_progress"
    assert "[PLAN POSITION" in wm.plan_position
    assert "orch.pending_wave_launch" in wm.orch_slots
    assert "team_9332" in wm.orch_slots["orch.pending_wave_launch"]
    assert wm.saved is True

    reloaded = store.load("plan_sync")
    assert reloaded.status == "in_progress"
    assert len(reloaded.audit.issues) == 0


def test_apply_plan_wm_sync_clears_breadcrumb_when_plan_done():
    team = Team(
        id="team_abc",
        name="Wave 5",
        plan_id="plan_done",
        wave_index=4,
        status="created",
        members=[TeamMember(step_id="step-9", task="Completion page")],
    )
    wm = _FakeWM()
    wm.orch_slots["orch.pending_wave_launch"] = "stale breadcrumb"

    plan = Plan(
        id="plan_done",
        title="Done plan",
        status="done",
        steps=[PlanStep(id="step-9", label="Completion", status="done")],
    )

    apply_plan_wm_sync(
        plan,
        wm=wm,
        team_manager=_FakeTeamManager([team]),
        persist=False,
    )

    assert "orch.pending_wave_launch" not in wm.orch_slots


def test_refresh_mode_does_not_mutate_plan_audit(tmp_path):
    store = PlanStore(tmp_path / ".plans")
    plan = Plan(
        id="plan_r",
        title="Refresh",
        status="blocked",
        audit=PlanAudit(
            issues=["Step not done: [step-8] partial"],
            all_criteria_met=False,
        ),
        steps=[
            PlanStep(id="step-8", label="Frontend", status="done"),
            PlanStep(id="step-9", label="Next", status="pending"),
        ],
    )
    store.save(plan)
    wm = _FakeWM()

    apply_plan_wm_sync(
        plan,
        wm=wm,
        plan_store=store,
        mode=SYNC_MODE_REFRESH,
        persist=False,
    )

    assert plan.status == "blocked"
    assert len(plan.audit.issues) == 1
    reloaded = store.load("plan_r")
    assert reloaded.status == "blocked"


def test_plain_wm_uses_upsert_fact_for_breadcrumb():
    team = Team(
        id="team_plain",
        name="Wave 1",
        plan_id="plan_p",
        wave_index=0,
        status="created",
        members=[TeamMember(step_id="step-1", task="Build UI")],
    )
    wm = _PlainWM()
    plan = Plan(
        id="plan_p",
        title="Plain",
        status="in_progress",
        steps=[PlanStep(id="step-1", label="Build UI", status="pending")],
    )

    apply_plan_wm_sync(
        plan,
        wm=wm,
        team_manager=_FakeTeamManager([team]),
        persist=False,
    )

    assert "[PLAN POSITION" in wm.plan_position
    assert "orch.pending_wave_launch" in wm.facts
    assert "team_plain" in wm.facts["orch.pending_wave_launch"]


def test_scrubs_compaction_and_stale_consolidation():
    wm = _FakeWM()
    wm._rings["consolidation"].positions["default"] = [
        _FakeSlot(domain="CompactionPending", content="sessions/foo empty"),
        _FakeSlot(domain="Consolidation.DayNarrative", content="Progress 3/9 done"),
        _FakeSlot(domain="Consolidation.TaskContext", content="old 3/11 context"),
    ]
    wm._rings["tactical_goals"].positions["default"] = [
        _FakeSlot(
            domain="Goal.Tactical.Fix_backend",
            content="Fix step-8 backend routes",
            slot_type="goal",
            level="tactical",
        ),
    ]
    plan = Plan(
        id="plan_scrub",
        title="Scrub test",
        steps=[
            PlanStep(id=f"step-{i}", label=f"S{i}", status="done")
            for i in range(1, 9)
        ] + [PlanStep(id="step-9", label="S9", status="pending")],
    )

    apply_plan_wm_sync(plan, wm=wm, persist=False)

    consol_domains = {
        s.domain for s in wm._rings["consolidation"].positions["default"]
    }
    assert "CompactionPending" not in consol_domains
    assert "Consolidation.DayNarrative" not in consol_domains
    assert "Consolidation.TaskContext" in consol_domains
    tactical = wm._rings["tactical_goals"].positions["default"]
    assert not any("step-8" in (s.content or "") for s in tactical)
