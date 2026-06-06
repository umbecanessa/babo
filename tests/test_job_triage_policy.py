"""Tests for solo-agent Job charter triage policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nls.agentic.goals import TurnTriage, _parse_triage_dict
from nls.agentic.job_triage_policy import (
    HINT_JOB_CHARTER,
    HINT_JOB_CONFIRM,
    apply_job_triage_policy,
    can_owner_apply_job_patch,
    execute_set_job,
    job_hint_active,
    job_loop_context_message,
    normalize_job_candidate,
    resolve_job_candidate,
)
from nls.runtime.job_trust import (
    clear_task_job_candidate,
    load_job,
    patch_job_fields,
    read_task_job_candidate,
)


def test_normalize_job_candidate_filters_keys():
    raw = {
        "title": "Mod",
        "mission": "Moderate chat",
        "persona": "Friendly",
        "playbook": "Warn first",
        "in_scope": ["moderation"],
        "out_of_scope": "spam",
        "extra": "ignored",
    }
    out = normalize_job_candidate(raw)
    assert out["title"] == "Mod"
    assert out["out_of_scope"] == ["spam"]
    assert "extra" not in out


def test_parse_triage_dict_job_candidate():
    triage = _parse_triage_dict({
        "intent": "TASK_THINK",
        "thinking": True,
        "profile": "solo_structured",
        "goals": ["Propose charter"],
        "hints": [HINT_JOB_CHARTER],
        "deferred": [],
        "job_candidate": {
            "title": "Research Assistant",
            "mission": "Track papers",
        },
    })
    assert triage.job_candidate["title"] == "Research Assistant"
    assert HINT_JOB_CHARTER in triage.hints


def test_apply_job_triage_policy_strips_for_squad_member(monkeypatch):
    monkeypatch.setattr(
        "nls.agentic.job_triage_policy.agent_in_squad",
        lambda _aid: True,
    )
    goals, hints, candidate = apply_job_triage_policy(
        [HINT_JOB_CHARTER, HINT_JOB_CONFIRM],
        ["Propose charter"],
        {"title": "Mod"},
        agent_id="agent-1",
    )
    assert hints == []
    assert candidate == {}


def test_job_hint_active():
    assert job_hint_active([HINT_JOB_CHARTER])
    assert job_hint_active([HINT_JOB_CONFIRM])
    assert not job_hint_active(["fleet:squad_candidate"])


def test_can_owner_apply_job_patch_blocks_channel_session():
    ok, msg = can_owner_apply_job_patch(
        session_key="telegram:group:-123",
        dispatch_source="user",
    )
    assert not ok
    assert "Home chat" in msg


def test_can_owner_apply_job_patch_allows_home():
    ok, _msg = can_owner_apply_job_patch(
        session_key="websocket:main",
        dispatch_source="user",
    )
    assert ok


def test_job_loop_context_message_for_charter():
    msg = job_loop_context_message(
        "solo-agent",
        [HINT_JOB_CHARTER],
        {"title": "Mod", "mission": "Moderate"},
    )
    assert msg is not None
    assert "set_job" in msg
    assert "Mod" in msg


def test_job_loop_context_message_confirm():
    msg = job_loop_context_message(
        "solo-agent",
        [HINT_JOB_CONFIRM],
        {"title": "Mod", "mission": "Moderate"},
    )
    assert msg is not None
    assert "owner_confirmed=true" in msg
    assert "Mod" in msg


@pytest.mark.asyncio
async def test_execute_set_job_requires_confirmation(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    result = await execute_set_job(
        agent_dir=agent_dir,
        agent_id="test-agent",
        args={"title": "Mod"},
        session_key="websocket:main",
        dispatch_source="user",
    )
    assert result.is_error
    assert "owner_confirmed" in result.content


@pytest.mark.asyncio
async def test_execute_set_job_persists(tmp_path: Path, monkeypatch):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    monkeypatch.setattr(
        "nls.runtime.job_trust.sync_runtime_job_trust_for_agent",
        lambda _aid: None,
    )
    result = await execute_set_job(
        agent_dir=agent_dir,
        agent_id="test-agent",
        args={
            "owner_confirmed": True,
            "title": "Discord Mod",
            "mission": "Keep the server friendly",
            "in_scope": ["moderation"],
        },
        session_key="websocket:main",
        dispatch_source="user",
    )
    assert not result.is_error
    job = load_job(agent_dir)
    assert job.title == "Discord Mod"
    assert job.in_scope == ["moderation"]
    payload = json.loads(result.content)
    assert payload["title"] == "Discord Mod"


def test_turn_triage_reconcile_job_charter(monkeypatch):
    monkeypatch.setattr(
        "nls.agentic.job_triage_policy.solo_agent_eligible_for_job_charter",
        lambda _aid: True,
    )
    triage = TurnTriage(
        hints=[HINT_JOB_CHARTER],
        goals=["Propose"],
        job_candidate={"title": "Helper"},
    )
    triage.reconcile_job_charter_hints(agent_id="solo-1")
    assert triage.job_candidate["title"] == "Helper"


def test_can_owner_apply_job_patch_blocks_non_main_websocket():
    ok, msg = can_owner_apply_job_patch(
        session_key="websocket:project-abc",
        dispatch_source="user",
    )
    assert not ok
    assert "websocket:main" in msg


def test_apply_job_triage_policy_strips_when_fleet_active():
    goals, hints, candidate = apply_job_triage_policy(
        ["fleet:squad_candidate", HINT_JOB_CHARTER],
        ["Staff squad"],
        {"title": "Mod"},
        agent_id="solo-1",
    )
    assert HINT_JOB_CHARTER not in hints
    assert candidate == {}


def test_boost_job_charter_continuation_on_yes():
    from nls.agentic.goals import TurnTriage
    from nls.agentic.job_triage_policy import boost_job_charter_continuation

    triage = TurnTriage(hints=[], goals=[])
    boost_job_charter_continuation(
        triage,
        "Yes, that looks good",
        history=[
            {
                "role": "assistant",
                "content": "Here is your proposed Job charter with in_scope ...",
            },
        ],
        agent_id="solo-1",
    )
    assert HINT_JOB_CONFIRM in triage.hints
    assert any("set_job" in g for g in triage.goals)


def test_patch_job_fields_merges(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    job = patch_job_fields(agent_dir, {
        "title": "First Title",
        "mission": "First mission",
    })
    assert job.title == "First Title"
    job2 = patch_job_fields(agent_dir, {"persona": "Calm"})
    assert job2.title == "First Title"
    assert job2.persona == "Calm"


class _FakeSlot:
    def __init__(self, domain: str, content: str) -> None:
        self.domain = domain
        self.content = content


class _FakeRing:
    def __init__(self, slots: list[_FakeSlot]) -> None:
        self._slots = slots

    def remove_by_domain(self, domain: str) -> int:
        before = len(self._slots)
        self._slots = [s for s in self._slots if s.domain != domain]
        return before - len(self._slots)


def test_read_and_clear_task_job_candidate():
    ring = _FakeRing([
        _FakeSlot(
            "Task.JobCandidate",
            '{"title":"Mod","mission":"Moderate"}',
        ),
    ])
    assert read_task_job_candidate(ring)["title"] == "Mod"
    assert clear_task_job_candidate(ring) == 1
    assert read_task_job_candidate(ring) == {}


def test_resolve_job_candidate_falls_back_to_wm():
    ring = _FakeRing([
        _FakeSlot(
            "Task.JobCandidate",
            '{"title":"Helper","mission":"Assist owner"}',
        ),
    ])
    resolved = resolve_job_candidate(
        {},
        hints=[HINT_JOB_CONFIRM],
        working_memory=ring,
    )
    assert resolved["title"] == "Helper"


@pytest.mark.asyncio
async def test_execute_set_job_merges_wm_and_clears(tmp_path: Path, monkeypatch):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    ring = _FakeRing([
        _FakeSlot(
            "Task.JobCandidate",
            '{"title":"Stored Title","mission":"Stored mission","persona":"Calm"}',
        ),
    ])

    monkeypatch.setattr(
        "nls.runtime.job_trust.runtime_working_memory",
        lambda _aid: ring,
    )
    cleared: list[bool] = []
    monkeypatch.setattr(
        "nls.runtime.job_trust.sync_runtime_job_trust_for_agent",
        lambda _aid: None,
    )
    def _clear(_aid: str) -> None:
        cleared.append(True)
        clear_task_job_candidate(ring)

    monkeypatch.setattr(
        "nls.runtime.job_trust.clear_task_job_candidate_for_agent",
        _clear,
    )

    result = await execute_set_job(
        agent_dir=agent_dir,
        agent_id="test-agent",
        args={"owner_confirmed": True, "title": "Owner Override"},
        session_key="websocket:main",
        dispatch_source="user",
    )
    assert not result.is_error
    job = load_job(agent_dir)
    assert job.title == "Owner Override"
    assert job.mission == "Stored mission"
    assert job.persona == "Calm"
    assert cleared == [True]
    assert read_task_job_candidate(ring) == {}
