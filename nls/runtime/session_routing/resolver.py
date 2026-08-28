"""Route resolution — explicit targets only, inclusion + exclusion policy."""

from __future__ import annotations

import logging
import re
from typing import Any

from nls.runtime.autonomous_completion_delivery import parse_session_key_from_text
from nls.runtime.session_routing.config import (
    ReportChannelPolicy,
    SessionRoutingConfig,
    _QA_PURPOSES,
)
from nls.runtime.session_routing.surface import (
    is_home_session_key,
    is_routable_surface_session_key,
)
from nls.runtime.session_routing.types import DeliveryIntent, DeliveryTarget, RoutingContext

logger = logging.getLogger(__name__)

_INVESTIGATION_HINTS = re.compile(
    r"\b(bug|qa|investigat|root cause|black screen|regression|crash)\b",
    re.IGNORECASE,
)


def _intent_name(intent: DeliveryIntent | str) -> str:
    return intent.value if isinstance(intent, DeliveryIntent) else str(intent or "").strip().lower()


def _is_blocked(
    cfg: SessionRoutingConfig,
    *,
    session_key: str,
    channel: str,
    intent: DeliveryIntent,
) -> bool:
    name = _intent_name(intent)
    for rule in cfg.exclusions:
        blocks = rule.block_intents or ["report", "progress", "announce", "reply"]
        if name not in blocks and blocks != ["*"]:
            continue
        if rule.session_key and rule.session_key == session_key:
            return True
        if rule.channel and rule.channel == (channel or "").split(":")[0].lower():
            return True
    return False


def _channel_matches_policy(
    row: ReportChannelPolicy,
    *,
    intent: DeliveryIntent,
    investigation: bool,
    todo_tags: set[str],
) -> bool:
    intent_name = _intent_name(intent)
    allowed_intents = {i.lower() for i in (row.intents or [])}
    if allowed_intents and intent_name not in allowed_intents:
        return False
    if row.exclusion_tags and todo_tags.intersection(set(row.exclusion_tags)):
        return False
    if not row.purposes:
        return True
    if investigation:
        return bool(set(row.purposes) & _QA_PURPOSES)
    return row.broadcast_default


def _todo_tags_from_item(todo_item: Any) -> set[str]:
    if todo_item is None:
        return set()
    tags = {str(t).strip().lower() for t in (getattr(todo_item, "tags", None) or []) if str(t).strip()}
    return tags


def _todo_direct_session_key(todo_item: Any) -> str:
    if todo_item is None:
        return ""
    direct = str(getattr(todo_item, "report_session_key", "") or "").strip()
    if direct and is_routable_surface_session_key(direct):
        return direct
    for tag in getattr(todo_item, "tags", []) or []:
        raw = str(tag).strip()
        if raw.lower().startswith("session:"):
            key = raw.split(":", 1)[1].strip()
            if is_routable_surface_session_key(key):
                return key
    return ""


def _runtime_origin_session(ctx: RoutingContext) -> str:
    if ctx.origin_session_key and is_routable_surface_session_key(ctx.origin_session_key):
        return ctx.origin_session_key
    src = (ctx.foreground_source or "").strip()
    if src.startswith("user:channel"):
        sk = (ctx.foreground_session_key or "").strip()
        if is_routable_surface_session_key(sk):
            return sk
    return ""


def _policy_matched_channels(
    cfg: SessionRoutingConfig,
    *,
    intent: DeliveryIntent,
    investigation: bool,
    todo_tags: set[str],
    broadcast_only: bool = False,
) -> list[str]:
    keys: list[str] = []
    for row in cfg.report_channels:
        if broadcast_only and not row.broadcast_default:
            continue
        if not is_routable_surface_session_key(row.session_key):
            continue
        if not _channel_matches_policy(
            row, intent=intent, investigation=investigation, todo_tags=todo_tags,
        ):
            continue
        if _is_blocked(cfg, session_key=row.session_key, channel=row.session_key.split(":")[0], intent=intent):
            continue
        keys.append(row.session_key)
    return keys


def _primary_targets(cfg: SessionRoutingConfig, intent: DeliveryIntent) -> list[str]:
    primary = (cfg.primary_reachability_session_key or cfg.default_home_session_key or "websocket:main").strip()
    if _is_blocked(cfg, session_key=primary, channel=primary.split(":")[0] if ":" in primary else "websocket", intent=intent):
        return []
    return [primary]


def _home_mirror_target(cfg: SessionRoutingConfig, intent: DeliveryIntent) -> str | None:
    home = (cfg.default_home_session_key or "websocket:main").strip()
    if not cfg.mirror_channel_progress_to_home:
        return None
    if _is_blocked(cfg, session_key=home, channel="websocket", intent=DeliveryIntent.MIRROR):
        return None
    return home


def resolve_delivery_targets(
    runtime: Any,
    cfg: SessionRoutingConfig,
    *,
    intent: DeliveryIntent,
    ctx: RoutingContext,
    todo_item: Any | None = None,
) -> list[DeliveryTarget]:
    """Resolve explicit delivery targets for an intent."""
    intent_name = _intent_name(intent)
    todo_tags = _todo_tags_from_item(todo_item)
    blob = " ".join(
        x for x in (
            ctx.todo_title,
            ctx.todo_description,
            ctx.prompt,
            getattr(todo_item, "notes", "") if todo_item else "",
        ) if x
    )
    investigation = bool(_INVESTIGATION_HINTS.search(blob))

    explicit: list[str] = []
    if ctx.explicit_targets:
        explicit.extend(ctx.explicit_targets)
    todo_key = _todo_direct_session_key(todo_item)
    if todo_key:
        explicit.append(todo_key)
    for part in (blob, ctx.prompt):
        parsed = parse_session_key_from_text(part or "")
        if parsed:
            explicit.append(parsed)
    origin = _runtime_origin_session(ctx)
    if origin and intent in (DeliveryIntent.REPLY, DeliveryIntent.PROGRESS, DeliveryIntent.REPORT):
        explicit.insert(0, origin)

    seen: set[str] = set()
    ordered: list[str] = []
    for key in explicit:
        sk = (key or "").strip()
        if not sk or sk in seen:
            continue
        if intent_name in ("report", "announce") and not (
            is_routable_surface_session_key(sk, runtime) or is_home_session_key(sk)
        ):
            continue
        if _is_blocked(cfg, session_key=sk, channel=sk.split(":")[0], intent=intent):
            continue
        seen.add(sk)
        ordered.append(sk)

    if ordered:
        targets = [
            DeliveryTarget(session_key=sk, intent=intent)
            for sk in ordered
        ]
        mirror = _home_mirror_target(cfg, intent)
        if mirror and mirror not in seen and is_routable_surface_session_key(origin, runtime):
            targets.append(DeliveryTarget(session_key=mirror, intent=DeliveryIntent.MIRROR, mirror=True))
        return targets

    if ctx.broadcast or cfg.default_report_mode == "broadcast_matching":
        matched = _policy_matched_channels(
            cfg, intent=intent, investigation=investigation, todo_tags=todo_tags,
        )
        if matched:
            return [DeliveryTarget(session_key=sk, intent=intent) for sk in matched]

    if intent in (DeliveryIntent.REPORT, DeliveryIntent.ANNOUNCE):
        matched = _policy_matched_channels(
            cfg,
            intent=intent,
            investigation=investigation,
            todo_tags=todo_tags,
            broadcast_only=(cfg.default_report_mode != "origin_or_explicit"),
        )
        if len(matched) == 1:
            return [DeliveryTarget(session_key=matched[0], intent=intent)]
        if len(matched) > 1 and ctx.broadcast:
            return [DeliveryTarget(session_key=sk, intent=intent) for sk in matched]

    if intent in (DeliveryIntent.PROGRESS, DeliveryIntent.REPORT, DeliveryIntent.ANNOUNCE):
        if origin:
            return [DeliveryTarget(session_key=origin, intent=intent)]
        primary = _primary_targets(cfg, intent)
        if primary:
            return [DeliveryTarget(session_key=primary[0], intent=intent)]

    if intent == DeliveryIntent.REPLY and origin:
        return [DeliveryTarget(session_key=origin, intent=intent)]

    return []


def resolve_report_session_keys(
    runtime: Any,
    cfg: SessionRoutingConfig,
    *,
    ctx: RoutingContext,
    todo_item: Any | None = None,
) -> list[str]:
    targets = resolve_delivery_targets(
        runtime,
        cfg,
        intent=DeliveryIntent.REPORT,
        ctx=ctx,
        todo_item=todo_item,
    )
    return [t.session_key for t in targets if t.session_key]
