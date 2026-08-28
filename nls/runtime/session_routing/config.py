"""Persisted session routing configuration per agent."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nls.runtime.agent_runtime import (
    DEFAULT_HOME_SESSION_KEY,
    is_valid_home_session_key,
)

logger = logging.getLogger(__name__)

_QA_PURPOSES = frozenset({"qa", "qa_reports", "bug_reports", "investigations"})
_BUILTIN_SURFACE_CHANNELS = frozenset({"discord", "telegram", "whatsapp", "slack", "email"})


@dataclass
class ReportChannelPolicy:
    session_key: str
    label: str = ""
    purposes: list[str] = field(default_factory=list)
    intents: list[str] = field(default_factory=lambda: ["report", "progress", "announce"])
    exclusion_tags: list[str] = field(default_factory=list)
    broadcast_default: bool = False

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> ReportChannelPolicy | None:
        sk = str(row.get("session_key") or "").strip()
        if not sk:
            return None
        purpose = str(row.get("purpose") or "").strip()
        purposes = list(row.get("purposes") or [])
        if purpose and purpose not in purposes:
            purposes.insert(0, purpose)
        intents = list(row.get("intents") or row.get("inclusion", {}).get("intents") or [])
        if not intents:
            intents = ["report", "progress", "announce"]
        tags = list(row.get("exclusion_tags") or row.get("exclusion", {}).get("todo_tags") or [])
        return cls(
            session_key=sk,
            label=str(row.get("label") or "").strip(),
            purposes=[str(p).strip().lower() for p in purposes if str(p).strip()],
            intents=[str(i).strip().lower() for i in intents if str(i).strip()],
            exclusion_tags=[str(t).strip().lower() for t in tags if str(t).strip()],
            broadcast_default=bool(row.get("broadcast_default", False)),
        )


@dataclass
class DeliveryExclusion:
    session_key: str = ""
    channel: str = ""
    block_intents: list[str] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> DeliveryExclusion:
        block = list(row.get("block_intents") or row.get("block") or [])
        return cls(
            session_key=str(row.get("session_key") or "").strip(),
            channel=str(row.get("channel") or "").strip().lower(),
            block_intents=[str(x).strip().lower() for x in block if str(x).strip()],
            reason=str(row.get("reason") or "").strip(),
        )


@dataclass
class SessionRoutingConfig:
    version: str = "1.0"
    default_home_session_key: str = DEFAULT_HOME_SESSION_KEY
    primary_reachability_session_key: str = ""
    mirror_channel_progress_to_home: bool = False
    default_report_mode: str = "origin_or_explicit"
    report_channels: list[ReportChannelPolicy] = field(default_factory=list)
    exclusions: list[DeliveryExclusion] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "default_home_session_key": self.default_home_session_key,
            "primary_reachability_session_key": self.primary_reachability_session_key,
            "mirror_channel_progress_to_home": self.mirror_channel_progress_to_home,
            "default_report_mode": self.default_report_mode,
            "report_channels": [asdict(row) for row in self.report_channels],
            "exclusions": [asdict(row) for row in self.exclusions],
        }


def is_valid_reachability_session_key(session_key: str, runtime: Any | None = None) -> bool:
    sk = (session_key or "").strip()
    if not sk:
        return False
    if is_valid_home_session_key(sk):
        return True
    from nls.runtime.session_routing.surface import is_routable_surface_session_key

    return is_routable_surface_session_key(sk, runtime)


def _merge_job_report_channels(agent_dir: Path, cfg: SessionRoutingConfig) -> None:
    if not agent_dir:
        return
    try:
        from nls.runtime.job_trust import load_job

        job = load_job(agent_dir)
        job_rows = getattr(job, "report_channels", None) or []
    except Exception:
        return
    existing = {row.session_key for row in cfg.report_channels}
    for raw in job_rows:
        if not isinstance(raw, dict):
            continue
        row = ReportChannelPolicy.from_dict(raw)
        if row is None or row.session_key in existing:
            continue
        cfg.report_channels.append(row)
        existing.add(row.session_key)


def load_session_routing_config(agent_dir: Path, runtime: Any | None = None) -> SessionRoutingConfig:
    meta_path = agent_dir / "session_meta.json"
    cfg = SessionRoutingConfig()
    if meta_dir := runtime:
        home = getattr(meta_dir, "default_home_session_key", "") or ""
        if is_valid_home_session_key(home):
            cfg.default_home_session_key = home

    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                routing = meta.get("session_routing")
                if isinstance(routing, dict):
                    home = str(routing.get("default_home_session_key") or "").strip()
                    if is_valid_home_session_key(home):
                        cfg.default_home_session_key = home
                    primary = str(routing.get("primary_reachability_session_key") or "").strip()
                    if is_valid_reachability_session_key(primary, runtime):
                        cfg.primary_reachability_session_key = primary
                    cfg.mirror_channel_progress_to_home = bool(
                        routing.get("mirror_channel_progress_to_home", False),
                    )
                    mode = str(routing.get("default_report_mode") or "").strip()
                    if mode:
                        cfg.default_report_mode = mode
                    for raw in routing.get("report_channels") or []:
                        if isinstance(raw, dict):
                            row = ReportChannelPolicy.from_dict(raw)
                            if row is not None:
                                cfg.report_channels.append(row)
                    for raw in routing.get("exclusions") or []:
                        if isinstance(raw, dict):
                            cfg.exclusions.append(DeliveryExclusion.from_dict(raw))
                _home_meta = str(meta.get("default_home_session_key") or "").strip()
                if is_valid_home_session_key(_home_meta):
                    cfg.default_home_session_key = _home_meta
        except Exception as exc:
            logger.debug("load_session_routing_config failed: %s", exc)

    if not cfg.primary_reachability_session_key:
        cfg.primary_reachability_session_key = cfg.default_home_session_key

    _merge_job_report_channels(agent_dir, cfg)
    return cfg


def save_session_routing_config(agent_dir: Path, cfg: SessionRoutingConfig) -> None:
    meta_path = agent_dir / "session_meta.json"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except Exception:
            meta = {}
    meta["default_home_session_key"] = cfg.default_home_session_key
    meta["session_routing"] = cfg.to_dict()
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
