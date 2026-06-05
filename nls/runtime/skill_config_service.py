"""Schema-driven skill config inspect/apply for any agent (self or squad member)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from nls.skills_setup_policy import (
    instruction_skill_setup_hint,
    is_instruction_only_skill,
)

logger = logging.getLogger(__name__)

MULTI_FACE_CONFIGURE_RECIPE = (
    "MULTI FACE configure_member (per member, after owner pastes token):\n"
    "  squad(action='configure_member', target_agent_id='...', channel='discord',\n"
    "    skill_config={'bot_token':'...', 'owner_identity':'<owner discord username>'},\n"
    "    interaction_mode='shared_only', owner_confirmed=true)\n"
    "  interaction_mode is TOP-LEVEL — never inside skill_config.\n"
    "  dm_policy is set by the preset (open|allowlist|disabled) — do not invent values."
)

MULTI_FACE_TESTING_HINT = (
    "MULTI FACE Discord testing: run check_channel_readiness(channel_id=...) first — "
    "bots must be invited to the channel in Discord. Then discord_send with "
    "<@bot_id> mentions (use allowed_mentions via markup, not plain @name)."
)


@dataclass
class SkillConfigOutcome:
    ok: bool
    content: str
    is_error: bool = False


def resolve_skill_name(
    skill_name: str = "",
    channel: str = "",
) -> str:
    """Resolve skill_name or channel alias (e.g. discord → discord-channel)."""
    name = (skill_name or "").strip()
    if name:
        return name
    ch = (channel or "").strip().lower()
    if not ch:
        return ""
    from nls.runtime.channel_policy_profiles import CHANNEL_TO_SKILL

    return CHANNEL_TO_SKILL.get(ch, ch if ch.endswith("-channel") else f"{ch}-channel")


def patch_has_secret_fields(schema: list, config_patch: dict[str, Any]) -> bool:
    schema_map = {f.key: f for f in schema}
    for key in config_patch:
        field = schema_map.get(key)
        if field is not None and getattr(field, "type", "") == "secret":
            return True
    return False


def coerce_config_field(field: Any, value: Any) -> Any:
    if field.type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)

    if field.type == "number":
        try:
            return float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"expected a number, got {type(value).__name__}") from exc

    if field.type == "list":
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        raise ValueError("expected a list or comma-separated string")

    if field.type == "choice":
        val_str = str(value)
        if field.options and val_str not in field.options:
            from nls.runtime.interaction_policy import INTERACTION_PRESETS

            if field.key == "dm_policy":
                lowered = val_str.lower()
                if lowered in INTERACTION_PRESETS:
                    raise ValueError(
                        f"got preset name '{value}' — dm_policy must be one of "
                        f"{field.options}. Use top-level interaction_mode='{lowered}'."
                    )
                raise ValueError(
                    f"must be one of {field.options}, got '{value}'. "
                    "Use top-level interaction_mode preset instead of raw dm_policy."
                )
            raise ValueError(f"must be one of {field.options}, got '{value}'")
        return val_str

    return str(value) if value is not None else ""


def _owner_confirm_hint(configure_tool_label: str) -> str:
    if "configure_member" in configure_tool_label:
        return "owner_confirmed=true"
    return "owner_confirm=true"


def normalize_config_apply_params(
    config_patch: dict[str, Any] | None,
    interaction_mode: str,
) -> tuple[dict[str, Any], str, list[str]]:
    """Hoist common mis-placements from skill_config into top-level apply params."""
    from nls.runtime.interaction_policy import INTERACTION_PRESETS

    patch = dict(config_patch or {})
    mode = (interaction_mode or "").strip().lower()
    notes: list[str] = []

    for key in ("interaction_mode", "_interaction_preset"):
        if key in patch and not mode:
            mode = str(patch.pop(key)).strip().lower()
            notes.append(
                f"Moved '{key}' from skill_config to top-level interaction_mode."
            )

    dm = patch.get("dm_policy")
    if isinstance(dm, str) and dm.strip().lower() in INTERACTION_PRESETS and not mode:
        mode = dm.strip().lower()
        patch.pop("dm_policy", None)
        notes.append(
            f"dm_policy='{dm}' is a preset name — use top-level interaction_mode='{mode}'."
        )

    return patch, mode, notes


class SkillConfigService:
    """Inspect and apply bundled skill config for a target agent."""

    def __init__(self, agent_id: str, *, inference_agent_id: str | None = None) -> None:
        self._agent_id = agent_id
        self._inference_agent_id = inference_agent_id or agent_id

    def _load_skill(self, skill_name: str) -> tuple[Any | None, SkillConfigOutcome | None]:
        try:
            from server.main import app
        except ImportError:
            return None, SkillConfigOutcome(False, "Error: server not available.", True)

        sl = getattr(app.state, "skill_loader", None)
        if sl is None:
            return None, SkillConfigOutcome(False, "Error: skill loader not initialized.", True)

        sk = sl.skills.get(skill_name)
        if sk is None:
            return None, SkillConfigOutcome(
                False, f"Error: skill '{skill_name}' not found.", True,
            )
        return sk, None

    def inspect(self, skill_name: str) -> SkillConfigOutcome:
        sk, err = self._load_skill(skill_name)
        if err is not None:
            return err

        meta = sk.meta
        schema = meta.config_schema if meta else []
        ctx = sk.context

        if not schema:
            if is_instruction_only_skill(meta):
                return SkillConfigOutcome(
                    False,
                    instruction_skill_setup_hint(skill_name, sk.path),
                    True,
                )
            return SkillConfigOutcome(
                False,
                (
                    f"Skill '{skill_name}' has no config_schema declared. "
                    "Only bundled NLS channel/integration skills support configuration."
                ),
                True,
            )

        current: dict[str, Any] = {}
        if ctx:
            current = ctx.load_config(agent_id=self._agent_id)

        lines: list[str] = [
            f"Configuration for '{sk.name}' on agent {self._agent_id}:",
        ]
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
            opts = f" options: {field.options}" if field.options else ""

            lines.append(f"  - {key}{req}{cat}: {display} ({status}){opts}")
            if field.description:
                lines.append(f"    {field.description}")

            if field.required and not is_set:
                missing.append(key)

        if missing:
            lines.append(f"\nMissing required fields: {', '.join(missing)}")
        else:
            lines.append("\nAll required fields are configured.")

        try:
            from nls.runtime.interaction_policy import summarize_interaction_mode

            summary = summarize_interaction_mode(sk.name, current)
            if summary:
                lines.append(f"\nInteraction mode: {summary}")
        except Exception:
            pass

        if sk.name.endswith("-channel"):
            lines.append(f"\n{MULTI_FACE_CONFIGURE_RECIPE}")

        return SkillConfigOutcome(True, "\n".join(lines))

    async def apply(
        self,
        skill_name: str,
        config_patch: dict[str, Any] | None,
        *,
        interaction_mode: str = "",
        interaction_intent: str = "",
        owner_confirm: bool = False,
        context_turns: list[dict] | None = None,
        configure_tool_label: str = "skill_configure",
    ) -> SkillConfigOutcome:
        sk, err = self._load_skill(skill_name)
        if err is not None:
            return err

        meta = sk.meta
        schema = meta.config_schema if meta else []
        ctx = sk.context

        if not schema:
            if is_instruction_only_skill(meta):
                return SkillConfigOutcome(
                    False,
                    instruction_skill_setup_hint(skill_name, sk.path),
                    True,
                )
            if config_patch and ctx:
                current = ctx.load_config(agent_id=self._agent_id)
                current.update(config_patch)
                ctx.save_config(current, agent_id=self._agent_id)
                reload_adapter_for_agent(sk, self._agent_id)
                return SkillConfigOutcome(True, f"Config saved for '{skill_name}' (no schema declared).")
            return SkillConfigOutcome(
                False,
                (
                    f"Skill '{skill_name}' has no config_schema declared. "
                    "Only bundled NLS channel/integration skills support configuration."
                ),
                True,
            )

        patch, interaction_mode, hoist_notes = normalize_config_apply_params(
            config_patch, interaction_mode,
        )

        if patch and patch_has_secret_fields(schema, patch) and not owner_confirm:
            confirm_flag = _owner_confirm_hint(configure_tool_label)
            return SkillConfigOutcome(
                False,
                (
                    "Secret credential fields require owner confirmation. "
                    "Call ask_user() to confirm the owner pasted these values, then retry "
                    f"with {confirm_flag} via {configure_tool_label}."
                ),
                True,
            )

        preset, owner_ref, preset_err = await self._resolve_interaction_preset(
            sk,
            schema,
            interaction_mode=interaction_mode,
            interaction_intent=interaction_intent,
            owner_confirm=owner_confirm,
            context_turns=context_turns,
            configure_tool_label=configure_tool_label,
        )
        if preset_err is not None:
            return preset_err

        if preset:
            try:
                current = ctx.load_config(agent_id=self._agent_id) if ctx else {}
                from nls.runtime.interaction_policy import expand_interaction_preset

                owner_from_config = None
                if isinstance(patch.get("owner_identity"), str):
                    owner_from_config = patch["owner_identity"]
                elif isinstance(patch.get("owner_identity"), list):
                    owner_from_config = patch["owner_identity"]
                owner_for_expand = owner_from_config or owner_ref or None
                mode_patch = expand_interaction_preset(
                    skill_name,
                    preset,
                    owner=owner_for_expand,
                    current=current,
                )
                merged_patch = dict(mode_patch)
                merged_patch.update(patch)
                if owner_ref and not merged_patch.get("owner_identity"):
                    field_type = next(
                        (f.type for f in schema if f.key == "owner_identity"),
                        "string",
                    )
                    if field_type == "list":
                        merged_patch["owner_identity"] = [owner_ref]
                    else:
                        merged_patch["owner_identity"] = owner_ref
                patch = merged_patch
                owner_err = self._validate_owner_presets(preset, patch, current)
                if owner_err is not None:
                    return owner_err
            except ValueError as exc:
                return SkillConfigOutcome(False, f"Error: {exc}", True)

        if not patch and not preset:
            return SkillConfigOutcome(
                False,
                "Provide config fields and/or interaction_mode to apply changes.",
                True,
            )

        outcome = self._apply_patch(sk, schema, patch)
        if outcome.ok and hoist_notes:
            outcome = SkillConfigOutcome(
                True,
                outcome.content + "\n" + "\n".join(hoist_notes),
            )
        return outcome

    def _unknown_config_key_hint(self, key: str) -> str | None:
        if key in ("interaction_mode", "interaction_intent"):
            return (
                f"'{key}' is a top-level parameter on configure_member / skill_configure — "
                "not a skill_config key."
            )
        if key == "_interaction_preset":
            return (
                "Use top-level interaction_mode (e.g. 'shared_only') — "
                "_interaction_preset is written automatically by the preset expander."
            )
        return None

    def _apply_patch(
        self,
        sk: Any,
        schema: list,
        config_patch: dict[str, Any],
    ) -> SkillConfigOutcome:
        from nls.runtime.interaction_policy import finalize_workspace_groups, interaction_runtime_keys

        ctx = sk.context
        if ctx is None:
            return SkillConfigOutcome(False, "Error: skill context not available.", True)

        schema_keys = {f.key for f in schema}
        extra_keys = interaction_runtime_keys(sk.name)
        unknown = [
            k for k in config_patch
            if k not in schema_keys and k not in extra_keys
        ]
        if unknown:
            hints = [
                h for k in unknown
                if (h := self._unknown_config_key_hint(k))
            ]
            msg = (
                f"Error: unknown config keys: {', '.join(unknown)}. "
                f"Valid keys: {', '.join(sorted(schema_keys | set(extra_keys)))}"
            )
            if hints:
                msg += "\n" + "\n".join(f"  - {h}" for h in hints)
            return SkillConfigOutcome(False, msg, True)

        schema_map = {f.key: f for f in schema}
        coerced: dict[str, Any] = {}
        errors: list[str] = []

        for key, value in config_patch.items():
            if key not in schema_keys:
                continue
            field = schema_map[key]
            try:
                coerced[key] = coerce_config_field(field, value)
            except ValueError as exc:
                errors.append(f"{key}: {exc}")

        if errors:
            return SkillConfigOutcome(
                False,
                "Validation errors:\n" + "\n".join(f"  - {e}" for e in errors),
                True,
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

        if coerced.get("bot_token") and sk.name in (
            "discord-channel", "slack-channel", "telegram-channel",
        ):
            current["enabled"] = True

        ctx.save_config(current, agent_id=self._agent_id)
        finalize_workspace_groups(current, sk.name)
        reload_adapter_for_agent(sk, self._agent_id)

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
        return SkillConfigOutcome(
            True,
            f"Config saved for '{sk.name}' on agent {self._agent_id}: {', '.join(parts)}",
        )

    async def _resolve_interaction_preset(
        self,
        sk: Any,
        schema: list,
        *,
        interaction_mode: str,
        interaction_intent: str,
        owner_confirm: bool,
        context_turns: list[dict] | None,
        configure_tool_label: str,
    ) -> tuple[str, str, SkillConfigOutcome | None]:
        from nls.runtime.interaction_policy import (
            INTERACTION_PRESETS,
            classify_interaction_intent,
            summarize_interaction_mode,
        )

        preset = (interaction_mode or "").strip().lower()
        owner_ref = ""

        if interaction_intent and not preset:
            channel_summary = ""
            ctx = sk.context
            if ctx:
                current = ctx.load_config(agent_id=self._agent_id)
                channel_summary = summarize_interaction_mode(sk.name, current)

            vllm_client = self._resolve_vllm_client()
            if vllm_client is None:
                return "", "", SkillConfigOutcome(
                    False,
                    (
                        "Error: interaction_intent requires inference runtime. "
                        f"Use interaction_mode explicitly: {', '.join(sorted(INTERACTION_PRESETS))}."
                    ),
                    True,
                )

            history = normalize_context_turns(context_turns)
            if not history:
                history = runtime_history_fallback(self._inference_agent_id)

            classified = await classify_interaction_intent(
                vllm_client,
                interaction_intent,
                skill_name=sk.name,
                channel_summary=channel_summary,
                history=history,
            )
            if not classified.preset:
                return "", "", SkillConfigOutcome(
                    False,
                    classified.user_facing_summary or "Could not classify interaction intent.",
                    True,
                )
            preset = classified.preset
            owner_ref = classified.owner_ref
            if classified.needs_confirmation and not owner_confirm:
                confirm_flag = _owner_confirm_hint(configure_tool_label)
                return "", "", SkillConfigOutcome(
                    False,
                    (
                        "Interaction policy needs owner confirmation before applying.\n"
                        f"Proposed preset: {preset}\n"
                        f"Summary: {classified.user_facing_summary or preset}\n"
                        f"Confidence: {classified.confidence:.2f}\n"
                        f"Call ask_user() to confirm, then retry {configure_tool_label} "
                        f"with {confirm_flag}."
                    ),
                    True,
                )

        if preset and preset not in INTERACTION_PRESETS:
            return "", "", SkillConfigOutcome(
                False,
                (
                    f"Error: unknown interaction_mode '{preset}'. "
                    f"Valid: {', '.join(sorted(INTERACTION_PRESETS))}."
                ),
                True,
            )

        if preset == "open_community" and not owner_confirm:
            confirm_flag = _owner_confirm_hint(configure_tool_label)
            return "", "", SkillConfigOutcome(
                False,
                (
                    "open_community widens private and shared access. "
                    f"Confirm with ask_user(), then retry with {confirm_flag}."
                ),
                True,
            )

        return preset, owner_ref, None

    def _validate_owner_presets(
        self,
        preset: str,
        patch: dict[str, Any],
        current: dict[str, Any],
    ) -> SkillConfigOutcome | None:
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

        return SkillConfigOutcome(
            False,
            (
                f"Error: preset '{preset}' requires owner_identity or allow_from. "
                "Set owner_identity in config or configure identity first."
            ),
            True,
        )

    def _resolve_vllm_client(self) -> Any | None:
        try:
            from server.main import app

            am = getattr(app.state, "agent_manager", None)
            if am is None:
                return None
            runtime = am.get_runtime(self._inference_agent_id)
            if runtime is None:
                return None
            return getattr(runtime, "vllm_client", None)
        except Exception:
            return None


def normalize_context_turns(context_turns: list[dict] | None) -> list[dict]:
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


def runtime_history_fallback(agent_id: str) -> list[dict]:
    try:
        from server.main import app

        am = getattr(app.state, "agent_manager", None)
        if am is None:
            return []
        runtime = am.get_runtime(agent_id)
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


def reload_adapter_for_agent(sk: Any, agent_id: str) -> None:
    ctx = sk.context
    if ctx is None:
        return
    adapter = getattr(ctx, "adapter", None)
    if adapter is None:
        return
    fresh = ctx.load_config(agent_id=agent_id)
    configs = getattr(adapter, "_agent_configs", None)
    if configs is not None:
        configs[agent_id] = fresh
        logger.info(
            "skill_config: reloaded adapter for %s/%s",
            sk.name, agent_id,
        )


async def ensure_member_runtime(agent_id: str) -> Any | None:
    try:
        from server.main import app

        am = app.state.agent_manager
        rt = am.get_runtime(agent_id)
        if rt is None:
            await am.load_agent(agent_id)
            rt = am.get_runtime(agent_id)
        return rt
    except Exception as exc:
        logger.warning("ensure_member_runtime failed for %s: %s", agent_id, exc)
        return None


async def wire_channel_after_config(
    agent_id: str,
    skill_name: str,
    config_patch: dict[str, Any],
) -> str | None:
    """Connect inbound gateway when a bot token was saved (discord/slack)."""
    patch = config_patch if isinstance(config_patch, dict) else {}

    if skill_name == "discord-channel":
        token = str(patch.get("bot_token") or "").strip() or _load_saved_bot_token(
            agent_id, skill_name,
        )
        if not token:
            return None
        try:
            import importlib

            from server.main import app

            adapter_mod = importlib.import_module(
                "nls.skills.bundled.discord-channel.adapter",
            )
            DiscordSetupTool = adapter_mod.DiscordSetupTool

            sl = app.state.skill_loader
            sk = sl.skills.get("discord-channel")
            adapter = getattr(sk.context, "adapter", None) if sk else None
            if adapter is None:
                return None
            tool = DiscordSetupTool(adapter, agent_id)
            result = await tool.execute({"bot_token": token})
            if result.content:
                return str(result.content)
        except Exception as exc:
            logger.warning("wire discord after configure_member: %s", exc)
            return f"Config saved but Discord gateway wiring failed: {exc}"

    if skill_name == "slack-channel":
        if "bot_token" not in patch:
            return None
        token = str(patch.get("bot_token") or "").strip()
        if not token:
            return None
        secret = str(patch.get("signing_secret") or "").strip()
        if not secret:
            return (
                "Slack config saved. Provide signing_secret in skill_config and "
                "call configure_member again to connect the Events API."
            )
        try:
            import importlib

            from server.main import app

            adapter_mod = importlib.import_module(
                "nls.skills.bundled.slack-channel.adapter",
            )
            SlackSetupTool = adapter_mod.SlackSetupTool

            sl = app.state.skill_loader
            sk = sl.skills.get("slack-channel")
            adapter = getattr(sk.context, "adapter", None) if sk else None
            if adapter is None:
                return None
            tool = SlackSetupTool(adapter, agent_id)
            result = await tool.execute({
                "bot_token": token,
                "signing_secret": secret,
            })
            if result.content:
                return str(result.content)
        except Exception as exc:
            logger.warning("wire slack after configure_member: %s", exc)
            return f"Config saved but Slack wiring failed: {exc}"

    return None


def _load_saved_bot_token(agent_id: str, skill_name: str) -> str:
    try:
        from server.main import app

        sl = app.state.skill_loader
        sk = sl.skills.get(skill_name)
        ctx = getattr(sk, "context", None) if sk else None
        if ctx is None:
            return ""
        cfg = ctx.load_config(agent_id=agent_id)
        return str(cfg.get("bot_token") or "").strip()
    except Exception:
        return ""


def _mirror_scope_selections(
    lead_cfg: dict[str, Any],
    member_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Copy lead channel enablement to member for channels the member bot can see."""
    from nls.skills.channel_scope import scoped_channels_from_config

    lead_channels = scoped_channels_from_config(lead_cfg).get("channels") or {}
    member_channels = scoped_channels_from_config(member_cfg).get("channels") or {}
    selections: list[dict[str, Any]] = []
    for cid, lead_entry in lead_channels.items():
        if not isinstance(lead_entry, dict) or not lead_entry.get("enabled_desired"):
            continue
        member_entry = member_channels.get(cid)
        if not isinstance(member_entry, dict):
            continue
        if not member_entry.get("platform_access", True):
            continue
        selections.append({
            "channel_id": cid,
            "enabled": True,
            "require_mention": bool(lead_entry.get("require_mention", True)),
        })
    return selections


async def finalize_discord_member_channels(
    member_id: str,
    *,
    lead_agent_id: str | None = None,
    mirror_lead_scope: bool = True,
) -> str | None:
    """Sync Discord guild/channel scope for a member bot; optionally mirror lead scope."""
    try:
        from server.main import app

        from nls.skills.channel_scope import effective_channel_ids

        sl = app.state.skill_loader
        sk = sl.skills.get("discord-channel")
        adapter = getattr(sk.context, "adapter", None) if sk and sk.context else None
        if adapter is None:
            return None

        updated = await adapter.sync_channels_from_platform(member_id, auto_enable=True)
        listening = effective_channel_ids(updated)

        if not listening and mirror_lead_scope and lead_agent_id:
            lead_cfg = adapter._agent_cfg(lead_agent_id)
            member_cfg = adapter._agent_cfg(member_id)
            selections = _mirror_scope_selections(lead_cfg, member_cfg)
            if selections:
                updated = await adapter.apply_channels_bulk(member_id, selections)
                listening = effective_channel_ids(updated)

        status = adapter.get_status(member_id)
        sync_err = str(status.get("sync_error") or "").strip()
        active = int(status.get("active_channel_count") or 0)
        if active:
            return f"Discord scope synced — {active} channel(s) listening."
        if sync_err:
            return f"Discord scope sync incomplete: {sync_err}"
        return (
            "Discord scope synced but no channels listening — ensure the bot is invited "
            "to the server, then squad(action='invite_squad_bots', channel_id=...) and "
            "squad(action='sync_member_channels', target_agent_id=...)."
        )
    except Exception as exc:
        logger.warning("finalize_discord_member_channels failed for %s: %s", member_id, exc)
        return f"Discord scope sync failed: {exc}"


def assert_squad_lead_may_configure(
    lead_agent_id: str,
    target_agent_id: str,
) -> None:
    """Raise PermissionError/ValueError if lead cannot configure target."""
    from server.main import app

    sm = getattr(app.state, "squad_manager", None)
    if sm is None:
        raise RuntimeError("Squad manager not available")
    squad = sm.get_squad_for_agent(lead_agent_id)
    if squad is None or not squad.is_lead(lead_agent_id):
        raise PermissionError("Only the squad lead may configure member agents")
    if target_agent_id != lead_agent_id and not squad.is_member(target_agent_id):
        raise ValueError("target_agent_id must be a squad member")
