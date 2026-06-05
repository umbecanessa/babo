"""Fleet / squad channel topology — single public face vs multi-bot Discord/Slack.

Factual state for Cryptex channels ring, triage environment, squad tool results,
and dashboard channel UI. Not intent heuristics.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

ChannelName = Literal["discord", "slack"]
TopologyMode = Literal["none", "single_face", "multi_face", "planning"]

_CHANNEL_SKILL_DIRS: dict[str, str] = {
    "discord": "discord-channel",
    "slack": "slack-channel",
}

_TOPOLOGY_RULES = (
    "FLEET CHANNEL TOPOLOGY (Discord / Slack / Telegram / WhatsApp / Email):\n"
    "One channel credential set = one inbound route = one runtime agent.\n"
    "• SINGLE PUBLIC FACE: only the squad lead (or one agent) links a channel "
    "integration — they are the public voice; members coordinate via squad "
    "inbox/todos/brief (lead may send on their behalf).\n"
    "• MULTI PUBLIC FACE: each agent that should speak in-channel needs its OWN "
    "credentials on THAT agent (Dashboard → agent → Tools → channel skill), with "
    "scope limited to their role.\n"
    "Never reuse the lead's bot token / phone / email on members."
)


def data_root_from_agent_dir(agent_dir: str | Path) -> Path:
    return Path(agent_dir).parent.parent


def load_agent_channel_config(
    data_root: Path,
    agent_id: str,
    channel: ChannelName,
) -> dict[str, Any] | None:
    from nls.runtime.channel_agent_config import load_agent_channel_config as _load

    if channel not in ("discord", "slack"):
        return None
    return _load(data_root, agent_id, channel)


def agent_channel_connected(
    data_root: Path,
    agent_id: str,
    channel: ChannelName,
) -> bool:
    from nls.runtime.channel_agent_config import agent_channel_is_configured

    if channel not in ("discord", "slack"):
        return False
    return agent_channel_is_configured(data_root, agent_id, channel)


def _channel_summary(cfg: dict[str, Any] | None) -> str:
    if not cfg:
        return ""
    scoped = cfg.get("scoped_channels") or {}
    channels = scoped.get("channels") or {}
    names = sorted(
        str(c.get("name", "")).strip()
        for c in channels.values()
        if isinstance(c, dict) and c.get("effective_enabled") and c.get("name")
    )
    if not names:
        return "connected"
    shown = ", ".join(names[:8])
    if len(names) > 8:
        shown += f", +{len(names) - 8} more"
    return shown


@dataclass
class AgentChannelFace:
    agent_id: str
    role: str  # lead | member | solo
    discord_connected: bool = False
    slack_connected: bool = False
    discord_channels: str = ""
    slack_channels: str = ""


@dataclass
class FleetTopologySnapshot:
    mode: TopologyMode = "none"
    platform: ChannelName | None = None
    viewer_agent_id: str = ""
    viewer_role: str = "solo"
    squad_id: str = ""
    squad_name: str = ""
    faces: list[AgentChannelFace] = field(default_factory=list)

    def connected_face_count(self, platform: ChannelName) -> int:
        if platform == "discord":
            return sum(1 for f in self.faces if f.discord_connected)
        return sum(1 for f in self.faces if f.slack_connected)


def _resolve_squad(app: Any, agent_id: str) -> Any | None:
    try:
        reg = getattr(app.state, "squad_registry", None)
        if reg is not None:
            return reg.get_for_agent(agent_id)
    except Exception:
        pass
    return None


def build_fleet_topology_snapshot(
    *,
    agent_id: str,
    agent_dir: str | Path | None,
    app: Any | None = None,
    squad: Any | None = None,
    planning_fleet: bool = False,
) -> FleetTopologySnapshot:
    """Build factual topology for an agent (solo, lead, or member)."""
    snap = FleetTopologySnapshot(viewer_agent_id=agent_id)
    if not agent_dir:
        if planning_fleet:
            snap.mode = "planning"
        return snap

    data_root = data_root_from_agent_dir(agent_dir)
    if squad is None and app is not None:
        squad = _resolve_squad(app, agent_id)

    if squad is None:
        solo = AgentChannelFace(
            agent_id=agent_id,
            role="solo",
            discord_connected=agent_channel_connected(data_root, agent_id, "discord"),
            slack_connected=agent_channel_connected(data_root, agent_id, "slack"),
            discord_channels=_channel_summary(
                load_agent_channel_config(data_root, agent_id, "discord"),
            ),
            slack_channels=_channel_summary(
                load_agent_channel_config(data_root, agent_id, "slack"),
            ),
        )
        snap.faces = [solo]
        if planning_fleet and solo.discord_connected:
            snap.mode = "planning"
            snap.platform = "discord"
        elif solo.discord_connected or solo.slack_connected:
            snap.mode = "single_face"
            snap.platform = "discord" if solo.discord_connected else "slack"
        return snap

    snap.squad_id = getattr(squad, "id", "") or ""
    snap.squad_name = getattr(squad, "name", "") or ""
    lead_id = getattr(squad, "lead_agent_id", "") or ""
    member_ids = list(getattr(squad, "member_agent_ids", None) or [])
    all_ids = [lead_id, *member_ids]

    for aid in all_ids:
        if not aid:
            continue
        role = "lead" if aid == lead_id else "member"
        d_cfg = load_agent_channel_config(data_root, aid, "discord")
        s_cfg = load_agent_channel_config(data_root, aid, "slack")
        snap.faces.append(
            AgentChannelFace(
                agent_id=aid,
                role=role,
                discord_connected=agent_channel_connected(data_root, aid, "discord"),
                slack_connected=agent_channel_connected(data_root, aid, "slack"),
                discord_channels=_channel_summary(d_cfg),
                slack_channels=_channel_summary(s_cfg),
            ),
        )

    if agent_id == lead_id:
        snap.viewer_role = "lead"
    elif agent_id in member_ids:
        snap.viewer_role = "member"
    else:
        snap.viewer_role = "solo"

    platform: ChannelName | None = None
    if any(f.discord_connected for f in snap.faces):
        platform = "discord"
    elif any(f.slack_connected for f in snap.faces):
        platform = "slack"

    snap.platform = platform
    if platform is None:
        snap.mode = "planning" if planning_fleet else "none"
        return snap

    count = snap.connected_face_count(platform)
    snap.mode = "multi_face" if count >= 2 else "single_face"
    return snap


def topology_to_dict(snap: FleetTopologySnapshot) -> dict[str, Any]:
    return {
        "mode": snap.mode,
        "platform": snap.platform,
        "viewer_agent_id": snap.viewer_agent_id,
        "viewer_role": snap.viewer_role,
        "squad_id": snap.squad_id,
        "squad_name": snap.squad_name,
        "faces": [
            {
                "agent_id": f.agent_id,
                "role": f.role,
                "discord_connected": f.discord_connected,
                "slack_connected": f.slack_connected,
                "discord_channels": f.discord_channels,
                "slack_channels": f.slack_channels,
            }
            for f in snap.faces
        ],
    }


def _face_line(f: AgentChannelFace, platform: ChannelName) -> str:
    if platform == "discord":
        if f.discord_connected:
            ch = f.discord_channels or "connected"
            return f"  • {f.role} {f.agent_id[:8]}…: Discord CONNECTED ({ch})"
        return f"  • {f.role} {f.agent_id[:8]}…: Discord NOT linked on this agent"
    if f.slack_connected:
        ch = f.slack_channels or "connected"
        return f"  • {f.role} {f.agent_id[:8]}…: Slack CONNECTED ({ch})"
    return f"  • {f.role} {f.agent_id[:8]}…: Slack NOT linked on this agent"


def render_topology_guidance(
    snap: FleetTopologySnapshot,
    *,
    compact: bool = False,
) -> str:
    """Human/agent-readable topology block for rings, triage, and tool results."""
    if snap.mode == "none" and not snap.faces:
        return ""

    if snap.mode == "none" and not any(
        f.discord_connected or f.slack_connected for f in snap.faces
    ):
        return ""

    lines: list[str] = []
    if compact:
        lines.append(_TOPOLOGY_RULES.split("\n")[0])
    else:
        lines.append(_TOPOLOGY_RULES)

    if snap.mode == "planning":
        lines.append(
            "\nPLANNING FLEET: Before spawn_member, ask the owner which model they want:"
        )
        lines.append(
            "  A) SINGLE FACE — one linked channel integration on the lead; members work "
            "via squad inbox only (no extra credentials)."
        )
        lines.append(
            "  B) MULTI FACE — each public-speaking agent gets its own channel "
            "credentials on their agent card (Tools → channel skill); scope per role."
        )
        solo = snap.faces[0] if len(snap.faces) == 1 else None
        plat = snap.platform or "discord"
        solo_connected = solo and (
            solo.discord_connected if plat == "discord" else solo.slack_connected
        )
        solo_channels = (
            solo.discord_channels if plat == "discord" else solo.slack_channels
        ) if solo else ""
        if solo and solo_connected:
            lines.append(
                f"\nThis agent already has {plat} connected ({solo_channels}). "
                "That integration becomes the lead's public face unless members get "
                "their own credentials."
            )
        return "\n".join(lines)

    plat = snap.platform or "discord"
    plat_label = plat.upper()

    if snap.mode == "single_face":
        lines.append(f"\nCURRENT MODE: SINGLE PUBLIC FACE ({plat_label})")
        lines.append(
            "Only one squad agent has this channel linked. All inbound messages hit "
            "that agent's integration. Members without their own credentials do NOT "
            "receive in-channel traffic directly — use squad(action='brief'|'assign'); "
            "post via channel send tools only from the connected agent."
        )
    elif snap.mode == "multi_face":
        lines.append(f"\nCURRENT MODE: MULTI PUBLIC FACE ({plat_label})")
        lines.append(
            "Multiple agents have their own channel credentials. Inbound routes per "
            "agent_id; scope channels in Tools so roles do not overlap. "
            "Squad lead tests members via discord_send in a shared scoped channel "
            "(@mention member bot ids — lead sends as itself, members wake on mention)."
        )

    if snap.squad_name:
        lines.append(f"Squad: {snap.squad_name} ({snap.squad_id})")

    if snap.faces:
        lines.append("Channel faces:")
        for f in snap.faces:
            lines.append(_face_line(f, plat))

    if snap.viewer_role == "lead" and snap.mode == "single_face":
        members_without = [
            f for f in snap.faces
            if f.role == "member" and not (
                f.discord_connected if plat == "discord" else f.slack_connected
            )
        ]
        if members_without:
            lines.append(
                "\nTo give a member their own in-channel voice: owner opens that member's "
                "agent → Tools → channel skill → separate credentials + channel scope "
                "(do NOT copy the lead's credentials). After linking, spawn/brief is unchanged."
            )

    if snap.viewer_role == "member":
        viewer = next((f for f in snap.faces if f.agent_id == snap.viewer_agent_id), None)
        connected = viewer and (
            viewer.discord_connected if plat == "discord" else viewer.slack_connected
        )
        if not connected:
            lines.append(
                "\nYOU (member): no channel integration on this agent — work arrives via "
                "squad_wake/todos. To listen in-channel as yourself, the owner must "
                "link dedicated credentials on YOUR agent (not the lead's)."
            )

    return "\n".join(lines)


def spawn_member_channel_note(*, multi_face_recommended: bool = False) -> str:
    base = (
        "Channel setup: new members do NOT inherit the lead's Discord/Slack bot. "
        "SINGLE FACE — member works via squad inbox only. "
        "MULTI FACE — lead uses squad(action='configure_member', target_agent_id=..., "
        "channel='discord', skill_config={bot_token, owner_identity}, "
        "interaction_mode='shared_only', owner_confirmed=true) — one call per member."
    )
    if multi_face_recommended:
        return base + " For mod/QA in public channels, multi-face is usually required."
    return base


def ask_user_topology_questions() -> list[str]:
    """Suggested ask_user prompts for lead during fleet bootstrap."""
    return [
        "Should mod and QA speak in Discord as separate bots (multi-face), or only "
        "you as one bot with members working behind the scenes (single-face)?",
        "If multi-face: will you create separate Discord applications (bot tokens) "
        "for each agent, or start single-face and add bots later?",
    ]
