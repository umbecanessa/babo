"""Context-aware breadcrumb hints injected after tool results.

Instead of hardcoding "NEXT: do X" strings inside individual tool
implementations, this module evaluates a rule table against live runtime
context (available tools, deferred actions, plan state, agent mode) and
returns optional system-message hints for the loop to inject.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

_COMM_SEND_TOOLS = frozenset({
    "whatsapp_send", "telegram_send", "email_send",
})


@dataclass(frozen=True, slots=True)
class BreadcrumbContext:
    """Snapshot of runtime state passed to each rule."""

    tool_name: str = ""
    action: str = ""
    is_error: bool = False
    result_details: dict[str, Any] = field(default_factory=dict)
    unlocked_tools: frozenset[str] = field(default_factory=frozenset)
    deferred_actions: tuple[dict[str, Any], ...] = ()
    communications_sent: tuple[str, ...] = ()
    is_coordinator: bool = False
    goals: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BreadcrumbRule:
    """Single breadcrumb rule.

    * *trigger*        – ``(tool_name, action)`` that must match.
                         ``action`` may be ``"*"`` to match any action.
    * *requires_tools* – all of these must be in ``unlocked_tools``.
    * *requires_any*   – at least one of these must be in ``unlocked_tools``.
    * *condition*      – optional predicate for extra checks.
    * *render*         – produces the hint text from the context.
    """

    trigger: tuple[str, str]
    render: Callable[[BreadcrumbContext], str]
    requires_tools: frozenset[str] = field(default_factory=frozenset)
    requires_any: frozenset[str] = field(default_factory=frozenset)
    condition: Callable[[BreadcrumbContext], bool] | None = None


class BreadcrumbEngine:
    """Evaluates rules against context and returns at most one hint."""

    __slots__ = ("_rules", "_index")

    def __init__(self, rules: list[BreadcrumbRule] | None = None) -> None:
        self._rules = list(rules or DEFAULT_RULES)
        self._index: dict[tuple[str, str], list[BreadcrumbRule]] = {}
        for r in self._rules:
            self._index.setdefault(r.trigger, []).append(r)

    def add_rule(self, rule: BreadcrumbRule) -> None:
        self._rules.append(rule)
        self._index.setdefault(rule.trigger, []).append(rule)

    def evaluate(self, ctx: BreadcrumbContext) -> str | None:
        """Return the first matching hint, or ``None``."""
        exact = self._index.get((ctx.tool_name, ctx.action), ())
        wildcard = self._index.get((ctx.tool_name, "*"), ())
        candidates = (*exact, *wildcard)
        for rule in candidates:
            if rule.requires_tools and not rule.requires_tools <= ctx.unlocked_tools:
                continue
            if rule.requires_any and not rule.requires_any & ctx.unlocked_tools:
                continue
            if rule.condition is not None:
                try:
                    if not rule.condition(ctx):
                        continue
                except Exception:
                    logger.debug("Breadcrumb condition failed", exc_info=True)
                    continue
            try:
                text = rule.render(ctx)
            except Exception:
                logger.debug("Breadcrumb render failed", exc_info=True)
                continue
            if text:
                return text
        return None


# -------------------------------------------------------------------
# Rule helpers
# -------------------------------------------------------------------

_KNOWN_CHANNELS = frozenset({"whatsapp", "telegram", "email"})


def _deferred_channels(ctx: BreadcrumbContext) -> list[str]:
    """Return deferred channel names not yet sent.

    Communications entries follow the format ``"channel to recipient: preview"``
    (produced by compactor ``_extract_file_ops``).  We extract the first token
    and match it against known channel names.
    """
    sent_channels: set[str] = set()
    for entry in ctx.communications_sent:
        token = entry.split(maxsplit=1)[0].lower() if entry else ""
        if token in _KNOWN_CHANNELS:
            sent_channels.add(token)

    return [
        da["channel"]
        for da in ctx.deferred_actions
        if da.get("channel")
        and da["channel"] in _KNOWN_CHANNELS
        and da["channel"] not in sent_channels
    ]


def _has_delegatable_steps(ctx: BreadcrumbContext) -> bool:
    steps = ctx.result_details.get("steps", [])
    return any(s.get("delegatable") for s in steps)


# -------------------------------------------------------------------
# Default rules
# -------------------------------------------------------------------

def _render_todo_plan(ctx: BreadcrumbContext) -> str:
    tid = ctx.result_details.get("todo_id", "???")
    return (
        f"[BREADCRUMB] NEXT: Create a plan for this todo -- "
        f"plan(action='create', todo_id='{tid}', "
        f"title='<descriptive title>'). "
        f"The title parameter is REQUIRED."
    )


def _render_plan_team(ctx: BreadcrumbContext) -> str:
    pid = ctx.result_details.get("plan_id", "???")
    return (
        f"[BREADCRUMB] NEXT: Create a team to execute delegatable steps -- "
        f"team(action='create', plan_id='{pid}'). "
        f"The plan_id parameter is REQUIRED."
    )


def _render_team_launch(ctx: BreadcrumbContext) -> str:
    tid = ctx.result_details.get("team_id", "???")
    return (
        f"[BREADCRUMB] NEXT: Launch the team to spawn delegates -- "
        f"team(action='launch', team_id='{tid}')."
    )


def _render_plan_notify(ctx: BreadcrumbContext) -> str:
    channels = _deferred_channels(ctx)
    if not channels:
        return ""
    tool_map = {
        "whatsapp": "whatsapp_send",
        "telegram": "telegram_send",
        "email": "email_send",
    }
    available = [
        ch for ch in channels
        if tool_map.get(ch, "") in ctx.unlocked_tools
    ]
    if not available:
        return ""
    tools_str = ", ".join(tool_map[ch] for ch in available)
    return (
        f"[BREADCRUMB] Notify the user of project completion via "
        f"{', '.join(available)}. Use {tools_str} with file_path to "
        f"attach deliverables (do NOT paste content as text or URLs). "
        f"Use contacts tool first if needed."
    )


def _render_wave_file_history(ctx: BreadcrumbContext) -> str:
    wave = ctx.result_details.get("wave")
    wave_label = f"Wave {wave}" if wave is not None else "Wave"
    return (
        f"[BREADCRUMB] {wave_label} complete. "
        f"Use file_history() to review what files delegates created or modified, "
        f"or file_history(path='<file>', detail=True) for full diffs."
    )


DEFAULT_RULES: list[BreadcrumbRule] = [
    # todo(add) → plan(create)
    BreadcrumbRule(
        trigger=("todo", "add"),
        requires_tools=frozenset({"plan"}),
        render=_render_todo_plan,
    ),
    # plan(create) with delegatable steps → team(create)
    BreadcrumbRule(
        trigger=("plan", "create"),
        requires_tools=frozenset({"team"}),
        condition=_has_delegatable_steps,
        render=_render_plan_team,
    ),
    # team(create) → team(launch)
    BreadcrumbRule(
        trigger=("team", "create"),
        requires_tools=frozenset({"team"}),
        render=_render_team_launch,
    ),
    # plan(complete) on top-level plan with deferred channels → notify
    BreadcrumbRule(
        trigger=("plan", "complete"),
        requires_any=_COMM_SEND_TOOLS,
        condition=lambda ctx: (
            not ctx.result_details.get("parent_id")
            and len(_deferred_channels(ctx)) > 0
        ),
        render=_render_plan_notify,
    ),
    # team(advance) → hint to use file_history to review delegate work
    BreadcrumbRule(
        trigger=("team", "advance"),
        requires_tools=frozenset({"file_history"}),
        render=_render_wave_file_history,
    ),
]
