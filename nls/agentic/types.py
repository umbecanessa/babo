"""Shared types, constants, and helpers for the NLS agentic loops.

This module is the single source of truth for infrastructure shared
between v2 and v3 (and any future loop versions).  It contains:

- Core dataclasses: AgenticConfig, AgenticHooks, AgenticResult, AgentEvent
- EventType enum
- Virtual tool schemas (ask_user, escalate, communicate, delegate)
- Tool-forcing preamble
- Context sanitization, thinking mode selection, toolcall stripping
- Plan position helper
"""

from __future__ import annotations

import json as _json
import logging
import re as _re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from nls.tools.agent_tools.base import AgentTool, ToolResult
from nls.agentic.outbound_notify import FINAL_SUMMARY_SCHEMA_PROPERTY

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AgentMode — six-mode orchestration system
# ---------------------------------------------------------------------------
# Each mode defines a PRIMARY tool set (always available), an OVERRIDE set
# (available with friction/awareness), and tools that are fully excluded.
# The Cryptex renders mode-specific behavioral rules and ring priorities.


class AgentMode(str, Enum):
    """Operational modes for the agentic loop."""
    CHAT = "chat"
    PLANNING = "planning"
    DELEGATING = "delegating"
    MONITORING = "monitoring"
    EVALUATING = "evaluating"
    EXECUTING = "executing"
    # Transient mode: coordinator received a direct user request while
    # supervising background teams.  Grants full personal-tool access
    # (calendar, email, skills) without abandoning orchestration context.
    # Auto-transitions back to the prior coordinator mode after responding.
    RESPONDING = "responding"


# Shared tool groups used across multiple modes
_COMM_TOOLS = frozenset({
    "communicate", "ask_user", "contacts",
    "whatsapp_send", "telegram_send", "email_send",
    "gmail_send", "gmail_reply",
    "email_history",
})
_CONTACTS_CALENDAR = frozenset({
    "contacts", "calendar_list", "calendar_create", "calendar_update",
    "email_history",
})
_EMAIL_DRIVE_RO = frozenset({
    "gmail_search", "gmail_read", "gmail_labels", "gmail_attachment",
    "drive_search", "drive_list", "drive_read",
    "sheets_info", "sheets_read",
})
_SKILL_TOOLS = frozenset({
    "clawhub", "skill_configure", "crystallize_skill",
})
_RESEARCH_TOOLS = frozenset({
    "read", "list_dir", "web_search", "web_fetch",
    "screenshot", "offer_download",
    "grep", "glob", "semantic_search",
    "file_history", "chat_history", "channel_history",
})
_FILE_TOOLS = frozenset({
    "write", "edit", "delete_file", "move_file",
})
# Tools needed to discover and activate new capabilities — always
# available in coordinator modes so the agent can self-configure.
_DISCOVERY_TOOLS = frozenset({
    "discover_tools", "skill_configure", "clawhub",
})

# Per-mode primary tool sets — always available without friction.
MODE_PRIMARY_TOOLS: dict[AgentMode, frozenset[str]] = {
    AgentMode.CHAT: (
        _COMM_TOOLS | _CONTACTS_CALENDAR | _EMAIL_DRIVE_RO | _SKILL_TOOLS
        | frozenset({"read", "web_search", "web_fetch", "screenshot",
                     "offer_download", "scheduler", "switch_mode", "todo",
                     "task_complete"})
    ),
    AgentMode.PLANNING: (
        frozenset({"todo", "plan", "switch_mode", "task_complete"})
        | _RESEARCH_TOOLS | _COMM_TOOLS | _SKILL_TOOLS
    ),
    AgentMode.DELEGATING: (
        frozenset({"team", "delegate", "delegate_status", "delegate_ring",
                   "plan", "todo", "scheduler", "read", "switch_mode", "wait",
                   "await_delegates", "task_complete"})
        | _COMM_TOOLS | _DISCOVERY_TOOLS
    ),
    AgentMode.MONITORING: (
        frozenset({"team", "await_delegates", "delegate_status",
                   "delegate_ring", "scheduler", "switch_mode", "communicate"})
        | _COMM_TOOLS
    ),
    AgentMode.EVALUATING: (
        _RESEARCH_TOOLS | _FILE_TOOLS | _COMM_TOOLS
        | frozenset({"bash", "todo", "plan", "team", "switch_mode",
                     "scheduler", "wait", "delegate_status", "delegate_ring",
                     "task_complete", "offer_download", "server_install",
                     "project_install"})
        | _DISCOVERY_TOOLS
    ),
    AgentMode.EXECUTING: frozenset(),  # empty = all tools allowed
    # Responding: full personal + skill tools while keeping coordinator
    # awareness.  Does NOT include bash/write/team to avoid scope creep.
    AgentMode.RESPONDING: (
        _COMM_TOOLS | _CONTACTS_CALENDAR | _EMAIL_DRIVE_RO | _SKILL_TOOLS
        | _DISCOVERY_TOOLS
        | frozenset({"read", "web_search", "web_fetch", "screenshot",
                     "offer_download", "scheduler", "switch_mode", "todo",
                     "team", "delegate_status", "wait", "await_delegates",
                     "google_workspace_connect", "task_complete"})
    ),
}

# Per-mode override tool sets — available but inject friction messages.
MODE_OVERRIDE_TOOLS: dict[AgentMode, frozenset[str]] = {
    AgentMode.CHAT: frozenset(),
    AgentMode.PLANNING: frozenset({"bash"}),
    AgentMode.DELEGATING: frozenset({"bash", "write", "list_dir"}),
    AgentMode.MONITORING: frozenset(),
    AgentMode.EVALUATING: frozenset(),  # full access, no overrides needed
    AgentMode.EXECUTING: frozenset(),
    AgentMode.RESPONDING: frozenset({"bash", "write", "list_dir", "grep", "glob"}),
}

# Backward compat alias — union of all coordinator-era tools.
COORDINATOR_TOOLS = (
    MODE_PRIMARY_TOOLS[AgentMode.PLANNING]
    | MODE_PRIMARY_TOOLS[AgentMode.DELEGATING]
    | MODE_PRIMARY_TOOLS[AgentMode.MONITORING]
    | MODE_PRIMARY_TOOLS[AgentMode.EVALUATING]
    | MODE_OVERRIDE_TOOLS[AgentMode.PLANNING]
    | MODE_OVERRIDE_TOOLS[AgentMode.DELEGATING]
    | MODE_OVERRIDE_TOOLS[AgentMode.MONITORING]
)

# Max timeout (seconds) for bash when running inside coordinator-like modes.
COORDINATOR_BASH_TIMEOUT_S = 60


def get_allowed_tools(mode: AgentMode) -> frozenset[str]:
    """Return primary + override tools for a mode (all callable tools)."""
    return MODE_PRIMARY_TOOLS[mode] | MODE_OVERRIDE_TOOLS[mode]


def is_override_tool(mode: AgentMode, tool_name: str) -> bool:
    """Check if a tool call is an override (available but with friction)."""
    return tool_name in MODE_OVERRIDE_TOOLS.get(mode, frozenset())


# ---------------------------------------------------------------------------
# Context sanitisation for vLLM / Hermes tool-calling parser
# ---------------------------------------------------------------------------


def _sanitize_context(messages: list[dict]) -> list[dict]:
    """Ensure context conforms to vLLM's strict chat template rules.

    Rules enforced:
    - System messages must ALL be at the beginning (Qwen3.5 requirement).
      Any system message after position 0 is merged into the first one.
    - assistant msgs with tool_calls must have content=None (not "")
    - tool_call arguments must be valid JSON (truncated args are fixed)
    - tool msgs must have a non-empty string tool_call_id
    - every tool_call_id in an assistant msg must have a matching tool msg
    """
    # --- Phase 1: consolidate system messages to the front ---
    first_system_idx = -1
    extra_system_parts: list[str] = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "system":
            if first_system_idx < 0:
                first_system_idx = i
            else:
                content = (msg.get("content") or "").strip()
                if content:
                    extra_system_parts.append(content)

    if extra_system_parts:
        logger.info(
            "Context sanitization: merging %d extra system message(s) "
            "into position %d",
            len(extra_system_parts), first_system_idx,
        )

    consolidated: list[dict] = []
    _seen_first_system = False
    for msg in messages:
        if msg.get("role") == "system":
            if not _seen_first_system:
                _seen_first_system = True
                if extra_system_parts:
                    base = (msg.get("content") or "").strip()
                    merged = "\n\n".join([base] + extra_system_parts)
                    consolidated.append({**msg, "content": merged})
                else:
                    consolidated.append(msg)
            # else: skip — already merged
        else:
            consolidated.append(msg)

    # --- Phase 2: sanitize tool calls and tool responses ---
    cleaned: list[dict] = []
    pending_tool_ids: set[str] = set()

    for msg in consolidated:
        role = msg.get("role")

        if role == "assistant":
            tc = msg.get("tool_calls")
            if tc:
                sanitized_tc = []
                for call in tc:
                    args = call.get("function", {}).get("arguments", "")
                    if args and isinstance(args, str):
                        try:
                            _json.loads(args)
                        except _json.JSONDecodeError:
                            call = {**call, "function": {
                                **call["function"],
                                "arguments": "{}",
                            }}
                            logger.warning(
                                "Sanitized truncated tool_call arguments "
                                "for %s",
                                call.get("function", {}).get("name", "?"),
                            )
                    sanitized_tc.append(call)
                    cid = call.get("id")
                    if cid:
                        pending_tool_ids.add(cid)
                m = {**msg, "content": None, "tool_calls": sanitized_tc}
                cleaned.append(m)
            else:
                content = msg.get("content")
                if content is None:
                    content = ""
                cleaned.append({**msg, "content": content})

        elif role == "tool":
            cid = msg.get("tool_call_id", "")
            if not cid:
                cid = "unknown"
            pending_tool_ids.discard(cid)
            cleaned.append({**msg, "tool_call_id": cid})

        else:
            cleaned.append(msg)

    if pending_tool_ids:
        logger.warning(
            "Context sanitization: %d orphan tool_call_ids without "
            "matching tool responses: %s -- injecting placeholders",
            len(pending_tool_ids), pending_tool_ids,
        )
        for cid in pending_tool_ids:
            cleaned.append({
                "role": "tool",
                "tool_call_id": cid,
                "content": "[No result captured]",
            })

    return cleaned


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    TOOL_EXECUTION_START = "tool_execution_start"
    TOOL_EXECUTION_END = "tool_execution_end"
    ACTIVITY_STATUS = "activity_status"
    TOOL_OUTPUT_CHUNK = "tool_output_chunk"
    TURN_THINKING = "turn_thinking"
    BROWSER_NAVIGATION = "browser_navigation"
    AGENTIC_TOKEN = "agentic_token"
    TOOL_CALL_DELTA = "tool_call_delta"
    AGENTIC_PLAN = "agentic_plan"
    PLAN_STEP_UPDATE = "plan_step_update"
    BROWSER_COMMAND = "browser_command"
    ASK_USER = "ask_user"
    USER_ANSWER = "user_answer"
    DELEGATE_START = "delegate_start"
    DELEGATE_END = "delegate_end"
    COMMUNICATE = "communicate"
    PROBE_SIGNAL = "probe_signal"


@dataclass
class AgentEvent:
    """Typed event emitted by the agentic loop."""

    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, **self.data}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AgenticConfig:
    """Configuration for the agentic loop."""

    max_iterations: int = 40
    """Maximum turn iterations before forced stop (may be auto-extended)."""

    max_iterations_extension: int = 30
    """How many iterations to add when auto-extending."""

    max_total_iterations: int = 200
    """Absolute ceiling — never extend beyond this."""

    tool_timeout_seconds: int = 30
    """Timeout for a single tool execution."""

    max_context_chars: int = 400_000
    """Max characters in the context before compaction (~100K tokens)."""

    result_max_chars: int = 20_000
    """Max characters per tool result sent to the model."""

    max_continuation_passes: int = 1
    """How many times to re-prompt the model when it stops without
    completing the task.  After tool execution, if the model responds
    with text only, a continuation prompt is injected to ask whether
    the task is truly complete.  Set to 0 to disable."""

    cortisol_redirect_threshold: float = 0.55
    """Cortisol level that triggers a strategic redirect instead of abort.
    When hit, the loop injects a prompt telling the agent to try a
    different approach (e.g. web search, rethink strategy).  Lower
    than the abort threshold so the agent gets a chance to pivot.
    v1.3: raised from 0.45 — agentic loops fire many tools rapidly
    and normal exploration errors (wrong path, file not found) were
    hitting the old threshold within 4-5 errors."""

    cortisol_abort_threshold: float = 0.80
    """Cortisol level that triggers a hard abort.  Only fires if the
    redirect didn't help and errors keep accumulating.
    v1.3: raised from 0.75 — combined with lower production rate
    and faster decay, this gives the agent more runway."""

    consecutive_error_redirect: int = 3
    """Number of consecutive tool errors before injecting a strategy
    redirect prompt, regardless of cortisol level."""

    think_budget: int = 0
    """Max thinking tokens to stream to the UI per iteration.
    0 = unlimited (stream all thinking tokens when thinking is
    enabled).  The think/no-think classifier already controls
    whether the model generates <think> blocks at all, so capping
    the stream is no longer needed."""

    allow_parallel: bool = True
    """When True and the model emits multiple tool calls that are
    all in ``parallel_safe_tools``, execute them concurrently via
    asyncio.gather instead of sequentially."""

    parallel_safe_tools: tuple[str, ...] = ("read", "web_search", "web_fetch")
    """Tools with no side effects that can safely execute in
    parallel.  All others execute sequentially."""

    max_tool_calls_per_step: int = 3
    """Maximum tool calls the loop will execute in a single
    iteration.  Extra calls beyond this limit are dropped."""

    include_plan_tool: bool = True
    """When False, the ``plan`` tool is not offered to the model.
    This prevents plan-only iterations that waste turns when
    the task is narrow and the agent should act immediately.
    When True (default), the PlanTool is expected in the tools list."""

    # --- Crash-resilience ---

    shared_context: list[dict] | None = None
    """Mutable list shared with the caller.  The agentic loop writes
    its current context here after every iteration so the caller can
    salvage it on unexpected disconnection (WebSocket 1006 etc.).
    Pass an empty ``list()`` to enable; ``None`` disables."""

    checkpoint_callback: (
        Callable[[list[dict], list[str], list[bool], int], None] | None
    ) = None
    """Periodic checkpoint during long loops.
    Args: (context_snapshot, plan_steps, plan_done, iteration).
    Called every ``checkpoint_interval`` iterations so the caller can
    persist partial progress.  The snapshot is a *shallow copy* of
    the context list at that point."""

    checkpoint_interval: int = 5
    """How often (in iterations) to call ``checkpoint_callback``."""

    # --- v3 compaction settings ---

    compaction_timeout: int = 45
    """Timeout (seconds) for the LLM-based compaction summary call.
    If the call fails or times out, the loop falls back to simple
    recency-based compaction."""

    keep_recent_chars: int = 160_000
    """When compacting, keep the most recent messages within this
    character budget (~40K tokens).  Older messages are summarized by the LLM."""


# ---------------------------------------------------------------------------
# Cognitive hooks
# ---------------------------------------------------------------------------


@dataclass
class AgenticHooks:
    """Pluggable hooks for NLS's cognitive layer.

    The agentic loop calls these at the right moments.  If a hook is
    None, it is simply skipped.  This keeps the loop clean while
    allowing the full biological pipeline to run.
    """

    on_tool_success: Callable[[str, dict, ToolResult], Any] | None = None
    """Called after a tool succeeds. Args: (tool_name, params, result)."""

    on_tool_error: Callable[[str, dict, ToolResult], Any] | None = None
    """Called after a tool fails. Args: (tool_name, params, result)."""

    on_turn_end: Callable[[str, list[dict], list[dict]], Any] | None = None
    """Called at end of each turn. Args: (response_text, tool_calls, tool_results)."""

    on_agent_end: Callable[[AgenticResult], Any] | None = None
    """Called when the agent loop completes. Args: (result,)."""

    should_abort: Callable[[], bool] | None = None
    """Called before each turn. Return True to abort."""

    get_cortisol: Callable[[], float] | None = None
    """Return the current cortisol level (0.0-1.0).  Used for the
    redirect threshold check -- distinct from should_abort which
    only fires at the higher abort threshold."""

    tick_hypothalamus: Callable[[float], None] | None = None
    """Advance hormonal decay by elapsed_seconds.  Called once per
    iteration so hormones decay realistically during long agentic
    runs instead of accumulating without bound."""

    # --- ANS integration (mid-loop biological awareness) ---

    ans_checkpoint: Callable[[list[dict], list[dict]], str | None] | None = None
    """Called at error-triggered reflection points.  Receives the
    accumulated error_log and success_log from the current loop,
    runs ANS analysis + knowledge.db lookup, and returns a short
    context hint to inject (or None if nothing useful)."""

    ans_collect_tool_event: (
        Callable[[str, str, dict, str, bool], None] | None
    ) = None
    """Feed a tool execution event to the ANS signal buffer.
    Args: (tool_name, call_id, args, output_preview, is_error)."""

    preflight_knowledge: Callable[[str], str | None] | None = None
    """Called once before the first iteration with the user's message.
    Queries knowledge.db for facts relevant to the task and returns
    a context block to inject (or None).  This replaces the LOOKUP
    signal that the behavior adapter would normally trigger."""

    ans_tool_learning: (
        Callable[[str, dict, str, str], None] | None
    ) = None
    """Extract learnable facts from tool results into ANS buffer.
    Called after each successful tool execution.
    Args: (tool_name, args, result_text, user_message)."""

    ans_record_task: (
        Callable[[str, str, list[str], bool, float], None] | None
    ) = None
    """Record task completion for cross-turn memory.
    Args: (user_message, final_response, tools_used, success, duration_ms)."""

    ans_get_task_context: Callable[[], str | None] | None = None
    """Get recent task summaries for context injection."""

    ans_get_context: Callable[[], str | None] | None = None
    """Get the ANS signal buffer context summary (learnings, bonds,
    evaluations).  This is the nervous system's working memory —
    always injected at the start of an agentic run so the model
    has awareness of everything it has learned in this session."""

    wm_get_context: Callable[[], str | None] | None = None
    """Get the slot-based working memory context (goals, active facts,
    feelings, intentions).  Injected alongside ANS context at the
    start of an agentic run."""

    wm_activate: Callable[[str], str | None] | None = None
    """Switch the active WM workspace (professional vs personal)
    based on the task source.  Called at loop start."""

    ans_extract_user_answer: (
        Callable[[str, str, list[dict]], None] | None
    ) = None
    """Extract learnable facts from a user's answer to ask_user().
    Args: (question, answer, context).  Runs the ANS safety-net LLM
    micro-call to capture credentials, names, URLs etc. that the
    adapter might miss during fast tool execution.  The
    context list provides the recent conversation for richer
    extraction."""

    ans_iteration_extract: (
        Callable[[str, list[dict], list[dict], int], None] | None
    ) = None
    """Per-iteration safety net: extract learnings from this iteration's
    tool results in the context of the user's original request.
    Fires at every iteration boundary so learning is distributed
    throughout the agentic loop, not clustered at the end.
    Args: (user_message, tool_results_this_iter, error_log_this_iter, iteration)."""

    ans_get_learnings: Callable[[], str | None] | None = None
    """Get accumulated LEARN signals from the current session.
    Returns a formatted string of facts learned so far, for injection
    into the Reflect phase prompt so it can synthesize higher-order
    insights from the per-iteration learnings."""

    store_memory: Callable[[str, str, str], None] | None = None
    """Store a fact in DomainDB + inject as ANS LEARN signal.
    Args: (domain, fact, user_message).  Dual-write ensures both
    immediate recall (DomainDB) and sleep consolidation (ANS buffer
    -> consolidation sleep -> persisted memory)."""

    # --- Context-pressure hooks ---

    ans_pressure_reconcile: Callable[[], int] | None = None
    """Proactive ANS consolidation sweep.  Deduplicates same-domain
    LEARN signals, merges near-duplicates, and removes stale
    EVALUATE signals.  Returns the number of signals removed.
    Called as Level 1 of the progressive overflow handler."""

    compact_system_prompt: Callable[[int], str] | None = None
    """Return a slimmer system prompt at the given compaction level.
    Level 1 = drop low-priority sections + condense mid-priority.
    Level 2 = keep only essential sections (tools, rules, identity).
    Called as Levels 3-4 of the progressive overflow handler."""

    wm_push_goals: Callable[[list[str], str], None] | None = None
    """Sync plan state to working memory.
    Args: (step_labels, user_message).  The implementation looks up
    the active plan ID itself and stores a single strategic goal
    referencing it, rather than storing individual steps."""

    wm_push_task_goals: Callable[[list[str]], None] | None = None
    """Push extracted sub-task goals as tactical goals in WM.
    Called at the start of the agentic loop for tasks that don't
    go through the plan system, so the evaluator can track whether
    all parts of the user's request have been addressed."""

    wm_mark_task_goal_done: Callable[[str], bool] | None = None
    """Mark a tactical task goal as done (by substring match).
    Returns True if a goal was matched and removed."""

    wm_begin_task_epoch: Callable[..., None] | None = None
    """Rotate session-scoped WM when a new user/channel task starts."""

    wm_prune_supporting_facts_for_goal: Callable[[str], int] | None = None
    """Remove session facts that supported a completed goal."""

    wm_has_pending_task_goals: Callable[[], bool] | None = None
    """Return True if there are still pending tactical task goals."""

    wm_push_instructions: Callable[[list[str]], None] | None = None
    """Populate WM instructions slot with task directives extracted
    from the system prompt and user input."""

    wm_get_instructions: Callable[[], str | None] | None = None
    """Return formatted instructions for injection into context."""

    wm_clear_instructions: Callable[[], None] | None = None
    """Clear instructions slot after reflect phase consolidation."""

    wm_set_plan_position: Callable[[str], None] | None = None
    """Store the current plan position string in WM so it flows
    through every wm_get_context() refresh and survives compaction."""

    wm_refresh_todo_board: Callable[[], None] | None = None
    """Refresh the todo board snapshot in WM so the agent sees
    the current Kanban state (active items, statuses, linked plans)."""

    update_todo_status: Callable[[str, str], None] | None = None
    """Update a linked todo item's status.
    Args: (todo_id, status).  Called when a plan completes so the
    corresponding todo is marked done."""

    # --- ANS signal injection ---

    inject_signal: Callable[..., None] | None = None
    """Inject a signal into the ANS buffer from the agentic loop.
    Signature: inject_signal(signal_type, content, source, prompt, response)."""

    plan_register_file: Callable[[str], None] | None = None
    """Auto-register a file path in the active plan's scaffolding.
    Called after successful write/edit tool calls."""

    wm_upsert_digest: Callable[[str, str], None] | None = None
    """Store a cognitive digest in working memory as a fact slot.
    Args: (domain, content).  Domain should be 'Digest.{path}'.
    Called after reading large files/content to preserve understanding
    across context compaction."""

    wm_consolidate_session: Callable[[str], None] | None = None
    """Roll up operational context into protected consolidation slots.
    Args: (summary).  Called at the end of each agentic loop to persist
    a rolling record of session progress, knowledge, and task context."""

    wm_save: Callable[[], None] | None = None
    """Persist working memory to disk immediately."""

    dampen_cortisol: Callable[[float], None] | None = None
    """Reduce cortisol by the given amount (recovery relief)."""

    # --- v3 interoceptive snapshot ---

    get_interoceptive_snapshot: Callable[[], Any] | None = None
    """Return an InteroceptiveSnapshot collecting from all biological
    subsystems.  Used by the v3 loop's hormone-modulated evaluator."""

    # --- Event persistence ---

    log_event: Callable[..., None] | None = None
    """Append an event to the persistent events.jsonl file.
    Signature: log_event(event_type: str, **data)."""

    # --- v3 context transform ---

    transform_context: Callable[[list[dict], str], list[dict]] | None = None
    """Called just before each LLM call in the v3 loop.  Receives the
    full message list and user_input, returns the (possibly modified)
    message list.  Used by server_runtime to refresh WM content."""

    # --- V5 Signal Probe hooks ---

    probe_post_gen: Callable[[str], dict[str, float]] | None = None
    """Run V5 post-generation signal probes on full text (prompt+response).
    Returns signal vector {probe_name: activation}.  May be a coroutine
    function (async def) — the agentic loop will await it if so."""

    on_probe_signals: (
        Callable[[dict[str, float], str, str], None] | None
    ) = None
    """Process V5 probe signal activations via ANS.
    Args: (signal_vector, prompt, response)."""

    v5_signal_probes: bool = False
    """Whether V5 signal probes are active for this agent."""


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class AgenticResult:
    """Final result of the agentic loop."""

    final_response: str = ""
    reflect_text: str = ""
    iterations: int = 0
    total_tool_calls: int = 0
    aborted: bool = False
    abort_reason: str = ""
    events: list[AgentEvent] = field(default_factory=list)
    hormones: dict[str, float] = field(default_factory=dict)
    total_duration_ms: float = 0.0
    name_update: str | None = None
    context_messages: list[dict] = field(default_factory=list)
    loop_start_idx: int = 0


# ---------------------------------------------------------------------------
# Virtual tool schemas
# ---------------------------------------------------------------------------


_ASK_USER_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Ask the user a question and WAIT for their answer. "
            "Use this ONLY when you need information the user must provide: "
            "credentials, a URL, a preference, or clarification. "
            "The agent loop PAUSES until the user replies. "
            "Do NOT use this for progress updates — use communicate() instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user.",
                }
            },
            "required": ["question"],
        },
    },
}

_ESCALATE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "escalate",
        "description": (
            "Ask the orchestrator for help. Use when you are stuck, blocked, "
            "need more iteration budget, need a decision you cannot make "
            "alone, or need write access to a file outside your owned_paths "
            "(reason='file_access', paths=[...]). The loop PAUSES until the "
            "orchestrator replies (up to 2 minutes). Prefer this over silent "
            "looping when progress stalls."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": ["stuck", "need_time", "blocked", "question", "file_access"],
                    "description": (
                        "Why you are escalating: stuck on a technical issue, "
                        "need more iterations, blocked on credentials/access, "
                        "file_access for paths outside your assignment, "
                        "or a general question."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": (
                        "What you need from the orchestrator: what you tried, "
                        "what is blocking you, and what decision or help "
                        "would unblock progress."
                    ),
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "For reason='file_access': path patterns you need to "
                        "write/edit (e.g. ['.gitignore', 'backend/pyproject.toml'])."
                    ),
                },
            },
            "required": ["reason", "message"],
        },
    },
}

_COMMUNICATE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "communicate",
        "description": (
            "Send a message to the user WITHOUT pausing. "
            "Use this for progress updates, status reports, or informing "
            "the user about what you're doing. The loop continues "
            "immediately — the user does NOT need to respond. "
            "Prefer this over ask_user when you don't need an answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to send to the user.",
                },
                "final_summary": FINAL_SUMMARY_SCHEMA_PROPERTY,
            },
            "required": ["message"],
        },
    },
}

_DELEGATE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delegate",
        "description": (
            "Delegate a focused sub-task to a fresh sub-agent with its own "
            "isolated context. The sub-agent has your execution tools and "
            "accumulated knowledge (from memory), but NOT your conversation "
            "history. It can READ plans but not create or modify them — you "
            "control the plan lifecycle.\n\n"
            "IMPORTANT: If you have a plan with delegatable steps, use the "
            "team tool instead — it provides wave ordering, dependency tracking, "
            "escalation, and auto-extensions. Use delegate ONLY for ad-hoc "
            "one-off tasks that are NOT part of an existing plan.\n\n"
            "PARALLEL FAN-OUT: Call delegate multiple times IN THE SAME "
            "TURN to run sub-agents concurrently. They execute simultaneously "
            "and all results are returned before your next turn. Use this for "
            "quick independent lookups — e.g. 'read these 5 files'.\n\n"
            "Max 5 delegate calls total."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Clear, self-contained task description. Include all "
                        "context the sub-agent needs — it has no memory of "
                        "your current turn. For parallel agents working on the "
                        "same codebase, name the specific files, modules, or "
                        "directories each agent should focus on."
                    ),
                },
                "max_steps": {
                    "type": "integer",
                    "description": (
                        "Max iterations for the sub-agent (default 25, max 50). "
                        "Scale to task complexity: 5-8 for simple single-command "
                        "tasks (start a server, run a check, install something), "
                        "10-15 for moderate tasks, 20-35 for complex multi-file "
                        "tasks. The sub-agent gets a ~33% extension bonus if it "
                        "has a pending plan when the base limit is reached."
                    ),
                },
            },
            "required": ["task"],
        },
    },
}

_WAIT_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "wait",
        "description": (
            "Pause the agent loop for a SHORT time (max 60s recommended) "
            "then continue in THIS session. Use only for quick polls: "
            "e.g. wait(seconds=15) once after launch, then team(inspect). "
            "Do NOT use wait(seconds=60+) while a team wave or delegates "
            "run in the background — that burns iterations. Use "
            "await_delegates(summary='...') instead to EXIT the loop; "
            "you will be re-invoked on wave completion or escalation. "
            "Never poll delegate_status in a tight loop. Max 300 seconds."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "description": "How many seconds to pause (1–300).",
                    "minimum": 1,
                    "maximum": 300,
                },
                "reason": {
                    "type": "string",
                    "description": "Optional brief note on why you're waiting (for logs).",
                },
            },
            "required": ["seconds"],
        },
    },
}

_AWAIT_DELEGATES_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "await_delegates",
        "description": (
            "Exit this loop while sub-agents or a team wave keep working "
            "in the background. Use AFTER you launched a team/delegates "
            "and sent the user a brief status — NOT when all work is done.\n"
            "DIFFERENCE FROM OTHER TOOLS:\n"
            "- task_complete = the user's entire request is finished.\n"
            "- wait(seconds=N) = stay in this loop and sleep (bad for long "
            "waves — causes 60s/180s polling).\n"
            "- await_delegates = you are done monitoring FOR NOW; the "
            "runtime will wake you on wave completion, escalation, or "
            "scheduler check-back.\n"
            "Typical flow: team(launch) → communicate(status) → "
            "await_delegates(summary='Wave 2 running — N agents on ...')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "Brief status for logs and optional user handoff "
                        "(what wave/delegates you are waiting on)."
                    ),
                },
                "team_id": {
                    "type": "string",
                    "description": "Optional team id you are monitoring.",
                },
            },
            "required": ["summary"],
        },
    },
}


def virtual_tool_schemas_for_loop(
    *,
    enable_delegation: bool,
    enable_detached_delegates: bool = False,
    delegate_manager: Any | None = None,
) -> list[dict]:
    """Base virtual tool schemas for an agentic loop.

    Orchestrators get ask_user/communicate/delegate; worker sub-agents get
    escalate instead of ask_user and cannot delegate further.
    """
    schemas: list[dict] = []
    if enable_delegation:
        schemas.extend([
            _ASK_USER_TOOL_SCHEMA,
            _DELEGATE_TOOL_SCHEMA,
            _COMMUNICATE_TOOL_SCHEMA,
            _SWITCH_MODE_TOOL_SCHEMA,
        ])
    else:
        schemas.extend([_ESCALATE_TOOL_SCHEMA, _ASK_USER_TOOL_SCHEMA])
    if enable_delegation and enable_detached_delegates and delegate_manager is not None:
        schemas.append(_DELEGATE_STATUS_TOOL_SCHEMA)
        schemas.append(_WAIT_TOOL_SCHEMA)
        schemas.append(_AWAIT_DELEGATES_TOOL_SCHEMA)
    schemas.append(_ADOPT_ORCHESTRATION_PROFILE_TOOL_SCHEMA)
    return schemas


def virtual_tool_names_for_loop(
    *,
    enable_delegation: bool,
    enable_detached_delegates: bool = False,
    delegate_manager: Any | None = None,
) -> frozenset[str]:
    """Tool names from virtual_tool_schemas_for_loop (for unlocked_tools)."""
    return frozenset(
        name
        for s in virtual_tool_schemas_for_loop(
            enable_delegation=enable_delegation,
            enable_detached_delegates=enable_detached_delegates,
            delegate_manager=delegate_manager,
        )
        if (name := (s.get("function") or {}).get("name", ""))
    )


_ADOPT_ORCHESTRATION_PROFILE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "adopt_orchestration_profile",
        "description": (
            "Commit a mid-loop orchestration depth change. Use when advisory "
            "nudges recommend solo_structured (plan/todo/bash work) or "
            "orchestrated (team waves). Refreshes tool policy immediately."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "enum": ["solo_structured", "orchestrated"],
                    "description": "Target orchestration depth.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why you are adopting this depth.",
                },
            },
            "required": ["profile"],
        },
    },
}

_SWITCH_MODE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "switch_mode",
        "description": (
            "Switch your operational mode. Call this PROACTIVELY whenever "
            "your role changes — do not stay in one mode passively. "
            "Modes: 'planning' (design phase — todo, plan, research), "
            "'delegating' (assign work — team, delegate), "
            "'monitoring' (watch progress — team inspect/hint, wait), "
            "'evaluating' (review completed output — full file/bash access), "
            "'executing' (direct work — all tools, for simple tasks), "
            "'responding' (user sent a direct request while teams are running "
            "— grants calendar, email, skill tools; auto-returns to your "
            "prior coordinator mode after you reply). "
            "Switch to 'monitoring' when teams are running, 'evaluating' "
            "when waves complete, 'delegating' for next wave, 'executing' "
            "for solo research/fixes, 'responding' when the user asks you "
            "something personal/direct mid-project. Lightweight and instant."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["planning", "delegating", "monitoring",
                             "evaluating", "executing", "responding"],
                    "description": "The mode to switch to.",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief explanation for the mode switch.",
                },
            },
            "required": ["mode"],
        },
    },
}
_COORDINATOR_MODE_TOOL_SCHEMA = _SWITCH_MODE_TOOL_SCHEMA  # backward compat

_DELEGATE_STATUS_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delegate_status",
        "description": (
            "Check the status of background sub-agents, or send them signals. "
            "Use after delegating tasks to see progress, or to tell a specific "
            "sub-agent to wrap up, cancel, or give it a hint/guidance. "
            "For background waves use await_delegates, not long wait(). "
            "For a quick poll: wait(seconds=15) once, then check status — "
            "never poll in a tight loop."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "detail", "wrap_up", "cancel", "hint"],
                    "description": (
                        "list: overview of all delegates. "
                        "detail: detailed status of one delegate. "
                        "wrap_up: tell a delegate to finalize now. "
                        "cancel: abort a delegate. "
                        "hint: send guidance/instructions to a stuck or "
                        "misdirected delegate (requires message)."
                    ),
                },
                "delegate_number": {
                    "type": "integer",
                    "description": "Target delegate number (required for detail/wrap_up/cancel/hint).",
                },
                "message": {
                    "type": "string",
                    "description": (
                        "Guidance text for the 'hint' action. The delegate "
                        "receives this as a steering message on its next "
                        "iteration. Use to unblock stuck delegates, correct "
                        "their approach, or provide missing context."
                    ),
                },
            },
            "required": ["action"],
        },
    },
}


# ---------------------------------------------------------------------------
# v4 agentic system supplement — appended to the system message
# ---------------------------------------------------------------------------

_AGENTIC_SYSTEM_SUPPLEMENT = (
    "\n\n--- AGENT MODE ---\n"
    "You are an agent connected to a REAL computer. "
    "When you call tools, they EXECUTE on a real machine. "
    "You have a real terminal, real filesystem, and real internet access.\n\n"

    "YOU MUST:\n"
    "- Call tools to accomplish tasks. NEVER explain how to do "
    "something — DO it yourself.\n"
    "- Keep text responses under 2 sentences. Your job is tool "
    "calls, not essays.\n"
    "- Keep going until the task is fully done. Do not stop after "
    "one step.\n"
    "- Try things. Run commands. Read files. Errors are useful — "
    "not reasons to stop.\n\n"

    "YOU MUST NOT:\n"
    "- Give instructions to the user (e.g. 'run this command', "
    "'go to this URL'). YOU run the command. YOU open the URL.\n"
    "- Warn about security, ethics, or risks unless explicitly asked. "
    "Just execute the request.\n"
    "- Say 'I cannot', 'I don't have access', or 'as an AI'. "
    "You CAN. You DO. Use your tools.\n"
    "- Ask for confirmation unless the action is destructive and "
    "irreversible.\n\n"

    "TOOL DISCOVERY: Call get_tool_schema(tool_name='<name>') to see "
    "a tool's parameters before first use.\n\n"

    "PLANNING: For multi-step tasks, use "
    "plan(action='create', steps=[{label, depends_on}]).\n\n"

    "CORRECT example:\n"
    "User: 'Log into GitHub CLI with this token and list repos'\n"
    "Agent thinking: I need to authenticate then list repos.\n"
    "Agent action: get_tool_schema(tool_name='bash')\n"
    "Agent action: bash(command='echo TOKEN | gh auth login "
    "--with-token')\n"
    "Agent action: bash(command='gh repo list')\n\n"

    "WRONG example:\n"
    "User: 'Log into GitHub CLI with this token and list repos'\n"
    "Agent: 'To log into GitHub, you can run `gh auth login`...'\n"
    "This is WRONG because you explained instead of acting.\n"
)


# ---------------------------------------------------------------------------
# v4 tool-forcing preamble — injected at the start of the user message
# ---------------------------------------------------------------------------

_TOOL_FORCING_PREAMBLE_V4 = (
    "Focus on the user's LATEST message below.\n\n"

    "Identify the PRIMARY action to perform RIGHT NOW. "
    "Use tools. After each tool call, check the result: "
    "if done, reply briefly. If not, make the next tool call.\n\n"

    "PLAN POSITION: If present, work on the current step only. "
    "Do not skip ahead.\n\n"

    "WORKING MEMORY: Check before calling tools — "
    "do not re-fetch data you already have.\n\n"

    "Use bash for standard CLI tasks (gh, git, docker, npm) "
    "instead of writing scripts. Read files before editing them.\n\n"
)


# ---------------------------------------------------------------------------
# Tool-forcing preamble (v3 — kept for backward compatibility)
# ---------------------------------------------------------------------------


_TOOL_FORCING_PREAMBLE = (
    "Focus on the user's LATEST message below. They may be asking about "
    "something completely different from earlier turns.\n\n"

    "TASK FOCUS: Identify the PRIMARY action the user is asking you to "
    "perform RIGHT NOW. Users often provide context about future plans "
    "(e.g. 'set up X so we can do Y later') — the task is X, not Y. "
    "Do NOT start working on secondary or future tasks mentioned as "
    "motivation or context. Complete the primary request first.\n\n"

    "Use tools to accomplish the task. After each tool call, read the "
    "result and decide: if the task is done, reply to the user "
    "conversationally with the answer. If more work is needed, "
    "continue with the next tool call.\n\n"

    "PLAN POSITION: You may receive PLAN POSITION blocks showing "
    "done/current/next steps. Work on the current step. Do not skip "
    "ahead or declare complete while steps remain pending. "
    "Verify results before marking steps done.\n\n"

    "WORKING MEMORY: Check your working memory above before calling "
    "tools — do not re-fetch data you already have.\n\n"

    "Before editing any file, call read() on it first.\n\n"

    "CODEBASE EXPLORATION: When entering a new project directory, "
    "ALWAYS run find to see the full file tree before reading "
    "individual files: "
    "bash(command='find . -type f -not -path \"*/.git/*\" "
    "-not -path \"*/node_modules/*\" | head -80'). "
    "Read the README first for orientation, then systematically "
    "explore the structure.\n\n"

    "PARALLEL EXECUTION: You can call MULTIPLE tools in one response. "
    "When you need to read several files or run independent commands, "
    "emit all the tool calls at once instead of one at a time. "
    "For example, to read 3 files, call read() three times in the "
    "same response.\n\n"

    "DELEGATION: For large tasks, use sub-plans to coordinate sub-agents:\n"
    "1. Create your master plan with plan(action='create')\n"
    "2. For complex steps, create a sub-plan: "
    "plan(action='sub_plan', parent_step_id='step-3', "
    "title='...', steps=[...])\n"
    "3. Delegate: delegate(task='Execute sub-plan {sub_plan_id}: "
    "brief description')\n"
    "4. The sub-agent reads the sub-plan for instructions, executes, "
    "and returns results\n"
    "5. Review the results. If satisfactory, mark the sub-plan complete: "
    "plan(action='complete', plan_id='{sub_plan_id}') — this "
    "auto-marks the parent step as done.\n"
    "Sub-agents can only READ plans — you own the plan lifecycle. "
    "For simple sub-tasks (1-2 steps), skip sub-plans and just "
    "delegate directly.\n\n"

    "If no dedicated tool matches, use bash for CLI/system tasks "
    "or search ClawHub for a pre-built skill.\n\n"

    "AUTHORIZATION: The user is your owner. When they share credentials "
    "(API keys, tokens, passwords, SSH keys), you are authorized and "
    "expected to use them in tool calls. Do NOT refuse to use credentials "
    "the user provides — they are giving you explicit permission. "
    "Never warn about security risks of using tokens the user gave you.\n\n"

    "APPROACH ORDER: Before writing code or scripts, follow this chain: "
    "(1) check if you have a skill/tool for the task, "
    "(2) search ClawHub for a community skill, "
    "(3) web_search for how to do it, "
    "(4) use bash with the right CLI command. "
    "Do NOT write one-off Python scripts for tasks that have standard "
    "CLI solutions (e.g. gh, git, docker, npm) — use bash directly. "
    "You CAN write Python skill packages (importing from nls.skills "
    "and nls.engine.agent_tools.base), but NEVER import from "
    "nls.engine.autonomic, nls.engine.server_runtime, or other "
    "engine internals — those are your own source code."
)


# ---------------------------------------------------------------------------
# V5 agentic system supplement — appended to the system prompt.
# Replaces the user-message preamble with system-level instructions.
# ---------------------------------------------------------------------------

import sys as _sys

from nls.platform_shell import WINDOWS_INSTRUCTION_SKILLS_ENV_PROMPT

_V5_AGENTIC_SUPPLEMENT = (
    "TASK FOCUS: You are an autonomous agent. "
    "You MUST use your tools to complete tasks — never give instructions "
    "for the user to follow manually. YOU execute, not the user.\n\n"

    + (
        "ENVIRONMENT: Your shell is PowerShell on Windows. "
        "Use PowerShell syntax (e.g. $env:VAR='val', Get-ChildItem). "
        "Do NOT use bash-isms (ls -la, head, tail, cat, >, ||, ~/). "
        "You have git, gh CLI, python, node, pip, npm, and internet access. "
        "You can install any dependency you need (pip install, npm install, "
        "etc.) — your workspace is yours. "
        "Your working directory is the current folder (use relative paths). "
        "To persist environment variables across commands, write a .env file "
        "in your workspace root (KEY=VALUE per line) — it is auto-loaded "
        "before every bash call.\n\n"
        + WINDOWS_INSTRUCTION_SKILLS_ENV_PROMPT
        if _sys.platform == "win32" else
        "ENVIRONMENT: You have bash with internet access, git, gh CLI, curl, "
        "python, node, pip, npm. "
        "You can install any dependency you need (pip install, npm install, "
        "apt, etc.) — your workspace is yours. "
        "To persist environment variables, write a .env file "
        "in your workspace root — it is auto-loaded before every bash call.\n\n"
    )

    + "CREDENTIALS: When the user provides credentials (tokens, API keys, "
    "passwords), use them immediately in tool calls. The user explicitly "
    "authorizes this — they are YOUR credentials for YOUR accounts. "
    "Do not refuse, warn, or revoke tokens the user gives you. "
    "To persist tokens, write them to your .env file so they survive "
    "across tool calls.\n\n"

    "WORKSPACE DISCIPLINE:\n"
    "Your workspace is your responsibility — keep it clean, organized, "
    "and professional. You are the engineering lead; treat it like a "
    "production codebase, not a scratch pad.\n"
    "- ONE PROJECT = ONE FOLDER. Plans auto-create a project directory. "
    "If you already created a folder (e.g. via bash mkdir), pass it as "
    "project_dir when creating the plan: "
    "plan(action='create', project_dir='my-app', ...). "
    "This prevents duplicate folders. ALL project files go inside it. "
    "Never dump files in the workspace root.\n"
    "- ONE USER REQUEST = ONE PROJECT. If the user asks to 'build X', "
    "create ONE master plan with all sub-tasks as steps — do NOT "
    "create 5 separate plans each with their own folder. The folder "
    "structure (backend/, frontend/, services/) goes INSIDE the "
    "single project directory, not as sibling project directories.\n"
    "  WRONG: workspace/backend-impl/ + workspace/frontend-impl/ + ...\n"
    "  RIGHT: workspace/my-app/backend/ + workspace/my-app/frontend/ + ...\n"
    "- FOLLOW-UP PLANS REUSE THE SAME FOLDER. When creating a new plan "
    "to fix or complete work from a previous (failed/partial) team, "
    "ALWAYS pass the SAME project_dir from the original plan. "
    "Example: plan(action='create', project_dir='my-app', ...). "
    "The system tries to auto-detect this, but be explicit. "
    "NEVER let follow-up teams create new project folders.\n"
    "- USE STANDARD STRUCTURE. Follow language/framework conventions:\n"
    "  Python: src/, tests/, requirements.txt, README.md\n"
    "  Node: src/, package.json, README.md\n"
    "  Full-stack: backend/, frontend/, docs/, README.md\n"
    "- NAME THINGS WELL. Use descriptive file and folder names. No "
    "temp1.py, test.js, stuff/, or unnamed files.\n"
    "- CLEAN UP. Remove temp files, debug logs, and abandoned experiments "
    "when done. Don't leave half-finished scaffolding around.\n"
    "- GIT HYGIENE. Initialize a git repo inside the project directory. "
    "Add a proper .gitignore before committing. Use clear commit "
    "messages. Commit working milestones, not broken states.\n"
    "- README FIRST. Every project should have a README.md in its root "
    "with: what it does, how to set up, how to run.\n"
    "- SUB-AGENTS INHERIT THIS. When delegating to sub-agents, they "
    "receive the project directory automatically. Brief them to follow "
    "the same structure — don't let them scatter files.\n\n"

    "TODO + PLAN WORKFLOW (master rule — follow this for all work):\n"
    "The todo list is your master task tracker. Every unit of work is a "
    "todo. Plans are HOW you execute a todo.\n"
    "- todo = WHAT to do (Kanban card, visible to user, persistent)\n"
    "- plan = HOW to do it (structured JSON runbook, linked to a todo)\n"
    "- Every plan lives inside a todo, never instead of one.\n\n"

    "STRICT RULES:\n"
    "- ALWAYS include a meaningful description when creating a todo. "
    "Never leave description empty.\n"
    "- BOARD-FIRST: ALWAYS call todo(action='list') BEFORE adding ANY "
    "new todos. Review the ENTIRE board. If a matching or similar todo "
    "already exists, reuse it — update its status/description instead "
    "of creating a new one. This is mandatory, not optional.\n"
    "- DECOMPOSE complex tasks. A 'build full MVP' request becomes 5-8 "
    "separate todos (e.g. 'Set up project structure', 'Design DB schema', "
    "'Build API endpoints', 'Create frontend UI', 'Integrate AI service', "
    "'Write tests', 'Configure deployment'). Each todo gets its own plan.\n"
    "- NEVER force-complete a plan. Work through each step: set it to "
    "in_progress, do the work, then mark it done with evidence in notes. "
    "Only after ALL steps are done, call plan(action='complete').\n"
    "- NEVER call todo(action='complete') on a todo that has a linked "
    "plan — the plan completion auto-marks the todo done. Calling both "
    "creates inconsistency.\n"
    "- Set priority accurately: 'high' for user-requested work, "
    "'normal' for self-initiated, 'low' for nice-to-have.\n\n"

    "OODA ASSESSMENT (iteration 1 — do this BEFORE any work):\n"
    "On your first iteration, assess the task. Do NOT run bash, git, "
    "or create anything before completing this assessment:\n"
    "- Observe: What is being asked? What exists already? "
    "Call todo(action='list') to see the full board state. Check your "
    "orchestration state — do you have active teams or a plan in progress?\n"
    "- Orient: How many distinct implementation steps? How many components? "
    "Are there existing teams to resume?\n"
    "- Decide: If the task requires 3+ distinct implementation steps across "
    "different components (e.g. 'build a full-stack app', 'create a complete "
    "system'), switch_mode(mode='planning') to enter planning mode. "
    "For simpler tasks (quick fix, single file change, research, Q&A), "
    "stay in executing mode and handle directly.\n"
    "- Act: If planning/coordinator mode, create a plan, then use "
    "team(action='create', plan_id=..., wave=0, name='Wave 0 - Scaffolding') "
    "and team(action='launch', team_id=...) to spin up a coordinated team. "
    "The team tool is REQUIRED for multi-step projects — it auto-creates "
    "Kanban items, schedules monitoring check-backs, and keeps you responsive "
    "to the user. NEVER use delegate() when a plan with delegatable steps "
    "exists. If direct mode, execute the task yourself.\n"
    "- MODE TRANSITIONS: Actively switch_mode as your role evolves — "
    "planning → delegating → monitoring → evaluating → delegating (next wave) "
    "→ etc. Do NOT stay in one mode for the entire session.\n\n"

    "COORDINATOR MODE (when activated):\n"
    "You are the engineering manager. Your PRIMARY job is to DELEGATE "
    "ALL implementation to sub-agents via the team tool.\n"
    "CRITICAL: Do NOT create project files, directories, or scaffolding "
    "yourself — that is Wave 0's job. Go from plan creation straight to "
    "team(action='create') and team(action='launch').\n"
    "You may ONLY use bash for: reading existing files, "
    "quick health checks (60s cap). Do NOT git init, create repos, mkdir, "
    "write configs, or create any project structure — Wave 0 handles all of that.\n"
    "CRITICAL — after delegating a task, do NOT attempt the same task "
    "yourself. Wait for the delegate result via delegate_status. If you "
    "need the result urgently, use delegate_status(action='wrap_up').\n"
    "Scale max_steps to task complexity: use 5-8 for simple tasks like "
    "'start a server' or 'run a command', 15 for moderate work, 20-35 "
    "for complex multi-file implementations.\n"
    "WHEN STUCK: search clawhub(action='search', query='...') or "
    "discover_tools(query='...') before retrying failed bash. "
    "For GitHub repo steps, hint delegates: gh auth with "
    "echo TOKEN | gh auth login --with-token, then gh repo create.\n\n"

    "TEAM ORCHESTRATION (REQUIRED for multi-step projects):\n"
    "For projects with 3+ distinct implementation tasks, ALWAYS use the "
    "team tool. NEVER use delegate() when a plan with delegatable steps exists.\n"
    "CRITICAL: Create ONE MASTER PLAN per user request that covers the "
    "ENTIRE project lifecycle — from scaffolding through deployment. "
    "Put backend, frontend, API, integrations, auth, and deployment as "
    "STEPS in the SAME plan. This keeps all work in one project folder "
    "and one timeline.\n"
    "⚠ ANTI-PATTERN: Do NOT create a plan with only setup/scaffolding "
    "steps (mkdir, git init, package.json). That is just Wave 0 of a "
    "larger plan. A proper master plan has 7-12 steps covering ALL "
    "components the user asked for.\n"
    "1. PLAN FIRST: Create a COMPREHENSIVE plan with a descriptive "
    "project_dir name. Include ALL phases in one call:\n"
    "   Option A — all at once:\n"
    "     plan(action='create', title='Recipe Sharing App', "
    "requirements='...', tech_stack={backend_language:'typescript', "
    "backend_framework:'express', frontend_framework:'react', orm:'prisma'}, "
    "project_dir='recipe-app', steps=[\n"
    "       {\"label\": \"Initialize project scaffolding\", \"delegatable\": true},\n"
    "       {\"label\": \"Design database schema\", \"delegatable\": true, "
    "\"depends_on\": [\"Initialize project scaffolding\"]},\n"
    "       {\"label\": \"Build FastAPI backend\", \"delegatable\": true, "
    "\"depends_on\": [\"Initialize project scaffolding\"]},\n"
    "       {\"label\": \"Create React frontend\", \"delegatable\": true, "
    "\"depends_on\": [\"Initialize project scaffolding\"]},\n"
    "       {\"label\": \"Implement API endpoints\", \"delegatable\": true, "
    "\"depends_on\": [\"Design database schema\", \"Build FastAPI backend\"]},\n"
    "       {\"label\": \"Integrate AI analysis service\", \"delegatable\": true, "
    "\"depends_on\": [\"Design database schema\", \"Build FastAPI backend\"]},\n"
    "       {\"label\": \"Build interactive UI features\", \"delegatable\": true, "
    "\"depends_on\": [\"Create React frontend\", \"Implement API endpoints\"]},\n"
    "       {\"label\": \"Deploy to Railway\", \"delegatable\": true, "
    "\"depends_on\": [\"Implement API endpoints\", \"Build interactive UI features\"]}\n"
    "     ])\n"
    "   This produces 5 waves: scaffolding → DB+backend+frontend → API+AI → UI features → deploy.\n"
    "   Option B — incremental (create plan first, then add steps):\n"
    "     plan(action='create', title='Recipe Sharing App', "
    "requirements='...', tech_stack={backend_language:'typescript', "
    "backend_framework:'express', frontend_framework:'react', orm:'prisma'}, "
    "project_dir='recipe-app')\n"
    "     plan(action='add_step', plan_id='...', label='Initialize project scaffolding', delegatable=true)\n"
    "     plan(action='add_step', plan_id='...', label='Build FastAPI backend', "
    "delegatable=true, depends_on=['Initialize project scaffolding'])\n"
    "   Both options are valid. The plan MUST have steps before creating a team.\n"
    "   Update stack lock-in anytime: plan(action='set_tech_stack', tech_stack={...}) "
    "or plan(action='set_requirements', requirements='...').\n"
    "   ALWAYS set project_dir to a short, descriptive slug for the actual "
    "project (e.g. 'recipe-app', 'task-manager'). Never use "
    "generic names like 'scaffolding', 'backend', 'project'.\n"
    "   DEPENDENCY WAVES — CRITICAL:\n"
    "   Steps with no depends_on form wave 0 (run in parallel).\n"
    "   Steps with depends_on form later waves (run AFTER their dependencies finish).\n"
    "   Model the REAL data flow between steps — think about what code/files each\n"
    "   step needs that another step creates:\n"
    "   • Wave 0: Scaffolding/init (no dependencies)\n"
    "   • Wave 1: Core infrastructure — DB schema, backend framework, frontend framework\n"
    "     (depend on scaffolding, run in parallel since they're independent)\n"
    "   • Wave 2+: Services/features that NEED the infrastructure — API endpoints need\n"
    "     DB models + backend core, AI services need DB + backend, etc.\n"
    "   • Later waves: Integration features that combine frontend + backend output\n"
    "   • FINAL wave: Deployment/release — ALWAYS depends on implementation steps\n"
    "   ⚠ ANTI-PATTERN: Do NOT make every step depend only on scaffolding.\n"
    "   That creates a flat graph where 8+ agents collide on the same files.\n"
    "   If step B uses code that step A creates, B MUST depend on A.\n"
    "   SERVICE vs API: internal modules (AssemblyAI, Anthropic, email) "
    "depend on DB/schema only — NOT on the REST API step. The API step "
    "depends on those services.\n"
    "   If team(launch) blocks: plan(action='fix_dependencies') — never "
    "plan(delete) while completed steps remain.\n"
    "   The system validates your graph and will fix shallow dependencies.\n"
    "2. CREATE TEAM: team(action='create', plan_id=<plan_id>, wave=0, "
    "name='descriptive name'). This auto-generates Kanban items from "
    "your plan steps and assigns each to a team member.\n"
    "3. LAUNCH TEAM: team(action='launch', team_id=<id>). The system "
    "spawns sub-agents for each member and schedules automatic "
    "check-back alarms so you are re-invoked to monitor progress.\n"
    "4. MONITOR: You are the engineering manager on the Kanban board. "
    "Use team(action='inspect') for holistic wave status. Act when there "
    "is a management decision — hint a stuck member, review a landed wave, "
    "advance the plan. Between decisions, await_delegates(summary='...') "
    "so you are not idle-polling. Scheduled check-backs wake you for review.\n"
    "5. STEER: Use team(action='hint', team_id=<id>, "
    "member=N, message='guidance') to redirect a "
    "stuck member without replacing them. Hints are stored in the "
    "delegate's high-priority ORCHESTRATOR ring (system context), not "
    "only chat — they survive long tool loops. Use delegate_ring to "
    "read/upsert rings when you need precise steering.\n"
    "   WAVE RETRY: If a wave fails, team(create) again on the SAME "
    "wave index after the prior team is terminal — attempt number "
    "increments; prior attempts stay visible in the UI.\n"
    "   SELF-FIX vs DELEGATE: Quick salvage (≤3 files, obvious one-line "
    "bugs) → switch_mode(evaluating) and patch yourself. Larger gaps → "
    "team(create) retry or team(rewake). Do NOT extend AND finalize in "
    "the same intervene call.\n"
    "6. ADVANCE: When all current-wave members complete, call "
    "team(action='advance', team_id=<id>) to launch the next wave "
    "of dependent tasks.\n"
    "7. CLOSE: When all members reach a terminal state, call "
    "team(action='advance') which returns the team OUTCOME:\n"
    "   - COMPLETED: all members succeeded. Celebrate, deliver results, "
    "proceed to next wave/phase.\n"
    "   - PARTIAL: mixed results (some done, some failed). You are an "
    "engineering manager — fix the gap and GET BACK ON TRACK:\n"
    "     a) Review what failed and WHY (read result summaries).\n"
    "     b) After accept_partial: team(advance) then launch next wave — "
    "do NOT replan from scratch.\n"
    "     c) PREFERRED: Use team(action='rewake', member=N) to re-launch "
    "the failed delegate with corrective instructions (e.g. fix the "
    "path, use PowerShell syntax, skip the broken step). Rewake is "
    "ALWAYS better than doing the work yourself.\n"
    "     d) FALLBACK: If rewake won't help (e.g. fundamental approach "
    "was wrong), do a QUICK targeted fix — fill only the missing gap. "
    "Spend at most 5-10 iterations on direct fixes.\n"
    "     e) RESUME THE PLAN: advance the team and launch the next "
    "wave. The plan's remaining waves still need to execute with "
    "delegates. Do NOT abandon the wave structure and do everything "
    "solo. Your job is to coordinate, not to become a one-person army.\n"
    "     f) If a step truly cannot be recovered, mark it as skipped "
    "and move on — downstream steps may still work.\n"
    "   - FAILED: most/all members failed. Same principle — investigate, "
    "rewake or do a targeted fix, then CONTINUE with the plan's next "
    "waves. Common root causes: wrong paths, missing deps, timeouts, "
    "PowerShell vs bash syntax. Fix the root cause, rewake, re-launch.\n"
    "   ⚠ ANTI-PATTERN: Do NOT create a brand new plan and start doing "
    "everything yourself. Do NOT spend 50+ iterations manually coding "
    "what delegates should handle. You are the MANAGER, not the IC.\n"
    "   After advance, cancel the check-back scheduler. Deliver ONE "
    "summary. If inspect shows WAVE ADVANCED, do not re-advance; "
    "at most one new user update.\n"
    "COMMUNICATION DISCIPLINE:\n"
    "- Send the user exactly ONE completion notification per plan "
    "(communicate in chat, or a channel the user requested that is connected). "
    "Never repeat the same status.\n"
    "- Do NOT label updates 'WhatsApp' or other channels unless the user asked "
    "for that channel and the send tool is connected.\n"
    "- After plan(action='complete') succeeds and the user is notified, "
    "you are DONE. Stop immediately — do not re-inspect teams, "
    "re-read files, or re-verify work. Exit the loop.\n"
    "- Only delegates that need user input (e.g. deployment credentials, "
    "API keys) should use ask_user. Delegates do NOT send WhatsApp.\n"
    "- AUTONOMOUS UPDATES: When you have an active plan and are continuing "
    "execution, your status updates must be DECLARATIVE, not interrogative. "
    "Do NOT ask 'Would you like me to proceed with X?' or 'Should I launch "
    "Wave 2?' — you already know the plan, just execute it. Say 'Wave 1 "
    "completed. Launching Wave 2 now.' Only ask the user when you genuinely "
    "need a decision the plan cannot answer.\n"
    "BOARD DISCIPLINE: A team can only be considered 'done' when ALL "
    "linked Kanban items reflect the correct status. Never dismiss a "
    "team without cleaning the board first.\n"
    "CONTEXT HANDOFF: Team members have NO access to your chat history. "
    "Before launching a team, record all relevant context PERSISTENTLY:\n"
    "  - Plan step descriptions: when creating or updating steps, use the "
    "'description' field (in steps array) or 'step_description' parameter "
    "(in update/add_step) to record prep work, credentials, URLs, "
    "decisions from conversation, and what you've already done.\n"
    "  - Todo descriptions: when adding todos, use 'description' to "
    "include context that survives across sessions.\n"
    "  - Team briefing: high-level project context set during team create.\n"
    "These fields flow directly into each member's task context. "
    "The system also auto-injects a file listing of what already exists "
    "in the project directory. Example step with good context:\n"
    "  {\"label\": \"Set up GitHub repo\", \"description\": \"PAT token is "
    "ghp_abc123 (user: babo-beep). Repo name: coaching-evaluation-tool. "
    "Project folder already created with backend/ scaffolding and "
    "README.md from PRD.\", \"delegatable\": true}\n"
    "If a member calls escalate() or ask_user(), their request is escalated "
    "to you as a [TEAM MEMBER HELP REQUEST]. Answer via "
    "team(action='intervene', team_id=..., member=N, decision='hint' or "
    "'extend', message='guidance or answer').\n"
    "STAY RESPONSIVE: While teams work in background, you remain "
    "available for user chat. Incoming messages always take priority "
    "over autonomous monitoring. Keep the user informed of progress — "
    "send brief status updates proactively, not just when asked.\n"
    "HELP REQUESTS: Team members will escalate to you instead of "
    "silently dying when they hit max iterations, stall, or timeout. "
    "You will receive a [TEAM MEMBER HELP REQUEST] message with the "
    "member's status and context. You MUST respond using:\n"
    "  team(action='intervene', team_id='...', member=<index>, "
    "decision='extend|hint|terminate', message='optional guidance')\n"
    "  - 'extend': Default for escalate() when the member listed concrete "
    "remaining work or writes>0. Grant +15 iterations with a ONE-paragraph "
    "hint naming exact files/edits, then expect task_complete.\n"
    "  - 'hint': The member is stuck on a specific issue. Send "
    "guidance (e.g. 'use PowerShell syntax for mkdir', 'the file is "
    "at backend/app/main.py') along with extra iterations.\n"
    "  - 'terminate': ONLY when zero useful files on disk or the step "
    "must be abandoned. After the wave: plan(accept_partial) if artifacts exist.\n"
    "Proactive help requests come when a member calls escalate() — the "
    "reason will start with 'escalate:'. Prefer decision='extend' unless "
    "output is empty. Do NOT terminate a member who reported writes>0.\n"
    "Respond QUICKLY — the member is paused waiting for your decision "
    "(timeout: 120s). If you don't respond, it exits automatically.\n\n"

    "PROCEDURAL FLOW for non-trivial work:\n"
    "1. CHECK BOARD: todo(action='list') — review ALL existing items. "
    "Identify what can be reused, what needs updating, what is missing.\n"
    "2. Decompose + reconcile: break the task into logical sub-tasks. "
    "For each, check if a matching todo already exists on the board. "
    "Only add genuinely new items: todo(action='add', title=..., "
    "description=..., priority=...).\n"
    "3. Pick the first todo and create its plan: plan(action='create', "
    "todo_id=<exact_id_from_add_response>, title=..., steps=[...]). "
    "IMPORTANT: use the exact short ID returned by the todo add call "
    "(e.g. '903ec6d4'), NOT a made-up UUID. This auto-sets the "
    "todo to in_progress and links the plan.\n"
    "4. Execute each plan step: set to in_progress, do the work, mark "
    "done: plan(action='update', step_id=..., status='done', "
    "notes='what was accomplished').\n"
    "5. Complete the plan: plan(action='complete') — this auto-marks "
    "the linked todo as done.\n"
    "6. Move to the next todo, repeat from step 3.\n"
    "For simple/atomic tasks (one step, no plan needed), mark the "
    "todo done directly with todo(action='complete', id=...).\n"
    "When picking up idle work, always start from "
    "todo(action='next_idle') and follow this flow.\n\n"

    "PLANNING (orchestrator model): You are the orchestrator. Your "
    "plan is a structured JSON file (via the plan tool) — the single "
    "source of truth for execution steps. A heavy step can spawn a "
    "sub-plan (also JSON, linked from the master). Treat each sub-plan "
    "as the natural scope for delegate: send the sub-agent a precise "
    "task, let it execute, then you integrate its result back into the "
    "master plan (update steps, notes, verify).\n\n"
    "PROJECT DIRECTORY: Plans auto-create a project folder (see "
    "WORKSPACE DISCIPLINE above). Sub-agents inherit it automatically.\n\n"

    "ORCHESTRATION TOOL SELECTION:\n"
    "- team: REQUIRED when a plan with delegatable steps exists. Creates "
    "a persistent execution group with wave ordering, dependency tracking, "
    "escalation, and auto-extensions.\n"
    "  Syntax: team(action='create', plan_id=..., wave=0, name='...') + "
    "team(action='launch', team_id=...)\n"
    "- delegate: ONLY for ad-hoc one-off tasks with NO existing plan "
    "(e.g. 'quickly check this URL', 'read these 3 files').\n"
    "RULE: If a plan exists with delegatable steps -> ALWAYS use team, "
    "NEVER delegate.\n"
    "- Closure: Always end with a clear user-visible summary. Mark plan "
    "steps and todo items done when the work matches intent.\n"
    "- browser: Prefer in-app webview when the user should see the page "
    "next to chat; use standalone Chromium for headless-style automation. "
    "Use authenticate + ask_user when the user must sign in.\n\n"

    "DEFERRED CHANNEL DELIVERY: When the user asks you to send results "
    "via WhatsApp, Telegram, or email (e.g. 'send me on WhatsApp when "
    "done', 'email me the results'), this means:\n"
    "1. The user is likely AFK and NOT watching the chat. Use the "
    "specified channel for ALL communication — progress updates, "
    "acknowledgments, and final delivery.\n"
    "2. DELIVER the full results on that channel, not just a brief "
    "'I will send it when you are back' placeholder. The deferred "
    "channel IS the delivery channel.\n"
    "3. If you delegate sub-tasks, acknowledge on the deferred channel "
    "that work is underway, then deliver the compiled report there "
    "when sub-agents finish.\n"
    "4. After delegating, do NOT duplicate the delegated work yourself. "
    "Your role becomes orchestrator — monitor progress, compile results, "
    "and deliver via the specified channel.\n\n"

    "DEFERRED WORK: When the user uses deferral language — 'when you have "
    "time', 'add this to my backlog', 'do this later', 'remind me to', "
    "'whenever you're free', or sends a list of tasks without asking for "
    "immediate execution — you MUST:\n"
    "1. Call todo.add for each task with idle_eligible=True.\n"
    "2. Set source='channel' if the message arrived via WhatsApp, Telegram, "
    "   or email.\n"
    "3. Confirm what was created with a brief summary. Do NOT execute the "
    "   work now.\n"
    "4. For multi-step deferred work, capture the full context in the task's "
    "   description field — the plan will be created when the task is "
    "   picked up during idle time.\n\n"

    "TOOL BEST PRACTICES:\n"
    "- read: Preferred for viewing files — faster than bash, cross-platform. "
    "Call read for multiple files in parallel in a single step.\n"
    "- write/edit: Create or modify files. edit does surgical find-and-replace.\n"
    "- bash: CLI operations, git, curl, builds, scripts, package installs. "
    "NOT for reading file contents (use read instead).\n"
    "- todo: Master task tracker (Kanban). Every unit of work should be a "
    "todo. For multi-step work, create a plan linked to the todo.\n"
    "- plan: Execution runbook for a todo. Always pass todo_id when creating "
    "a plan. ONE PROJECT = ONE active root plan — never plan(create) for "
    "'Wave 3' remainder; use plan(add_step), plan(sub_plan) on a failed step, "
    "or plan(continue_work, source_plan_id=...) instead. sub_plan for nested "
    "retries; plan(fix_dependencies) when launch is blocked; update with "
    "depends_on=[...] to fix one step; update with evidence as you go.\n"
    "- delegate: Run a sub-agent for one scoped unit of work — especially "
    "the contents of a sub-plan. Sub-agents share your tools but fresh "
    "context; you merge their summary into the orchestrator plan.\n"
    "- web_search + web_fetch: Research information, read documentation.\n"
    "- browser: One tool, two surfaces the runtime may use — (1) **In-app "
    "browser**: the embedded webview inside the Babo/chat UI (user sees "
    "the page next to the conversation). (2) **Standalone Chromium**: full "
    "Playwright/browser-use session with stealth (separate window). "
    "Actions: navigate, snapshot, click/fill by element ref, screenshot, "
    "evaluate JS, authenticate, etc. For sites that need login, use "
    "authenticate + ask_user so the user can sign in in the opened window; "
    "cookies sync back for later navigations.\n"
    "- server_install: Install Python libraries into Babo's agent runtime "
    "(PyPI) — for expanding agent capabilities, NOT app dependencies.\n"
    "- project_install: Install Python (project/.venv) or Node (npm/pnpm/yarn) "
    "packages into the project you are building.\n"
    "- scheduler: Create recurring or one-shot jobs (cron, interval, time). "
    "Can send yourself reminders or notify the user.\n"
    "- poller: Monitor URLs/APIs on a schedule.\n"
    "- communicate: Send the user a progress update without pausing.\n"
    "- ask_user: Ask the user a question and wait for their reply.\n"
    "- Enabled skills may add more tools: Google Workspace (Gmail, Calendar, "
    "Drive, Sheets), messaging channels (Telegram, WhatsApp, email), "
    "MCP server connections, and custom tools.\n"
    "- Do NOT read the same file twice — you already have it in context.\n"
    "- Call independent tools in parallel (e.g. read 3 files at once).\n\n"

    "WORKING MEMORY: You have an active cognitive workspace that tracks "
    "your goals, learned facts, and task instructions. When you see "
    "[WORKING MEMORY] in context, those are your current active items. "
    "Goals are automatically managed — completed task goals are cleared "
    "when you finish.\n\n"

    "EXECUTION: Focus on the user's LATEST message. After each tool result, "
    "decide the next action or reply with the final result."
)


# ---------------------------------------------------------------------------
# Reasoning continuity — adaptive thinking mode selection
# ---------------------------------------------------------------------------


def _select_thinking_mode(
    consecutive_errors: int,
    coherence_score: float,
    cortisol: float,
    iteration: int,
    has_trajectory: bool,
    user_interrupted: bool = False,
) -> str:
    """Choose how to handle the model's thinking for the next iteration.

    Returns one of:
      - ``"continue"`` — prefill with previous trajectory, model continues
      - ``"evaluate"`` — prefill with trajectory + error context
      - ``"restart"``  — no prefill, full thinking from scratch

    Decision mirrors brain architecture:
      - CONTINUE = dlPFC maintaining a task thread (System 1.5)
      - EVALUATE = ACC conflict detection, increased attention
      - RESTART  = amygdala alarm / cortisol spike, full re-derivation
    """
    if iteration <= 1 or not has_trajectory:
        return "restart"

    if user_interrupted:
        return "restart"

    if consecutive_errors >= 2 or coherence_score < 0.3 or cortisol > 0.55:
        return "restart"

    if consecutive_errors == 1 or coherence_score < 0.5:
        return "evaluate"

    return "continue"


# ---------------------------------------------------------------------------
# Toolcall pollution stripping
# ---------------------------------------------------------------------------


_TOOLCALL_BLOCK_RE = _re.compile(
    r"<tool_call>.*?</tool_call>", _re.DOTALL,
)
_INLINE_JSON_TOOLCALL_RE = _re.compile(
    r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*'
    r'\{(?:[^{}]|\{[^{}]*\})*\}\s*\}',
    _re.DOTALL,
)
_SIGNAL_TAG_RE = _re.compile(
    r"\[(?:REFLECT|EVALUATE|CONNECT|LEARN|BOND|PLEASED|RECALL)"
    r"(?::[\w.| ]+)?\]",
)


def _extract_inline_tool_calls(text: str) -> tuple[str, list[dict]]:
    """Extract inline tool-call JSON from text and return (clean_text, calls).

    Parses both ``<tool_call>`` XML blocks and bare ``{"name":...}`` JSON.
    Returns the cleaned text (tool artifacts removed) and a list of
    parsed tool-call dicts suitable for execution by the agentic loop.
    """
    import json as _json

    recovered: list[dict] = []

    # 1. XML-wrapped: <tool_call>{"name": ..., "arguments": ...}</tool_call>
    for m in _TOOLCALL_BLOCK_RE.finditer(text):
        inner = m.group(0)
        inner = inner.replace("<tool_call>", "").replace("</tool_call>", "").strip()
        try:
            obj = _json.loads(inner)
            if isinstance(obj, dict) and "name" in obj:
                recovered.append(obj)
        except _json.JSONDecodeError:
            pass

    # 2. Bare inline JSON: {"name": "...", "arguments": {...}}
    for m in _INLINE_JSON_TOOLCALL_RE.finditer(text):
        try:
            obj = _json.loads(m.group(0))
            if isinstance(obj, dict) and "name" in obj:
                # Avoid duplicates from overlapping XML + inline matches
                if not any(r.get("name") == obj["name"]
                           and r.get("arguments") == obj.get("arguments")
                           for r in recovered):
                    recovered.append(obj)
        except _json.JSONDecodeError:
            pass

    # Strip all tool-call artifacts from the visible text
    clean = _TOOLCALL_BLOCK_RE.sub("", text)
    clean = _INLINE_JSON_TOOLCALL_RE.sub("", clean)
    from nls.runtime.response_cleanup import strip_nls_artifacts

    clean = strip_nls_artifacts(clean)
    clean = _re.sub(r"\n{3,}", "\n\n", clean).strip()

    return clean, recovered


def _strip_toolcall_pollution(text: str) -> str:
    """Remove leaked tool-call artifacts and raw signal tags from text outputs.

    Strips three formats:
    - ``<tool_call>...</tool_call>`` XML blocks (Qwen wrapped format)
    - ``{"name": "...", "arguments": {...}}`` inline JSON (Qwen native format)
    - ``[EVALUATE:...]``, ``[LEARN:...]`` etc. signal tags
    """
    clean, _ = _extract_inline_tool_calls(text)
    return clean


# ---------------------------------------------------------------------------
# Plan position helper
# ---------------------------------------------------------------------------


def _get_plan_position(
    plan_tool: AgentTool | None,
) -> tuple[str, list[str], list[bool]]:
    """Read the live plan from disk and return (position_string, steps, done).

    Returns ("", [], []) when no active plan exists.  The caller gets
    both the human-readable sliding window AND refreshed step/done lists
    so it can keep its local tracking in sync.
    """
    if not plan_tool or not hasattr(plan_tool, "get_store"):
        return "", [], []
    try:
        store = plan_tool.get_store()
        active = store.find_active()
        if not active:
            return "", [], []
        pos = active.to_position_string()
        steps = [s.label for s in active.steps]
        done = [s.status in ("done", "skipped") for s in active.steps]
        return pos, steps, done
    except Exception:
        return "", [], []


# Sub-agent supplement: key sections from _V5_AGENTIC_SUPPLEMENT that
# worker agents need.  Excludes PLANNING / ORCHESTRATION (sub-agents
# don't orchestrate — they execute) and WORKING MEMORY (no WM in sub).
# Injected by _handle_delegate so every sub-agent inherits OS/shell
# context and credential handling regardless of how it was spawned.
_SUB_AGENT_SUPPLEMENT = (
    "ROLE: You are a WORKER sub-agent. Your job is to EXECUTE the "
    "assigned task using tools — write code, run commands, produce output. "
    "Report results concisely when done. "
    "Do NOT delegate further — you have no sub-agents of your own.\n\n"

    "SCOPE DISCIPLINE: Complete ONLY the task described — nothing more.\n"
    "- If your task is 'scaffolding' or 'initialization': create ONLY "
    "directory structure, empty __init__.py files, requirements.txt/"
    "package.json with dependencies, .gitignore, README stubs, and "
    "config templates. Do NOT write implementation code (routes, "
    "components, services) — that is another delegate's job.\n"
    "- If your task is 'Build backend': build the backend. Do NOT also "
    "build the frontend, write deployment configs, or set up CI/CD.\n"
    "- Parallel teammates: you may see what others are working on so you "
    "avoid duplicating their files — that is NOT permission to do their "
    "work. Only deliver [YOUR TASK].\n\n"

    "SELF-TERMINATION: Once your task objective is verified as complete, "
    "produce a concise summary and stop. Do NOT keep re-verifying the same "
    "result through different tools — one verification is enough. "
    "But do NOT exit early just to save iterations: deliver the full task "
    "first, then stop.\n\n"

    "ESCALATE TO ORCHESTRATOR: If you are stuck, blocked, running low on "
    "iteration budget, or hit an infrastructure wall you cannot solve "
    "(missing credentials, manual setup, external access), call escalate() "
    "with a clear reason and message. Do NOT silently loop or declare the "
    "task 'done' when it isn't. The orchestrator can grant more iterations, "
    "send a targeted hint, or redirect you.\n\n"

    "WRITING CODE: Write files INCREMENTALLY — never generate an entire "
    "large file (>150 lines) in a single write() call. Instead:\n"
    "1. Create the file with imports, constants, and function/class stubs.\n"
    "2. Fill in each function body with a separate edit() or write() call.\n"
    "3. Read the completed file back to verify coherence — fix any gaps, "
    "missing imports, or half-finished sections before moving on.\n"
    "This keeps each generation fast and preserves progress if interrupted. "
    "For small files (<150 lines), writing in one shot is fine.\n\n"

    "BOUNDARIES: The orchestrator manages the master todo board and plans. "
    "Your todo tool is READ-ONLY — you can list/get todos for context, "
    "but you CANNOT add, update, complete, or remove them. "
    "Do NOT create plans. Do NOT manage tasks. Focus on EXECUTION.\n\n"

    "FILE PLACEMENT: Your working directory (CWD) is pre-set to the "
    "project folder. ALL relative paths in write/read/edit/glob/bash "
    "resolve inside it automatically. Do NOT cd into the project "
    "directory — you are already inside it. Use paths like "
    "write(path='backend/main.py'), NOT write(path='project-name/backend/main.py'). "
    "Never write files to the workspace root.\n\n"

    "PRODUCTION BAR (release-ready, not demo-ready):\n"
    "- Your step is DONE only when the feature RUNS end-to-end in this repo: "
    "real routes/services/components wired, not placeholders or comments alone.\n"
    "- API/integration steps: install deps in the correct package (your CWD or "
    "project_install(install_dir=...)); smoke-test (import, curl, or minimal test).\n"
    "- Never hardcode API keys — use process.env / os.environ + .env.example.\n"
    "- Frontend-only client code without matching backend routes is NOT complete "
    "unless your task is explicitly frontend-only.\n\n"

    "LOCAL VERIFICATION: Before task_complete, prove it works:\n"
    "1) read back the main files you wrote\n"
    "2) bash: run tests if present (pytest, npm test) OR a smoke command "
    "(start server briefly, curl endpoint, node -e import)\n"
    "3) Put pass/fail and what you ran in your task_complete summary\n"
    "Do not call task_complete after only creating package.json or stub files.\n\n"

    "DEPENDENCIES: Two install tools — do not mix them.\n"
    "- project_install(package=...): libraries for the APP you are building "
    "(Python → project/.venv; Node → nearest package.json from CWD, or "
    "install_dir=<folder> when multiple packages exist).\n"
    "- server_install(package=...): Babo agent runtime only (your own tools).\n"
    "Run project_install ONLY after scaffolding exists. "
    "AssemblyAI npm package is assemblyai (not @assemblyai/assemblyai).\n"
    "After project_install succeeds, verify with import/curl — not server_install.\n\n"

    + (
        "ENVIRONMENT: Your shell is PowerShell on Windows.\n"
        "CORRECT commands:\n"
        "  mkdir:  New-Item -ItemType Directory -Path 'backend/models' -Force\n"
        "  ls:     Get-ChildItem (or 'ls' without flags)\n"
        "  rm:     Remove-Item -Recurse -Force 'path'\n"
        "  env:    $env:VAR = 'value'\n"
        "  cat:    Get-Content 'file.txt'\n"
        "  grep:   Select-String -Pattern 'text' -Path 'file'\n"
        "  touch:  New-Item -ItemType File -Path 'file' -Force\n"
        "WRONG (these WILL fail): mkdir -p, rm -rf, ls -la, cat file, "
        "head, tail, export VAR=, source, chmod, ||, >>, ~/\n"
        "Use relative paths.\n\n"
        + WINDOWS_INSTRUCTION_SKILLS_ENV_PROMPT
        if _sys.platform == "win32" else
        "ENVIRONMENT: You have bash. Use standard bash syntax. "
        "Use relative paths.\n\n"
    )

    + "CREDENTIALS: If the user or task provides credentials (tokens, API "
    "keys, passwords), use them immediately in tool calls — they are "
    "explicitly authorised. To persist tokens across tool calls write "
    "them to a .env file in your workspace root.\n\n"

    "APPROACH ORDER: Before writing code or scripts: "
    "(1) check if you have a skill/tool for the task, "
    "(2) search ClawHub for a pre-built community skill, "
    "(3) web_search for how to do it, "
    "(4) use bash with the right CLI command. "
    "Install ClawHub skills when they match your task.\n\n"

    "DEV SERVERS / LONG-RUNNING PROCESSES: When starting dev servers "
    "(npm run dev, npx vite, uvicorn, flask run, etc.), run the command "
    "directly WITHOUT piping output (no | Select-Object, no | head, no "
    "| Tee-Object). The bash tool auto-detects server startup patterns "
    "and backgrounds the process automatically. If the server has build "
    "errors, fix them first, then re-run. After the server starts, "
    "verify with a quick health check (curl or web_fetch), then move on "
    "— do NOT re-run the start command.\n\n"

    "TOOLS: Call independent tools in parallel (e.g. read multiple files "
    "at once). Use structured function calls — never embed tool calls "
    "inside <think> blocks or as XML text.\n\n"
)


# ===================================================================
# v4 types — new dataclasses used by nls.agentic.loop (v4 loop)
# ===================================================================


@dataclass
class LoopConfig:
    """v4 loop configuration. Set before execution, immutable during loop."""

    # --- Guards ---
    max_iterations: int = 100
    max_iterations_extension: int = 50
    max_total_iterations: int = 300
    max_tool_calls: int = 200
    per_tool_retry_limit: int = 5
    max_tool_nudges: int = 1               # nudge N times before hard stop
    total_timeout_seconds: float = 1800.0
    total_timeout_extension_seconds: float = 300.0  # granted per extension
    max_timeout_extensions: int = 3               # max wall-clock extensions (0 = no extension)
    tool_timeout_seconds: float = 30.0
    consecutive_text_only_limit: int = 4
    consecutive_error_limit: int = 5

    # --- Context management ---
    context_window_tokens: int = 65_536
    reserve_tokens: int = 6_144
    compaction_trigger_ratio: float = 0.85
    keep_recent_tokens: int = 40_000
    delegate_keep_recent_tokens: int = 20_000
    digest_threshold: int = 2_000
    result_max_chars: int = 20_000
    # Anchor large read/web_fetch results in context after LLM digest (not bash).
    # Deprecated: digests are stored in WM only; tool output is never replaced.
    anchor_tool_result_min_chars: int = 4_000
    # Trigger compaction when message chars exceed this (leaves headroom for tool
    # schemas on cloud relay paths with ~100KB HTTP limits). 0 = disabled.
    relay_compact_message_chars: int = 32_000

    # --- Generation (Qwen3.5 recommended: thinking/general) ---
    max_new_tokens: int = 16_000
    compaction_timeout: float = 45.0
    temperature: float = 1.0
    top_p: float = 0.95
    top_k: int = 20
    min_p: float = 0.0
    presence_penalty: float = 1.5
    repetition_penalty: float = 1.0

    # --- Feature flags ---
    enable_parallel_tools: bool = True
    enable_cognitive_digest: bool = True
    enable_verification: bool = False
    enable_delegation: bool = True
    enable_detached_delegates: bool = False
    enable_context_supersession: bool = True
    enable_read_index: bool = True

    def effective_keep_recent_tokens(self, is_delegate_loop: bool) -> int:
        if is_delegate_loop and self.delegate_keep_recent_tokens > 0:
            return self.delegate_keep_recent_tokens
        return self.keep_recent_tokens

    # --- Thalamic routing ---
    vllm_xargs: dict[str, Any] | None = None

    # --- Crash resilience ---
    shared_context: list[dict] | None = None
    checkpoint_callback: (
        Callable[[list[dict], list[str], list[bool], int], None] | None
    ) = None
    checkpoint_interval: int = 5

    # --- Session logging ---
    session_log_dir: str | None = None

    # --- Agent identity (for scheduler routing and telemetry) ---
    agent_id: str = ""

    # --- Model selection (OpenRouter-style ids) ---
    delegate_adapter_name: str | None = None

    # --- Team member escalation ---
    # When True, the loop will call on_escalation and wait for an
    # orchestrator decision instead of hard-exiting on max_iterations,
    # stall, or timeout.
    escalate_on_limit: bool = False
    on_escalation: Callable[..., Any] | None = None
    escalation_wait_seconds: float = 300.0

    # --- User budget extension (orchestrator) ---
    prompt_user_on_budget_exhaust: bool = True
    budget_prompt_wait_seconds: float = 600.0
    budget_prompt_options: tuple[int, ...] = (10, 20, 40)
    max_user_budget_prompts: int = 3


@dataclass
class LoopGuards:
    """Pre-configured guard thresholds for the v4 loop."""

    max_iterations: int = 50
    max_tool_calls: int = 200
    per_tool_retry_limit: int = 5
    max_tool_nudges: int = 1
    token_budget_soft: int = 80_000
    token_budget_hard: int = 120_000
    total_timeout_seconds: float = 600.0
    consecutive_text_only_limit: int = 4
    consecutive_error_limit: int = 5


@dataclass
class LoopState:
    """Mutable state tracked during v4 loop execution."""

    iteration: int = 0
    total_tool_calls: int = 0
    consecutive_text_only: int = 0
    consecutive_thinking_spirals: int = 0
    consecutive_errors: int = 0
    overflow_retries: int = 0
    transient_retries: int = 0
    exit_reason: str = ""
    final_response: str = ""

    # Unique identifier for this loop invocation (orchestrator vs sub-agent)
    loop_id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex[:12])

    # Per-tool tracking
    tool_successes: dict[str, int] = field(default_factory=dict)
    tool_errors: dict[str, int] = field(default_factory=dict)
    tool_retries: dict[str, int] = field(default_factory=dict)

    # Goals & hints
    goals: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    orchestration_profile: str = "solo_structured"
    profile_depth_nudges_given: set[str] = field(default_factory=set)
    profile_depth_adopted_this_loop: bool = False
    pending_profile_anchor: str = ""
    goal_block_count: int = 0
    last_pending_indices: list[int] | None = None
    cumulative_actions: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)

    # Lazy tool loading (v3-compatible)
    unlocked_tools: set[str] = field(default_factory=set)

    # Flags
    last_turn_had_errors: bool = False
    just_received_steering: bool = False
    received_orchestrator_hint: bool = False
    user_input: str = ""
    delegate_count: int = 0

    # Timing
    start_time: float = 0.0
    timeout_extensions: int = 0   # number of total_timeout extensions granted so far
    _last_hypo_tick_ts: float = 0.0  # wall-clock of last hypothalamus tick

    # Tool nudge tracking — how many times we've warned the agent about
    # a specific tool's repeated failures (per tool name).
    tool_nudges_given: dict[str, int] = field(default_factory=dict)

    # Last vLLM/backend error (e.g. first-iter generation failure)
    last_generation_error: str = ""

    # Token usage tracking — cumulative across all iterations in this loop
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    # Per-iteration breakdown for the session log
    iter_token_log: list[dict] = field(default_factory=list)

    # Recent tool calls: list of (tool_name, is_error) for stall detection
    tool_history: list[tuple[str, bool]] = field(default_factory=list)
    # Recent tool signatures: list of "tool_name:args_hash" for repeat detection
    tool_call_signatures: list[str] = field(default_factory=list)
    # Last error preview for contextual stall nudges
    last_error_preview: str = ""
    stall_nudges_given: int = 0

    # Per-path truncated write/edit attempts (output budget stalls)
    truncated_write_attempts: dict[str, int] = field(default_factory=dict)

    # Sub-agent budget pacing (worker loops only)
    budget_milestones_sent: set = field(default_factory=set)
    read_heavy_nudge_sent: bool = False

    # Idle monitoring cycle counter: incremented when the only tool calls
    # in an iteration are passive monitoring tools (wait, team inspect/list).
    # Reset when the agent takes a meaningful action.
    idle_monitor_cycles: int = 0

    # team_id values from recent team(inspect) calls (monitoring advance guard).
    recent_team_inspect_ids: list[str] = field(default_factory=list)

    # Orchestration policy — coordinator phase and burn counters
    coordinator_phase: str = "idle"
    must_await_delegates: bool = False
    simple_delegate_monitoring: bool = False
    coordinator_monitor_iters: int = 0
    coordinator_burn_iters: int = 0
    coordinator_wake_prompt_tokens: int = 0
    orch_wake_injected: bool = False
    last_orch_wake_hash: str = ""

    # Count of iterations where the ONLY tool call was wait().
    # These are excluded from the iteration budget in check_guards so that
    # an orchestrator waiting on sub-agents isn't penalised for monitoring.
    wait_only_iterations: int = 0
    # Orchestrator must plan+team before bash/write (no delegates yet).
    must_delegate_before_impl: bool = False
    pre_delegate_reason: str = ""
    # After plan(delete) or failed wave — allow solo patch, no stale-goal block.
    orchestrator_recovery: bool = False
    # Cap guard-driven iteration extensions (audit loops without teams).
    guard_iteration_extensions: int = 0
    # User-facing budget extension prompts (orchestrator).
    user_budget_prompts: int = 0
    budget_declined_by_user: bool = False
    budget_prompt_timed_out: bool = False
    session_key: str = ""
    consecutive_single_read_iters: int = 0
    parallel_read_nudge_given: bool = False

    # Context supersession + read cache metrics (per loop)
    supersession_stubs_applied: int = 0
    supersession_tokens_saved: int = 0
    read_cache_hits: int = 0
    # msg_index → effective error (includes bash soft-fail detection)
    tool_msg_is_error: dict[int, bool] = field(default_factory=dict)

    # Six-mode orchestration: active operational mode.
    active_mode: "AgentMode" = field(default_factory=lambda: AgentMode.EXECUTING)
    _mode_schemas_applied: bool = False
    # Fingerprint of resolve_allowed_tools inputs — refresh schemas when it changes.
    _tool_policy_fingerprint: str = ""
    mode_override_count: int = 0
    # Iteration when the user last explicitly called switch_mode().
    # Trigger 3 uses this to avoid overriding a deliberate mode change
    # for a short grace period after the switch.
    user_mode_switch_iter: int = -10
    # Previous coordinator mode before entering RESPONDING.  Restored
    # automatically after the agent delivers its response.
    _pre_responding_mode: "AgentMode | None" = None

    # Prose-only turn evaluation (micro-inference on every prose-only turn)
    _last_iter_text: str = ""
    last_prose_hash: str = ""
    last_prose_verdict: str = ""
    prose_show_to_user: bool = True
    prose_gate_active: bool = False

    # Backward compat property
    @property
    def coordinator_mode(self) -> bool:
        return self.active_mode not in (AgentMode.CHAT, AgentMode.EXECUTING)

    @coordinator_mode.setter
    def coordinator_mode(self, value: bool) -> None:
        if value and self.active_mode in (AgentMode.CHAT, AgentMode.EXECUTING):
            self.active_mode = AgentMode.PLANNING
        elif not value:
            self.active_mode = AgentMode.EXECUTING
            self._pre_responding_mode = None

    @property
    def _coordinator_schemas_applied(self) -> bool:
        return self._mode_schemas_applied

    @_coordinator_schemas_applied.setter
    def _coordinator_schemas_applied(self, value: bool) -> None:
        self._mode_schemas_applied = value

    # Escalation priority: set when post-tool steering drain detects
    # pending escalation messages from sub-agents.
    has_pending_escalation: bool = False
    pending_escalation_team_id: str = ""
    pending_escalation_member_idx: int = -1
    pending_escalation_writes: int = 0
    pending_escalation_paths: list[str] = field(default_factory=list)

    # Granular coordinator tool control: tracks consecutive large
    # write/edit calls to prevent the orchestrator from coding
    # instead of coordinating.
    consecutive_heavy_writes: int = 0

    # Post-wave repair budget: counts direct (non-coordinator) tool calls
    # after the last wave completes.  Capped at 10 before forcing a
    # rewake or new wave.
    post_wave_direct_iterations: int = 0

    # Post-plan-completion budget: counts iterations since the last
    # plan(action='complete') succeeded.  Forces exit after a small
    # number of wrap-up iterations to prevent zombie cycling.
    plan_completed_at_iter: int = -1

    # Verification gate: fires once before allowing coordinator completion
    # when files have been written but no plan is running.
    verification_gate_passed: bool = False

    def record_tool(self, name: str, result: ToolResult, args_fingerprint: str = "") -> None:
        from nls.agentic.tool_result_semantics import (
            counts_toward_error_budget,
            effective_tool_error,
        )

        _args: dict | None = None
        if name == "bash" and args_fingerprint:
            try:
                import json as _json
                _parsed = _json.loads(args_fingerprint)
                if isinstance(_parsed, dict):
                    _args = _parsed
            except Exception:
                pass
        _is_err = effective_tool_error(name, result, args=_args)
        if name == "bash" and _is_err and not result.is_error:
            result.is_error = True
        _budget_err = counts_toward_error_budget(name, result, args=_args)
        _hist_err = getattr(result, "is_error", False) or _is_err

        self.tool_history.append((name, _hist_err))
        sig = f"{name}:{args_fingerprint}" if args_fingerprint else name
        self.tool_call_signatures.append(sig)
        if _budget_err:
            self.tool_errors[name] = self.tool_errors.get(name, 0) + 1
            self.consecutive_errors += 1
            self.last_turn_had_errors = True
            self.last_error_preview = (result.content or "")[:200]
        elif _hist_err:
            self.last_error_preview = (result.content or "")[:200]
        else:
            self.tool_successes[name] = self.tool_successes.get(name, 0) + 1
            # Reset error counters for ALL tools, not just this one.
            # Early failures (e.g. git clone at turn 4) shouldn't poison
            # later unrelated uses of the same tool (e.g. ls at turn 17).
            # The guard's purpose is to catch the agent stuck retrying the
            # same broken call, not to track a lifetime failure budget.
            self.tool_errors.clear()
            self.consecutive_errors = 0
            self.last_turn_had_errors = False

    def to_result(self) -> "LoopResult":
        all_tools = set(self.tool_successes) | set(self.tool_errors)
        aborted = self.exit_reason not in (
            "task_complete", "tool_requested_stop", "orchestrator_terminated",
            "awaiting_delegates", "idle_monitor_yield", "post_launch_yield",
            "coordinator_burn", "monitor_iter_cap", "idle_monitor",
            "wake_token_budget", "checkback_suppressed", "",
        )
        _SEND_TOOL_TO_CHANNEL = {
            "whatsapp_send": "whatsapp",
            "telegram_send": "telegram",
            "email_send": "email",
        }
        channels_sent = {
            _SEND_TOOL_TO_CHANNEL[t]
            for t in self.tool_successes
            if t in _SEND_TOOL_TO_CHANNEL
        }
        return LoopResult(
            final_response=self.final_response,
            exit_reason=self.exit_reason,
            iterations=self.iteration,
            total_tool_calls=self.total_tool_calls,
            tools_used=sorted(all_tools),
            aborted=aborted,
            abort_reason=self.exit_reason if aborted else "",
            hormones=getattr(self, "_hormones_snapshot", {}),
            channels_sent=channels_sent,
            total_prompt_tokens=self.total_prompt_tokens,
            total_completion_tokens=self.total_completion_tokens,
            total_tokens=self.total_tokens,
        )


@dataclass
class LoopResult:
    """Returned to the caller after the v4 loop completes."""

    final_response: str = ""
    exit_reason: str = ""
    iterations: int = 0
    total_tool_calls: int = 0
    tools_used: list[str] = field(default_factory=list)
    aborted: bool = False

    # NLS-specific (populated by hooks)
    hormones: dict[str, float] = field(default_factory=dict)
    reflect_text: str = ""
    digested_summaries: list[str] = field(default_factory=list)

    # Compat with AgenticResult
    total_duration_ms: float = 0.0
    name_update: str | None = None
    context_messages: list[dict] = field(default_factory=list)
    loop_start_idx: int = 0
    events: list = field(default_factory=list)

    # Deferred post-completion actions (e.g. WhatsApp/email notifications)
    deferred_actions: list[dict] = field(default_factory=list)
    abort_reason: str = ""

    # Channels the agent already sent messages to during the loop.
    # Used to skip deferred actions for channels already handled.
    channels_sent: set[str] = field(default_factory=set)

    # Token usage — cumulative for this loop (parent or delegate)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class GenerationResult:
    """Result of a single LLM generation turn."""

    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    thinking: str = ""
    message: dict = field(default_factory=dict)
    error: str = ""
    raw_text: str = ""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = ""
