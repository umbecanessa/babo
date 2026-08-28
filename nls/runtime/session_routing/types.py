"""Session routing types — delivery intents, contexts, and targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DeliveryIntent(str, Enum):
    REPLY = "reply"
    PROGRESS = "progress"
    REPORT = "report"
    ANNOUNCE = "announce"
    STEER = "steer"
    MIRROR = "mirror"


class ReportMode(str, Enum):
    ORIGIN_OR_EXPLICIT = "origin_or_explicit"
    BROADCAST_MATCHING = "broadcast_matching"
    PRIMARY_FALLBACK = "primary_fallback"


@dataclass
class RoutingContext:
    """Who is speaking and under what work binding."""

    source: str = ""
    foreground_session_key: str = ""
    foreground_source: str = ""
    origin_session_key: str = ""
    todo_id: str = ""
    todo_title: str = ""
    todo_description: str = ""
    prompt: str = ""
    explicit_targets: list[str] = field(default_factory=list)
    broadcast: bool = False


@dataclass
class DeliveryTarget:
    session_key: str
    intent: DeliveryIntent
    channel: str = ""
    reply_target: str = ""
    send_kwargs: dict[str, Any] = field(default_factory=dict)
    mirror: bool = False


@dataclass
class DeliveryOutcome:
    delivered: bool = False
    targets: list[str] = field(default_factory=list)
    home: bool = False
    skipped_reason: str = ""
    errors: list[str] = field(default_factory=list)
