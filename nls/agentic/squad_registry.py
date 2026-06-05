"""Global squad registry — persistent multi-agent groups."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

INBOX_STATUSES = ("proposed", "approved", "rejected")


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class SquadInboxItem:
    id: str = ""
    title: str = ""
    description: str = ""
    priority: str = "normal"
    suggested_assignee_id: str = ""
    proposer_agent_id: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = "member"
    status: str = "proposed"
    assignee_agent_id: str = ""
    member_todo_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    reject_reason: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"inbox_{_short_id()}"
        now = time.time()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SquadInboxItem:
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class SquadEscalation:
    id: str = ""
    member_agent_id: str = ""
    reason: str = ""
    context: str = ""
    status: str = "open"
    created_at: float = 0.0
    resolved_at: float = 0.0
    resolution: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"esc_{_short_id()}"
        if not self.created_at:
            self.created_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SquadEscalation:
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class SquadPendingAction:
    id: str = ""
    action_type: str = ""  # delete_agent
    target_agent_id: str = ""
    requested_by: str = ""
    title: str = ""
    description: str = ""
    status: str = "pending"  # pending, approved, rejected
    created_at: float = 0.0
    resolved_at: float = 0.0
    resolution_note: str = ""
    delete_squad_on_approve: bool = False
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"pending_{_short_id()}"
        if not self.created_at:
            self.created_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SquadPendingAction:
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class Squad:
    id: str = ""
    name: str = ""
    lead_agent_id: str = ""
    member_agent_ids: list[str] = field(default_factory=list)
    paused: bool = False
    inbox: list[SquadInboxItem] = field(default_factory=list)
    escalations: list[SquadEscalation] = field(default_factory=list)
    pending_actions: list[SquadPendingAction] = field(default_factory=list)
    checkback_enabled: bool = True
    checkback_interval_seconds: int = 1800
    proposal_sla_seconds: int = 14400
    last_checkback_at: float = 0.0
    member_checkback_enabled: bool = True
    member_checkback_interval_seconds: int = 3600
    member_last_checkback_at: dict[str, float] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"squad_{_short_id()}"
        now = time.time()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        self._normalize_members()

    def _normalize_members(self) -> None:
        members = list(dict.fromkeys(self.member_agent_ids or []))
        if self.lead_agent_id and self.lead_agent_id not in members:
            members.insert(0, self.lead_agent_id)
        self.member_agent_ids = members

    @property
    def all_member_ids(self) -> list[str]:
        return list(dict.fromkeys([self.lead_agent_id, *self.member_agent_ids]))

    def is_member(self, agent_id: str) -> bool:
        return agent_id in self.all_member_ids

    def is_lead(self, agent_id: str) -> bool:
        return agent_id == self.lead_agent_id

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["inbox"] = [i.to_dict() for i in self.inbox]
        d["escalations"] = [e.to_dict() for e in self.escalations]
        d["pending_actions"] = [p.to_dict() for p in self.pending_actions]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Squad:
        inbox = [
            SquadInboxItem.from_dict(i)
            for i in (d.get("inbox") or [])
            if isinstance(i, dict)
        ]
        escalations = [
            SquadEscalation.from_dict(e)
            for e in (d.get("escalations") or [])
            if isinstance(e, dict)
        ]
        pending_actions = [
            SquadPendingAction.from_dict(p)
            for p in (d.get("pending_actions") or [])
            if isinstance(p, dict)
        ]
        valid = {
            f for f in cls.__dataclass_fields__
            if f not in ("inbox", "escalations", "pending_actions")
        }
        base = {k: v for k, v in d.items() if k in valid}
        base["inbox"] = inbox
        base["escalations"] = escalations
        base["pending_actions"] = pending_actions
        return cls(**base)


class SquadRegistry:
    """Loads/saves squads under ``{data_dir}/squads/``."""

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "squads"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "index.json"
        self._squads: dict[str, Squad] = {}
        self._agent_index: dict[str, str] = {}
        self._load_all()

    def _load_all(self) -> None:
        if self._index_path.exists():
            try:
                idx = json.loads(self._index_path.read_text(encoding="utf-8"))
                for sid in idx.get("squad_ids") or []:
                    self._load_one(sid)
            except (json.JSONDecodeError, OSError):
                pass
        for path in self._dir.glob("squad_*.json"):
            sid = path.stem
            if sid not in self._squads:
                self._load_one(sid)

    def _load_one(self, squad_id: str) -> Squad | None:
        path = self._dir / f"{squad_id}.json"
        if not path.exists():
            return None
        try:
            squad = Squad.from_dict(json.loads(path.read_text(encoding="utf-8")))
            self._squads[squad.id] = squad
            for aid in squad.all_member_ids:
                self._agent_index[aid] = squad.id
            return squad
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load squad %s: %s", squad_id, exc)
            return None

    def _save_index(self) -> None:
        self._index_path.write_text(
            json.dumps(
                {"version": "1.0", "squad_ids": sorted(self._squads.keys())},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def save(self, squad: Squad) -> None:
        squad.updated_at = time.time()
        squad._normalize_members()
        path = self._dir / f"{squad.id}.json"
        path.write_text(
            json.dumps(squad.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        prev = self._squads.get(squad.id)
        self._squads[squad.id] = squad
        for aid in squad.all_member_ids:
            self._agent_index[aid] = squad.id
        self._save_index()

    def create(
        self,
        *,
        name: str,
        lead_agent_id: str,
        member_agent_ids: list[str] | None = None,
    ) -> Squad:
        for aid in [lead_agent_id, *(member_agent_ids or [])]:
            if aid and aid in self._agent_index:
                raise ValueError(
                    f"Agent {aid} already belongs to squad {self._agent_index[aid]}"
                )
        squad = Squad(
            name=name.strip() or "Squad",
            lead_agent_id=lead_agent_id,
            member_agent_ids=list(member_agent_ids or []),
        )
        self.save(squad)
        return squad

    def get(self, squad_id: str) -> Squad | None:
        return self._squads.get(squad_id) or self._load_one(squad_id)

    def get_for_agent(self, agent_id: str) -> Squad | None:
        sid = self._agent_index.get(agent_id)
        return self.get(sid) if sid else None

    def list_squads(self) -> list[Squad]:
        return sorted(self._squads.values(), key=lambda s: s.name.lower())

    def delete(self, squad_id: str) -> bool:
        squad = self.get(squad_id)
        if squad is None:
            return False
        path = self._dir / f"{squad_id}.json"
        if path.exists():
            path.unlink()
        for aid in squad.all_member_ids:
            self._agent_index.pop(aid, None)
        self._squads.pop(squad_id, None)
        self._save_index()
        return True

    def update_members(
        self,
        squad_id: str,
        *,
        lead_agent_id: str | None = None,
        member_agent_ids: list[str] | None = None,
        name: str | None = None,
    ) -> Squad:
        squad = self.get(squad_id)
        if squad is None:
            raise ValueError(f"Squad {squad_id} not found")
        old_member_ids = set(squad.all_member_ids)
        if name is not None:
            squad.name = name.strip() or squad.name
        if lead_agent_id is not None:
            squad.lead_agent_id = lead_agent_id
        if member_agent_ids is not None:
            squad.member_agent_ids = member_agent_ids
        squad._normalize_members()
        for aid in squad.all_member_ids:
            if self._agent_index.get(aid) not in (None, squad.id):
                raise ValueError(f"Agent {aid} already in another squad")
        for aid in old_member_ids - set(squad.all_member_ids):
            if self._agent_index.get(aid) == squad.id:
                self._agent_index.pop(aid, None)
        self.save(squad)
        return squad

    def update_settings(
        self,
        squad_id: str,
        *,
        checkback_enabled: bool | None = None,
        checkback_interval_seconds: int | None = None,
        proposal_sla_seconds: int | None = None,
    ) -> Squad:
        squad = self.get(squad_id)
        if squad is None:
            raise ValueError(f"Squad {squad_id} not found")
        if checkback_enabled is not None:
            squad.checkback_enabled = checkback_enabled
        if checkback_interval_seconds is not None:
            squad.checkback_interval_seconds = max(
                300, int(checkback_interval_seconds),
            )
        if proposal_sla_seconds is not None:
            squad.proposal_sla_seconds = max(300, int(proposal_sla_seconds))
        self.save(squad)
        return squad

    def append_message(self, squad_id: str, record: dict[str, Any]) -> None:
        log_path = self._dir / f"{squad_id}_messages.jsonl"
        line = json.dumps({**record, "ts": time.time()}, ensure_ascii=False)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
