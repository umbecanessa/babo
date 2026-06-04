"""Fleet channel topology — single vs multi public face."""

from __future__ import annotations

import json
from pathlib import Path

from nls.runtime.fleet_channel_topology import (
    agent_channel_connected,
    build_fleet_topology_snapshot,
    render_topology_guidance,
    topology_to_dict,
)


def _write_discord_cfg(data_root: Path, agent_id: str, *, channels: list[str]) -> None:
    path = data_root / "skills" / "discord-channel" / "agents" / f"{agent_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    ch_map = {
        str(i): {
            "name": name,
            "effective_enabled": True,
        }
        for i, name in enumerate(channels)
    }
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "bot_token": "test-token",
                "scoped_channels": {"channels": ch_map, "guilds": {}},
            }
        ),
        encoding="utf-8",
    )


def test_single_face_topology(tmp_path: Path):
    lead = "lead-1"
    mod = "mod-1"
    agent_dir = tmp_path / "agents" / lead
    agent_dir.mkdir(parents=True)
    _write_discord_cfg(tmp_path, lead, channels=["general"])

    class _Squad:
        id = "squad_1"
        name = "Test"
        lead_agent_id = lead
        member_agent_ids = [mod]

    snap = build_fleet_topology_snapshot(
        agent_id=lead,
        agent_dir=agent_dir,
        squad=_Squad(),
    )
    assert snap.mode == "single_face"
    assert snap.platform == "discord"
    assert snap.connected_face_count("discord") == 1

    text = render_topology_guidance(snap)
    assert "SINGLE PUBLIC FACE" in text
    assert "separate credentials" in text.lower()

    d = topology_to_dict(snap)
    assert d["mode"] == "single_face"
    assert len(d["faces"]) == 2


def test_multi_face_topology(tmp_path: Path):
    lead = "lead-2"
    qa = "qa-2"
    agent_dir = tmp_path / "agents" / lead
    agent_dir.mkdir(parents=True)
    _write_discord_cfg(tmp_path, lead, channels=["admin"])
    _write_discord_cfg(tmp_path, qa, channels=["bug-reports"])

    class _Squad:
        id = "squad_2"
        name = "Fleet"
        lead_agent_id = lead
        member_agent_ids = [qa]

    snap = build_fleet_topology_snapshot(
        agent_id=lead,
        agent_dir=agent_dir,
        squad=_Squad(),
    )
    assert snap.mode == "multi_face"
    text = render_topology_guidance(snap)
    assert "MULTI PUBLIC FACE" in text


def test_planning_fleet_solo_with_discord(tmp_path: Path):
    aid = "solo-1"
    agent_dir = tmp_path / "agents" / aid
    agent_dir.mkdir(parents=True)
    _write_discord_cfg(tmp_path, aid, channels=["general"])

    snap = build_fleet_topology_snapshot(
        agent_id=aid,
        agent_dir=agent_dir,
        planning_fleet=True,
    )
    assert snap.mode == "planning"
    text = render_topology_guidance(snap)
    assert "SINGLE FACE" in text
    assert "MULTI FACE" in text


def test_member_view_no_discord(tmp_path: Path):
    lead = "lead-3"
    mod = "mod-3"
    agent_dir = tmp_path / "agents" / mod
    agent_dir.mkdir(parents=True)
    _write_discord_cfg(tmp_path, lead, channels=["general"])

    class _Squad:
        id = "squad_3"
        name = "Mods"
        lead_agent_id = lead
        member_agent_ids = [mod]

    snap = build_fleet_topology_snapshot(
        agent_id=mod,
        agent_dir=agent_dir,
        squad=_Squad(),
    )
    assert snap.viewer_role == "member"
    text = render_topology_guidance(snap)
    assert "YOU (member)" in text
    assert agent_channel_connected(tmp_path, mod, "discord") is False
