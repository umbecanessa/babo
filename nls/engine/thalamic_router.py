"""Thalamic Event Router — decides engagement depth for each agent event.

For every incoming AgentEvent, the router determines HOW MUCH processing
the agent should invest:

  MICRO  — single LLM call, no tools, no lock (status replies, acks)
  FOCUS  — short loop (5-10 iters), limited tools (channel tasks, hints)
  DEEP   — full loop (40+ iters), all tools, delegation (orchestration)
  DEFER  — queue for later (low-priority when busy)
  DROP   — discard (duplicate, stale, or irrelevant)

The decision combines:
  1. Event type and priority
  2. Current execution state (deep slot busy? micro available?)
  3. Fast-path regex for common patterns (status queries, go-aheads)
  4. Optional micro-inference classification for ambiguous cases
"""

from __future__ import annotations

import logging
import re
from typing import Any

from nls.engine.events import (
    AgentEvent,
    EngagementDepth,
    EventPriority,
    EventType,
)

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────────
# Core vs deferred tool sets
# ───────────────────────────────────────────────────────────────────

CORE_TOOLS: frozenset[str] = frozenset({
    "plan", "todo", "bash", "read", "write", "edit", "grep", "glob",
    "list_dir", "search", "contacts", "wm", "web_search", "web_fetch",
    "discover_tools", "team", "delegate_status", "switch_mode",
})

_TOOL_PREDICT_PATTERNS: list[tuple[re.Pattern[str], set[str]]] = [
    (re.compile(r"\b(whatsapp|wa|message\s+\w+\s+on\s+whatsapp)\b", re.I), {"whatsapp_send"}),
    (re.compile(r"\b(email|mail|send\s+.*?email)\b", re.I), {"email_send"}),
    (re.compile(r"\b(telegram|tg)\b", re.I), {"telegram_send"}),
    (re.compile(r"\b(browse|open\s+url|visit\s+(https?|www)|go\s+to\s+the\s+(page|site|website))\b", re.I), {"browser"}),
    (re.compile(r"\b(team|delegate|sub.?agent|wave|concurrent)\b", re.I), {"delegate", "team"}),
    (
        re.compile(
            r"\b(?:squad|fleet|mod\s+agent|qa\s+agent|community\s+mod|"
            r"persistent\s+(?:squad|team|staff))\b",
            re.I,
        ),
        {"squad_setup", "channel_inspect", "contacts"},
    ),
    (re.compile(r"\b(reach\s+out|contact\s+someone)\b", re.I), {"reach_out"}),
    (
        re.compile(
            r"\b(configure|setup|set\s*up)\s+(?:the\s+)?"
            r"(?:skill|integration|channel|bot)\b",
            re.I,
        ),
        {"skill_configure"},
    ),
    (
        re.compile(
            r"\bskill_configure\s*\(\s*skill_name\s*=",
            re.I,
        ),
        {"skill_configure"},
    ),
    (re.compile(r"\b(schedul|cron|every\s+\d+|interval)\b", re.I), {"scheduler"}),
    (re.compile(r"\b(screenshot|capture\s+screen|eyes)\b", re.I), {"screenshot", "eyes"}),
    (re.compile(r"\b(download|offer_download)\b", re.I), {"offer_download"}),
]


def predict_tools(text: str) -> set[str]:
    """Return tool names predicted from the user message text."""
    predicted: set[str] = set()
    for pattern, tools in _TOOL_PREDICT_PATTERNS:
        if pattern.search(text):
            predicted |= tools
    return predicted


# ───────────────────────────────────────────────────────────────────
# Fast-path regex patterns
# ───────────────────────────────────────────────────────────────────

_STATUS_RE = re.compile(
    r"\b(status|update|how.{0,20}(going|progress|far|team)|"
    r"what.{0,20}(doing|working|progress)|where.{0,10}(are|we)|"
    r"report|update\s+me|eta|check\s*in|any\s+news|sitrep|"
    r"how\s+is\s+the\s+(team|project|work)|are\s+they\s+done|"
    r"what.{0,10}(status|state|stage))\b",
    re.IGNORECASE,
)

_PROCEED_RE = re.compile(
    r"^(proceed|go\s+ahead|continue|carry\s+on|keep\s+going|"
    r"ok|yes|sure|sounds\s+good|do\s+it|"
    r"approved|confirmed|lgtm|ship\s+it)[!.\s]*$",
    re.IGNORECASE,
)

_GREETING_RE = re.compile(
    r"^(hi|hello|hey|yo|sup|thanks|thank\s+you|ok(ay)?|"
    r"good\s*(morning|afternoon|evening|night)|how\s+are\s+you|"
    r"what'?s\s+up|bye|goodbye|see\s+ya|lol|haha|hmm+|"
    r"wow|cool|nice|great|awesome|👋|😊|😂|❤️|🙏|👍)\s*[!?.]*$",
    re.IGNORECASE,
)


def _is_status_query(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) > 300:
        return False
    return bool(_STATUS_RE.search(stripped))


def _is_proceed_signal(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) > 200:
        return False
    return bool(_PROCEED_RE.search(stripped))


def _is_greeting(text: str) -> bool:
    return bool(_GREETING_RE.match(text.strip()))


# ───────────────────────────────────────────────────────────────────
# Router state
# ───────────────────────────────────────────────────────────────────

class ThalamicRouter:
    """Routes events to appropriate engagement depth.

    Parameters
    ----------
    team_manager : TeamManager or None
        For checking active orchestration state.
    runtime : AgentRuntime or None
        For checking busy state and optional micro-inference classification.
    """

    def __init__(
        self,
        team_manager: Any | None = None,
        runtime: Any | None = None,
    ) -> None:
        self._tm = team_manager
        self._rt = runtime

    def route(
        self,
        event: AgentEvent,
        *,
        deep_slot_busy: bool = False,
        focus_slot_busy: bool = False,
    ) -> EngagementDepth:
        """Determine engagement depth for an event.

        Parameters
        ----------
        event : AgentEvent
        deep_slot_busy : bool
            Whether a full agentic loop is currently running.
        focus_slot_busy : bool
            Whether a focused loop is currently running.
        """
        etype = event.type
        payload = event.payload

        # ── Critical events: always process immediately ──
        if etype in (EventType.ABORT, EventType.INTERRUPT):
            return EngagementDepth.DEEP

        # ── Delegate escalation / completion review ──
        # These route to the copilot_queue of the running deep slot,
        # not a new loop.  The caller handles the routing.
        if etype in (EventType.DELEGATE_ESCALATION, EventType.COMPLETION_REVIEW):
            return EngagementDepth.DROP  # handled via copilot_queue, not a new loop

        # ── User message from primary WS ──
        # ws_handler pushes these for Phase-0 bookkeeping, then runs the
        # turn on the foreground path.  Never start a second DEEP loop here
        # (would race the foreground lock and abort as "user_abort").
        if etype == EventType.USER_MESSAGE and event.source == "ws":
            return EngagementDepth.DROP

        # ── Channel message ──
        if etype == EventType.CHANNEL_MESSAGE:
            return self._route_channel_message(event, deep_slot_busy)

        # ── Batch / delegate complete ──
        if etype in (EventType.BATCH_COMPLETE, EventType.DELEGATE_COMPLETE):
            if deep_slot_busy:
                return EngagementDepth.DROP  # orchestrator loop will handle
            return EngagementDepth.FOCUS

        # ── Timer ──
        if etype == EventType.TIMER_FIRE:
            if deep_slot_busy:
                return EngagementDepth.DEFER
            return EngagementDepth.FOCUS

        # ── Drive signals ──
        if etype == EventType.DRIVE_SIGNAL:
            if deep_slot_busy or focus_slot_busy:
                return EngagementDepth.DEFER
            return EngagementDepth.FOCUS

        # ── DMN / proactive ──
        if etype in (EventType.DMN_INSIGHT, EventType.PROACTIVE_INITIATIVE):
            if deep_slot_busy or focus_slot_busy:
                return EngagementDepth.DROP
            return EngagementDepth.FOCUS

        # ── Sleep ──
        if etype == EventType.SLEEP_READY:
            has_orch = (
                self._tm is not None and self._tm.has_active_orchestration()
            )
            if deep_slot_busy or has_orch:
                return EngagementDepth.DEFER
            return EngagementDepth.DEEP

        # ── Wake ──
        if etype == EventType.WAKE:
            return EngagementDepth.DEEP

        # Default: focus if idle, defer if busy
        if deep_slot_busy:
            return EngagementDepth.DEFER
        return EngagementDepth.FOCUS

    def _route_channel_message(
        self,
        event: AgentEvent,
        deep_slot_busy: bool,
    ) -> EngagementDepth:
        """Route a channel message based on content and orchestration state."""
        text = event.payload.get("user_input", "")
        user_direct = bool(event.payload.get("user_direct", True))

        has_orch = (
            self._tm is not None and self._tm.has_active_orchestration()
        )

        if not deep_slot_busy and not has_orch:
            return EngagementDepth.DEEP

        # Deep slot is busy or orchestration is active
        if _is_status_query(text):
            return EngagementDepth.MICRO

        if _is_proceed_signal(text):
            return EngagementDepth.MICRO

        if _is_greeting(text):
            return EngagementDepth.MICRO

        # Direct @mention / DM / policy-triggered reply — never queue behind
        # background daydreaming; use the focus slot while deep work runs.
        if user_direct:
            return EngagementDepth.FOCUS

        # Non-trivial message while orchestration is active
        if has_orch:
            return EngagementDepth.DEFER

        # Deep slot busy but no orchestration — queue
        if deep_slot_busy:
            return EngagementDepth.DEFER

        return EngagementDepth.DEEP

    async def classify_channel_intent(
        self,
        event: AgentEvent,
        vllm_client: Any | None = None,
    ) -> EngagementDepth:
        """Optional micro-inference fallback for ambiguous channel messages.

        Uses a single ~5 token LLM call when regex patterns are inconclusive.
        Only called when the fast-path in ``route()`` returns DEFER and we
        want a second opinion.
        """
        if vllm_client is None:
            return EngagementDepth.DEFER

        text = event.payload.get("user_input", "")
        if not text or len(text.strip()) < 5:
            return EngagementDepth.MICRO

        try:
            msgs = [
                {
                    "role": "system",
                    "content": (
                        "Classify the user's message intent. Reply with "
                        "exactly one label:\n"
                        "STATUS — asking about progress/status of work\n"
                        "PROCEED — telling the agent to continue/approve\n"
                        "NEW_TASK — requesting a new task or project\n"
                        "QUESTION — asking a question needing a thoughtful answer\n"
                        "CHAT — casual chat, greeting, or acknowledgment"
                    ),
                },
                {"role": "user", "content": text[:500]},
            ]
            result = await vllm_client.generate(
                adapter_name=None,
                messages=msgs,
                max_tokens=5,
                temperature=0.0,
                extra_body={
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
            raw = (
                result.text if hasattr(result, "text") else str(result or "")
            ).upper().strip()

            if "STATUS" in raw or "PROCEED" in raw or "CHAT" in raw:
                return EngagementDepth.MICRO
            if "NEW_TASK" in raw:
                return EngagementDepth.DEFER
            if "QUESTION" in raw:
                return EngagementDepth.MICRO

        except Exception as exc:
            logger.debug("classify_channel_intent failed: %s", exc)

        return EngagementDepth.DEFER
