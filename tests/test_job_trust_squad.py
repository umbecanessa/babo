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
