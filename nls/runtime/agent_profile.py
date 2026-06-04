"""Resolve orchestration profile from Job, Trust, Squad, and dispatch source."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nls.agentic.orchestration_profile_spec import normalize_profile
from nls.runtime.dispatch_sources import is_squad_orchestration_dispatch_source
from nls.runtime.job_trust import (
    apply_trust_to_profile,
    load_job,
    load_trust,
)

_SQUAD_REGISTRY: Any | None = None
_SQUAD_REGISTRY_DIR: Path | None = None


def _get_squad_registry(data_dir: Path | None = None) -> Any | None:
    global _SQUAD_REGISTRY, _SQUAD_REGISTRY_DIR
    try:
        from server.main import app

        reg = getattr(app.state, "squad_registry", None)
        if reg is not None:
            return reg
    except Exception:
        pass
    if data_dir is None:
        return None
    if _SQUAD_REGISTRY is not None and _SQUAD_REGISTRY_DIR == data_dir:
        return _SQUAD_REGISTRY
    try:
        from nls.agentic.squad_registry import SquadRegistry

        _SQUAD_REGISTRY = SquadRegistry(data_dir)
        _SQUAD_REGISTRY_DIR = data_dir
        return _SQUAD_REGISTRY
    except Exception:
        return None


def resolve_orchestration_profile_for_agent(
    agent_dir: Path,
    agent_id: str,
    base_profile: str,
    dispatch_source: str,
    *,
    data_dir: Path | None = None,
) -> str:
    """Apply job default, squad lead role, dispatch wakes, and trust channel caps."""
    profile = normalize_profile(base_profile)
    job = load_job(agent_dir)
    if job.default_profile:
        profile = normalize_profile(job.default_profile)

    dd = data_dir or agent_dir.parent
    reg = _get_squad_registry(dd)
    squad = reg.get_for_agent(agent_id) if reg is not None else None

    if is_squad_orchestration_dispatch_source(dispatch_source):
        if squad is not None and squad.is_lead(agent_id):
            profile = "squad_lead"
    elif squad is not None and squad.is_lead(agent_id):
        if profile in ("solo_structured", "conversational"):
            profile = "squad_lead"

    trust = load_trust(agent_dir)
    profile = apply_trust_to_profile(profile, trust, dispatch_source)
    return normalize_profile(profile)
