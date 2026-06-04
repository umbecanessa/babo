"""Channel-agnostic interaction policy — presets, expansion, micro-inference.

User-facing presets map to per-skill config (private surface + shared surface).
Natural-language intent is resolved via micro-inference only — no keyword heuristics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

INTERACTION_PRESETS = frozenset({
    "owner_private_only",
    "shared_only",
    "owner_plus_shared",
    "trusted_allowlist",
    "open_community",
})

from nls.runtime.channel_policy_profiles import (
    CHANNEL_TO_SKILL,
    RUNTIME_CONFIG_KEYS,
    SKILL_TO_CHANNEL,
    runtime_config_keys,
)

EMAIL_THREAD_POLICIES = frozenset({"open", "owner_initiated", "allowlist", "disabled"})

CLASSIFY_CONFIDENCE_THRESHOLD = 0.75

_EMAIL_ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", re.IGNORECASE)

_CLASSIFY_SYSTEM = (
    "Classify how the user wants their agent to be reachable on a communication channel.\n"
    "Works in ANY language and informal jargon — infer meaning, do not match keywords.\n\n"
    "PRESETS (pick exactly one):\n"
    "  owner_private_only — only the owner in private/1:1; no groups/channels/threads\n"
    "  shared_only — no private DMs; only scoped groups/channels/email threads\n"
    "  owner_plus_shared — owner in private AND bot listens in shared spaces\n"
    "  trusted_allowlist — owner + explicit allowlist only (private and shared/thread)\n"
    "  open_community — open private + broad shared (warn if risky)\n\n"
    "Output JSON only:\n"
    '{"preset":"<preset>","owner_ref":"<owner id/email/username if stated>",'
    '"confidence":0.0-1.0,"needs_confirmation":true|false,'
    '"user_facing_summary":"<one sentence in English>"}\n\n'
    "Set needs_confirmation true when intent is ambiguous or widens access.\n"
    "If channel context is provided, respect what is already configured.\n"
)

INTERACTION_PRESET_META_KEY = "_interaction_preset"

INTERACTION_SETUP_HINT = (
    "Set who can reach the agent (private DMs + shared groups/channels/email threads):\n"
    "  1. channel_inspect(action='get', channel='<channel>') for current state\n"
    "  2. skill_configure(skill_name='...', interaction_mode='<preset>' OR "
    "interaction_intent='owner words', owner_confirm=true after ask_user())\n"
    "  Presets: owner_private_only | shared_only | owner_plus_shared | "
    "trusted_allowlist | open_community\n"
    "  Never dm_policy='enabled'. The integration enabled flag is separate from reachability."
)


@dataclass
class InteractionClassification:
    preset: str
    owner_ref: str = ""
    confidence: float = 0.0
    needs_confirmation: bool = True
    user_facing_summary: str = ""
    raw: dict[str, Any] | None = None


def skill_name_to_channel(skill_name: str) -> str | None:
    return SKILL_TO_CHANNEL.get((skill_name or "").strip())


def channel_skill_name(channel: str) -> str | None:
    return CHANNEL_TO_SKILL.get((channel or "").strip().lower())


def interaction_runtime_keys(skill_name: str) -> frozenset[str]:
    """Schema-external keys persisted when applying interaction policy."""
    return runtime_config_keys(skill_name) | {INTERACTION_PRESET_META_KEY}


def _stamp_preset(patch: dict[str, Any], preset: str) -> dict[str, Any]:
    patch[INTERACTION_PRESET_META_KEY] = preset
    return patch


def _normalize_owner_list(
    owner: str | list[str] | None,
    current: dict[str, Any],
) -> list[str]:
    if owner is None:
        oi = current.get("owner_identity", [])
        if isinstance(oi, str) and oi.strip():
            return [oi.strip()]
        if isinstance(oi, list):
            return [str(x).strip() for x in oi if str(x).strip()]
        return []
    if isinstance(owner, str):
        return [owner.strip()] if owner.strip() else []
    return [str(x).strip() for x in owner if str(x).strip()]


def _merge_allow_from(current: dict[str, Any], owners: list[str]) -> list[str]:
    allow = list(current.get("allow_from") or [])
    allow_lower = {str(a).lower() for a in allow}
    for o in owners:
        if o.lower() not in allow_lower:
            allow.append(o)
    return allow


def expand_interaction_preset(
    skill_name: str,
    preset: str,
    *,
    owner: str | list[str] | None = None,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map channel-agnostic preset to a skill config patch."""
    preset = (preset or "").strip().lower()
    if preset not in INTERACTION_PRESETS:
        raise ValueError(
            f"Unknown interaction preset '{preset}'. "
            f"Valid: {', '.join(sorted(INTERACTION_PRESETS))}"
        )

    current = dict(current or {})
    owners = _normalize_owner_list(owner, current)
    channel = skill_name_to_channel(skill_name)
    if channel is None:
        raise ValueError(f"Skill '{skill_name}' has no interaction policy profile")

    if channel in ("discord", "slack"):
        patch = _expand_workspace_channel(preset, owners, current)
    elif channel in ("telegram", "whatsapp"):
        patch = _expand_messaging_group(preset, owners, current)
    elif channel == "email":
        patch = _expand_email(preset, owners, current)
    else:
        raise ValueError(f"No expander for skill '{skill_name}'")
    return _stamp_preset(patch, preset)


def _expand_workspace_channel(
    preset: str,
    owners: list[str],
    current: dict[str, Any],
) -> dict[str, Any]:
    from nls.skills.channel_scope import compile_groups_policy

    patch: dict[str, Any] = {}

    if preset == "owner_private_only":
        patch["dm_policy"] = "allowlist"
        patch["allow_from"] = list(owners)
        scoped = deepcopy(current.get("scoped_channels") or {"guilds": {}, "channels": {}})
        for entry in scoped.get("channels", {}).values():
            if isinstance(entry, dict):
                entry["enabled_desired"] = False
                entry["effective_enabled"] = False
        patch["scoped_channels"] = scoped
        patch["groups"] = {"__none__": {"require_mention": True, "allow_from": []}}

    elif preset == "shared_only":
        patch["dm_policy"] = "disabled"
        merged = deepcopy(current)
        merged.update(patch)
        patch["groups"] = compile_groups_policy(merged)

    elif preset == "owner_plus_shared":
        patch["dm_policy"] = "allowlist"
        patch["allow_from"] = _merge_allow_from(current, owners)
        merged = deepcopy(current)
        merged.update(patch)
        patch["groups"] = compile_groups_policy(merged)

    elif preset == "trusted_allowlist":
        allow = _merge_allow_from(current, owners)
        patch["dm_policy"] = "allowlist"
        patch["allow_from"] = allow
        merged = deepcopy(current)
        merged.update(patch)
        groups = compile_groups_policy(merged)
        for gcfg in groups.values():
            if isinstance(gcfg, dict):
                gcfg["allow_from"] = list(allow) if allow else []
        patch["groups"] = groups

    elif preset == "open_community":
        patch["dm_policy"] = "open"
        merged = deepcopy(current)
        merged.update(patch)
        patch["groups"] = compile_groups_policy(merged)

    return patch


def _expand_messaging_group(
    preset: str,
    owners: list[str],
    current: dict[str, Any],
) -> dict[str, Any]:
    patch: dict[str, Any] = {}

    if preset == "owner_private_only":
        patch["dm_policy"] = "allowlist"
        patch["allow_from"] = list(owners)
        patch["groups"] = {"__none__": {"require_mention": True, "allow_from": []}}

    elif preset == "shared_only":
        patch["dm_policy"] = "disabled"
        patch["groups"] = {"*": {"require_mention": True, "allow_from": ["*"]}}

    elif preset == "owner_plus_shared":
        patch["dm_policy"] = "allowlist"
        patch["allow_from"] = _merge_allow_from(current, owners)
        patch["groups"] = {"*": {"require_mention": True, "allow_from": ["*"]}}

    elif preset == "trusted_allowlist":
        allow = _merge_allow_from(current, owners)
        patch["dm_policy"] = "allowlist"
        patch["allow_from"] = allow
        patch["groups"] = {
            "*": {
                "require_mention": True,
                "allow_from": list(allow) if allow else [],
            },
        }

    elif preset == "open_community":
        patch["dm_policy"] = "open"
        patch["groups"] = {"*": {"require_mention": False, "allow_from": ["*"]}}

    return patch


def _expand_email(
    preset: str,
    owners: list[str],
    current: dict[str, Any],
) -> dict[str, Any]:
    patch: dict[str, Any] = {}

    if preset == "owner_private_only":
        patch["dm_policy"] = "allowlist"
        patch["allow_from"] = list(owners)
        patch["thread_policy"] = "disabled"

    elif preset == "shared_only":
        patch["dm_policy"] = "disabled"
        patch["thread_policy"] = "owner_initiated"

    elif preset == "owner_plus_shared":
        patch["dm_policy"] = "allowlist"
        patch["allow_from"] = _merge_allow_from(current, owners)
        patch["thread_policy"] = "owner_initiated"

    elif preset == "trusted_allowlist":
        allow = _merge_allow_from(current, owners)
        patch["dm_policy"] = "allowlist"
        patch["allow_from"] = allow
        patch["thread_policy"] = "allowlist"

    elif preset == "open_community":
        patch["dm_policy"] = "open"
        patch["thread_policy"] = "open"

    return patch


def infer_preset_from_config(skill_name: str, cfg: dict[str, Any]) -> str | None:
    """Structural inverse of expand (best-effort, no NLP)."""
    stored = str(cfg.get(INTERACTION_PRESET_META_KEY, "")).strip().lower()
    if stored in INTERACTION_PRESETS:
        return stored

    channel = skill_name_to_channel(skill_name)
    if not channel:
        return None

    dm = str(cfg.get("dm_policy", "")).lower()
    shared_off = False
    shared_on = False
    shared_allowlist_only = False

    if channel in ("discord", "slack"):
        try:
            from nls.skills.channel_scope import list_scoped_channels

            chs = list_scoped_channels(cfg)
            active = sum(1 for c in chs if c.get("effective_enabled"))
            shared_off = active == 0
            shared_on = active > 0
            groups = cfg.get("groups") or {}
            if shared_on and groups:
                shared_allowlist_only = all(
                    isinstance(g, dict)
                    and "*" not in (g.get("allow_from") or [])
                    for g in groups.values()
                )
        except Exception:
            pass
    elif channel in ("telegram", "whatsapp"):
        g = cfg.get("groups") or {}
        shared_off = bool(g.get("__none__"))
        shared_on = "*" in g or len(g) > 1 or (
            len(g) == 1 and "__none__" not in g
        )
        if shared_on and "*" in g:
            af = (g.get("*") or {}).get("allow_from") or []
            shared_allowlist_only = bool(af) and "*" not in af
    elif channel == "email":
        tp = str(cfg.get("thread_policy", "owner_initiated")).lower()
        shared_off = tp == "disabled"
        shared_on = tp != "disabled"
        shared_allowlist_only = tp == "allowlist"

    if dm == "allowlist" and shared_off:
        return "owner_private_only"
    if dm == "disabled" and shared_off:
        return None
    if dm == "disabled" and shared_on:
        return "shared_only"
    if dm == "allowlist" and shared_on and shared_allowlist_only:
        return "trusted_allowlist"
    if dm == "allowlist" and shared_on:
        return "owner_plus_shared"
    if dm == "open":
        return "open_community"
    return None


def summarize_interaction_mode(skill_name: str, cfg: dict[str, Any]) -> str:
    """Factual one-line mode summary for channel_inspect."""
    channel = skill_name_to_channel(skill_name)
    if not channel:
        return ""

    dm = cfg.get("dm_policy", "?")
    parts: list[str] = [f"private={dm}"]

    if channel in ("discord", "slack"):
        try:
            from nls.skills.channel_scope import list_scoped_channels

            channels = list_scoped_channels(cfg)
            active = sum(1 for c in channels if c.get("effective_enabled"))
            if channels:
                active_chs = [c for c in channels if c.get("effective_enabled")]
                if active_chs and all(c.get("require_mention", True) for c in active_chs):
                    mention = "mention-required"
                elif active_chs:
                    mention = "mixed-mention"
                else:
                    mention = "none listening"
                parts.append(f"shared={active}/{len(channels)} channels, {mention}")
            else:
                parts.append("shared=none")
        except Exception:
            parts.append("shared=unknown")

    elif channel in ("telegram", "whatsapp"):
        groups = cfg.get("groups") or {}
        if groups.get("__none__"):
            parts.append("shared=off")
        elif "*" in groups:
            gm = groups["*"]
            rm = (
                "mention-required"
                if gm.get("require_mention", True)
                else "mention-optional"
            )
            parts.append(f"shared=groups *, {rm}")
        elif groups:
            parts.append(f"shared={len(groups)} group(s)")
        else:
            parts.append("shared=default")

    elif channel == "email":
        tp = cfg.get("thread_policy", "owner_initiated")
        parts.append(f"shared=threads:{tp}")

    preset = infer_preset_from_config(skill_name, cfg)
    if preset:
        return f"mode={preset} | " + " | ".join(parts)
    if parts[0] == "private=disabled" and any("shared=off" in p or "shared=none" in p for p in parts[1:]):
        return "mode=unset (private and shared off) | " + " | ".join(parts)
    return "mode=custom | " + " | ".join(parts)


def parse_email_addresses(header: str) -> list[str]:
    if not header:
        return []
    return [normalize_email_address(m) for m in _EMAIL_ADDR_RE.findall(header)]


def normalize_email_address(raw: str) -> str:
    """Extract bare address from 'Name <user@host>' or plain email."""
    text = (raw or "").strip()
    angle = re.search(r"<([^>]+)>", text)
    if angle:
        text = angle.group(1).strip()
    return text.lower()


def is_shared_email_inbound(
    headers: dict[str, str],
    agent_addresses: set[str],
) -> bool:
    """True when the message is a multi-party thread (To/CC), not cold 1:1."""
    to_addrs = set(parse_email_addresses(headers.get("To", headers.get("to", ""))))
    cc_addrs = set(parse_email_addresses(headers.get("Cc", headers.get("cc", ""))))
    if cc_addrs:
        return True
    agents = {a.lower() for a in agent_addresses if a}
    non_agent_to = to_addrs - agents
    return len(non_agent_to) >= 1


def check_email_inbound_policy(
    cfg: dict[str, Any],
    sender: str,
    headers: dict[str, str],
    agent_addresses: set[str],
) -> bool:
    """Private surface (1:1) vs shared surface (multi-party threads)."""
    from nls.runtime.channels import PolicyEnforcer

    sender_l = normalize_email_address(sender)
    if not sender_l:
        return False

    if is_shared_email_inbound(headers, agent_addresses):
        tp = str(cfg.get("thread_policy", "owner_initiated")).lower()
        if tp == "disabled":
            return False
        if tp == "open":
            return True

        owners = cfg.get("owner_identity", [])
        if isinstance(owners, str):
            owners = [owners]
        owners_l = {str(o).lower() for o in owners if o}
        allow_l = {str(a).lower() for a in cfg.get("allow_from", []) if a}

        participants: set[str] = set()
        participants.update(parse_email_addresses(headers.get("To", headers.get("to", ""))))
        participants.update(parse_email_addresses(headers.get("Cc", headers.get("cc", ""))))
        participants.add(sender_l)

        if tp == "owner_initiated":
            return bool(owners_l & participants) or sender_l in owners_l
        if tp == "allowlist":
            return bool(participants) and all(
                p in allow_l | owners_l for p in participants
            )
        return False

    enforcer = PolicyEnforcer(cfg)
    return enforcer.check_dm(sender_l)


def _parse_classification_blob(blob: str) -> InteractionClassification | None:
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        start = blob.find("{")
        end = blob.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(blob[start : end + 1])
        except json.JSONDecodeError:
            return None

    if not isinstance(parsed, dict):
        return None

    preset = str(parsed.get("preset", "")).strip().lower()
    if preset not in INTERACTION_PRESETS:
        return None

    confidence = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0

    needs_confirmation = parsed.get("needs_confirmation", True)
    if not isinstance(needs_confirmation, bool):
        needs_confirmation = confidence < CLASSIFY_CONFIDENCE_THRESHOLD

    return InteractionClassification(
        preset=preset,
        owner_ref=str(parsed.get("owner_ref", "") or "").strip(),
        confidence=confidence,
        needs_confirmation=needs_confirmation,
        user_facing_summary=str(parsed.get("user_facing_summary", "") or "").strip(),
        raw=parsed,
    )


async def classify_interaction_intent(
    vllm_client: Any,
    user_intent: str,
    *,
    skill_name: str,
    channel_summary: str = "",
    history: list[dict] | None = None,
    adapter_name: str | None = None,
    owner_hint: str = "",
) -> InteractionClassification:
    """Micro-inference: natural language → closed preset enum (any language)."""
    if not (user_intent or "").strip():
        return InteractionClassification(
            preset="",
            needs_confirmation=True,
            user_facing_summary="No intent text provided.",
        )

    system = _CLASSIFY_SYSTEM
    if channel_summary:
        system += f"\nCURRENT CHANNEL STATE:\n{channel_summary.strip()}\n"
    if owner_hint:
        system += f"\nOwner hint from caller: {owner_hint}\n"
    system += f"\nTarget skill: {skill_name}\n"

    msgs: list[dict] = [{"role": "system", "content": system}]
    if history:
        for turn in history[-4:]:
            role = turn.get("role", "user")
            content = turn.get("content") or ""
            if role in ("user", "assistant") and content:
                msgs.append({"role": role, "content": content[:400]})
    msgs.append({"role": "user", "content": user_intent.strip()})

    try:
        from nls.runtime.inference_compat import prepare_micro_inference

        _micro_msgs, _micro_body = prepare_micro_inference(
            msgs, vllm_client, adapter_name=adapter_name,
        )
        result = await asyncio.wait_for(
            vllm_client.generate(
                messages=_micro_msgs,
                adapter_name=adapter_name,
                max_tokens=256,
                temperature=0.1,
                extra_body=_micro_body,
            ),
            timeout=30.0,
        )
        text = result.text if hasattr(result, "text") else str(result or "")
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        blob = text[start : end + 1] if start != -1 and end != -1 else text
        classified = _parse_classification_blob(blob)
        if classified is not None:
            if classified.confidence < CLASSIFY_CONFIDENCE_THRESHOLD:
                classified.needs_confirmation = True
            return classified
        logger.warning("interaction_policy: unparseable classify blob: %s", blob[:200])
    except Exception as exc:
        logger.warning("interaction_policy classify failed: %s", exc)

    return InteractionClassification(
        preset="",
        needs_confirmation=True,
        confidence=0.0,
        user_facing_summary=(
            "Could not classify interaction intent — use interaction_mode "
            f"({', '.join(sorted(INTERACTION_PRESETS))}) or ask_user() to confirm."
        ),
    )


def finalize_workspace_groups(cfg: dict[str, Any], skill_name: str) -> None:
    """Recompile groups after scoped_channels patch (discord/slack)."""
    if skill_name not in ("discord-channel", "slack-channel"):
        return
    from nls.skills.channel_scope import compile_groups_policy

    cfg["groups"] = compile_groups_policy(cfg)
