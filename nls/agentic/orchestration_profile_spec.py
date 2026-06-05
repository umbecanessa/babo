"""Declarative orchestration-depth specs — single source of truth.

Each ``OrchestrationProfile`` maps to a ``ProfileOrchestrationSpec`` that
drives tool gating, Cryptex ring/behavioral composition, plan semantics,
guard strictness, and evaluator completion rules.  Downstream modules read
the spec instead of scattering ``if profile ==`` checks.

Profiles (4):
  conversational — chat + quick lookup/discovery tools; no plan/team/bash/write
  solo_structured — solo IC execution with plan/todo/file tools
  orchestrated — full engineering-manager stack with teams and delegates
  squad_lead — orchestrated superset + squad coordination domains
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nls.agentic.goals import OrchestrationProfile

if TYPE_CHECKING:
    from nls.agentic.types import LoopState

_VALID_PROFILES = frozenset({
    "conversational", "solo_structured", "orchestrated", "squad_lead",
})

_DEFAULT_PROFILE: OrchestrationProfile = "solo_structured"

# Behavioral slot domains owned by engineering-manager orchestration only.
_EM_ONLY_BEHAVIORAL_DOMAINS = frozenset({
    "coordinator_mode",
    "team_orchestration",
    "orchestration_tools",
    "help_requests",
    "plan_dependency_example",
    "repair_budget",
    "plan_discipline",
    "mode_awareness",
    "autonomous_updates",
    "dmn_discipline",
    "ooda_assessment",
    "em_completion_review",
})

# Conversational / direct-tool rules — only when profile is conversational.
_CONVERSATIONAL_ONLY_BEHAVIORAL_DOMAINS = frozenset({
    "answer_in_prose",
    "direct_tool_answer",
})

# Squad lead coordination — not shown to solo or generic orchestrated turns.
_SQUAD_LEAD_ONLY_BEHAVIORAL_DOMAINS = frozenset({
    "squad_coordination",
    "squad_channel_topology",
    "squad_help_requests",
    "squad_inbox_discipline",
})

# Plan-linked instruction domains in RING_INSTRUCTIONS.
_PLAN_INSTRUCTION_DOMAINS = frozenset({
    "plan_requirements",
    "tech_stack",
    "_plan_position",
})

# Tool-group keys in RING_TOOLS_MCP (domain prefix tool_group.*).
_TOOL_GROUP_PLANNING = "planning"


def normalize_profile(profile: str | None) -> OrchestrationProfile:
    p = (profile or _DEFAULT_PROFILE).strip().lower()
    if p == "direct_tool":
        p = "conversational"
    if p in _VALID_PROFILES:
        return p  # type: ignore[return-value]
    return _DEFAULT_PROFILE


@dataclass(frozen=True)
class ProfileOrchestrationSpec:
    profile: OrchestrationProfile

    # Tools denied (subtracted from mode allowlist).
    tool_deny: frozenset[str]

    # Behavioral domains allowed (None = all domains).
    behavioral_domains: frozenset[str] | None

    # Rings that may render at all (others skipped entirely).
    rings_visible: frozenset[str]

    # Rings that render only when a plan is active on this turn.
    rings_when_plan_active: frozenset[str]

    # tool_group.* entries hidden from tools_mcp ring.
    tool_groups_hidden: frozenset[str]

    # Plan semantics
    auto_mark_delegatable_multi_step: bool
    default_step_delegatable: bool
    inject_tech_stack_block: bool

    # AgentMode / coordinator
    allow_coordinator_modes: bool

    # Guards (engineering-manager strictness)
    em_pre_delegate_blocks: bool
    em_cold_start_goal_blocks: bool
    em_static_tool_hints: bool
    solo_static_tool_hints: bool
    skill_discovery_on_stall: bool
    em_assessment_loop: bool

    # Evaluator
    complete_on_prose: bool
    complete_on_implicit_delivery: bool
    complete_on_plan_artifacts: bool
    complete_on_plan_step_started: bool

    def tool_allowed(self, name: str, allowed: frozenset[str]) -> bool:
        if name in self.tool_deny:
            return False
        return name in allowed

    def behavioral_domain_visible(self, domain: str) -> bool:
        if self.behavioral_domains is None:
            return True
        return domain in self.behavioral_domains

    def ring_visible(self, ring_id: str, *, has_active_plan: bool) -> bool:
        if ring_id in self.rings_when_plan_active:
            return has_active_plan
        return ring_id in self.rings_visible

    def instruction_domain_visible(self, domain: str, *, has_active_plan: bool) -> bool:
        if domain in _PLAN_INSTRUCTION_DOMAINS:
            if not self.inject_tech_stack_block and domain == "tech_stack":
                return False
            return has_active_plan and self.profile in (
                "solo_structured", "orchestrated",
            )
        return True

    def tool_group_visible(self, group_key: str) -> bool:
        return group_key not in self.tool_groups_hidden


def _spec_conversational() -> ProfileOrchestrationSpec:
    """Chat + quick tools (lookup, discovery). No plan/team/bash/write."""
    return ProfileOrchestrationSpec(
        profile="conversational",
        tool_deny=frozenset({
            "team", "plan", "todo", "delegate", "bash", "write", "edit",
            "delete_file", "move_file", "server_install", "project_install",
            "await_delegates", "delegate_status", "scheduler",
            # switch_mode kept available so chat-depth turns can reach executing.
        }),
        behavioral_domains=frozenset({
            "answer_in_prose",
            "task_focus",
            "tool_best_practices",
            "direct_tool_answer",
            "working_memory_intro",
            "communication_discipline",
            "contacts_hygiene",
            "deferred_channel_delivery",
            "deferred_work",
            "credentials_handling",
            "escalate_to_user",
            "credential_hygiene",
            "orchestration_depth",
        }),
        rings_visible=frozenset({
            "identity", "user_model", "consolidation", "emotional",
            "behavioral", "environment", "channels", "tools_mcp",
            "skills", "project_facts", "instructions", "tactical_goals",
        }),
        rings_when_plan_active=frozenset(),
        tool_groups_hidden=frozenset({_TOOL_GROUP_PLANNING}),
        auto_mark_delegatable_multi_step=False,
        default_step_delegatable=False,
        inject_tech_stack_block=False,
        allow_coordinator_modes=False,
        em_pre_delegate_blocks=False,
        em_cold_start_goal_blocks=False,
        em_static_tool_hints=False,
        solo_static_tool_hints=False,
        skill_discovery_on_stall=False,
        em_assessment_loop=False,
        complete_on_prose=True,
        complete_on_implicit_delivery=True,
        complete_on_plan_artifacts=False,
        complete_on_plan_step_started=False,
    )


def _spec_solo_structured() -> ProfileOrchestrationSpec:
    return ProfileOrchestrationSpec(
        profile="solo_structured",
        tool_deny=frozenset({"team", "delegate", "await_delegates", "delegate_status"}),
        behavioral_domains=None,  # all except EM-only (filtered below)
        rings_visible=frozenset({
            "identity", "user_model", "consolidation", "emotional",
            "behavioral", "environment", "channels", "tools_mcp",
            "skills", "project_facts", "credentials", "instructions",
            "tactical_goals", "wake_attention",
        }),
        rings_when_plan_active=frozenset({"orchestration"}),
        tool_groups_hidden=frozenset(),  # plan visible; team stripped at tool level
        auto_mark_delegatable_multi_step=False,
        default_step_delegatable=False,
        inject_tech_stack_block=True,  # only when plan.tech_stack non-empty
        allow_coordinator_modes=False,
        em_pre_delegate_blocks=False,
        em_cold_start_goal_blocks=False,
        em_static_tool_hints=False,
        solo_static_tool_hints=True,
        skill_discovery_on_stall=False,
        em_assessment_loop=False,
        complete_on_prose=False,
        complete_on_implicit_delivery=True,
        complete_on_plan_artifacts=True,
        complete_on_plan_step_started=True,
    )


def _spec_squad_lead() -> ProfileOrchestrationSpec:
    """Squad lead — full EM surface plus squad coordination behavioral domains."""
    return ProfileOrchestrationSpec(
        profile="squad_lead",
        tool_deny=frozenset(),
        behavioral_domains=None,
        rings_visible=frozenset({
            "identity", "user_model", "consolidation", "emotional",
            "behavioral", "environment", "channels", "tools_mcp",
            "skills", "project_facts", "credentials", "instructions",
            "tactical_goals", "orchestration", "wake_attention",
            "strategic_goals",
        }),
        rings_when_plan_active=frozenset(),
        tool_groups_hidden=frozenset(),
        auto_mark_delegatable_multi_step=True,
        default_step_delegatable=False,
        inject_tech_stack_block=True,
        allow_coordinator_modes=True,
        em_pre_delegate_blocks=True,
        em_cold_start_goal_blocks=True,
        em_static_tool_hints=True,
        solo_static_tool_hints=False,
        skill_discovery_on_stall=True,
        em_assessment_loop=True,
        complete_on_prose=False,
        complete_on_implicit_delivery=True,
        complete_on_plan_artifacts=True,
        complete_on_plan_step_started=False,
    )


def _spec_orchestrated() -> ProfileOrchestrationSpec:
    return ProfileOrchestrationSpec(
        profile="orchestrated",
        tool_deny=frozenset(),
        behavioral_domains=None,
        rings_visible=frozenset({
            "identity", "user_model", "consolidation", "emotional",
            "behavioral", "environment", "channels", "tools_mcp",
            "skills", "project_facts", "credentials", "instructions",
            "tactical_goals", "orchestration", "wake_attention",
            "strategic_goals",
        }),
        rings_when_plan_active=frozenset(),
        tool_groups_hidden=frozenset(),
        auto_mark_delegatable_multi_step=True,
        default_step_delegatable=False,
        inject_tech_stack_block=True,
        allow_coordinator_modes=True,
        em_pre_delegate_blocks=True,
        em_cold_start_goal_blocks=True,
        em_static_tool_hints=True,
        solo_static_tool_hints=False,
        skill_discovery_on_stall=True,
        em_assessment_loop=True,
        complete_on_prose=False,
        complete_on_implicit_delivery=True,
        complete_on_plan_artifacts=True,
        complete_on_plan_step_started=False,
    )


_SPECS: dict[str, ProfileOrchestrationSpec] = {
    "conversational": _spec_conversational(),
    "solo_structured": _spec_solo_structured(),
    "orchestrated": _spec_orchestrated(),
    "squad_lead": _spec_squad_lead(),
}


def get_profile_spec(profile: str | None) -> ProfileOrchestrationSpec:
    return _SPECS[normalize_profile(profile)]


_PROFILE_META_TOOLS = frozenset({
    "adopt_orchestration_profile",
    "get_tool_schema",
    "switch_mode",
})


def apply_tool_deny(allowed: frozenset[str], profile: str | None) -> frozenset[str]:
    """Restrict tool schema by orchestration depth."""
    spec = get_profile_spec(profile)
    if not spec.tool_deny:
        return allowed
    filtered = allowed - spec.tool_deny
    if "communicate" in allowed:
        filtered = filtered | frozenset({"communicate"})
    return filtered | (_PROFILE_META_TOOLS & allowed)


def is_light_orchestration_profile(profile: str | None) -> bool:
    return normalize_profile(profile) == "conversational"


def behavioral_domain_visible_for_profile(domain: str, profile: str | None) -> bool:
    """Whether a behavioral slot domain may render for this profile."""
    spec = get_profile_spec(profile)
    if spec.behavioral_domains is not None:
        return spec.behavioral_domain_visible(domain)
    p = spec.profile
    if domain in _CONVERSATIONAL_ONLY_BEHAVIORAL_DOMAINS and p != "conversational":
        return False
    # solo_structured: all except EM-only domains
    if p == "solo_structured":
        return domain not in _EM_ONLY_BEHAVIORAL_DOMAINS
    if p == "orchestrated":
        if domain in _SQUAD_LEAD_ONLY_BEHAVIORAL_DOMAINS:
            return False
        return domain not in _CONVERSATIONAL_ONLY_BEHAVIORAL_DOMAINS
    if p == "squad_lead":
        if domain in _CONVERSATIONAL_ONLY_BEHAVIORAL_DOMAINS:
            return False
        return True
    return True


def cap_profile_for_tool_surface(profile: str, allowed_tools: frozenset[str]) -> str:
    """Downgrade profile when triage depth exceeds available tool surface."""
    p = normalize_profile(profile)
    if p == "squad_lead" and "squad" not in allowed_tools:
        p = "orchestrated"
    if p == "orchestrated" and "team" not in allowed_tools:
        p = "solo_structured"
    if p == "solo_structured":
        effective = apply_tool_deny(allowed_tools, p)
        if "plan" in allowed_tools and "plan" not in effective:
            if any(
                t in effective
                for t in ("web_search", "web_fetch", "read", "browser", "clawhub")
            ):
                return "conversational"
    return p


def is_solo_execution_profile(profile: str | None) -> bool:
    """True when the agent executes plan steps itself (no team waves)."""
    return normalize_profile(profile) == "solo_structured"


def plan_step_delegatable_default(profile: str | None) -> bool:
    return get_profile_spec(profile).default_step_delegatable


def should_auto_mark_delegatable(profile: str | None, step_count: int) -> bool:
    spec = get_profile_spec(profile)
    return step_count >= 3 and spec.auto_mark_delegatable_multi_step


def profile_anchor_message(profile: str | None) -> str:
    """Single system-line anchor when Cryptex behavioral slots carry the detail."""
    spec = get_profile_spec(profile)
    anchors = {
        "conversational": (
            "[ORCHESTRATION DEPTH: conversational] Answer in chat. Use lookup "
            "and discovery tools (web_search, browser, clawhub) when helpful. "
            "No plan, team, todo, delegate, bash, or file writes."
        ),
        "solo_structured": (
            "[ORCHESTRATION DEPTH: solo_structured] Execute yourself with "
            "allowed tools. No team waves or sub-agent teams."
        ),
        "squad_lead": (
            "[ORCHESTRATION DEPTH: squad_lead] You lead a persistent squad. "
            "Use squad tools to approve inbox items, assign member todos, "
            "spawn_member (create + job + brief), add_member, remove_member, "
            "inspect_member_config, configure_member (member skill/channel config), "
            "set_member_job (update member charters), set_lead_job (your job — only "
            "after ask_user owner_confirmed=true), request_trust_change (owner "
            "dashboard approval), pause_member, request_delete_member (owner confirms), "
            "and resolve squad_escalate requests. Full tool surface retained."
        ),
    }
    return anchors.get(spec.profile, "")


def evaluate_plan_artifact_complete(
    state: "LoopState",
    hooks: Any | None,
) -> bool:
    """True when plan deliverables exist or solo work produced files + prose."""
    spec = get_profile_spec(getattr(state, "orchestration_profile", None))
    if not spec.complete_on_plan_artifacts:
        return False
    last_text = getattr(state, "_last_iter_text", "") or ""
    written = list(getattr(state, "files_written", []) or [])
    if hooks is None:
        return False
    plan_tool = getattr(hooks, "_cached_plan_tool", None)
    if plan_tool is None:
        return False
    try:
        store = plan_tool.get_store()
        plan = store.find_active()
        if plan is None:
            return False
        from pathlib import Path

        ws = Path(plan_tool._workspace)
        if plan.steps and all(s.status in ("done", "skipped") for s in plan.steps):
            return True
        done_with_files = [
            s for s in plan.steps
            if s.status in ("done", "skipped") and (s.output_files or [])
        ]
        if done_with_files:
            for step in done_with_files:
                for rel in step.output_files or []:
                    rel = (rel or "").strip()
                    if not rel:
                        continue
                    candidates = [ws / rel]
                    if plan.project_dir:
                        candidates.insert(0, ws / plan.project_dir / rel)
                    if not any(p.is_file() for p in candidates):
                        return False
            return True
        if (
            spec.profile == "solo_structured"
            and plan.steps
            and written
            and state.consecutive_text_only >= 1
            and len(last_text) > 80
        ):
            return True
    except Exception:
        return False
    return False


def evaluate_plan_step_started_complete(
    state: "LoopState",
    hooks: Any | None,
) -> bool:
    """True when plan exists, a step is in_progress, and prose was delivered."""
    if not get_profile_spec(
        getattr(state, "orchestration_profile", None),
    ).complete_on_plan_step_started:
        return False
    if state.consecutive_text_only < 1:
        return False
    last_text = getattr(state, "_last_iter_text", "") or ""
    if len(last_text) < 80:
        return False
    if hooks is None:
        return False
    plan_tool = getattr(hooks, "_cached_plan_tool", None)
    if plan_tool is None:
        return False
    try:
        plan = plan_tool.get_store().find_active()
        if plan is None:
            return False
        in_progress = [s for s in plan.steps if s.status == "in_progress"]
        return bool(in_progress)
    except Exception:
        return False
