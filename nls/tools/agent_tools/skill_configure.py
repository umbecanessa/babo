"""Generic skill configuration tool.

Allows the agent to inspect and update any skill's config based on its
declared ``config_schema``.  Replaces per-skill ``/configure`` endpoints
and hardcoded tool-result hacks with a single, schema-driven tool.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from nls.skills_setup_policy import (
    instruction_skill_setup_hint,
    is_instruction_only_skill,
)

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
            "(e.g. telegram-channel, whatsapp-channel, email-channel). "
            "Call with only skill_name to see required/missing fields; "
            "call with skill_name + config to set values. "
            "ClawHub/AgentSkill instruction packages (installed via clawhub) "
            "do NOT use this tool — read their SKILL.md and follow with bash()."
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
            },
            "required": ["skill_name"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        skill_name = params.get("skill_name", "").strip()
        config_patch = params.get("config")

        if not skill_name:
            return ToolResult(content="Error: skill_name is required.", is_error=True)

        try:
            from server.main import app
        except ImportError:
            return ToolResult(content="Error: server not available.", is_error=True)

        sl = getattr(app.state, "skill_loader", None)
        if sl is None:
            return ToolResult(content="Error: skill loader not initialized.", is_error=True)

        sk = sl.skills.get(skill_name)
        if sk is None:
            return ToolResult(
                content=f"Error: skill '{skill_name}' not found.",
                is_error=True,
            )

        meta = sk.meta
        schema = meta.config_schema if meta else []
        ctx = sk.context

        if not schema:
            if is_instruction_only_skill(meta):
                return ToolResult(
                    content=instruction_skill_setup_hint(skill_name, sk.path),
                    is_error=True,
                )
            if config_patch and ctx:
                current = ctx.load_config(agent_id=self._agent_id)
                current.update(config_patch)
                ctx.save_config(current, agent_id=self._agent_id)
                self._reload_adapter(sk)
                return ToolResult(content=f"Config saved for '{skill_name}' (no schema declared).")
            return ToolResult(
                content=(
                    f"Skill '{skill_name}' has no config_schema declared. "
                    f"Only bundled NLS channel/integration skills support skill_configure."
                ),
                is_error=True,
            )

        if config_patch is None:
            return self._inspect(sk, schema)

        return self._apply(sk, schema, config_patch)

    def _inspect(self, sk: Any, schema: list) -> ToolResult:
        ctx = sk.context
        current = {}
        if ctx:
            current = ctx.load_config(agent_id=self._agent_id)

        lines: list[str] = [f"Configuration for '{sk.name}':"]
        missing: list[str] = []

        for field in schema:
            key = field.key
            val = current.get(key, field.default)
            is_set = key in current and current[key] not in (None, "", [])
            status = "SET" if is_set else "NOT SET"

            if field.type == "secret" and is_set:
                display = "***masked***"
            else:
                display = json.dumps(val) if val is not None else "null"

            req = " (REQUIRED)" if field.required else ""
            cat = f" [{field.category}]" if field.category else ""
            opts = ""
            if field.options:
                opts = f" options: {field.options}"

            lines.append(f"  - {key}{req}{cat}: {display} ({status}){opts}")
            if field.description:
                lines.append(f"    {field.description}")

            if field.required and not is_set:
                missing.append(key)

        if missing:
            lines.append(f"\nMissing required fields: {', '.join(missing)}")
            lines.append("Please ask the user for these values and call skill_configure again with them.")
        else:
            lines.append("\nAll required fields are configured.")

        return ToolResult(content="\n".join(lines))

    def _apply(self, sk: Any, schema: list, config_patch: dict[str, Any]) -> ToolResult:
        ctx = sk.context
        if ctx is None:
            return ToolResult(content="Error: skill context not available.", is_error=True)

        schema_keys = {f.key for f in schema}
        unknown = [k for k in config_patch if k not in schema_keys]
        if unknown:
            return ToolResult(
                content=f"Error: unknown config keys: {', '.join(unknown)}. "
                f"Valid keys: {', '.join(sorted(schema_keys))}",
                is_error=True,
            )

        schema_map = {f.key: f for f in schema}
        coerced: dict[str, Any] = {}
        errors: list[str] = []

        for key, value in config_patch.items():
            field = schema_map[key]
            try:
                coerced[key] = self._coerce(field, value)
            except ValueError as e:
                errors.append(f"{key}: {e}")

        if errors:
            return ToolResult(
                content="Validation errors:\n" + "\n".join(f"  - {e}" for e in errors),
                is_error=True,
            )

        current = ctx.load_config(agent_id=self._agent_id)
        current.update(coerced)

        identity_values: list[str] = []
        for key, value in coerced.items():
            field = schema_map[key]
            if field.category == "identity":
                if isinstance(value, list):
                    identity_values.extend(value)
                elif value:
                    identity_values.append(str(value))

        if identity_values:
            dm_policy = current.get("dm_policy", "open")
            if dm_policy == "allowlist":
                allow_from = list(current.get("allow_from", []))
                for v in identity_values:
                    if v not in allow_from:
                        allow_from.append(v)
                current["allow_from"] = allow_from

        ctx.save_config(current, agent_id=self._agent_id)

        self._reload_adapter(sk)

        saved_display: dict[str, Any] = {}
        for key in coerced:
            field = schema_map[key]
            if field.type == "secret":
                saved_display[key] = "***saved***"
            else:
                saved_display[key] = current[key]

        parts = [f"{k}={json.dumps(v)}" for k, v in saved_display.items()]
        return ToolResult(content=f"Config saved for '{sk.name}': {', '.join(parts)}")

    def _coerce(self, field: Any, value: Any) -> Any:
        """Coerce a value to the declared field type."""
        if field.type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)

        if field.type == "number":
            try:
                return float(value)
            except (ValueError, TypeError):
                raise ValueError(f"expected a number, got {type(value).__name__}")

        if field.type == "list":
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                return [v.strip() for v in value.split(",") if v.strip()]
            raise ValueError(f"expected a list or comma-separated string")

        if field.type == "choice":
            if field.options and str(value) not in field.options:
                raise ValueError(f"must be one of {field.options}, got '{value}'")
            return str(value)

        return str(value) if value is not None else ""

    def _reload_adapter(self, sk: Any) -> None:
        """Notify the adapter (if any) that config changed."""
        ctx = sk.context
        if ctx is None:
            return
        adapter = getattr(ctx, "adapter", None)
        if adapter is None:
            return
        configs = getattr(adapter, "_agent_configs", None)
        if configs is not None:
            fresh = ctx.load_config(agent_id=self._agent_id)
            configs[self._agent_id] = fresh
            logger.info(
                "skill_configure: reloaded adapter config for %s/%s",
                sk.name, self._agent_id,
            )


def create_skill_configure_tool(agent_id: str) -> SkillConfigureTool:
    """Factory function for per-agent skill_configure tool instances."""
    return SkillConfigureTool(agent_id=agent_id)
