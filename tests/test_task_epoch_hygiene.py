"""Task-epoch WM hygiene — scalable lifecycle rules."""

from __future__ import annotations

from nls.agentic.task_epoch_hygiene import (
    apply_goal_evaluation_to_wm,
    begin_task_epoch,
    clear_session_ephemeral_slots,
    goals_same_task_epoch,
    is_fresh_task_dispatch,
    is_session_ephemeral_slot,
    reconcile_goals_with_hints,
    research_domain_key,
    should_begin_task_epoch,
)
from nls.brain.working_memory import ACCESS_SESSION, ACCESS_SYSTEM, WMSlot


def test_fresh_task_dispatch_includes_channel():
    assert is_fresh_task_dispatch("user")
    assert is_fresh_task_dispatch("user:channel")
    assert not is_fresh_task_dispatch("team_wave_complete:abc")
    assert not is_fresh_task_dispatch("scheduler:job1")


def test_orchestration_wake_skips_epoch():
    assert not should_begin_task_epoch(
        "team_wave_complete:team-1",
        ["Build API"],
        ["Search docs"],
    )


def test_continuation_same_goals_skips_epoch():
    prior = ["Search ClawHub for Discord skills"]
    new = ["Search ClawHub for existing Discord skills"]
    assert goals_same_task_epoch(prior, new)
    assert not should_begin_task_epoch("user", new, prior)


def test_new_goals_begin_epoch():
    assert should_begin_task_epoch(
        "user",
        ["Scaffold native discord-channel skill"],
        ["Search ClawHub for Discord skills"],
    )


def test_empty_follow_up_on_channel_skips_epoch():
    assert not should_begin_task_epoch(
        "user:channel",
        [],
        ["Scaffold native skill"],
    )


def test_empty_goals_never_begin_epoch():
    assert not should_begin_task_epoch("user", [], None)
    assert not should_begin_task_epoch("user:channel", [], ["Prior task goal"])


def test_apply_goal_evaluation_only_newly_done():
    calls: list[str] = []

    class _Hooks:
        def wm_mark_task_goal_done(self, goal: str) -> bool:
            calls.append(f"done:{goal}")
            return True

        def wm_prune_supporting_facts_for_goal(self, goal: str) -> int:
            calls.append(f"prune:{goal}")
            return 0

    goals = ["Search ClawHub", "Scaffold skill"]
    apply_goal_evaluation_to_wm(
        _Hooks(),
        goals,
        pending_indices=[1],
        previous_pending=[0, 1],
    )
    assert calls == ["done:Search ClawHub", "prune:Search ClawHub"]
    calls.clear()
    apply_goal_evaluation_to_wm(
        _Hooks(),
        goals,
        pending_indices=[1],
        previous_pending=[1],
    )
    assert calls == []


def test_session_ephemeral_slot_detection():
    assert is_session_ephemeral_slot(WMSlot(
        slot_type="fact",
        content="read SKILL.md",
        domain="Skill.discord",
        source="clawhub",
        access=ACCESS_SESSION,
    ))
    assert not is_session_ephemeral_slot(WMSlot(
        slot_type="credential",
        content="token",
        domain="Project.Credential.GitHub",
        source="user",
    ))
    assert not is_session_ephemeral_slot(WMSlot(
        slot_type="behavioral",
        content="rules",
        domain="native_skill_authoring",
        access=ACCESS_SYSTEM,
    ))


def test_reconcile_setup_hint_demotes_exploratory_only_goal():
    goals = reconcile_goals_with_hints(
        ["Search ClawHub for existing Discord skills"],
        ["setup:native_skill"],
    )
    assert len(goals) == 1
    assert "Scaffold" in goals[0]


def test_reconcile_keeps_deliverable_over_exploratory():
    goals = reconcile_goals_with_hints(
        [
            "Search ClawHub for Discord skills",
            "Scaffold discord-channel native plugin",
        ],
        ["setup:native_skill"],
    )
    assert goals[0].startswith("Scaffold")
    assert not any("Search ClawHub" in g for g in goals)


def test_research_domain_key_supersedes_by_url():
    args = {"url": "https://example.com/a"}
    k1 = research_domain_key("web_fetch", args)
    k2 = research_domain_key("web_fetch", args)
    assert k1 == k2
    assert k1.startswith("Research:web_fetch:")


class _Ring:
    def __init__(self, slots):
        self.positions = {"default": list(slots)}


class _Cryptex:
    def __init__(self, slots):
        self._rings = {"project_facts": _Ring(slots)}


def test_clear_session_ephemeral_slots():
    slots = [
        WMSlot(
            slot_type="fact",
            content="fetch ok",
            domain="Research:web_fetch:abc",
            source="tool",
            access=ACCESS_SESSION,
        ),
        WMSlot(
            slot_type="fact",
            content="permanent",
            domain="Project.Name",
            source="user",
        ),
    ]
    cryptex = _Cryptex(slots)
    n = clear_session_ephemeral_slots(cryptex, None)
    assert n == 1
    assert len(cryptex._rings["project_facts"].positions["default"]) == 1
