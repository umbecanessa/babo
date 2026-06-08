"""Policy for inbound channel agentic loops — full tool surface for job work.

No message-content regex heuristics. Upgrades are driven by:
  1. A persisted owner Job charter (job.json on disk), or
  2. Turn triage classifier intent (TASK_*), already computed before the loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .orchestration_policy import invalidate_tool_policy_cache
from .types import AgentMode, LoopState


def is_channel_dispatch_source(dispatch_source: str) -> bool:
    return (dispatch_source or "").strip().startswith("user:channel")


def agent_has_job_charter(agent_dir: Path | str | None) -> bool:
    """True when the owner persisted a Job charter (job.json), not default fallbacks."""
    if not agent_dir:
        return False
    try:
        from nls.runtime.job_trust import job_path, load_job

        root = Path(agent_dir)
        if not job_path(root).is_file():
            return False
        job = load_job(root)
    except Exception:
        return False
    return bool(job.updated_at > 0 or (job.playbook or "").strip())


def channel_dispatch_requires_execution(
    *,
    agent_dir: Path | str | None,
    triage_intent: str = "",
) -> bool:
    """True when a channel turn should use EXECUTING, not chat-only tools."""
    if agent_has_job_charter(agent_dir):
        return True
    return (triage_intent or "").upper().startswith("TASK")


def _resolve_channel_profile(
    profile: str,
    agent_dir: Path | str | None,
) -> str:
    if agent_dir and agent_has_job_charter(agent_dir):
        try:
            from nls.runtime.agent_profile import normalize_profile
            from nls.runtime.job_trust import load_job

            job = load_job(Path(agent_dir))
            if job.default_profile:
                return normalize_profile(job.default_profile)
        except Exception:
            pass
    if profile == "conversational":
        return "solo_structured"
    return profile


def apply_channel_loop_policy(
    state: LoopState,
    *,
    user_input: str,
    dispatch_source: str,
    profile: str,
    agent_dir: Path | str | None = None,
    triage_intent: str = "",
) -> str:
    """Force executing + structured profile for Job-backed or TASK channel turns."""
    del user_input  # policy is charter/triage driven, not message regex

    if not is_channel_dispatch_source(dispatch_source):
        return profile

    if not channel_dispatch_requires_execution(
        agent_dir=agent_dir,
        triage_intent=triage_intent,
    ):
        return profile

    profile = _resolve_channel_profile(profile, agent_dir)
    if state.orchestration_profile == "conversational" or profile != state.orchestration_profile:
        state.orchestration_profile = profile
        invalidate_tool_policy_cache(state)

    if state.active_mode != AgentMode.EXECUTING:
        state.active_mode = AgentMode.EXECUTING
        invalidate_tool_policy_cache(state)

    if not state.goals:
        state.goals = ["Handle the inbound channel message and reply on-channel"]

    return profile
