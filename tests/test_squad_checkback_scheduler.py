"""Squad checkback scheduler tests."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from nls.agentic.squad_checkback_scheduler import (
    DEFAULT_CHECKBACK_INTERVAL_SECONDS,
    SquadCheckbackScheduler,
)
from nls.agentic.squad_registry import SquadRegistry, SquadInboxItem


def test_checkback_wake_on_interval(tmp_path: Path):
    reg = SquadRegistry(tmp_path)
    squad = reg.create(
        name="Test",
        lead_agent_id="lead1",
        member_agent_ids=["m1"],
    )
    squad.checkback_enabled = True
    squad.checkback_interval_seconds = 60
    squad.last_checkback_at = 0
    squad.member_checkback_enabled = False
    reg.save(squad)

    wakes: list[tuple[str, str, str]] = []

    def enqueue(agent_id: str, prompt: str, source: str) -> None:
        wakes.append((agent_id, prompt, source))

    mgr = MagicMock()
    mgr.build_checkback_detail.return_value = "status summary"
    mgr._wake_lead = lambda s, kind, detail="": enqueue(
        s.lead_agent_id, "prompt", f"squad_{kind}:{s.id}",
    )

    sched = SquadCheckbackScheduler(reg, mgr, has_dispatch_prefix=lambda _a, _p: False)
    assert sched.tick() == 1
    assert wakes[0][0] == "lead1"
    assert wakes[0][2].startswith("squad_checkback:")

    squad2 = reg.get(squad.id)
    assert squad2 is not None
    assert squad2.last_checkback_at > 0


def test_member_checkback_wake_on_interval(tmp_path: Path):
    from nls.runtime.job_trust import save_job, JobDocument

    reg = SquadRegistry(tmp_path)
    squad = reg.create(
        name="Members",
        lead_agent_id="lead1",
        member_agent_ids=["m1", "m2"],
    )
    squad.checkback_enabled = False
    squad.member_checkback_enabled = True
    squad.member_checkback_interval_seconds = 60
    squad.member_last_checkback_at = {}
    reg.save(squad)

    agents = tmp_path / "agents"
    for mid in ("m1", "m2"):
        d = agents / mid
        d.mkdir(parents=True)
        save_job(
            d,
            JobDocument(
                title=f"Role {mid}",
                mission="Coordinate with squad on linked channels.",
                background_enabled=True,
            ),
        )

    wakes: list[tuple[str, str, str]] = []

    def enqueue(agent_id: str, prompt: str, source: str) -> None:
        wakes.append((agent_id, prompt, source))

    mgr = MagicMock()
    mgr._agent_dir = lambda aid: agents / aid
    mgr.build_member_checkback_detail.side_effect = (
        lambda s, mid: f"[SQUAD MEMBER CHECKBACK] job for {mid}"
    )
    mgr._wake_member = lambda s, mid, kind, detail="": enqueue(
        mid, detail, f"squad_member_{kind}:{s.id}",
    )

    sched = SquadCheckbackScheduler(reg, mgr, has_dispatch_prefix=lambda _a, _p: False)
    assert sched.tick() == 2
    assert {w[0] for w in wakes} == {"m1", "m2"}
    assert all(w[2].startswith("squad_member_checkback:") for w in wakes)

    squad2 = reg.get(squad.id)
    assert squad2 is not None
    assert squad2.member_last_checkback_at.get("m1", 0) > 0


def test_checkback_skips_when_disabled(tmp_path: Path):
    reg = SquadRegistry(tmp_path)
    squad = reg.create(name="Off", lead_agent_id="lead1", member_agent_ids=[])
    squad.checkback_enabled = False
    squad.last_checkback_at = 0
    reg.save(squad)

    mgr = MagicMock()
    sched = SquadCheckbackScheduler(reg, mgr)
    assert sched.tick() == 0
    mgr._wake_lead.assert_not_called()


def test_urgent_wake_rate_limited(tmp_path: Path):
    """Open escalations must not wake the lead every scheduler tick."""
    reg = SquadRegistry(tmp_path)
    squad = reg.create(name="Urgent", lead_agent_id="lead1", member_agent_ids=[])
    squad.checkback_enabled = True
    squad.checkback_interval_seconds = 3600
    squad.last_checkback_at = time.time() - 10
    from nls.agentic.squad_registry import SquadEscalation

    squad.escalations.append(
        SquadEscalation(member_agent_id="m1", reason="stuck", status="open"),
    )
    reg.save(squad)

    wakes: list[int] = []

    mgr = MagicMock()
    mgr.build_checkback_detail.return_value = ""
    mgr._wake_lead = lambda s, k, d="": wakes.append(1)

    sched = SquadCheckbackScheduler(reg, mgr, has_dispatch_prefix=lambda _a, _p: False)
    assert sched.tick() == 0
    assert len(wakes) == 0


def test_urgent_wake_on_overdue_proposal(tmp_path: Path):
    reg = SquadRegistry(tmp_path)
    squad = reg.create(name="Urgent", lead_agent_id="lead1", member_agent_ids=[])
    squad.checkback_enabled = True
    squad.checkback_interval_seconds = DEFAULT_CHECKBACK_INTERVAL_SECONDS
    squad.last_checkback_at = 0
    squad.inbox.append(
        SquadInboxItem(
            title="Old proposal",
            status="proposed",
            created_at=time.time() - 20000,
        ),
    )
    reg.save(squad)

    woke = []

    mgr = MagicMock()
    mgr.build_checkback_detail.return_value = ""
    mgr._wake_lead = lambda s, k, d="": woke.append(1)

    sched = SquadCheckbackScheduler(reg, mgr, has_dispatch_prefix=lambda _a, _p: False)
    assert sched.tick() == 1
