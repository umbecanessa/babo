"""Job and Trust documents — owner charter + action rails for agents.

Persistence:
  data/agents/{agent_id}/job.json
  data/agents/{agent_id}/trust.json

Synced into Cryptex as ACCESS_SYSTEM slots (immune to task-epoch clears).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nls.brain.working_memory import ACCESS_SYSTEM

logger = logging.getLogger(__name__)

DEFAULT_JOB_TITLE = "General helpful assistant"
DEFAULT_JOB_MISSION = (
    "You are a capable assistant for your owner. Help with questions and tasks "
    "within your tools and policies. Be clear, honest, and resourceful."
)

JOB_FILE = "job.json"
TRUST_FILE = "trust.json"

# Domains owned by job/trust sync — cleared before re-upsert
_JOB_DOMAIN_PREFIXES = ("Job.", "Goal.Strategic.Job.")
_TRUST_DOMAIN_PREFIXES = ("Trust.",)
_SQUAD_DOMAIN_PREFIX = "Squad."


@dataclass
class ChannelTrustOverlay:
    """Per-channel profile/tool caps."""

    channel_key: str = ""
    profile_cap: str = ""
    tools_allow: list[str] = field(default_factory=list)
    tools_deny: list[str] = field(default_factory=list)
    public_channel: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ChannelTrustOverlay:
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class TrustDocument:
    version: str = "1.0"
    tools_allow: list[str] = field(default_factory=list)
    tools_deny: list[str] = field(default_factory=list)
    action_classes_allow: list[str] = field(default_factory=list)
    action_classes_deny: list[str] = field(default_factory=list)
    channel_overlays: list[ChannelTrustOverlay] = field(default_factory=list)
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["channel_overlays"] = [o.to_dict() for o in self.channel_overlays]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TrustDocument:
        overlays = [
            ChannelTrustOverlay.from_dict(o)
            for o in (d.get("channel_overlays") or [])
            if isinstance(o, dict)
        ]
        valid = {f for f in cls.__dataclass_fields__ if f != "channel_overlays"}
        base = {k: v for k, v in d.items() if k in valid}
        base["channel_overlays"] = overlays
        return cls(**base)


@dataclass
class JobDocument:
    version: str = "1.0"
    title: str = DEFAULT_JOB_TITLE
    mission: str = DEFAULT_JOB_MISSION
    persona: str = ""
    playbook: str = ""
    in_scope: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    refusal_template: str = (
        "Thanks for reaching out. That's outside what I can do in my role. "
        "If you need help with something I am set up for, let me know."
    )
    refusal_examples: list[str] = field(default_factory=list)
    escalation_paths: list[str] = field(default_factory=list)
    default_profile: str = ""
    strategic_priorities: list[str] = field(default_factory=list)
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JobDocument:
        valid = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in d.items() if k in valid}
        if not data.get("title"):
            data["title"] = DEFAULT_JOB_TITLE
        if not data.get("mission"):
            data["mission"] = DEFAULT_JOB_MISSION
        return cls(**data)

    @property
    def display_title(self) -> str:
        t = (self.title or "").strip()
        return t or DEFAULT_JOB_TITLE


def job_path(agent_dir: Path) -> Path:
    return agent_dir / JOB_FILE


def trust_path(agent_dir: Path) -> Path:
    return agent_dir / TRUST_FILE


def load_job(agent_dir: Path) -> JobDocument:
    p = job_path(agent_dir)
    if not p.exists():
        return JobDocument()
    try:
        return JobDocument.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load job.json for %s: %s", agent_dir.name, exc)
        return JobDocument()


def load_trust(agent_dir: Path) -> TrustDocument:
    p = trust_path(agent_dir)
    if not p.exists():
        return TrustDocument()
    try:
        return TrustDocument.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load trust.json for %s: %s", agent_dir.name, exc)
        return TrustDocument()


def save_job(agent_dir: Path, job: JobDocument) -> JobDocument:
    agent_dir.mkdir(parents=True, exist_ok=True)
    job.updated_at = time.time()
    job_path(agent_dir).write_text(
        json.dumps(job.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return job


def save_trust(agent_dir: Path, trust: TrustDocument) -> TrustDocument:
    agent_dir.mkdir(parents=True, exist_ok=True)
    trust.updated_at = time.time()
    trust_path(agent_dir).write_text(
        json.dumps(trust.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return trust


def _clear_domains_on_ring(ring: Any, prefixes: tuple[str, ...]) -> None:
    if ring is None:
        return
    for pos, slots in list(getattr(ring, "positions", {}).items()):
        if not isinstance(slots, list):
            continue
        ring.positions[pos] = [
            s for s in slots
            if not any((s.domain or "").startswith(p) for p in prefixes)
        ]


def clear_job_trust_slots(cryptex: Any) -> None:
    """Remove prior Job/Trust/Squad SYSTEM slots before re-sync."""
    if cryptex is None:
        return
    rings = getattr(cryptex, "_rings", None) or {}
    for ring_id in ("identity", "instructions", "behavioral", "strategic_goals"):
        ring = rings.get(ring_id)
        if ring is None:
            continue
        prefixes = _JOB_DOMAIN_PREFIXES + _TRUST_DOMAIN_PREFIXES
        if ring_id == "behavioral":
            prefixes = prefixes + (_SQUAD_DOMAIN_PREFIX,)
        _clear_domains_on_ring(ring, prefixes)


def sync_job_trust_to_cryptex(
    cryptex: Any,
    *,
    job: JobDocument | None = None,
    trust: TrustDocument | None = None,
    squad_context: str = "",
) -> int:
    """Upsert Job/Trust (and optional squad blurb) into Cryptex. Returns slot count."""
    if cryptex is None:
        return 0
    clear_job_trust_slots(cryptex)
    count = 0
    job = job or JobDocument()
    trust = trust or TrustDocument()

    identity = cryptex._rings.get("identity")
    if identity is not None:
        identity.upsert_slot(
            domain="Job.Title",
            content=job.display_title,
            slot_type="identity",
            salience=1.0,
            source="job",
            access=ACCESS_SYSTEM,
        )
        count += 1
        if job.mission:
            identity.upsert_slot(
                domain="Job.Mission",
                content=job.mission.strip(),
                slot_type="identity",
                salience=0.95,
                source="job",
                access=ACCESS_SYSTEM,
            )
            count += 1
        if job.persona:
            identity.upsert_slot(
                domain="Job.Persona",
                content=job.persona.strip(),
                slot_type="identity",
                salience=0.9,
                source="job",
                access=ACCESS_SYSTEM,
            )
            count += 1

    instructions = cryptex._rings.get("instructions")
    if instructions is not None:
        if job.playbook:
            instructions.upsert_slot(
                domain="Job.Playbook",
                content=job.playbook.strip(),
                slot_type="instruction",
                salience=0.95,
                source="job",
                access=ACCESS_SYSTEM,
            )
            count += 1
        if job.escalation_paths:
            instructions.upsert_slot(
                domain="Job.Escalation",
                content="\n".join(f"- {p}" for p in job.escalation_paths if p),
                slot_type="instruction",
                salience=0.85,
                source="job",
                access=ACCESS_SYSTEM,
            )
            count += 1

    behavioral_parts: list[str] = []
    if job.in_scope:
        behavioral_parts.append(
            "IN SCOPE:\n" + "\n".join(f"- {x}" for x in job.in_scope if x)
        )
    if job.out_of_scope:
        behavioral_parts.append(
            "OUT OF SCOPE (decline politely, no destructive tools):\n"
            + "\n".join(f"- {x}" for x in job.out_of_scope if x)
        )
    if job.refusal_template:
        behavioral_parts.append(f"REFUSAL TEMPLATE: {job.refusal_template.strip()}")
    if job.refusal_examples:
        behavioral_parts.append(
            "REFUSAL EXAMPLES:\n"
            + "\n".join(f"- {x}" for x in job.refusal_examples if x)
        )
    if trust.tools_deny:
        behavioral_parts.append(
            "TRUST — tools never use: " + ", ".join(trust.tools_deny)
        )
    if trust.action_classes_deny:
        behavioral_parts.append(
            "TRUST — forbidden actions: " + ", ".join(trust.action_classes_deny)
        )
    if squad_context:
        behavioral_parts.append(squad_context.strip())

    if behavioral_parts:
        cryptex.upsert_behavioral(
            domain="Job.Boundaries",
            content="\n\n".join(behavioral_parts),
            render_mode="agentic",
            access=ACCESS_SYSTEM,
            consolidation_status="permanent",
            source="job",
            salience=1.0,
        )
        count += 1
        cryptex.upsert_behavioral(
            domain="Job.Boundaries",
            content="\n\n".join(behavioral_parts),
            render_mode="chat",
            access=ACCESS_SYSTEM,
            consolidation_status="permanent",
            source="job",
            salience=1.0,
        )
        count += 1

    strategic = cryptex._rings.get("strategic_goals")
    if strategic is not None:
        for i, pri in enumerate(job.strategic_priorities[:5]):
            if not pri:
                continue
            strategic.upsert_slot(
                domain=f"Goal.Strategic.Job.{i + 1}",
                content=pri.strip(),
                slot_type="goal",
                salience=0.8,
                source="job",
                access=ACCESS_SYSTEM,
            )
            count += 1

    if trust.channel_overlays:
        overlay_lines = []
        for ov in trust.channel_overlays:
            cap = f" profile_cap={ov.profile_cap}" if ov.profile_cap else ""
            pub = " public" if ov.public_channel else ""
            overlay_lines.append(f"- {ov.channel_key}{pub}{cap}")
        cryptex.upsert_environment(
            "Trust.Channels",
            "Channel trust overlays:\n" + "\n".join(overlay_lines),
            source="trust",
            salience=0.9,
        )
        count += 1

    return count


def sync_squad_context_to_cryptex(cryptex: Any, squad_context: str) -> None:
    """Inject squad membership block (ACCESS_SYSTEM)."""
    if not cryptex or not squad_context.strip():
        return
    cryptex.upsert_behavioral(
        domain="Squad.Membership",
        content=squad_context.strip(),
        render_mode="agentic",
        access=ACCESS_SYSTEM,
        consolidation_status="permanent",
        source="squad",
        salience=0.95,
    )
    cryptex.upsert_behavioral(
        domain="Squad.Membership",
        content=squad_context.strip(),
        render_mode="chat",
        access=ACCESS_SYSTEM,
        consolidation_status="permanent",
        source="squad",
        salience=0.95,
    )


def resolve_channel_overlay(
    trust: TrustDocument,
    dispatch_source: str,
) -> ChannelTrustOverlay | None:
    """Match user:channel:* or channel id keys in trust overlays."""
    src = (dispatch_source or "").strip().lower()
    if not src.startswith("user:"):
        return None
    channel_part = src
    if src.startswith("user:channel:"):
        channel_part = src.split("user:channel:", 1)[-1]
    best: ChannelTrustOverlay | None = None
    for ov in trust.channel_overlays:
        key = (ov.channel_key or "").strip().lower()
        if not key:
            continue
        if key in channel_part or channel_part in key:
            if best is None or len(key) > len(best.channel_key or ""):
                best = ov
    return best


def apply_trust_to_profile(
    base_profile: str,
    trust: TrustDocument,
    dispatch_source: str,
) -> str:
    """Cap profile for public/channel overlays."""
    from nls.agentic.orchestration_profile_spec import normalize_profile

    profile = normalize_profile(base_profile)
    ov = resolve_channel_overlay(trust, dispatch_source)
    if ov and ov.profile_cap:
        cap = normalize_profile(ov.profile_cap)
        order = (
            "conversational",
            "solo_structured",
            "orchestrated",
            "squad_lead",
        )
        try:
            if order.index(cap) < order.index(profile):
                return cap
        except ValueError:
            return cap
    return profile


def is_tool_denied_by_trust(
    tool_name: str,
    trust: TrustDocument,
    dispatch_source: str = "",
) -> str | None:
    """Return denial reason if tool blocked, else None."""
    name = (tool_name or "").strip()
    if not name:
        return None
    if name in trust.tools_deny:
        return f"Tool '{name}' is denied by agent trust policy."
    if trust.tools_allow and name not in trust.tools_allow:
        return f"Tool '{name}' is not in the trust allowlist."
    ov = resolve_channel_overlay(trust, dispatch_source)
    if ov:
        if ov.tools_deny and name in ov.tools_deny:
            return f"Tool '{name}' is denied on channel '{ov.channel_key}'."
        if ov.tools_allow and name not in ov.tools_allow:
            return f"Tool '{name}' is not allowed on channel '{ov.channel_key}'."
    return None
