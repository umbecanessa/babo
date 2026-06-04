"""Job and Trust REST API for agents."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from nls.runtime.job_trust import (
    JobDocument,
    TrustDocument,
    ChannelTrustOverlay,
    load_job,
    load_trust,
    save_job,
    save_trust,
    sync_job_trust_to_cryptex,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["job-trust"])


class ChannelTrustOverlayModel(BaseModel):
    channel_key: str = ""
    profile_cap: str = ""
    tools_allow: list[str] = Field(default_factory=list)
    tools_deny: list[str] = Field(default_factory=list)
    public_channel: bool = False


class TrustPatchModel(BaseModel):
    tools_allow: list[str] | None = None
    tools_deny: list[str] | None = None
    action_classes_allow: list[str] | None = None
    action_classes_deny: list[str] | None = None
    channel_overlays: list[ChannelTrustOverlayModel] | None = None


class JobPatchModel(BaseModel):
    title: str | None = None
    mission: str | None = None
    persona: str | None = None
    playbook: str | None = None
    in_scope: list[str] | None = None
    out_of_scope: list[str] | None = None
    refusal_template: str | None = None
    refusal_examples: list[str] | None = None
    escalation_paths: list[str] | None = None
    default_profile: str | None = None
    strategic_priorities: list[str] | None = None


def _agent_dir(request: Request, agent_id: str):
    agent_dir = request.app.state.settings.agents_dir / agent_id
    if not agent_dir.exists():
        raise HTTPException(404, "Agent not found")
    return agent_dir


def _sync_runtime(request: Request, agent_id: str) -> None:
    runtime = request.app.state.agent_manager.get_runtime(agent_id)
    if runtime is None:
        return
    if hasattr(runtime, "sync_job_trust"):
        runtime.sync_job_trust()


@router.get("/{agent_id}/job")
async def get_job(agent_id: str, request: Request) -> dict[str, Any]:
    agent_dir = _agent_dir(request, agent_id)
    job = load_job(agent_dir)
    return job.to_dict()


@router.patch("/{agent_id}/job")
async def patch_job(agent_id: str, body: JobPatchModel, request: Request) -> dict[str, Any]:
    agent_dir = _agent_dir(request, agent_id)
    job = load_job(agent_dir)
    data = body.model_dump(exclude_none=True)
    for key, val in data.items():
        if hasattr(job, key):
            setattr(job, key, val)
    save_job(agent_dir, job)
    _sync_runtime(request, agent_id)
    logger.info("Agent %s: job updated (title=%s)", agent_id, job.display_title)
    return job.to_dict()


@router.get("/{agent_id}/trust")
async def get_trust(agent_id: str, request: Request) -> dict[str, Any]:
    agent_dir = _agent_dir(request, agent_id)
    trust = load_trust(agent_dir)
    return trust.to_dict()


@router.patch("/{agent_id}/trust")
async def patch_trust(agent_id: str, body: TrustPatchModel, request: Request) -> dict[str, Any]:
    agent_dir = _agent_dir(request, agent_id)
    trust = load_trust(agent_dir)
    data = body.model_dump(exclude_none=True)
    if "channel_overlays" in data:
        trust.channel_overlays = [
            ChannelTrustOverlay.from_dict(o) for o in data["channel_overlays"]
        ]
        del data["channel_overlays"]
    for key, val in data.items():
        if hasattr(trust, key):
            setattr(trust, key, val)
    save_trust(agent_dir, trust)
    _sync_runtime(request, agent_id)
    return trust.to_dict()
