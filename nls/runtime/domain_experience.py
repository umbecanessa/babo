"""Per-agent domain and skill usage tracking (persisted on disk).

Tracks which knowledge domains and skills the agent has used across sleep
cycles. Used by drives, the agentic bridge, and crystallization — not for
local adapter routing (OSS uses BYO inference only).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DomainEntry(BaseModel):
    domain_path: str
    encounter_count: int = 0
    last_seen_cycle: int = 0
    first_seen_cycle: int = 0
    prompts: list[str] = Field(default_factory=list)


class SkillDomainEntry(BaseModel):
    skill_name: str
    encounter_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    first_used_cycle: int = 0
    last_used_cycle: int = 0
    associated_domains: list[str] = Field(default_factory=list)
    myelination_score: float = 0.0


class DomainTracker(BaseModel):
    """Domain encounter frequency and per-skill usage."""

    domains: dict[str, DomainEntry] = Field(default_factory=dict)
    skill_encounters: dict[str, SkillDomainEntry] = Field(default_factory=dict)
    current_cycle: int = 0

    def record_encounter(
        self,
        domain_path: str,
        prompts: list[str] | None = None,
    ) -> DomainEntry:
        if domain_path not in self.domains:
            self.domains[domain_path] = DomainEntry(
                domain_path=domain_path,
                encounter_count=0,
                first_seen_cycle=self.current_cycle,
            )
        entry = self.domains[domain_path]
        entry.encounter_count += 1
        entry.last_seen_cycle = self.current_cycle
        if prompts:
            existing = set(entry.prompts)
            for p in prompts:
                if p not in existing:
                    entry.prompts.append(p)
            entry.prompts = entry.prompts[-20:]
        return entry

    def record_skill_encounter(
        self,
        skill_name: str,
        domain_path: str = "",
        success: bool = True,
    ) -> SkillDomainEntry:
        if skill_name not in self.skill_encounters:
            self.skill_encounters[skill_name] = SkillDomainEntry(
                skill_name=skill_name,
                first_used_cycle=self.current_cycle,
            )
        entry = self.skill_encounters[skill_name]
        entry.encounter_count += 1
        entry.last_used_cycle = self.current_cycle
        if success:
            entry.success_count += 1
        else:
            entry.failure_count += 1
        if domain_path and domain_path not in entry.associated_domains:
            entry.associated_domains.append(domain_path)
            entry.associated_domains = entry.associated_domains[-20:]

        total = max(entry.encounter_count, 1)
        freq_weight = min(total / 50.0, 1.0)
        success_rate = entry.success_count / total
        entry.myelination_score = freq_weight * success_rate
        return entry

    def get_skill_relevance(self, query_domains: list[str] | None = None) -> dict[str, float]:
        relevance: dict[str, float] = {}
        for name, entry in self.skill_encounters.items():
            base = entry.myelination_score
            if query_domains:
                overlap = sum(
                    1 for d in entry.associated_domains if d in query_domains
                )
                domain_boost = min(overlap / max(len(query_domains), 1), 1.0)
                relevance[name] = base * 0.7 + domain_boost * 0.3
            else:
                relevance[name] = base
        return relevance

    def advance_cycle(self) -> None:
        self.current_cycle += 1

    def is_stale(self, domain_path: str, stale_after: int) -> bool:
        entry = self.domains.get(domain_path)
        if entry is None:
            return True
        return (self.current_cycle - entry.last_seen_cycle) >= stale_after


class ExperienceTracker:
    """Loads and saves domain_tracker.json and skill_tracker.json per agent."""

    def __init__(self) -> None:
        self.domain_tracker = DomainTracker()

    def load_state(self, directory: Path) -> bool:
        directory.mkdir(parents=True, exist_ok=True)
        tracker_path = directory / "domain_tracker.json"
        skill_tracker_path = directory / "skill_tracker.json"
        loaded = False

        if tracker_path.exists():
            try:
                raw = json.loads(tracker_path.read_text(encoding="utf-8"))
                domains_raw = raw.get("domains") or {}
                domains: dict[str, DomainEntry] = {}
                for path, val in domains_raw.items():
                    if isinstance(val, dict):
                        domains[path] = DomainEntry(domain_path=path, **{
                            k: v for k, v in val.items() if k != "domain_path"
                        })
                self.domain_tracker = DomainTracker(
                    domains=domains,
                    current_cycle=int(raw.get("current_cycle", 0)),
                )
                loaded = True
            except Exception as exc:
                logger.warning("Failed to load domain_tracker.json: %s", exc)

        if skill_tracker_path.exists():
            try:
                raw = json.loads(skill_tracker_path.read_text(encoding="utf-8"))
                for k, v in raw.items():
                    self.domain_tracker.skill_encounters[k] = SkillDomainEntry(**v)
                loaded = True
            except Exception as exc:
                logger.warning("Failed to load skill_tracker.json: %s", exc)

        return loaded

    def save_state(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        tracker_path = directory / "domain_tracker.json"
        tracker_path.write_text(
            self.domain_tracker.model_dump_json(indent=2),
            encoding="utf-8",
        )
        if self.domain_tracker.skill_encounters:
            skill_tracker_path = directory / "skill_tracker.json"
            data = {
                k: v.model_dump()
                for k, v in self.domain_tracker.skill_encounters.items()
            }
            skill_tracker_path.write_text(
                json.dumps(data, indent=2),
                encoding="utf-8",
            )
