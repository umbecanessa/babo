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

from nls.agentic.profile_guard_policy import (
    breadcrumb_rule_matches_profile,
    em_static_tool_hints_enabled,
    normalize_profile,
    solo_static_tool_hints_enabled,
)

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
    orchestration_profile: str = "solo_structured"


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
    # When set, rule only fires for these orchestration profiles.
    profiles: frozenset[str] | None = None


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
            if not breadcrumb_rule_matches_profile(
                rule.profiles, ctx.orchestration_profile,
            ):
                continue
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
        f"[BREADCRUMB] Plan created — close the planning loop:\n"
        f"1) switch_mode(mode='delegating') if still in planning\n"
        f"2) team(action='create', plan_id='{pid}') — plan_id REQUIRED\n"
        f"3) team(action='launch', team_id=<new_team_id>)\n"
        f"4) switch_mode(mode='monitoring') → await_delegates(summary='...')\n"
        f"Do NOT scaffold or write project files yourself — Wave 0 delegates do that."
    )


def _render_plan_execute_solo(ctx: BreadcrumbContext) -> str:
    pid = ctx.result_details.get("plan_id", "???")
    steps = ctx.result_details.get("steps", [])
    first_label = ""
    if steps and isinstance(steps[0], dict):
        first_label = str(steps[0].get("label") or steps[0].get("name") or "").strip()
    step_hint = (
        f"Start with step 1 ({first_label!r}): "
        if first_label
        else "Start with step 1: "
    )
    return (
        f"[BREADCRUMB] Plan {pid} created — SOLO workflow (no team waves):\n"
        f"1) switch_mode(mode='executing') if still in planning\n"
        f"2) {step_hint}plan(action='update', step_id=..., status='in_progress'), "
        f"then do the work yourself (write/bash/edit)\n"
        f"3) Mark each step done before moving on — do NOT use team or delegate\n"
        f"4) When all steps finish: plan(action='complete', plan_id='{pid}')"
    )


def _render_team_launch(ctx: BreadcrumbContext) -> str:
    tid = ctx.result_details.get("team_id", "???")
    return (
        f"[BREADCRUMB] NEXT: Launch the team to spawn delegates -- "
        f"team(action='launch', team_id='{tid}')."
    )


def _render_team_launch_duplicate(ctx: BreadcrumbContext) -> str:
    tid = ctx.result_details.get("team_id", "???")
    return (
        f"[BREADCRUMB] Team already exists — do NOT team(create) again. "
        f"NEXT: team(action='launch', team_id='{tid}')."
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


def _render_accept_partial_advance(ctx: BreadcrumbContext) -> str:
    pid = ctx.result_details.get("plan_id", "???")
    return (
        f"[BREADCRUMB] Partial step accepted on plan {pid}. "
        f"NEXT: team(action='inspect') on the wave team once, then "
        f"team(action='advance', team_id=<team_id>) if the wave is complete. "
        f"Then team(action='create', plan_id='{pid}', wave=N) → "
        f"team(action='launch') for the next pending delegatable steps. "
        f"Do NOT plan(delete) or plan(create) from scratch."
    )


def _render_fix_deps_team(ctx: BreadcrumbContext) -> str:
    pid = ctx.result_details.get("plan_id", "???")
    if ctx.result_details.get("cycles_remaining", 0) > 0:
        return (
            f"[BREADCRUMB] Cycles remain on {pid} — "
            f"plan(action='update', step_id='...', depends_on=[...]) "
            f"then retry fix_dependencies."
        )
    return (
        f"[BREADCRUMB] Graph repaired on {pid}. "
        f"NEXT: team(action='create', plan_id='{pid}', wave=N) → "
        f"team(action='launch')."
    )


def _render_create_needs_advance(ctx: BreadcrumbContext) -> str:
    tid = ctx.result_details.get("prior_team_id", "???")
    return (
        f"[BREADCRUMB] Wave finished but not advanced. "
        f"NEXT: team(action='advance', team_id='{tid}') — "
        f"then team(action='create', plan_id='{ctx.result_details.get('plan_id', '')}', "
        f"wave=N+1) or team(action='launch') on the auto-created team."
    )


def _render_inspect_needs_advance(ctx: BreadcrumbContext) -> str:
    tid = ctx.result_details.get("team_id", "???")
    return (
        f"[BREADCRUMB] Team {tid} is done but not advanced. "
        f"NEXT: team(action='advance', team_id='{tid}') before creating "
        f"or launching the next wave."
    )


def _render_inspect_completion_review(ctx: BreadcrumbContext) -> str:
    from nls.agentic.verification_hints import completion_review_verify_breadcrumb

    tid = ctx.result_details.get("team_id", "")
    return completion_review_verify_breadcrumb(team_id=tid or "...")


def _render_intervene_approve(ctx: BreadcrumbContext) -> str:
    """Use wave-aware text from team(intervene) — do not always block advance."""
    return (ctx.result_details.get("approve_breadcrumb") or "").strip()


def _render_advance_blocked(ctx: BreadcrumbContext) -> str:
    return (
        "[BREADCRUMB] Advance rejected — running delegates or pending reviews. "
        "team(inspect) or await_delegates; retry advance only when the wave "
        "is fully quiet."
    )


def _render_plan_ready_to_close(ctx: BreadcrumbContext) -> str:
    from nls.agentic.plan_work import format_plan_closure_nudge

    pid = str(ctx.result_details.get("plan_id") or "")
    if pid:
        return format_plan_closure_nudge(pid)
    return (
        "[BREADCRUMB] All plan steps are done. "
        "plan(verify) → plan(complete) → task_complete(summary='...')."
    )


def _render_plan_verify_passed(ctx: BreadcrumbContext) -> str:
    pid = str(ctx.result_details.get("plan_id") or "")
    return (
        f"[BREADCRUMB] Verify passed for plan {pid or 'active'}. "
        f"NEXT: plan(action='complete', plan_id='{pid}') then "
        "task_complete(summary='...')."
    )


def _render_post_launch_monitor(ctx: BreadcrumbContext) -> str:
    return (
        "[BREADCRUMB] Team launched — delegates are running in the background. "
        "NEXT: await_delegates(summary='...') to end this turn cleanly. "
        "Do NOT poll inspect/wait in a loop."
    )


def _render_wave_file_history(ctx: BreadcrumbContext) -> str:
    wave = ctx.result_details.get("wave")
    wave_label = f"Wave {wave}" if wave is not None else "Wave"
    return (
        f"[BREADCRUMB] {wave_label} complete. "
        f"Use file_history() to review what files delegates created or modified, "
        f"or file_history(path='<file>', detail=True) for full diffs."
    )


def _render_todo_list_solo(ctx: BreadcrumbContext) -> str:
    return (
        "[BREADCRUMB] NEXT: show the user the todos with "
        "todo(action='list', title='-')."
    )


def _render_fetch_then_write(ctx: BreadcrumbContext) -> str:
    return (
        "[BREADCRUMB] NEXT: use the fetched content to complete the deliverable. "
        "If a file was requested, write() it now with citations from what you fetched."
    )


def _render_write_then_edit(ctx: BreadcrumbContext) -> str:
    return (
        "[BREADCRUMB] File written. For further changes on the same path, "
        "use edit() — not another full write()."
    )


def _is_rewrite_blocked(ctx: BreadcrumbContext) -> bool:
    return ctx.is_error and bool(ctx.result_details.get("rewrite_blocked"))


def _render_rewrite_blocked(ctx: BreadcrumbContext) -> str:
    path = str(ctx.result_details.get("path") or "that file").strip()
    return (
        f"[BREADCRUMB] Full rewrite of {path} blocked — you already used write() "
        f"on this path. Prefer read() + edit() for fixes. If you need a fresh "
        f"from-scratch file, delete_file(path={path!r}) first, then write() again."
    )


def _render_lookup_answer(ctx: BreadcrumbContext) -> str:
    return (
        "[BREADCRUMB] NEXT: answer in chat from the lookup results. "
        "Do not create plans, files, or todos."
    )


_EM_PROFILES = frozenset({"orchestrated"})
_SOLO_PROFILES = frozenset({"solo_structured"})
_DIRECT_PROFILES = frozenset({"conversational"})
_SOLO_AND_EM_PROFILES = frozenset({"solo_structured", "orchestrated"})


DEFAULT_RULES: list[BreadcrumbRule] = [
    # --- Solo IC workflow (no team waves) ---
    BreadcrumbRule(
        trigger=("todo", "add"),
        profiles=_SOLO_PROFILES,
        requires_tools=frozenset({"todo"}),
        condition=lambda ctx: "plan" not in ctx.unlocked_tools,
        render=_render_todo_list_solo,
    ),
    BreadcrumbRule(
        trigger=("web_fetch", "*"),
        profiles=_SOLO_PROFILES,
        requires_tools=frozenset({"write"}),
        condition=lambda ctx: not ctx.is_error,
        render=_render_fetch_then_write,
    ),
    BreadcrumbRule(
        trigger=("write", "*"),
        condition=_is_rewrite_blocked,
        render=_render_rewrite_blocked,
    ),
    BreadcrumbRule(
        trigger=("write", "*"),
        profiles=_SOLO_PROFILES,
        requires_tools=frozenset({"edit"}),
        condition=lambda ctx: not ctx.is_error,
        render=_render_write_then_edit,
    ),
    # --- Direct lookup workflow ---
    BreadcrumbRule(
        trigger=("web_search", "*"),
        profiles=_DIRECT_PROFILES,
        condition=lambda ctx: not ctx.is_error,
        render=_render_lookup_answer,
    ),
    BreadcrumbRule(
        trigger=("web_fetch", "*"),
        profiles=_DIRECT_PROFILES,
        condition=lambda ctx: not ctx.is_error,
        render=_render_lookup_answer,
    ),
    BreadcrumbRule(
        trigger=("browser", "*"),
        profiles=_DIRECT_PROFILES,
        condition=lambda ctx: not ctx.is_error,
        render=_render_lookup_answer,
    ),
    # --- EM orchestration workflow ---
    # todo(add) → plan(create)
    BreadcrumbRule(
        trigger=("todo", "add"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"plan"}),
        render=_render_todo_plan,
    ),
    # plan(create) with delegatable steps → team(create)
    BreadcrumbRule(
        trigger=("plan", "create"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"team"}),
        condition=_has_delegatable_steps,
        render=_render_plan_team,
    ),
    # plan(create) on solo profile → execute steps yourself
    BreadcrumbRule(
        trigger=("plan", "create"),
        profiles=_SOLO_PROFILES,
        requires_tools=frozenset({"plan"}),
        condition=lambda ctx: not ctx.is_error,
        render=_render_plan_execute_solo,
    ),
    # team(create) → team(launch)
    BreadcrumbRule(
        trigger=("team", "create"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"team"}),
        condition=lambda ctx: not ctx.is_error,
        render=_render_team_launch,
    ),
    # team(create) duplicate — launch the existing team
    BreadcrumbRule(
        trigger=("team", "create"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"team"}),
        condition=lambda ctx: (
            ctx.is_error
            and bool(ctx.result_details.get("duplicate_team"))
        ),
        render=_render_team_launch_duplicate,
    ),
    # team(create) blocked — prior wave not advanced
    BreadcrumbRule(
        trigger=("team", "create"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"team"}),
        condition=lambda ctx: (
            ctx.is_error
            and bool(ctx.result_details.get("wave_needs_advance"))
        ),
        render=_render_create_needs_advance,
    ),
    # team(inspect) terminal wave not yet advanced
    BreadcrumbRule(
        trigger=("team", "inspect"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"team"}),
        condition=lambda ctx: bool(ctx.result_details.get("needs_advance")),
        render=_render_inspect_needs_advance,
    ),
    # team(inspect) while delegate waits in completion review
    BreadcrumbRule(
        trigger=("team", "inspect"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"team", "read"}),
        condition=lambda ctx: bool(
            ctx.result_details.get("pending_completion_review")
        ),
        render=_render_inspect_completion_review,
    ),
    BreadcrumbRule(
        trigger=("team", "intervene"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"team"}),
        condition=lambda ctx: (
            not ctx.is_error
            and ctx.result_details.get("decision") == "approve"
            and bool((ctx.result_details.get("approve_breadcrumb") or "").strip())
        ),
        render=_render_intervene_approve,
    ),
    BreadcrumbRule(
        trigger=("team", "advance"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"team"}),
        condition=lambda ctx: ctx.is_error,
        render=_render_advance_blocked,
    ),
    # team(launch) → yield via await_delegates
    BreadcrumbRule(
        trigger=("team", "launch"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"team", "await_delegates"}),
        condition=lambda ctx: ctx.is_coordinator,
        render=_render_post_launch_monitor,
    ),
    # plan(complete) on top-level plan with deferred channels → notify
    BreadcrumbRule(
        trigger=("plan", "complete"),
        profiles=_SOLO_AND_EM_PROFILES,
        requires_any=_COMM_SEND_TOOLS,
        condition=lambda ctx: (
            not ctx.result_details.get("parent_id")
            and len(_deferred_channels(ctx)) > 0
        ),
        render=_render_plan_notify,
    ),
    # team(advance) → all plan steps done — close the plan
    BreadcrumbRule(
        trigger=("team", "advance"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"plan", "task_complete"}),
        condition=lambda ctx: (
            not ctx.is_error
            and ctx.result_details.get("plan_ready_to_close")
        ),
        render=_render_plan_ready_to_close,
    ),
    # plan(verify) passed — complete the plan
    BreadcrumbRule(
        trigger=("plan", "verify"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"plan", "task_complete"}),
        condition=lambda ctx: (
            not ctx.is_error
            and ctx.result_details.get("all_criteria_met")
        ),
        render=_render_plan_verify_passed,
    ),
    # team(advance) → hint to use file_history to review delegate work
    BreadcrumbRule(
        trigger=("team", "advance"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"file_history"}),
        render=_render_wave_file_history,
    ),
    # plan(accept_partial) → advance wave, do not replan
    BreadcrumbRule(
        trigger=("plan", "accept_partial"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"team"}),
        condition=lambda ctx: bool(ctx.result_details.get("wave_needs_advance")),
        render=_render_accept_partial_advance,
    ),
    # plan(fix_dependencies) → launch next wave
    BreadcrumbRule(
        trigger=("plan", "fix_dependencies"),
        profiles=_EM_PROFILES,
        requires_tools=frozenset({"team"}),
        render=_render_fix_deps_team,
    ),
]


# -------------------------------------------------------------------
# Static tool-schema hints (pre-generation, token-efficient workflow)
# -------------------------------------------------------------------
# Post-result breadcrumbs stay authoritative for state-specific cases
# (errors, wave_needs_advance, deferred channels). These snippets only
# document the happy-path chain so the model sees it before the first call.

_TOOL_STATIC_HINTS_EM: dict[str, str] = {
    "todo": (
        "\n\nORCHESTRATION: After todo(add), if plan is available → "
        "plan(action='create', todo_id=<id>, title='...')."
    ),
    "plan": (
        "\n\nORCHESTRATION: After create with delegatable steps → "
        "team(action='create', plan_id=...). On partial waves use "
        "accept_partial then team(advance) — avoid plan(delete)+recreate."
    ),
    "team": (
        "\n\nORCHESTRATION: create → launch → await_delegates. "
        "If create fails (prior wave not advanced) → team(advance) first. "
        "After launch, monitor via await_delegates — do not inspect/wait loop."
    ),
}

_TOOL_STATIC_HINTS_SOLO: dict[str, str] = {
    "todo": (
        "\n\nWORKFLOW: After todo(add), call todo(action='list', title='-') "
        "to show items back when the user asked for a list."
    ),
    "plan": (
        "\n\nWORKFLOW (solo): plan(create) → switch_mode(executing) → work each "
        "step yourself (write/bash/edit). No team waves. plan(complete) when done."
    ),
    "write": (
        "\n\nWORKFLOW: After the first write() to a path, use edit() for "
        "targeted changes. To fully rewrite again, delete_file(path=...) first, "
        "then write()."
    ),
    "web_fetch": (
        "\n\nWORKFLOW: Use fetched content directly — cite real URLs. "
        "If a deliverable file was requested, write() it next."
    ),
}


def tool_description_supplement(
    tool_name: str,
    *,
    orchestration_profile: str | None = None,
) -> str:
    """Compact workflow text appended to tool definitions in the schema."""
    if orchestration_profile is None:
        return ""
    profile = normalize_profile(orchestration_profile)
    if em_static_tool_hints_enabled(profile):
        return _TOOL_STATIC_HINTS_EM.get(tool_name, "")
    if solo_static_tool_hints_enabled(profile):
        return _TOOL_STATIC_HINTS_SOLO.get(tool_name, "")
    return ""
