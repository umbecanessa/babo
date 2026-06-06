"""Owner-confirmed Job charter updates for solo agents."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from nls.tools.agent_tools.base import ToolResult

logger = logging.getLogger(__name__)

_SET_JOB_PARAMS: dict[str, Any] = {
    "title": {
        "type": "string",
        "description": "Job title (e.g. 'Discord Community Moderator').",
    },
    "mission": {
        "type": "string",
        "description": "Mission statement — why this role exists.",
    },
    "persona": {
        "type": "string",
        "description": "Tone and personality for this role.",
    },
    "playbook": {
        "type": "string",
        "description": "Operating playbook — how to behave day-to-day.",
    },
    "default_profile": {
        "type": "string",
        "description": "Orchestration profile cap (e.g. solo_structured).",
    },
    "in_scope": {
        "type": "array",
        "items": {"type": "string"},
        "description": "What this Job covers.",
    },
    "out_of_scope": {
        "type": "array",
        "items": {"type": "string"},
        "description": "What to refuse or escalate.",
    },
    "strategic_priorities": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Long-lived priorities for background Job check-backs.",
    },
    "background_enabled": {
        "type": "boolean",
        "description": "Enable idle Job background work (non-stock charter required).",
    },
    "background_interval_seconds": {
        "type": "integer",
        "description": "Minimum seconds between Job background wakes (min 300).",
    },
    "owner_confirmed": {
        "type": "boolean",
        "description": "True after ask_user() owner approval.",
    },
}


class SetJobTool:
    """Persist owner-approved Job charter to job.json (solo agents, Home chat only)."""

    def __init__(self, agent_dir: str | Path, agent_id: str) -> None:
        self._agent_dir = Path(agent_dir)
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "set_job"

    @property
    def description(self) -> str:
        return (
            "Update your persistent Job charter (job.json) after the owner confirms via "
            "ask_user(). Use for ongoing roles ('be my mod', 'you are my research assistant') "
            "— NOT one-shot tasks. Requires owner_confirmed=true. Solo agents only on Home "
            "chat; squad leads use squad(action='set_lead_job') and members receive jobs from "
            "the lead."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["owner_confirmed"],
            "properties": dict(_SET_JOB_PARAMS),
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        return ToolResult(
            content=(
                "set_job must run through the agent loop so Home-chat session "
                "guards apply — do not invoke this tool directly."
            ),
            is_error=True,
        )


def create_set_job_tool(agent_dir: str | Path, agent_id: str) -> SetJobTool:
    return SetJobTool(agent_dir, agent_id)
