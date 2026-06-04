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
            "(e.g. telegram-channel, whatsapp-channel, email-channel, discord-channel). "
            "Call with only skill_name to see required/missing fields; "
            "call with skill_name + config to set values. "
            "For who-can-reach-the-agent policy, prefer interaction_mode preset "
            "(owner_private_only, shared_only, owner_plus_shared, trusted_allowlist, "
            "open_community) or interaction_intent (natural language, any language) — "
            "not invalid dm_policy values like 'enabled'. "
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
                        "True after ask_user() confirms interaction_mode or classified intent."
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
        skill_name = params.get("skill_name", "").strip()
        config_patch = params.get("config")
        interaction_mode = (params.get("interaction_mode") or "").strip().lower()
        interaction_intent = (params.get("interaction_intent") or "").strip()
        owner_confirm = bool(params.get("owner_confirm", False))
        context_turns = params.get("context_turns")

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

        if config_patch is None and not interaction_mode and not interaction_intent:
            return self._inspect(sk, schema)

        if config_patch is None:
            config_patch = {}

        preset, owner_ref, preset_meta = await self._resolve_interaction_preset(
            sk,
            interaction_mode=interaction_mode,
            interaction_intent=interaction_intent,
            owner_confirm=owner_confirm,
            context_turns=context_turns,
        )
        if preset_meta is not None:
            return preset_meta

        if preset:
            try:
                current = ctx.load_config(agent_id=self._agent_id) if ctx else {}
                from nls.runtime.interaction_policy import expand_interaction_preset

                owner_from_config = None
                if isinstance(config_patch.get("owner_identity"), str):
                    owner_from_config = config_patch["owner_identity"]
                elif isinstance(config_patch.get("owner_identity"), list):
                    owner_from_config = config_patch["owner_identity"]
                owner_for_expand = owner_from_config or owner_ref or None
                mode_patch = expand_interaction_preset(
                    skill_name,
                    preset,
                    owner=owner_for_expand,
                    current=current,
                )
                merged_patch = dict(mode_patch)
                merged_patch.update(config_patch)
                if owner_ref and not merged_patch.get("owner_identity"):
                    field_type = next(
                        (f.type for f in schema if f.key == "owner_identity"),
                        "string",
                    )
                    if field_type == "list":
                        merged_patch["owner_identity"] = [owner_ref]
                    else:
                        merged_patch["owner_identity"] = owner_ref
                config_patch = merged_patch
                owner_err = self._validate_owner_presets(preset, merged_patch, current)
                if owner_err is not None:
                    return owner_err
            except ValueError as exc:
                return ToolResult(content=f"Error: {exc}", is_error=True)

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

        try:
            from nls.runtime.interaction_policy import summarize_interaction_mode

            summary = summarize_interaction_mode(sk.name, current)
            if summary:
                lines.append(f"\nInteraction mode: {summary}")
        except Exception:
            pass

        return ToolResult(content="\n".join(lines))

    async def _resolve_interaction_preset(
        self,
        sk: Any,
        *,
        interaction_mode: str,
        interaction_intent: str,
        owner_confirm: bool,
        context_turns: list[dict] | None = None,
    ) -> tuple[str, str, ToolResult | None]:
        """Return (preset, owner_ref, error_result)."""
        from nls.runtime.interaction_policy import (
            INTERACTION_PRESETS,
            classify_interaction_intent,
            summarize_interaction_mode,
        )

        preset = interaction_mode
        owner_ref = ""
        summary_line = ""

        if interaction_intent and not preset:
            channel_summary = ""
            ctx = sk.context
            if ctx:
                current = ctx.load_config(agent_id=self._agent_id)
                channel_summary = summarize_interaction_mode(sk.name, current)

            vllm_client = None
            try:
                from server.main import app

                am = getattr(app.state, "agent_manager", None)
                if am is not None:
                    runtime = am.get_runtime(self._agent_id)
                    if runtime is not None:
                        vllm_client = getattr(runtime, "vllm_client", None)
            except Exception:
                pass

            if vllm_client is None:
                return "", "", ToolResult(
                    content=(
                        "Error: interaction_intent requires inference runtime. "
                        f"Use interaction_mode explicitly: {', '.join(sorted(INTERACTION_PRESETS))}."
                    ),
                    is_error=True,
                )

            history = self._normalize_context_turns(context_turns)
            if not history:
                history = self._runtime_history_fallback()

            classified = await classify_interaction_intent(
                vllm_client,
                interaction_intent,
                skill_name=sk.name,
                channel_summary=channel_summary,
                history=history,
            )
            if not classified.preset:
                return "", "", ToolResult(
                    content=classified.user_facing_summary or "Could not classify interaction intent.",
                    is_error=True,
                )
            preset = classified.preset
            owner_ref = classified.owner_ref
            summary_line = classified.user_facing_summary
            if classified.needs_confirmation and not owner_confirm:
                return "", "", ToolResult(
                    content=(
                        f"Interaction policy needs owner confirmation before applying.\n"
                        f"Proposed preset: {preset}\n"
                        f"Summary: {summary_line or preset}\n"
                        f"Confidence: {classified.confidence:.2f}\n"
                        "Call ask_user() to confirm, then skill_configure again with "
                        "owner_confirm=true and the same interaction_mode or interaction_intent."
                    ),
                    is_error=True,
                )

        if preset and preset not in INTERACTION_PRESETS:
            return "", "", ToolResult(
                content=(
                    f"Error: unknown interaction_mode '{preset}'. "
                    f"Valid: {', '.join(sorted(INTERACTION_PRESETS))}. "
                    "Do not pass dm_policy='enabled' — use interaction_mode presets."
                ),
                is_error=True,
            )

        if preset == "open_community" and not owner_confirm:
            return "", "", ToolResult(
                content=(
                    "open_community widens private and shared access. "
                    "Confirm with ask_user(), then retry with owner_confirm=true."
                ),
                is_error=True,
            )

        return preset, owner_ref, None

    def _validate_owner_presets(
        self,
        preset: str,
        patch: dict[str, Any],
        current: dict[str, Any],
    ) -> ToolResult | None:
        """Block allowlist presets when no owner is known."""
        if preset not in ("owner_private_only", "owner_plus_shared", "trusted_allowlist"):
            return None
        if str(patch.get("dm_policy", "")).lower() != "allowlist":
            return None

        owners: list[str] = []
        oi = patch.get("owner_identity", current.get("owner_identity"))
        if isinstance(oi, str) and oi.strip():
            owners.append(oi.strip())
        elif isinstance(oi, list):
            owners.extend(str(x).strip() for x in oi if str(x).strip())

        allow = patch.get("allow_from", current.get("allow_from", []))
        if owners or (isinstance(allow, list) and allow):
            return None

        return ToolResult(
            content=(
                f"Error: preset '{preset}' requires owner_identity or allow_from. "
                "Set owner_identity in config= or configure identity first."
            ),
            is_error=True,
        )

    def _normalize_context_turns(
        self,
        context_turns: list[dict] | None,
    ) -> list[dict]:
        if not isinstance(context_turns, list):
            return []
        out: list[dict] = []
        for turn in context_turns[-6:]:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role", "")).strip().lower()
            content = str(turn.get("content", "")).strip()
            if role in ("user", "assistant") and content:
                out.append({"role": role, "content": content})
        return out

    def _runtime_history_fallback(self) -> list[dict]:
        try:
            from server.main import app

            am = getattr(app.state, "agent_manager", None)
            if am is None:
                return []
            runtime = am.get_runtime(self._agent_id)
            if runtime is None:
                return []
            history = getattr(runtime, "history", None) or []
            out: list[dict] = []
            for turn in history[-6:]:
                if not isinstance(turn, dict):
                    continue
                role = turn.get("role", "user")
                content = turn.get("content") or ""
                if role in ("user", "assistant") and content:
                    out.append({"role": role, "content": str(content)[:400]})
            return out
        except Exception:
            return []

    def _apply(self, sk: Any, schema: list, config_patch: dict[str, Any]) -> ToolResult:
        from nls.runtime.interaction_policy import finalize_workspace_groups, interaction_runtime_keys

        ctx = sk.context
        if ctx is None:
            return ToolResult(content="Error: skill context not available.", is_error=True)

        schema_keys = {f.key for f in schema}
        extra_keys = interaction_runtime_keys(sk.name)
        unknown = [
            k for k in config_patch
            if k not in schema_keys and k not in extra_keys
        ]
        if unknown:
            return ToolResult(
                content=f"Error: unknown config keys: {', '.join(unknown)}. "
                f"Valid keys: {', '.join(sorted(schema_keys | set(extra_keys)))}",
                is_error=True,
            )

        schema_map = {f.key: f for f in schema}
        coerced: dict[str, Any] = {}
        errors: list[str] = []

        for key, value in config_patch.items():
            if key not in schema_keys:
                continue
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
        for key in extra_keys:
            if key in config_patch:
                current[key] = config_patch[key]

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

        finalize_workspace_groups(current, sk.name)

        self._reload_adapter(sk)

        saved_display: dict[str, Any] = {}
        for key in config_patch:
            if key in schema_keys:
                field = schema_map[key]
                if field.type == "secret":
                    saved_display[key] = "***saved***"
                else:
                    saved_display[key] = current[key]
            elif key in extra_keys:
                saved_display[key] = f"<{key} updated>"

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
            val_str = str(value)
            if field.options and val_str not in field.options:
                if field.key == "dm_policy":
                    raise ValueError(
                        f"must be one of {field.options}, got '{value}'. "
                        "Use skill_configure interaction_mode preset instead of "
                        "inventing dm_policy values."
                    )
                raise ValueError(f"must be one of {field.options}, got '{value}'")
            return val_str

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
