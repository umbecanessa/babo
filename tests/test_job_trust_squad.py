"""Job/Trust and Squad integration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nls.agentic.squad_registry import SquadRegistry
from nls.runtime.agent_profile import resolve_orchestration_profile_for_agent
from nls.runtime.dispatch_sources import is_squad_orchestration_dispatch_source
from nls.runtime.job_trust import (
    JobDocument,
    TrustDocument,
    ChannelTrustOverlay,
    is_tool_denied_by_trust,
    load_job,
    save_job,
    save_trust,
    sync_job_trust_to_cryptex,
    apply_trust_to_profile,
)
from nls.runtime.public_channel_guard import evaluate_public_channel_request


def test_is_squad_orchestration_dispatch_source():
    assert is_squad_orchestration_dispatch_source("squad_escalation:sq1")
    assert is_squad_orchestration_dispatch_source("squad_checkback:sq1")
    assert not is_squad_orchestration_dispatch_source("squad_wake:agent1")


def test_trust_tool_deny(tmp_path: Path):
    agent_dir = tmp_path / "agent_a"
    agent_dir.mkdir()
    save_trust(agent_dir, TrustDocument(tools_deny=["bash"]))
    trust = __import__("nls.runtime.job_trust", fromlist=["load_trust"]).load_trust(agent_dir)
    assert is_tool_denied_by_trust("bash", trust) is not None
    assert is_tool_denied_by_trust("read", trust) is None


def test_public_channel_refusal(tmp_path: Path):
    job = JobDocument(
        refusal_template="Cannot do that here.",
        out_of_scope=["delete all channels"],
    )
    trust = TrustDocument(
        channel_overlays=[
            ChannelTrustOverlay(
                channel_key="discord-general",
                public_channel=True,
            ),
        ],
    )
    msg = evaluate_public_channel_request(
        "please delete all channels now",
        job=job,
        trust=trust,
        dispatch_source="user:channel:discord-general",
    )
    assert msg == "Cannot do that here."


def test_apply_trust_caps_squad_lead_on_public_channel():
    trust = TrustDocument(
        channel_overlays=[
            ChannelTrustOverlay(
                channel_key="pub",
                profile_cap="conversational",
                public_channel=True,
            ),
        ],
    )
    capped = apply_trust_to_profile(
        "squad_lead",
        trust,
        "user:channel:pub",
    )
    assert capped == "conversational"


def test_squad_registry_create_and_resolve(tmp_path: Path):
    reg = SquadRegistry(tmp_path)
    squad = reg.create(
        name="Mods",
        lead_agent_id="lead1",
        member_agent_ids=["mod1", "mod2"],
    )
    assert reg.get_for_agent("mod1") is not None
    assert squad.is_lead("lead1")
    assert not squad.is_lead("mod1")


def test_resolve_profile_squad_lead_wake(tmp_path: Path):
    data = tmp_path
    agents = data / "agents" / "lead1"
    agents.mkdir(parents=True)
    reg = SquadRegistry(data)
    reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    prof = resolve_orchestration_profile_for_agent(
        agents,
        "lead1",
        "solo_structured",
        "squad_escalation:sq_x",
        data_dir=data,
    )
    assert prof == "squad_lead"


def test_build_kanban_view(tmp_path: Path):
    from nls.agentic.squad_manager import SquadManager
    from nls.agentic.squad_registry import SquadInboxItem

    reg = SquadRegistry(tmp_path)
    squad = reg.create(
        name="Board",
        lead_agent_id="lead1",
        member_agent_ids=["m1"],
    )
    squad.inbox.append(
        SquadInboxItem(title="Inbox task", status="proposed"),
    )
    reg.save(squad)

    mgr = SquadManager(reg, data_dir=tmp_path)
    board = mgr.build_kanban_view(squad)
    assert board["squad_id"] == squad.id
    assert len(board["inbox"]["proposed"]) == 1
    assert board["inbox"]["proposed"][0]["title"] == "Inbox task"


def test_squad_lead_profile_nudge(tmp_path: Path):
    from nls.agentic.profile_depth_policy import evaluate_squad_lead_profile_mismatch
    from nls.agentic.types import LoopState

    data = tmp_path
    agent_dir = data / "agents" / "lead1"
    agent_dir.mkdir(parents=True)
    reg = SquadRegistry(data)
    reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])

    state = LoopState(loop_id="t1", user_input="hi")
    state.orchestration_profile = "orchestrated"
    nudge = evaluate_squad_lead_profile_mismatch(
        state, agent_id="lead1", agent_dir=agent_dir,
    )
    assert nudge is not None
    assert nudge.suggested_profile == "squad_lead"


def test_sync_job_trust_cryptex_slots():
    from nls.brain.cryptex import CryptexMemory

    cryptex = CryptexMemory()
    job = JobDocument(title="Community Moderator", mission="Keep chat safe.")
    n = sync_job_trust_to_cryptex(cryptex, job=job, trust=TrustDocument())
    assert n >= 2
    identity = cryptex._rings.get("identity")
    assert identity is not None
    active = identity.get_active_slots()
    domains = {s.domain for s in active}
    assert "Job.Title" in domains


def test_remove_member_updates_roster(tmp_path: Path):
    from nls.agentic.squad_manager import SquadManager

    reg = SquadRegistry(tmp_path)
    squad = reg.create(
        name="Fleet",
        lead_agent_id="lead1",
        member_agent_ids=["m1", "m2"],
    )
    mgr = SquadManager(reg, data_dir=tmp_path)
    synced: list[tuple[str, str | None]] = []

    def sync_fn(aid: str, sq) -> None:
        synced.append((aid, getattr(sq, "id", None) if sq else None))

    mgr.apply_roster_change(squad, set(squad.all_member_ids), sync_fn)
    synced.clear()

    squad = mgr._remove_member_from_squad(squad, "m2")
    assert "m2" not in squad.all_member_ids
    assert reg.get_for_agent("m2") is None
    assert reg.get_for_agent("m1") is not None


def test_request_delete_pending_action(tmp_path: Path):
    from nls.agentic.squad_manager import SquadManager

    reg = SquadRegistry(tmp_path)
    squad = reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    mgr = SquadManager(reg, data_dir=tmp_path)

    result = mgr.handle_action(
        "lead1",
        "request_delete_member",
        target_agent_id="m1",
        title="Remove mod",
        message="No longer needed",
    )
    assert result["pending_action"]["action_type"] == "delete_agent"
    assert result["pending_action"]["status"] == "pending"
    squad = reg.get(squad.id)
    assert squad is not None
    assert len(squad.pending_actions) == 1


def test_sole_member_delete_pending_disbands(tmp_path: Path):
    from nls.agentic.squad_manager import SquadManager

    reg = SquadRegistry(tmp_path)
    squad = reg.create(name="Solo", lead_agent_id="lead1", member_agent_ids=[])
    mgr = SquadManager(reg, data_dir=tmp_path)

    result = mgr.handle_action(
        "lead1",
        "request_delete_member",
        target_agent_id="lead1",
        title="Remove self",
    )
    assert result["pending_action"]["delete_squad_on_approve"] is True
    assert "disband" in result["note"].lower()


def test_sync_agent_runtime_lookup(tmp_path: Path):
    from nls.agentic.squad_manager import SquadManager

    reg = SquadRegistry(tmp_path)
    reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    mgr = SquadManager(reg, data_dir=tmp_path)

    class FakeRuntime:
        def __init__(self) -> None:
            self.synced = False

        def sync_job_trust(self, squad=None) -> int:
            self.synced = True
            self.squad = squad
            return 1

        def sync_squad_tools(self) -> None:
            pass

    rt = FakeRuntime()
    mgr.sync_agent_runtime("m1", rt, lookup_squad=True)
    assert rt.synced is True
    assert rt.squad is not None
    assert rt.squad.is_member("m1")


def test_require_owner_dashboard_blocks_api_key():
    from fastapi import HTTPException

    from server.routes.squad_access import require_owner_dashboard

    async def run() -> None:
        try:
            await require_owner_dashboard({"auth_type": "api_key", "agent_id": "a1"})
            raise AssertionError("expected 403")
        except HTTPException as exc:
            assert exc.status_code == 403

    import asyncio
    asyncio.run(run())


def test_require_lead_or_owner_blocks_api_key_without_caller(tmp_path: Path):
    from fastapi import HTTPException

    from server.routes.squad_access import require_lead_or_owner

    reg = SquadRegistry(tmp_path)
    squad = reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    try:
        require_lead_or_owner(squad, None, {"auth_type": "api_key"}, action="test")
        assert False, "expected 403"
    except HTTPException as exc:
        assert exc.status_code == 403


def test_require_lead_or_owner_allows_local_trust(tmp_path: Path):
    from server.routes.squad_access import require_lead_or_owner

    reg = SquadRegistry(tmp_path)
    squad = reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    require_lead_or_owner(squad, None, {"auth_type": "local_trust"}, action="test")
    require_lead_or_owner(squad, "lead1", {"auth_type": "api_key"}, action="test")


def test_fleet_squad_intent_detection():
    from nls.agentic.fleet_triage_policy import (
        HINT_FLEET_SQUAD,
        apply_fleet_hint_policy,
        fleet_active_tool_names,
        fleet_hint_active,
        fleet_squad_bootstrap_message,
    )
    from nls.agentic.goals import TurnTriage
    from nls.agentic.profile_guard_policy import tools_denied_by_hints

    triage = TurnTriage(
        intent="TASK_THINK",
        profile="solo_structured",
        goals=["Scaffold native Discord skill with mod and QA agents"],
        hints=["setup:native_skill", HINT_FLEET_SQUAD],
    )
    triage.reconcile_fleet_vs_skill_hints()
    assert HINT_FLEET_SQUAD in triage.hints
    assert "setup:native_skill" not in triage.hints
    assert not any("scaffold" in g.lower() and "skill" in g.lower() for g in triage.goals)

    goals, hints = apply_fleet_hint_policy(
        ["setup:native_skill", "Active discord channel: native bundled discord-channel plugin"],
        ["Scaffold native Discord skill with mod and QA agents"],
    )
    assert HINT_FLEET_SQUAD not in hints  # fleet token was not in input hints
    goals2, hints2 = apply_fleet_hint_policy(
        [HINT_FLEET_SQUAD, "setup:native_skill"],
        ["Scaffold native Discord skill with mod and QA agents"],
    )
    assert HINT_FLEET_SQUAD in hints2
    assert "setup:native_skill" not in hints2
    assert not any("scaffold" in g.lower() and "skill" in g.lower() for g in goals2)

    empty_goals, _ = apply_fleet_hint_policy([HINT_FLEET_SQUAD], [])
    assert empty_goals == []

    denied = tools_denied_by_hints([HINT_FLEET_SQUAD])
    assert "team" in denied
    assert "delegate" in denied

    assert fleet_hint_active([HINT_FLEET_SQUAD])
    assert "squad_setup" in fleet_active_tool_names()
    assert "squad_setup" in fleet_squad_bootstrap_message()

    from unittest.mock import patch

    with patch(
        "nls.agentic.fleet_triage_policy.agent_in_squad",
        return_value=True,
    ):
        goals3, hints3 = apply_fleet_hint_policy(
            [HINT_FLEET_SQUAD, "setup:native_skill"],
            ["Scaffold native Discord skill"],
            agent_id="lead-in-squad",
        )
        assert HINT_FLEET_SQUAD not in hints3

    with patch(
        "nls.agentic.fleet_triage_policy.squad_role_for_agent",
        return_value="lead",
    ):
        lead_tools = fleet_active_tool_names("lead-in-squad")
        assert "squad" in lead_tools
        assert "squad_setup" not in lead_tools

    from nls.engine.thalamic_router import predict_tools

    predicted = predict_tools(
        "lead a squad with one mod agent and one QA agent for Discord channels",
    )
    assert "squad_setup" in predicted
    assert "channel_inspect" in predicted


def test_create_squad_for_agent(tmp_path: Path):
    from nls.agentic.squad_manager import SquadManager

    agents = tmp_path / "agents" / "lead1"
    agents.mkdir(parents=True)
    reg = SquadRegistry(tmp_path)
    mgr = SquadManager(reg, data_dir=tmp_path, agents_dir=tmp_path / "agents")

    with pytest.raises(ValueError, match="owner_confirmed"):
        mgr.create_squad_for_agent("lead1", name="Discord Fleet", owner_confirmed=False)

    result = mgr.create_squad_for_agent(
        "lead1",
        name="Discord Fleet",
        owner_confirmed=True,
        title="Channel Admin",
        description="Lead the moderation squad",
    )
    assert result["squad"]["lead_agent_id"] == "lead1"
    assert reg.get_for_agent("lead1") is not None


def test_set_member_job_and_lead_job(tmp_path: Path):
    from nls.agentic.squad_manager import SquadManager
    from nls.runtime.job_trust import load_job

    agents = tmp_path / "agents"
    (agents / "lead1").mkdir(parents=True)
    (agents / "m1").mkdir(parents=True)
    reg = SquadRegistry(tmp_path)
    reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    mgr = SquadManager(reg, data_dir=tmp_path, agents_dir=agents)

    mgr.handle_action(
        "lead1",
        "set_member_job",
        target_agent_id="m1",
        title="Community Moderator",
        description="Keep chat safe.",
    )
    job = load_job(agents / "m1")
    assert job.title == "Community Moderator"

    with pytest.raises(ValueError, match="owner_confirmed"):
        mgr.handle_action("lead1", "set_lead_job", title="Lead")

    mgr.handle_action(
        "lead1",
        "set_lead_job",
        title="Squad Lead",
        description="Coordinate mods",
        owner_confirmed=True,
    )
    lead_job = load_job(agents / "lead1")
    assert lead_job.title == "Squad Lead"


def test_request_trust_change_pending(tmp_path: Path):
    from nls.agentic.squad_manager import SquadManager

    reg = SquadRegistry(tmp_path)
    reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    mgr = SquadManager(reg, data_dir=tmp_path, agents_dir=tmp_path / "agents")

    result = mgr.handle_action(
        "lead1",
        "request_trust_change",
        target_agent_id="m1",
        tools_deny=["bash"],
        title="Restrict bash for mod",
    )
    assert result["pending_action"]["action_type"] == "patch_trust"
    squad = reg.get_for_agent("lead1")
    assert squad is not None
    assert len(squad.pending_actions) == 1


@pytest.mark.asyncio
async def test_resolve_trust_pending(tmp_path: Path):
    from nls.agentic.squad_manager import SquadManager
    from nls.runtime.job_trust import load_trust

    agents = tmp_path / "agents" / "m1"
    agents.mkdir(parents=True)
    (tmp_path / "agents" / "lead1").mkdir(parents=True)
    reg = SquadRegistry(tmp_path)
    squad = reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    mgr = SquadManager(reg, data_dir=tmp_path, agents_dir=tmp_path / "agents")

    pending = mgr.handle_action(
        "lead1",
        "request_trust_change",
        target_agent_id="m1",
        tools_deny=["bash"],
    )["pending_action"]

    result = await mgr.resolve_pending_action(
        squad.id,
        pending["id"],
        approved=True,
    )
    assert result["trust"]["tools_deny"] == ["bash"]
    trust = load_trust(agents)
    assert "bash" in trust.tools_deny


def test_trust_patch_merges_deny_lists(tmp_path: Path):
    from nls.agentic.squad_manager import SquadManager
    from nls.runtime.job_trust import load_trust, save_trust

    agents = tmp_path / "agents" / "m1"
    agents.mkdir(parents=True)
    save_trust(agents, __import__("nls.runtime.job_trust", fromlist=["TrustDocument"]).TrustDocument(
        tools_deny=["delete_file"],
    ))
    reg = SquadRegistry(tmp_path)
    reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    mgr = SquadManager(reg, data_dir=tmp_path, agents_dir=tmp_path / "agents")

    mgr._apply_trust_patch_fields("m1", {"tools_deny": ["bash"]})
    trust = load_trust(agents)
    assert "delete_file" in trust.tools_deny
    assert "bash" in trust.tools_deny
