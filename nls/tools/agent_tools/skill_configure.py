"""Generic skill configuration tool — delegates to SkillConfigService."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nls.runtime.skill_config_service import SkillConfigService

from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)


class SkillConfigureTool:
    """Agent tool that reads/writes skill config using declared schemas."""

    def __init__(self, agent_id: str) -> None:
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "skill_configure"

    @property
    def description(self) -> str:
        return (
            "Inspect or update bundled NLS skills that declare config_schema "
            "(e.g. telegram-channel, whatsapp-channel, email-channel, discord-channel). "
            "Call with only skill_name to see required/missing fields; "
            "call with skill_name + config to set values. "
            "For who-can-reach-the-agent policy, prefer interaction_mode preset "
            "(owner_private_only, shared_only, owner_plus_shared, trusted_allowlist, "
            "open_community) or interaction_intent (natural language, any language) — "
            "not invalid dm_policy values like 'enabled'. "
            "Squad leads configure MEMBER agents via squad(action='configure_member', ...) — "
            "not skill_configure on the lead for member tokens. "
            "ClawHub/AgentSkill instruction packages do NOT use this tool."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to configure (e.g. 'telegram-channel')",
                },
                "config": {
                    "type": "object",
                    "description": (
                        "Key-value pairs to set. Omit to inspect current config. "
                        "Keys must match the skill's config_schema."
                    ),
                    "additionalProperties": True,
                },
                "interaction_mode": {
                    "type": "string",
                    "enum": [
                        "owner_private_only",
                        "shared_only",
                        "owner_plus_shared",
                        "trusted_allowlist",
                        "open_community",
                    ],
                    "description": (
                        "Channel-agnostic reachability preset (private + shared surfaces). "
                        "Expands to dm_policy, groups/scoped_channels, and email thread_policy."
                    ),
                },
                "interaction_intent": {
                    "type": "string",
                    "description": (
                        "Owner's natural-language policy request (any language). "
                        "Resolved via micro-inference to interaction_mode — not keyword matching."
                    ),
                },
                "owner_confirm": {
                    "type": "boolean",
                    "description": (
                        "True after ask_user() confirms credentials, interaction_mode, "
                        "or classified intent."
                    ),
                },
                "context_turns": {
                    "type": "array",
                    "description": (
                        "Optional recent conversation turns [{role, content}] for "
                        "interaction_intent micro-inference (any language)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["skill_name"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        skill_name = (params.get("skill_name") or "").strip()
        config_patch = params.get("config")
        interaction_mode = (params.get("interaction_mode") or "").strip().lower()
        interaction_intent = (params.get("interaction_intent") or "").strip()
        owner_confirm = bool(params.get("owner_confirm", False))
        context_turns = params.get("context_turns")

        if not skill_name:
            return ToolResult(content="Error: skill_name is required.", is_error=True)

        svc = SkillConfigService(self._agent_id)

        if config_patch is None and not interaction_mode and not interaction_intent:
            outcome = svc.inspect(skill_name)
            return ToolResult(content=outcome.content, is_error=outcome.is_error)

        outcome = await svc.apply(
            skill_name,
            config_patch if isinstance(config_patch, dict) else {},
            interaction_mode=interaction_mode,
            interaction_intent=interaction_intent,
            owner_confirm=owner_confirm,
            context_turns=context_turns,
            configure_tool_label="skill_configure",
        )
        return ToolResult(content=outcome.content, is_error=outcome.is_error)


def create_skill_configure_tool(agent_id: str) -> SkillConfigureTool:
    """Factory function for per-agent skill_configure tool instances."""
    return SkillConfigureTool(agent_id=agent_id)
