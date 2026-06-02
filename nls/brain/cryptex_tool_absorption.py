"""Action-time Cryptex / SubCryptex updates from tool results.

Orchestrator Cryptex uses *thin* absorption (decision-relevant summaries).
SubCryptex uses the same trigger semantics with *thick* IC detail (pre-existing
logic in ``sub_cryptex.py`` plus supplements here).

Position vocabulary (project rings):
  - ``{project_id}`` — normal workspace context (plan, todos, facts)
  - ``focus:verification`` — EM reviewing deliverables
  - ``focus:wave`` — wave launched / delegates running
  - ``focus:recovery`` — failed/partial wave repair decisions
  - ``focus:stakeholder`` — just communicated outward
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

# --- Focus positions (orchestrator project rings) ---
FOCUS_VERIFICATION = "focus:verification"
FOCUS_WAVE = "focus:wave"
FOCUS_RECOVERY = "focus:recovery"
FOCUS_STAKEHOLDER = "focus:stakeholder"
POS_RUNTIME = "runtime"
POS_IN_APP = "in_app"

# Backward-compatible aliases
POS_VERIFICATION = FOCUS_VERIFICATION
POS_ORCHESTRATION = FOCUS_WAVE
POS_FILE_CONTEXT = "file_context"
POS_DEFAULT = "general"

Depth = Literal["orchestrator", "delegate"]

_WRITE_TOOLS = frozenset({"write", "edit", "delete_file", "move_file", "offer_download"})
_READ_TOOLS = frozenset({"read", "grep", "glob", "list_dir", "semantic_search"})
_EXEC_TOOLS = frozenset({"bash", "project_install", "server_install"})
_RESEARCH_TOOLS = frozenset({
    "browser", "web_search", "web_fetch", "vision", "screenshot", "eyes",
})
_SKILL_TOOLS = frozenset({
    "discover_tools", "skill_configure", "clawhub", "crystallize",
})
_STAKEHOLDER_TOOLS = frozenset({"communicate", "ask_user", "contacts"})

# Orchestrator skips deep IC on these while coordinating with active delegates.
_ORCH_SKIP_DEEP_IC = _READ_TOOLS | _RESEARCH_TOOLS | frozenset({"list_dir"})


@dataclass(frozen=True)
class CryptexTriggerSpec:
    """Structured trigger → ring/position mapping."""

    tool: str
    action: str  # "*" = any, "intervene:approve" = compound
    ring: str
    position: str | None  # None = project slot, no rotation
    rotate: bool
    depth: Depth
    summary: str


# Canonical trigger registry (documentation + tests).
CRYPTEX_TRIGGER_SPECS: tuple[CryptexTriggerSpec, ...] = (
    CryptexTriggerSpec("team", "intervene:approve", "instructions", FOCUS_VERIFICATION, True, "orchestrator", "verification checklist"),
    CryptexTriggerSpec("team", "intervene:hint", "orchestration", None, False, "orchestrator", "hint sent"),
    CryptexTriggerSpec("team", "advance:ok", "orchestration", "project", True, "orchestrator", "wave advanced → project"),
    CryptexTriggerSpec("team", "advance:blocked", "wake_attention", "project", False, "orchestrator", "advance blocked"),
    CryptexTriggerSpec("team", "launch:ok", "orchestration", FOCUS_WAVE, True, "orchestrator", "delegates running"),
    CryptexTriggerSpec("team", "inspect:completion_review", "instructions", FOCUS_VERIFICATION, True, "orchestrator", "completion review"),
    CryptexTriggerSpec("plan", "verify", "instructions", FOCUS_VERIFICATION, True, "orchestrator", "plan verify"),
    CryptexTriggerSpec("plan", "accept_partial", "orchestration", FOCUS_RECOVERY, True, "orchestrator", "recovery path"),
    CryptexTriggerSpec("plan", "fix_dependencies", "instructions", FOCUS_RECOVERY, True, "orchestrator", "dependency repair"),
    CryptexTriggerSpec("plan", "read", "tactical_goals", None, False, "orchestrator", "plan snapshot"),
    CryptexTriggerSpec("plan", "complete", "strategic_goals", None, False, "orchestrator", "plan done"),
    CryptexTriggerSpec("plan", "update", "tactical_goals", None, False, "orchestrator", "step delta"),
    CryptexTriggerSpec("switch_mode", "evaluating", "instructions", FOCUS_VERIFICATION, True, "orchestrator", "evaluating"),
    CryptexTriggerSpec("switch_mode", "delegating", "orchestration", FOCUS_WAVE, False, "orchestrator", "delegating prep"),
    CryptexTriggerSpec("communicate", "*", "channels", POS_IN_APP, False, "orchestrator", "stakeholder update"),
    CryptexTriggerSpec("await_delegates", "*", "orchestration", "project", True, "orchestrator", "turn end"),
    CryptexTriggerSpec("escalate", "*", "wake_attention", "project", False, "orchestrator", "delegate escalation"),
    CryptexTriggerSpec("todo", "*", "tactical_goals", None, False, "orchestrator", "todo board delta"),
    CryptexTriggerSpec("scheduler", "*", "orchestration", None, False, "orchestrator", "scheduled wake"),
    CryptexTriggerSpec("poller", "*", "orchestration", None, False, "orchestrator", "poller job"),
    CryptexTriggerSpec("request_restart", "*", "environment", POS_RUNTIME, False, "orchestrator", "restart pending"),
    CryptexTriggerSpec("server_install", "*", "environment", POS_RUNTIME, False, "orchestrator", "runtime deps"),
    CryptexTriggerSpec("task_complete", "*", "task", "instructions", True, "delegate", "deliverable done"),
    CryptexTriggerSpec("escalate", "*", "task", "instructions", True, "delegate", "escalation sent"),
    CryptexTriggerSpec("browser", "*", "knowledge", None, False, "delegate", "external research"),
    CryptexTriggerSpec("web_search", "*", "knowledge", None, False, "delegate", "web research"),
)

# Human-readable map (legacy tests / docs).
TOOL_CRYPTEX_TRIGGERS: dict[str, dict[str, str]] = {
    "team": {
        "intervene:approve": "instructions→focus:verification",
        "intervene:hint": "orchestration→hint sent (slot only)",
        "advance:ok": "orchestration→project",
        "advance:blocked": "wake_attention + focus:verification",
        "launch:ok": "orchestration→focus:wave",
        "inspect:completion_review": "instructions→focus:verification",
    },
    "plan": {
        "verify": "instructions→focus:verification",
        "accept_partial": "orchestration→focus:recovery",
        "read": "tactical_goals→snapshot",
        "complete": "strategic_goals→milestone",
        "fix_dependencies": "instructions→focus:recovery",
        "update": "tactical_goals→delta",
    },
    "write|edit|read": {"*": "project_facts→one-line (orchestrator)"},
    "bash|project_install|server_install": {"*": "project_facts / environment"},
    "switch_mode:evaluating": {"*": "instructions→focus:verification"},
    "switch_mode:delegating": {"*": "orchestration→focus:wave (slot)"},
    "await_delegates": {"*": "orchestration→project"},
    "communicate": {"*": "channels→in_app + orchestration slot"},
    "ask_user|contacts": {"*": "channels→stakeholder"},
    "escalate": {"*": "wake_attention + verification (EM); task (delegate)"},
    "task_complete": {"*": "task→instructions (delegate)"},
    "todo": {"*": "tactical_goals→checklist delta"},
    "scheduler|poller": {"*": "orchestration→next wake"},
    "request_restart|server_install": {"*": "environment→runtime"},
    "skill_configure|discover_tools|clawhub|crystallize": {"*": "skills / tools_mcp"},
    "browser|web_search|web_fetch": {"*": "knowledge (delegate); thin skip (EM+delegates)"},
    "grep|glob|list_dir|semantic_search": {"*": "skip EM when delegates active"},
}


def _ring(cryptex: Any, ring_id: str) -> Any | None:
    rings = getattr(cryptex, "_rings", None)
    if isinstance(rings, dict):
        return rings.get(ring_id)
    get_ring = getattr(cryptex, "get_ring", None)
    if callable(get_ring):
        return get_ring(ring_id)
    return None


def _project_position(cryptex: Any) -> str:
    for attr in ("active_project", "_active_project"):
        val = getattr(cryptex, attr, None)
        if val:
            return str(val)
    ring = _ring(cryptex, "instructions")
    if ring is not None and getattr(ring, "active_position", ""):
        pos = ring.active_position
        if not str(pos).startswith("focus:"):
            return str(pos)
    from nls.brain.cryptex import DEFAULT_PROJECT
    return DEFAULT_PROJECT


def _resolve_position(cryptex: Any, position: str | None) -> str:
    if position is None or position == "project":
        return _project_position(cryptex)
    return position


def _rotate_ring(cryptex: Any, ring_id: str, position: str) -> None:
    ring = _ring(cryptex, ring_id)
    if ring is None:
        return
    try:
        ring.rotate(_resolve_position(cryptex, position))
    except Exception:
        logger.debug("cryptex rotate failed ring=%s pos=%s", ring_id, position, exc_info=True)


def _return_to_project(cryptex: Any, ring_id: str) -> None:
    _rotate_ring(cryptex, ring_id, "project")


def _upsert_slot(
    cryptex: Any,
    ring_id: str,
    domain: str,
    content: str,
    *,
    position: str | None = None,
    rotate: bool = False,
    salience: float = 0.9,
    slot_type: str = "fact",
    source: str = "tool",
) -> None:
    ring = _ring(cryptex, ring_id)
    if ring is None:
        upsert_fn = getattr(cryptex, "upsert_orchestration_slot", None)
        if ring_id == "orchestration" and callable(upsert_fn):
            try:
                upsert_fn(domain, content, salience=salience)
            except Exception:
                pass
        env_fn = getattr(cryptex, "upsert_environment", None)
        if ring_id == "environment" and callable(env_fn):
            try:
                env_fn(domain, content[:500], salience=salience, source=source)
            except Exception:
                pass
        return
    pos = _resolve_position(cryptex, position)
    try:
        ring.upsert_slot(
            domain=domain,
            content=content[:2000],
            slot_type=slot_type,
            salience=salience,
            source=source,
            position=pos,
        )
        if rotate:
            ring.rotate(pos)
    except Exception:
        logger.debug("cryptex upsert failed ring=%s domain=%s", ring_id, domain, exc_info=True)


def _focus(
    cryptex: Any,
    ring_id: str,
    focus_pos: str,
    domain: str,
    content: str,
    **kwargs: Any,
) -> None:
    _upsert_slot(
        cryptex, ring_id, domain, content,
        position=focus_pos, rotate=True, **kwargs,
    )


def _absorption_context(details: dict[str, Any] | None) -> dict[str, Any]:
    details = details or {}
    return {
        "coordinator_mode": bool(details.get("coordinator_mode")),
        "delegates_active": bool(details.get("delegates_active")),
        "active_mode": str(details.get("active_mode") or ""),
    }


def _orchestrator_skip_deep_ic(tool_name: str, ctx: dict[str, Any]) -> bool:
    if tool_name not in _ORCH_SKIP_DEEP_IC:
        return False
    if not ctx.get("coordinator_mode"):
        return False
    if ctx.get("delegates_active"):
        return True
    mode = ctx.get("active_mode", "")
    if mode in ("evaluating", "planning") and not ctx.get("delegates_active"):
        return False
    if mode in ("delegating", "monitoring"):
        return True
    return False


def _tool_path(args: dict[str, Any]) -> str:
    return str(
        args.get("path")
        or args.get("file_path")
        or args.get("pattern")
        or args.get("query")
        or ""
    )


def _research_label(tool_name: str, args: dict[str, Any]) -> str:
    key = (
        args.get("query")
        or args.get("url")
        or args.get("pattern")
        or args.get("command")
        or ""
    )
    return f"{tool_name}({str(key)[:60]})"


def _channel_position(args: dict[str, Any]) -> str:
    ch = str(args.get("channel") or args.get("via") or "").strip().lower()
    if ch in ("whatsapp", "telegram", "email", "sms"):
        return ch
    return POS_IN_APP


def _rotate_instructions_verification(cryptex: Any, checklist: str) -> None:
    from nls.brain.cryptex import RING_INSTRUCTIONS, RING_WAKE_ATTENTION

    _focus(
        cryptex,
        RING_INSTRUCTIONS,
        FOCUS_VERIFICATION,
        "Action:verification",
        checklist,
        salience=1.0,
        slot_type="instruction",
    )
    _upsert_slot(
        cryptex,
        RING_WAKE_ATTENTION,
        "Focus:verification",
        "Completion review / verify deliverables before advance.",
        salience=0.95,
    )


def _handle_team_orchestrator(
    cryptex: Any,
    action: str,
    args: dict[str, Any],
    result_str: str,
    is_error: bool,
    details: dict[str, Any],
) -> None:
    from nls.brain.cryptex import RING_INSTRUCTIONS, RING_ORCHESTRATION, RING_WAKE_ATTENTION

    if action == "intervene" and args.get("decision") == "approve" and not is_error:
        from nls.agentic.verification_hints import completion_review_verify_breadcrumb

        tid = str(args.get("team_id") or details.get("team_id") or "")
        _rotate_instructions_verification(
            cryptex,
            completion_review_verify_breadcrumb(team_id=tid),
        )
    elif action == "intervene" and args.get("decision") == "hint" and not is_error:
        member = args.get("member", "?")
        _upsert_slot(
            cryptex,
            RING_ORCHESTRATION,
            f"Delegate:hint:{member}",
            f"Hint sent to member {member}: {result_str[:300]}",
            salience=0.75,
        )
    elif action == "advance" and is_error:
        _upsert_slot(
            cryptex,
            RING_WAKE_ATTENTION,
            "Action:advance_blocked",
            result_str[:800],
            salience=0.95,
        )
        _rotate_instructions_verification(
            cryptex,
            "Advance blocked — inspect running delegates; do not retry advance blindly.",
        )
    elif action == "advance" and not is_error:
        _upsert_slot(
            cryptex,
            RING_ORCHESTRATION,
            "Wave:advanced",
            result_str[:600],
            position="project",
            rotate=True,
            salience=0.85,
        )
        _return_to_project(cryptex, RING_INSTRUCTIONS)
    elif action == "launch" and not is_error:
        _focus(
            cryptex,
            RING_ORCHESTRATION,
            FOCUS_WAVE,
            "Wave:launched",
            "Delegates running — use await_delegates; no IC work.",
            salience=0.9,
        )
    elif action == "inspect" and details.get("pending_completion_review"):
        from nls.agentic.verification_hints import completion_review_verify_breadcrumb

        tid = str(args.get("team_id") or details.get("team_id") or "")
        _rotate_instructions_verification(
            cryptex,
            completion_review_verify_breadcrumb(team_id=tid),
        )


def _handle_plan_orchestrator(
    cryptex: Any,
    action: str,
    args: dict[str, Any],
    result_str: str,
    is_error: bool,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    from nls.brain.cryptex import (
        RING_INSTRUCTIONS,
        RING_ORCHESTRATION,
        RING_STRATEGIC_GOALS,
        RING_TACTICAL_GOALS,
    )

    details = details or {}

    if action == "verify" and not is_error:
        from nls.agentic.verification_hints import pre_plan_verify_reminder

        _rotate_instructions_verification(
            cryptex,
            pre_plan_verify_reminder() + "\n" + result_str[:400],
        )
        if details.get("all_criteria_met"):
            from nls.agentic.plan_work import format_plan_closure_nudge

            plan_id = str(args.get("plan_id") or details.get("plan_id") or "")
            if plan_id:
                _focus(
                    cryptex,
                    RING_INSTRUCTIONS,
                    FOCUS_VERIFICATION,
                    "Action:plan_closure",
                    format_plan_closure_nudge(plan_id),
                    salience=1.0,
                    slot_type="instruction",
                )
    elif action == "accept_partial" and not is_error:
        _focus(
            cryptex,
            RING_ORCHESTRATION,
            FOCUS_RECOVERY,
            "Plan:accept_partial",
            result_str[:600],
            salience=0.9,
        )
        _focus(
            cryptex,
            RING_INSTRUCTIONS,
            FOCUS_RECOVERY,
            "Plan:recovery_mode",
            "Partial acceptance — repair failed steps or launch retry wave.",
            salience=0.95,
            slot_type="instruction",
        )
    elif action == "read" and not is_error:
        _upsert_slot(
            cryptex,
            RING_TACTICAL_GOALS,
            "Plan:read",
            result_str[:500],
            salience=0.85,
        )
    elif action == "complete" and not is_error:
        plan_id = str(args.get("plan_id") or "plan")
        _upsert_slot(
            cryptex,
            RING_STRATEGIC_GOALS,
            f"Plan:complete:{plan_id[-24:]}",
            result_str[:400] or f"Plan {plan_id} marked complete.",
            salience=0.95,
            slot_type="goal",
        )
    elif action == "update" and not is_error:
        step = str(args.get("step_id") or args.get("step") or "")
        _upsert_slot(
            cryptex,
            RING_TACTICAL_GOALS,
            f"Plan:update:{step[-24:]}",
            result_str[:400] or f"Updated step {step}.",
            salience=0.8,
        )
    elif action == "fix_dependencies" and not is_error:
        _focus(
            cryptex,
            RING_INSTRUCTIONS,
            FOCUS_RECOVERY,
            "Plan:fix_dependencies",
            "Dependencies repaired — team(create/launch) next.",
            salience=0.9,
            slot_type="instruction",
        )


def _handle_stakeholder_orchestrator(
    cryptex: Any,
    tool_name: str,
    args: dict[str, Any],
    result_str: str,
    is_error: bool,
    *,
    ctx: dict[str, Any] | None = None,
) -> None:
    from nls.brain.cryptex import RING_CHANNELS, RING_ORCHESTRATION

    ctx = ctx or {}

    if is_error:
        from nls.brain.cryptex import RING_WAKE_ATTENTION

        _upsert_slot(
            cryptex,
            RING_WAKE_ATTENTION,
            f"Stakeholder:error:{tool_name}",
            result_str[:400],
            salience=0.9,
        )
        return

    ch_pos = _channel_position(args)
    if tool_name == "communicate":
        msg = str(args.get("message", "") or "")[:300]
        _upsert_slot(
            cryptex,
            RING_CHANNELS,
            f"Channel:{ch_pos}",
            f"communicate(): {msg}",
            position=ch_pos,
            salience=0.75,
        )
        _upsert_slot(
            cryptex,
            RING_ORCHESTRATION,
            "Stakeholder:update",
            f"communicate(): {msg}",
            position="project",
            salience=0.7,
        )
        # Do not rotate orchestration away from focus:wave while delegates run.
        if not ctx.get("delegates_active"):
            _focus(
                cryptex,
                RING_ORCHESTRATION,
                FOCUS_STAKEHOLDER,
                "Turn:stakeholder",
                "Stakeholder updated — resume orchestration.",
                salience=0.65,
            )
    elif tool_name == "ask_user":
        q = str(args.get("question") or args.get("message") or "")[:300]
        _upsert_slot(
            cryptex,
            RING_CHANNELS,
            f"Ask:{ch_pos}",
            f"ask_user(): {q}",
            position=ch_pos,
            salience=0.8,
        )
    elif tool_name == "contacts":
        act = str(args.get("action") or "")
        _upsert_slot(
            cryptex,
            RING_CHANNELS,
            f"Contacts:{act}",
            result_str[:300],
            position=ch_pos,
            salience=0.7,
        )


def _handle_runtime_orchestrator(
    cryptex: Any,
    tool_name: str,
    args: dict[str, Any],
    result_str: str,
    is_error: bool,
) -> None:
    from nls.brain.cryptex import RING_ENVIRONMENT, RING_PROJECT_FACTS, RING_SKILLS, RING_TOOLS_MCP
    from nls.skills_setup_policy import (
        instruction_skill_setup_hint,
        is_instruction_only_skill,
        lookup_skill_meta,
        resolve_data_skills_dir,
        skill_configure_absorption_content,
        skill_md_path,
    )

    if tool_name == "request_restart" and not is_error:
        _upsert_slot(
            cryptex,
            RING_ENVIRONMENT,
            "Runtime:restart_pending",
            result_str[:400],
            position=POS_RUNTIME,
            salience=0.95,
        )
    elif tool_name == "server_install" and not is_error:
        pkg = str(args.get("package") or "")[:80]
        _upsert_slot(
            cryptex,
            RING_ENVIRONMENT,
            "Runtime:deps",
            f"server_install({pkg}): {result_str[:300]}",
            position=POS_RUNTIME,
            salience=0.85,
        )
        _upsert_slot(
            cryptex,
            RING_PROJECT_FACTS,
            "Deps:agent_runtime",
            result_str[:400],
            salience=0.75,
        )
    elif tool_name in _SKILL_TOOLS:
        action = str(args.get("action") or "").strip().lower()
        skill_slug = str(
            args.get("skill_name") or args.get("slug") or args.get("query") or ""
        ).strip()
        custom_content: str | None = None

        if tool_name == "skill_configure":
            custom_content = skill_configure_absorption_content(
                skill_slug, result_str, is_error=is_error,
            )
            if custom_content is None and is_error:
                return
        elif tool_name == "clawhub" and action == "install" and not is_error and skill_slug:
            meta, path = lookup_skill_meta(skill_slug)
            if is_instruction_only_skill(meta):
                custom_content = instruction_skill_setup_hint(skill_slug, path)
            else:
                md = skill_md_path(skill_slug)
                base = resolve_data_skills_dir()
                prefix = f"Installed '{skill_slug}'."
                if md is not None:
                    prefix += f" read(path='{md}')."
                elif base is not None:
                    prefix += f" Path: {base / skill_slug}."
                custom_content = f"{prefix} {result_str[:200]}".strip()

        if custom_content is not None:
            slot_content = custom_content[:400]
        elif is_error:
            return
        else:
            query = str(args.get("query") or args.get("skill") or tool_name)[:60]
            slot_content = f"{tool_name}({query}): {result_str[:250]}"

        domain_key = skill_slug or tool_name
        _upsert_slot(
            cryptex,
            RING_SKILLS,
            f"Skill:{domain_key}",
            slot_content,
            salience=0.85 if tool_name == "clawhub" else 0.8,
            slot_type="skill",
        )
        if tool_name == "discover_tools" and not is_error:
            _upsert_slot(
                cryptex,
                RING_TOOLS_MCP,
                "Tools:discovered",
                result_str[:400],
                salience=0.75,
            )
    elif tool_name in ("scheduler", "poller") and not is_error:
        from nls.brain.cryptex import RING_ORCHESTRATION

        label = str(args.get("name") or args.get("url") or tool_name)[:80]
        _upsert_slot(
            cryptex,
            RING_ORCHESTRATION,
            f"Schedule:{tool_name}",
            f"{tool_name}({label}): {result_str[:300]}",
            salience=0.7,
        )


def _handle_todo_orchestrator(
    cryptex: Any,
    args: dict[str, Any],
    result_str: str,
    is_error: bool,
) -> None:
    from nls.brain.cryptex import RING_TACTICAL_GOALS

    if is_error:
        return
    action = str(args.get("action") or "")
    title = str(args.get("title") or "")[:80]
    _upsert_slot(
        cryptex,
        RING_TACTICAL_GOALS,
        f"Todo:{action}",
        f"todo({action}) {title}: {result_str[:200]}",
        salience=0.75,
    )


def _cryptex_has_active_user_task(cryptex: Any) -> bool:
    """True when WM holds goals/instructions from an active user-driven task."""
    for ring_id in ("instructions", "tactical_goals"):
        ring = _ring(cryptex, ring_id)
        if ring is None:
            continue
        for s in ring.get_active_slots():
            src = getattr(s, "source", "") or ""
            if src in ("task", "task_extract", "todo-list", "user"):
                return True
    return False


def _research_fact_salience(cryptex: Any, *, is_error: bool) -> float:
    if is_error:
        return 0.5 if _cryptex_has_active_user_task(cryptex) else 0.75
    return 0.35 if _cryptex_has_active_user_task(cryptex) else 0.65


def _handle_research_orchestrator_thin(
    cryptex: Any,
    tool_name: str,
    args: dict[str, Any],
    result_str: str,
    is_error: bool,
) -> None:
    from nls.brain.cryptex import RING_PROJECT_FACTS

    if is_error:
        _upsert_slot(
            cryptex,
            RING_PROJECT_FACTS,
            f"ResearchError:{tool_name}",
            f"{_research_label(tool_name, args)} failed: {result_str[:200]}",
            salience=_research_fact_salience(cryptex, is_error=True),
        )
        return
    _upsert_slot(
        cryptex,
        RING_PROJECT_FACTS,
        f"Research:{tool_name}",
        f"{_research_label(tool_name, args)}: {result_str[:120]}",
        salience=_research_fact_salience(cryptex, is_error=False),
    )


def absorb_orchestrator_tool_result(
    cryptex: Any,
    tool_name: str,
    args: dict[str, Any],
    result_str: str,
    is_error: bool,
    *,
    details: dict[str, Any] | None = None,
    guardrails_registry: Any | None = None,
) -> None:
    """Update orchestrator Cryptex rings after a tool completes."""
    if cryptex is None:
        return

    if is_error and guardrails_registry is not None:
        from nls.tools.agent_tools.guardrails_registry import (
            record_tool_contract_guardrail,
        )

        record_tool_contract_guardrail(
            guardrails_registry,
            tool_name=tool_name,
            content=result_str,
            delegate_number=0,
            cryptex=cryptex,
        )

    details = dict(details or {})
    ctx = _absorption_context(details)
    action = str(args.get("action", "") or "").strip().lower()

    from nls.brain.cryptex import (
        RING_INSTRUCTIONS,
        RING_ORCHESTRATION,
        RING_WAKE_ATTENTION,
    )

    if tool_name == "team":
        _handle_team_orchestrator(cryptex, action, args, result_str, is_error, details)
    elif tool_name == "plan":
        _handle_plan_orchestrator(
            cryptex, action, args, result_str, is_error, details=details,
        )
    elif tool_name == "switch_mode" and not is_error:
        mode = str(args.get("mode", "") or "").lower()
        if mode == "evaluating":
            from nls.agentic.verification_hints import completion_review_verify_breadcrumb

            _rotate_instructions_verification(
                cryptex,
                completion_review_verify_breadcrumb(),
            )
        elif mode == "delegating":
            _upsert_slot(
                cryptex,
                RING_ORCHESTRATION,
                "Mode:delegating",
                "Preparing wave launch — team(create/launch) next.",
                position=FOCUS_WAVE,
                salience=0.8,
            )
    elif tool_name in _STAKEHOLDER_TOOLS:
        _handle_stakeholder_orchestrator(
            cryptex, tool_name, args, result_str, is_error, ctx=ctx,
        )
    elif tool_name == "await_delegates" and not is_error:
        summary = str(args.get("summary", "") or "")[:400]
        _upsert_slot(
            cryptex,
            RING_ORCHESTRATION,
            "Turn:await_delegates",
            summary or "Management turn ended — delegates background.",
            position="project",
            rotate=True,
            salience=0.8,
        )
        _return_to_project(cryptex, RING_INSTRUCTIONS)
    elif tool_name == "escalate" and not is_error:
        reason = str(args.get("reason", "") or "")[:400]
        _upsert_slot(
            cryptex,
            RING_WAKE_ATTENTION,
            "Delegate:escalation",
            reason or result_str[:400],
            salience=1.0,
        )
        _rotate_instructions_verification(
            cryptex,
            "Delegate escalated — team(inspect/hint/intervene) before IC work.",
        )
    elif tool_name == "todo":
        _handle_todo_orchestrator(cryptex, args, result_str, is_error)
    elif tool_name in ("scheduler", "poller", "request_restart", "server_install") or tool_name in _SKILL_TOOLS:
        _handle_runtime_orchestrator(cryptex, tool_name, args, result_str, is_error)
    elif tool_name in _RESEARCH_TOOLS:
        if not _orchestrator_skip_deep_ic(tool_name, ctx):
            _handle_research_orchestrator_thin(
                cryptex, tool_name, args, result_str, is_error,
            )
    elif tool_name in _ORCH_SKIP_DEEP_IC:
        if not _orchestrator_skip_deep_ic(tool_name, ctx):
            _handle_research_orchestrator_thin(
                cryptex, tool_name, args, result_str, is_error,
            )

    absorb_file_and_exec_result(
        cryptex,
        tool_name,
        args,
        result_str,
        is_error,
        depth="orchestrator",
        ctx=ctx,
    )


def absorb_file_and_exec_result(
    cryptex: Any,
    tool_name: str,
    args: dict[str, Any],
    result_str: str,
    is_error: bool,
    *,
    ring_map: dict[str, str] | None = None,
    depth: Depth = "orchestrator",
    ctx: dict[str, Any] | None = None,
) -> None:
    """Shared file/exec absorption — thin for EM, supplements delegate SubCryptex."""
    if cryptex is None:
        return

    ctx = ctx or {}
    ring_map = ring_map or {}
    facts_ring = ring_map.get("facts", "project_facts")

    if depth == "orchestrator" and _orchestrator_skip_deep_ic(tool_name, ctx):
        return

    path = _tool_path(args)

    if tool_name in _WRITE_TOOLS and not is_error and path:
        action = {
            "write": "Created", "edit": "Edited", "delete_file": "Deleted",
            "move_file": "Moved", "offer_download": "Offered",
        }.get(tool_name, "Modified")
        preview = f"{action} {path}"
        _upsert_slot(
            cryptex,
            facts_ring,
            f"FileWrite:{path[-48:]}",
            preview,
            salience=0.75 if depth == "orchestrator" else 0.8,
        )
    elif tool_name == "read" and not is_error and path:
        max_preview = 200 if depth == "orchestrator" else 500
        _upsert_slot(
            cryptex,
            facts_ring,
            f"FileRead:{path[-48:]}",
            f"Read {path}: {result_str[:max_preview]}",
            salience=0.7 if depth == "orchestrator" else 0.85,
        )
    elif tool_name in _EXEC_TOOLS and is_error:
        cmd = str(args.get("command", args.get("package", "")))[:80]
        _upsert_slot(
            cryptex,
            facts_ring,
            f"ExecError:{tool_name}",
            f"{tool_name} failed ({cmd}): {result_str[:300]}",
            salience=0.8,
        )
    elif tool_name in ("project_install", "server_install") and not is_error:
        if tool_name == "server_install" and depth == "orchestrator":
            return
        target = "project" if tool_name == "project_install" else "agent_runtime"
        _upsert_slot(
            cryptex,
            facts_ring,
            f"Deps:{target}",
            result_str[:400],
            salience=0.75,
        )
    elif tool_name in ("grep", "glob", "list_dir", "semantic_search") and not is_error and depth == "delegate":
        # SubCryptex native path already stores search hits — skip duplicate slot.
        return


def absorb_delegate_tool_result(
    sub_cryptex: Any,
    tool_name: str,
    args: dict[str, Any],
    result_str: str,
    is_error: bool,
    *,
    guardrails_registry: Any | None = None,
    delegate_number: int = 0,
) -> None:
    """Supplement SubCryptex after its native thick absorption."""
    ctx = {"coordinator_mode": False, "delegates_active": False, "active_mode": "executing"}

    if is_error and guardrails_registry is not None:
        from nls.tools.agent_tools.guardrails_registry import (
            record_tool_contract_guardrail,
        )

        record_tool_contract_guardrail(
            guardrails_registry,
            tool_name=tool_name,
            content=result_str,
            delegate_number=delegate_number,
        )

    absorb_file_and_exec_result(
        sub_cryptex,
        tool_name,
        args,
        result_str,
        is_error,
        ring_map={"facts": "knowledge"},
        depth="delegate",
        ctx=ctx,
    )

    from nls.brain.sub_cryptex import SUB_RING_TASK, _POS_INSTRUCTIONS

    if tool_name == "escalate" and not is_error:
        reason = str(args.get("reason", "") or result_str)[:400]
        _focus(
            sub_cryptex,
            SUB_RING_TASK,
            _POS_INSTRUCTIONS,
            "Escalation:sent",
            reason,
            salience=1.0,
            slot_type="instruction",
        )
    elif tool_name == "task_complete" and not is_error:
        _upsert_slot(
            sub_cryptex,
            SUB_RING_TASK,
            "Task:complete",
            result_str[:400],
            position=_POS_INSTRUCTIONS,
            salience=0.95,
            slot_type="instruction",
        )
    elif tool_name == "bash" and not is_error and "Routed:" in result_str:
        _focus(
            sub_cryptex,
            SUB_RING_TASK,
            _POS_INSTRUCTIONS,
            "Deps:routed",
            "Use project_install for project deps; pip/npm in bash is auto-routed.",
            salience=0.85,
            slot_type="instruction",
        )
    elif tool_name in _RESEARCH_TOOLS:
        label = _research_label(tool_name, args)
        if is_error:
            _upsert_slot(
                sub_cryptex,
                "knowledge",
                f"ResearchError:{tool_name}",
                f"{label} failed: {result_str[:250]}",
                salience=0.85,
            )
        else:
            _upsert_slot(
                sub_cryptex,
                "knowledge",
                f"Research:{tool_name}",
                f"{label}: {result_str[:400]}",
                salience=0.75,
            )
    elif tool_name in ("project_install", "server_install") and not is_error:
        target = "project" if tool_name == "project_install" else "agent_runtime"
        _focus(
            sub_cryptex,
            SUB_RING_TASK,
            _POS_INSTRUCTIONS,
            f"Deps:{target}",
            result_str[:400],
            salience=0.85,
            slot_type="instruction",
        )
    elif tool_name in _STAKEHOLDER_TOOLS and not is_error:
        _upsert_slot(
            sub_cryptex,
            SUB_RING_TASK,
            f"Stakeholder:{tool_name}",
            result_str[:300],
            position=_POS_INSTRUCTIONS,
            salience=0.8,
            slot_type="instruction",
        )


def absorb_wave_review_outcome(cryptex: Any, team: Any) -> None:
    """Rotate Cryptex focus when a wave lands (healthy vs recovery)."""
    if cryptex is None or team is None:
        return

    from nls.brain.cryptex import RING_INSTRUCTIONS, RING_ORCHESTRATION, RING_WAKE_ATTENTION

    _failed = [
        m.step_id for m in getattr(team, "members", [])
        if getattr(m, "status", "") in ("failed", "cancelled")
    ]
    outcome = team.compute_outcome() if hasattr(team, "compute_outcome") else "unknown"
    team_id = getattr(team, "id", "")
    plan_id = getattr(team, "plan_id", "")
    team_name = getattr(team, "name", team_id)
    ok_count = sum(
        1 for m in getattr(team, "members", [])
        if getattr(m, "status", "") == "done"
    )

    if outcome == "completed" and not _failed:
        from nls.agentic.plan_work import format_wave_complete_wake

        wake = format_wave_complete_wake(
            plan_id=plan_id,
            team_id=team_id,
            team_name=team_name,
            outcome=outcome,
            ok_count=ok_count,
            fail_count=0,
        )
        _upsert_slot(
            cryptex,
            RING_WAKE_ATTENTION,
            f"Wave:complete:{team_id[-16:]}",
            wake[:800],
            salience=1.0,
            source="wave_review",
        )
        _focus(
            cryptex,
            RING_INSTRUCTIONS,
            FOCUS_VERIFICATION,
            "Action:wave_review",
            wake[:1200],
            salience=1.0,
            slot_type="instruction",
        )
        _upsert_slot(
            cryptex,
            RING_ORCHESTRATION,
            f"Wave:landed:{team_id[-16:]}",
            f"Wave complete ({ok_count} ok) — review before advance.",
            position="project",
            salience=0.9,
        )
    else:
        from nls.agentic.plan_work import format_recovery_wake

        wake = format_recovery_wake(
            plan_id=plan_id,
            team_id=team_id,
            failed_step_ids=_failed,
        )
        _upsert_slot(
            cryptex,
            RING_WAKE_ATTENTION,
            f"Wave:recovery:{team_id[-16:]}",
            wake[:800],
            salience=1.0,
            source="wave_review",
        )
        _focus(
            cryptex,
            RING_ORCHESTRATION,
            FOCUS_RECOVERY,
            f"Wave:recovery:{team_id[-16:]}",
            wake[:600],
            salience=0.95,
        )
        _focus(
            cryptex,
            RING_INSTRUCTIONS,
            FOCUS_RECOVERY,
            "Action:recovery",
            wake[:1200],
            salience=1.0,
            slot_type="instruction",
        )


def absorb_wake_attention_content(cryptex: Any, board_content: str) -> None:
    """Align instruction focus when wake board shows completion reviews."""
    if cryptex is None or not (board_content or "").strip():
        return
    if "COMPLETION REVIEW" not in board_content:
        return
    from nls.agentic.verification_hints import completion_review_verify_breadcrumb

    _rotate_instructions_verification(
        cryptex,
        completion_review_verify_breadcrumb() + "\n" + board_content[:600],
    )
