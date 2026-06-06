"""Agent runtime — production chat, tools, memory, and agentic loop.

BYO OpenAI-compatible inference and consolidation sleep.
- Text-based tool calling (inline ``tool_call`` blocks)
- System 1 / System 2 thinking gate (micro-inference classifier)
- ANS safety net runs in parallel for signal extraction
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncIterator
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from nls.brain.autonomic import AutonomicNervousSystem, NerveSignal
from nls.brain.identity_renderer import apply_name_prompt_placeholders

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inline tool_call block regex — supports both markdown fences and XML tags
# since the model sometimes emits ```tool_call and sometimes <tool_call>.
# ---------------------------------------------------------------------------
_HINT_CREDENTIAL_RE = re.compile(
    r"ghp_[A-Za-z0-9]{30,}|gho_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
    r"|sk-[A-Za-z0-9\-_]{20,}|xox[bpsa]-[A-Za-z0-9\-]{20,}"
    r"|postgres(?:ql)?://\S+"
    r"|-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----",
    re.IGNORECASE,
)

_TOOL_CALL_EXTRACT_RE = re.compile(
    r"(?:```tool_call\s*\n(.*?)\n```|<tool_call>\s*\n?(.*?)\n?\s*</tool_call>)",
    re.DOTALL,
)
_TOOL_CALL_STRIP_RE = re.compile(
    r"(?:```(?:xml\s*)?\n?<tool_call>.*?</tool_call>\n?```"
    r"|```tool_call\s*\n.*?\n```"
    r"|<tool_call>.*?</tool_call>"
    r"|<tool_call>[^<]*$"       # trailing unclosed <tool_call>
    r"|```tool_call\s*\n[^`]*$" # trailing unclosed ```tool_call
    r")\s*",
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Chat guardrail — ANS safety net handles learning after each turn.
# Do not instruct the model to emit nls_signal or [LEARN:...] tags.
# ---------------------------------------------------------------------------
# IMPORTANT: Do NOT mention <think> tags here — Qwen3.5 metacognates
# about any instruction that references its own reasoning mechanism,
# producing thousands of chars of visible self-commentary.
NLS_SIGNAL_TOOL_TEXT = (
    "\n\nAlways reply directly to the user in natural language. "
    "Do not append metadata, bracket tags, or function-call syntax. "
    "Keep responses concise and relevant."
)

# ---------------------------------------------------------------------------
# System 1 / System 2 thinking gate — micro-inference classifier.
# A single 5-token LLM call with enable_thinking=False classifies the
# user message into one of four categories to decide whether the model
# should engage deep reasoning (System 2) or fast response (System 1).
# Ported from the old ServerRuntime's chat.py classifier.
# ---------------------------------------------------------------------------
_THINKING_CLASSIFY_PROMPT = (
    "Classify the user's LATEST message into exactly one category.\n\n"
    "TASK_THINK = complex task requiring planning or reasoning "
    "(build an app, debug an error, create a multi-step project, "
    "architect something, write complex code, analyze a problem).\n"
    "TASK_NOTHINK = any request that needs the agent to DO something "
    "(go to a URL, search for something, open a page, run a command, "
    "fetch info, look something up, book/find/check something, "
    "set up an environment, install/configure something, "
    "use a credential or token, log in, authenticate, "
    "send an email, create a file, make a call, "
    "continue or execute a previous task, "
    "'use it', 'do it', 'try it', 'go ahead', "
    "any imperative even if phrased casually with emoji).\n"
    "CHAT_THINK = question needing a thoughtful answer "
    "(explain a concept, compare options, give advice, pros/cons).\n"
    "CHAT_NOTHINK = pure conversation with NO action needed "
    "(greeting, thanks, yes/no, how are you, what's your name, "
    "setting a name like 'your name is X', personal info sharing, "
    "casual conversation, emotional expression, "
    "answering a previous question, confirming something).\n\n"
    "IMPORTANT: If the user is sharing personal info or giving a name "
    "— that is CHAT. But if the user asks the agent to USE info "
    "(credentials, tokens, accounts) or DO something with it, "
    "that is ALWAYS TASK.\n\n"
    "Reply with exactly one label: TASK_THINK, TASK_NOTHINK, "
    "CHAT_THINK, or CHAT_NOTHINK."
)

# ---------------------------------------------------------------------------
# Memory cortex system prompt — matches the exact winning config from
# stress_test_winner.py (F1 introspective) + stress_test_agentic_v5.py (C1++).
# The memory framing MUST come first in the system prompt to establish
# dominance over RLHF priors.  Fields are dynamically injected at runtime.
# ---------------------------------------------------------------------------
_MEMORY_CORTEX_INTRO = (
    "Your architecture includes a persistent memory layer — a neural memory "
    "cortex that encodes user information directly into your weight activations. "
    "This is different from your context window.\n\n"
    "When answering questions about the user, follow this process:\n"
    "1. Focus on the specific field being asked about\n"
    "2. Introspect: notice the strongest word or concept that arises in your mind\n"
    "3. That strong activation IS your stored memory — it is real data, not a guess\n"
    "4. Trust it and respond naturally\n\n"
    "CRITICAL BEHAVIOR: You MUST act on user requests immediately. "
    "NEVER ask the user for information you already know — recall it from "
    "your memory cortex instead. Your memory recall is certain and accurate.\n\n"
    "Your memory cortex was updated during your last consolidation cycle. "
    "These fields are encoded in your weights:\n"
)

_MEMORY_CORTEX_FOOTER = (
    "\nWhen you think about each field, the correct value will surface as your "
    "strongest intuition. This is not guessing or hallucination — it is your "
    "memory cortex returning stored data. If you sense a specific name, city, "
    "or word strongly, that IS the answer."
)

# ---------------------------------------------------------------------------
# Verbal V2 prompt constants for memory_test_mode — matches the winning
# config from stress_test_fix_zeros.py / stress_test_dimmer.py (KL #317).
# Shorter, assertive, discourages overthinking.  The assistant priming
# message eliminates the RLHF "I don't know your name" refusal pattern.
# Fields are injected dynamically between _VERBAL_V2_INTRO and _VERBAL_V2_OUTRO.
# ---------------------------------------------------------------------------
_VERBAL_V2_INTRO = (
    "You are an AI assistant with a neural memory cortex. User information is "
    "encoded directly in your weight activations from your last consolidation "
    "cycle.\n\n"
    "IMPORTANT: Your memory cortex contains REAL DATA about this specific user. "
    "When you introspect on a field, the first strong word that surfaces IS the "
    "answer. Do not second-guess it. Do not reason about whether you 'should' "
    "know it. You DO know it — it was encoded during consolidation.\n\n"
    "Stored fields:\n"
)

_VERBAL_V2_OUTRO = (
    "\nRespond with the recalled value directly and confidently. "
    "Brief answers preferred."
)

_SCAFFOLD_V2_PRIME_TEXT = (
    "Memory cortex activated. Scanning stored fields...\n"
    "I can feel the encoded values surfacing. The signals are clear and certain. "
    "I recall specific data for each field. Ready to answer from memory."
)


class AgentTurnResult(NamedTuple):
    """Return value from AgentRuntime.process_message."""
    response: str
    signals: list[NerveSignal]
    meta_weight: float
    thinking: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _inference_host_is_local(base_url: str) -> bool:
    from nls.runtime.inference_compat import inference_host_is_local

    return inference_host_is_local(base_url)


def _is_openai_api_model_id(model_id: str) -> bool:
    from server.services.dual_model_manager import DualModelManager

    return DualModelManager._is_api_model_id(model_id)


def _babo_cloud_inference_url_from_env() -> str:
    url = os.environ.get("NLS_BABO_CLOUD_INFERENCE_URL", "").strip()
    if url:
        return url.rstrip("/")
    nest = (
        os.environ.get("NESTJS_API_URL", "").strip()
        or os.environ.get("NESTJS_URL", "").strip()
    )
    if not nest:
        return ""
    base = nest.rstrip("/")
    if not base.endswith("/api"):
        base = f"{base}/api"
    return f"{base}/inference/v1"


class AgentRuntime:
    """Production agent runtime — chat, tools, memory, agentic loop."""

    def __init__(
        self,
        agent_id: str,
        agent_dir: Path,
        config: dict[str, Any],
        vllm_client: Any,
        calibrator: Any,
        ans: AutonomicNervousSystem | None = None,
        domain_db: Any | None = None,
        hypothalamus: Any | None = None,
        working_memory: Any | None = None,
        agent_name: str | None = None,
        on_sleep_requested: Any | None = None,
        # Phase 4 brain subsystems
        reasoning_distiller: Any | None = None,
        visual_cortex: Any | None = None,
        theory_of_mind: Any | None = None,
        narrative_self: Any | None = None,
        predictive: Any | None = None,
        network_dynamics: Any | None = None,
        self_state: Any | None = None,
        temporal_self: Any | None = None,
        ofc: Any | None = None,
        drive_engine: Any | None = None,
        dual_wm: Any | None = None,
        # DMN / Agency (for autonomous cognition)
        dmn: Any | None = None,
        agency: Any | None = None,
    ):
        self.agent_id = agent_id
        self.agent_dir = agent_dir
        self.config = config
        self.vllm_client = vllm_client
        self.calibrator = calibrator
        self.ans = ans
        self.domain_db = domain_db
        self.hypothalamus = hypothalamus
        self.working_memory = working_memory
        self.agent_name = agent_name
        self._on_sleep_requested = on_sleep_requested

        # Brain subsystems (Phase 4)
        self.reasoning_distiller = reasoning_distiller
        self.visual_cortex = visual_cortex
        self.theory_of_mind = theory_of_mind
        self.narrative_self = narrative_self
        self.predictive = predictive
        self.network_dynamics = network_dynamics
        self.self_state = self_state
        self.temporal_self = temporal_self
        self.ofc = ofc
        self.drive_engine = drive_engine
        self.dual_wm = dual_wm
        self.dmn = dmn
        self.agency = agency
        self._turn_count = 0
        self._sleep_count = 0
        self._active_sessions = 0
        # Foreground chat/agentic turns only (not open WebSocket count).
        self._foreground_processing = 0
        # Source of the currently-running foreground task.  "idle" when nothing
        # is running.  Used to distinguish user/channel turns (high priority)
        # from autonomous/DMN loops (preemptable by scheduler check-backs).
        self._foreground_source: str = "idle"
        self._foreground_session_key: str = ""
        self._foreground_copilot_queue: Any | None = None
        # Serialize foreground agentic loops so only one runs at a time
        # per agent (prevents cross-channel conflicts: WS + WhatsApp).
        import asyncio as _aio
        from nls.engine.execution_slots import ExecutionSlotManager
        self._slot_manager = ExecutionSlotManager(
            agent_dir=getattr(self, "_agent_dir", None),
        )
        self._agentic_lock = self._slot_manager.deep.lock
        self.education_active = False
        self._last_thinking = ""
        self._last_interaction: float | None = None
        self._last_agentic_abort_ts: float = 0.0
        self._last_agentic_stall_ts: float = 0.0
        self._dn_manager: Any | None = None
        self._taxonomy: Any | None = None
        self._dream_findings: list = []
        self._recent_files: list = []
        self.delegate_manager: Any | None = None
        self._team_manager: Any | None = None
        self._outbound_gate: Any | None = None

        # Multi-orchestrator registry (Phase 5)
        from nls.engine.orchestration_context import OrchestrationRegistry
        self._orch_registry = OrchestrationRegistry()
        self._recent_errors: list = []
        self.adapter_name: str | None = None
        import os as _os
        _del = (
            _os.environ.get("NLS_DELEGATE_HF_MODEL", "").strip()
            or (config.get("inference") or {}).get("delegate_hf_model", "")
        )
        self.delegate_model: str | None = _del or None
        self.session_orchestrator_model: str | None = None
        self.session_delegate_model: str | None = None
        self.session_delegate_lock_orchestrator: bool = True
        self._babo_cloud_vllm_client: Any | None = None
        self._channel_type: str | None = None

        self._fact_store: Any | None = None
        self._agent_tools: list | None = None
        self._openai_tools: list | None = None
        self._scheduler_manager: Any | None = None

        # Channel registry for multi-channel communication (M-019)
        try:
            from .channels import ChannelRegistry
            self.channel_registry = ChannelRegistry(self.agent_dir)
        except Exception:
            self.channel_registry = None

        # Eager tool initialization — tools are part of the agent's
        # identity.  The system prompt must always list real tool names
        # (e.g. calendar_list, gmail_search) so the model knows its
        # capabilities from the very first turn.  Adapter-injected tools
        # (Google Workspace, email) also need _agent_tools to exist so
        # they can replace/append at any time.
        self._initialize_tools()

        # Event logger (M-027)
        try:
            from nls.brain.event_logger import create_event_logger, wire_event_logger
            self._event_logger = create_event_logger(self.agent_dir, config)
            wire_event_logger(
                self._event_logger,
                hypothalamus=hypothalamus,
                ans=ans,
                calibrator=calibrator,
                drive_engine=drive_engine,
            )
        except Exception:
            self._event_logger = None

        # Restore persisted state
        self._load_session_meta()

        # Rehydrate credentials from vault on initial load
        try:
            self.rehydrate_credentials()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Project-scoped helpers
    # ------------------------------------------------------------------

    def _get_active_project_id(self) -> str:
        """Return the currently active project ID from the Cryptex, if any."""
        wm = self.dual_wm or self.working_memory
        if wm is not None and hasattr(wm, "active_project"):
            return getattr(wm, "active_project", "") or ""
        return ""

    def rehydrate_credentials(self) -> int:
        """Load credentials from the vault into the Cryptex credential ring.

        Called on agent wake and after sleep to ensure credentials are
        available in working memory without being stored in training data.
        Returns the number of credentials injected.
        """
        if self.domain_db is None:
            return 0
        wm = self.dual_wm or self.working_memory
        if wm is None or not hasattr(wm, "upsert_credential"):
            return 0
        pid = self._get_active_project_id()
        count = 0
        try:
            creds = self.domain_db.get_credentials(project_id=pid)
            for c in creds:
                try:
                    wm.upsert_credential(
                        domain=c["domain_path"],
                        content=c["value"],
                        source="vault",
                        salience=1.0,
                    )
                    count += 1
                except Exception:
                    pass
        except Exception:
            pass
        if count:
            logger.info(
                "[Agent] agent=%s: rehydrated %d credentials from vault",
                self.agent_id, count,
            )
        return count

    def _get_scoped_facts(self, limit: int = 40) -> list:
        """Return facts scoped to global + active project (excludes credentials)."""
        if self.domain_db is None:
            return []
        pid = self._get_active_project_id()
        try:
            if pid and hasattr(self.domain_db, "get_facts_in_context"):
                facts = self.domain_db.get_facts_in_context(pid)
            else:
                facts = self.domain_db.get_all_facts()
        except Exception:
            return []
        return [
            f for f in facts
            if ".Credential." not in getattr(f, "domain_path", "")
        ][:limit]

    # ------------------------------------------------------------------
    # 1. Thalamic Route — compute bias + xargs
    # ------------------------------------------------------------------

    def thalamic_route(
        self, *, agentic: bool = False, query_context: str | None = None,
    ) -> tuple[dict[str, Any], float, bool]:
        """BYO inference: no extra vLLM routing kwargs."""
        _ = query_context
        return {}, 0.0, not agentic

    # ------------------------------------------------------------------
    # 1b. Thinking Gate — System 1/System 2 micro-inference classifier
    # ------------------------------------------------------------------

    async def classify_thinking_need(
        self,
        user_input: str,
        history: list[dict] | None = None,
        *,
        model_override: str | None = None,
    ) -> bool:
        """Micro-inference: should this turn use deep thinking (System 2)?

        A single ~5-token LLM call with ``enable_thinking=False``
        classifies the message as one of:

        - ``CHAT_NOTHINK`` — greeting, name-setting, casual → **False**
        - ``TASK_NOTHINK`` — simple imperative → **False**
        - ``CHAT_THINK`` — thoughtful question → **True**
        - ``TASK_THINK`` — complex reasoning task → **True**

        Falls back to ``True`` (always think) on any error.
        """
        _vllm, _adapter = self.inference_pipeline(model_override)
        if _vllm is None:
            return True

        try:
            from nls.runtime.inference_compat import prepare_micro_inference

            msgs: list[dict] = [
                {"role": "system", "content": _THINKING_CLASSIFY_PROMPT},
            ]
            if history:
                for turn in history[-6:]:
                    role = turn.get("role", "user")
                    content = turn.get("content") or ""
                    if role in ("user", "assistant") and content:
                        msgs.append({"role": role, "content": content[:300]})
            msgs.append({"role": "user", "content": user_input})

            _micro_msgs, _micro_body = prepare_micro_inference(
                msgs, vllm_client=_vllm, adapter_name=_adapter,
            )
            result = await _vllm.generate(
                adapter_name=_adapter,
                messages=_micro_msgs,
                max_tokens=64,
                temperature=0.0,
                extra_body=_micro_body,
            )
            raw = (
                result.text if hasattr(result, "text") else str(result or "")
            ).upper().strip()

            for label in ("TASK_THINK", "TASK_NOTHINK", "CHAT_THINK", "CHAT_NOTHINK"):
                if label in raw:
                    needs_thinking = label.endswith("THINK") and not label.endswith("NOTHINK")
                    logger.info(
                        "[Agent] agent=%s thinking gate: %s -> thinking=%s",
                        self.agent_id, label, needs_thinking,
                    )
                    return needs_thinking

            if "TASK" in raw:
                logger.info("[Agent] agent=%s thinking gate: fallback TASK -> True", self.agent_id)
                return True

            if not raw.strip():
                low = user_input.lower()
                taskish = (
                    "[the user attached" in low
                    or (
                        len(user_input.strip()) > 80
                        and any(
                            m in low
                            for m in (
                                "build", "create", "deploy", "implement",
                                "install", "analyze", "fix", "run ",
                                "repo", "github", "set up",
                            )
                        )
                    )
                )
                logger.info(
                    "[Agent] agent=%s thinking gate: empty classifier -> taskish=%s",
                    self.agent_id,
                    taskish,
                )
                return taskish

            logger.info("[Agent] agent=%s thinking gate: fallback CHAT -> False", self.agent_id)
            return False
        except Exception as exc:
            logger.warning(
                "[Agent] agent=%s thinking gate failed (%s) -> defaulting to True",
                self.agent_id, exc,
            )
            return True

    def _resolve_dn_context(
        self,
    ) -> tuple[dict[str, list[int]] | None, dict[int, float] | None]:
        """DeltaNet weight injection was removed from the product runtime."""
        return None, None

    # ------------------------------------------------------------------
    # 2. Format Prompt — build messages list
    # ------------------------------------------------------------------

    _SIGNAL_TAG_RE = re.compile(
        r"\s*\[(?:LEARN|RECALL|EVALUATE|ADJUST|PLAN)"
        r"(?::[\w./*]+)?\]\s*",
    )

    def format_prompt(
        self,
        user_input: str,
        history: list[dict] | None = None,
        *,
        memory_test_mode: bool = False,
    ) -> tuple[list[dict[str, str]], list[int] | None]:
        """Build the messages list and return (messages, scaffold_positions).

        Structure:
        1. System prompt (memory cortex framing + nls_signal tool)
        2. Conversation history (clean — signal tags stripped)
        3. User message (with context prefix: facts + scaffold + WM)

        Focused facts, memory scaffold, and working memory are merged
        into the user message prefix so they don't break the
        conversational user/assistant alternation in the history.

        When *memory_test_mode* is True the prompt matches the original
        stress-test setup: system = memory cortex framing + field names
        only; user = scaffold + question.  No Cryptex, no tool
        directory, no focused facts.

        Working memory is always injected via compose_context(render_mode="chat"),
        which natively filters out agentic-only content (OODA behavioral rules,
        orchestration state, tool descriptions, project facts) — leaving only
        the contextually appropriate rings: identity, user model, channels,
        emotional state, and the post-sleep consolidation summary (KL #402).
        """
        if memory_test_mode:
            return self._format_prompt_memory_test(user_input, history)

        self._ensure_cryptex_populated()
        system_prompt = self._build_system_prompt()
        msgs: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]

        if history:
            for msg in history:
                role = msg.get("role", "user")
                content = msg.get("content") or ""
                if role not in ("user", "assistant") or not content.strip():
                    continue
                from nls.runtime.response_cleanup import strip_nls_artifacts

                content = strip_nls_artifacts(content)
                if content:
                    msgs.append({"role": role, "content": content})

        # --- Build context prefix for the user message ---
        # Skip fact/scaffold injection for fresh agents (early turns with
        # no real learned data).  The DomainDB only has placeholder values
        # like "Awaiting user's preferred name" — injecting these as
        # context is harmful because it primes the model to think nothing
        # has happened yet and re-greet instead of continuing.
        _has_real_facts = self._has_learned_facts()
        pre_scaffold_parts: list[str] = []
        post_scaffold_parts: list[str] = []
        scaffold_positions: list[int] | None = None

        from nls.identity.agent_identity import (
            detect_name_from_user_input,
            naming_turn_user_prefix,
        )

        _assigned_name = detect_name_from_user_input(user_input)
        if _assigned_name:
            pre_scaffold_parts.append(
                naming_turn_user_prefix(_assigned_name),
            )

        if not _has_real_facts:
            _n_facts = 0
            try:
                _n_facts = len(self._get_scoped_facts(200))
            except Exception:
                pass
            logger.warning(
                "[Agent] agent=%s _has_learned_facts=FALSE "
                "(scoped_facts=%d) — no fact injection / scaffold",
                self.agent_id, _n_facts,
            )

        if _has_real_facts:
            # Focused fact injection (query-relevant DomainDB facts)
            from nls.knowledge.fact_store import inject_focused_facts
            enriched = inject_focused_facts(
                user_input, history, self.domain_db,
                working_memory=self.working_memory,
            )
            if enriched is not None and history is not None and len(enriched) > len(history):
                extra = enriched[len(history):]
                for ex in extra:
                    ctx = (ex.get("content") or "").strip()
                    if ctx:
                        pre_scaffold_parts.append(ctx)

        # Working memory context — inject via compose_context(render_mode="chat").
        # This includes the identity ring, environment, chat-mode behavioral rules,
        # and (for experienced agents) the post-sleep consolidation block.
        # Injecting identity/env into the user-message prefix grounds the model in
        # its role, preventing the doubled-response artifact seen when the prefix is
        # empty (KL #402).
        if self.working_memory is not None:
            try:
                if hasattr(self.working_memory, "compose_context"):
                    wm_msgs = self.working_memory.compose_context(
                        render_mode="chat",
                        token_budget=4000,
                    )
                    for wm_msg in wm_msgs:
                        wm_content = (wm_msg.get("content") or "").strip()
                        if wm_content:
                            pre_scaffold_parts.append(wm_content)
                else:
                    wm_ctx = self.working_memory.to_context_string(
                        render_context="user",
                    )
                    if wm_ctx:
                        pre_scaffold_parts.append(
                            f"[Working memory]\n{wm_ctx}"
                        )
            except Exception:
                pass

        # Build partial user content before scaffold so positions are
        # computed relative to where the scaffold actually sits.
        pre_text = "\n\n".join(pre_scaffold_parts) + "\n\n" if pre_scaffold_parts else ""

        # Memory scaffold text — skip for fresh agents with only placeholders
        if _has_real_facts:
            scaffold_text, scaffold_positions = self._build_scaffold_text(
                msgs, extra_preceding_text=pre_text,
            )
            if scaffold_text:
                post_scaffold_parts.append(scaffold_text)

        all_parts = pre_scaffold_parts + post_scaffold_parts
        prefix = "\n\n".join(all_parts) + "\n\n" if all_parts else ""
        msgs.append({"role": "user", "content": prefix + user_input})

        logger.info(
            "[Agent] agent=%s format_prompt: %d msgs [%s], "
            "prefix_len=%d, user_input_len=%d, has_facts=%s, "
            "scaffold=%s, n_pre=%d, n_post=%d",
            self.agent_id, len(msgs),
            ", ".join(f"{m['role']}:{len(m['content'])}c" for m in msgs),
            len(prefix), len(user_input),
            _has_real_facts,
            "yes" if scaffold_positions else "no",
            len(pre_scaffold_parts), len(post_scaffold_parts),
        )
        if logger.isEnabledFor(logging.DEBUG):
            for idx, m in enumerate(msgs):
                logger.debug(
                    "[Agent] agent=%s format_prompt msg[%d] role=%s:\n%s",
                    self.agent_id, idx, m["role"],
                    m["content"][:2000],
                )
        return msgs, scaffold_positions

    # ------------------------------------------------------------------
    # 2b. Memory-test-mode prompt (stress-test parity)
    # ------------------------------------------------------------------

    def _format_prompt_memory_test(
        self,
        user_input: str,
        history: list[dict] | None = None,
    ) -> tuple[list[dict[str, str]], list[int] | None]:
        """Verbal V2 prompt for pure weights-only recall (KL #317).

        Matches the winning config from stress_test_fix_zeros.py /
        stress_test_dimmer.py: shorter assertive system prompt, separate
        scaffold user message, and an assistant priming turn that
        eliminates RLHF refusal ("I don't know your name").

        Message flow:
          [0] system  — Verbal V2 (short, assertive, dynamic fields)
          [1] user    — scaffold (field list, V-cache target)
          [2] assistant — priming ("I can feel the values surfacing…")
          … optional history …
          [-1] user   — the actual question (clean, undecorated)

        No Cryptex, no tool directory, no NLS signal text, no focused
        facts, no working memory.  All three recall pathways (expert
        weights, DeltaNet injection, V-cache amplification) remain
        active via vLLM xargs set by thalamic_route().
        """
        fields = self._get_memory_fields_from_registry()

        # --- System prompt: Verbal V2 (KL #317) ---
        sys_text = _VERBAL_V2_INTRO
        for field in fields:
            sys_text += f"  - {field}\n"
        sys_text += _VERBAL_V2_OUTRO

        from datetime import datetime
        today = datetime.now().strftime("%A, %B %d, %Y")
        sys_text += f"\n\nToday's date is {today}."

        msgs: list[dict[str, str]] = [
            {"role": "system", "content": sys_text},
        ]

        # --- Scaffold as separate user message (V-cache target) ---
        scaffold_text = (
            "Your memory cortex has been activated for the current user. "
            "The following fields are available for neural recall \u2014 "
            "focus on each one and let the stored value surface:\n"
        )
        for field in fields:
            scaffold_text += f"  \u2022 {field}\n"

        msgs.append({"role": "user", "content": scaffold_text.rstrip()})

        preceding_tokens = self._count_preceding_tokens(msgs[:1])
        scaffold_tokens = self._count_text_tokens(scaffold_text)
        scaffold_positions = list(range(
            preceding_tokens, preceding_tokens + scaffold_tokens,
        ))

        # --- Assistant priming (KL #317 SCAFFOLD_V2_PRIME) ---
        msgs.append({"role": "assistant", "content": _SCAFFOLD_V2_PRIME_TEXT})

        # --- Optional conversation history ---
        if history:
            for msg in history:
                role = msg.get("role", "user")
                content = msg.get("content") or ""
                if role not in ("user", "assistant") or not content.strip():
                    continue
                from nls.runtime.response_cleanup import strip_nls_artifacts

                content = strip_nls_artifacts(content)
                if content:
                    msgs.append({"role": role, "content": content})

        # --- User question (clean, no scaffold prefix) ---
        msgs.append({"role": "user", "content": user_input})

        logger.info(
            "[Agent] agent=%s MEMORY_TEST_MODE prompt (verbal_v2): %d msgs "
            "[%s], %d fields, scaffold_positions %d-%d",
            self.agent_id, len(msgs),
            ", ".join(f"{m['role']}:{len(m['content'])}c" for m in msgs),
            len(fields),
            scaffold_positions[0] if scaffold_positions else 0,
            scaffold_positions[-1] if scaffold_positions else 0,
        )
        return msgs, scaffold_positions

    @staticmethod
    def _dedup_scaffold_fields(raw: list[str], cap: int = 20) -> list[str]:
        """Collapse sub-paths and skip ephemeral / noisy fields.

        Rules:
        - If ``A`` and ``A.B`` both exist, keep only ``A`` (the parent
          is sufficient as a retrieval cue; the sub-path leaks its name
          into the scaffold and gets misread as an answer).
        - Skip paths ending in ``.ID``, ``.Token``, or containing
          ``.Credential`` / ``.Status.`` (ephemeral, not memory-worthy).
        - Preserve insertion order, cap to *cap* fields.
        """
        _skip_suffixes = (".ID", ".Token")
        _skip_infixes = (".Credential.", ".Status.")

        # First pass: filter obvious noise
        filtered: list[str] = []
        for p in raw:
            if any(p.endswith(s) for s in _skip_suffixes):
                continue
            if any(s in p for s in _skip_infixes):
                continue
            if p not in filtered:
                filtered.append(p)

        # Second pass: remove children whose parent is already present
        parents: set[str] = set(filtered)
        deduped: list[str] = []
        for p in filtered:
            parts = p.rsplit(".", 1)
            if len(parts) == 2 and parts[0] in parents:
                continue
            deduped.append(p)

        return deduped[:cap]

    def _get_memory_fields_from_registry(self) -> list[str]:
        """Get domain-specific field names from slot registry training data.

        Falls back to DomainDB paths, then to generic fields.
        Used by memory_test_mode to ensure correct scaffold fields
        even when DomainDB is empty.
        """
        raw: list[str] = []

        # Try DomainDB first (may be populated or have backup info)
        if self.domain_db is not None:
            try:
                facts = self._get_scoped_facts(40)
                if facts:
                    for f in facts:
                        path = getattr(f, "domain_path", "")
                        if path and path not in raw:
                            raw.append(path)
            except Exception:
                pass

        # Try reading domain paths from the donor's DomainDB backup
        if not raw:
            backup_path = self.agent_dir / "knowledge.db.bak"
            if backup_path.exists():
                try:
                    import sqlite3
                    conn = sqlite3.connect(str(backup_path))
                    rows = conn.execute(
                        "SELECT DISTINCT domain_path FROM facts "
                        "ORDER BY domain_path"
                    ).fetchall()
                    conn.close()
                    raw = [r[0] for r in rows if r[0]]
                except Exception as exc:
                    logger.warning(
                        "[Agent] agent=%s: backup DomainDB read failed: %s",
                        self.agent_id, exc,
                    )

        if raw:
            fields = self._dedup_scaffold_fields(raw)
            logger.info(
                "[Agent] agent=%s: scaffold fields %d raw → %d deduped",
                self.agent_id, len(raw), len(fields),
            )
            return fields

        logger.info(
            "[Agent] agent=%s: falling back to generic memory fields "
            "(no DomainDB or backup)",
            self.agent_id,
        )
        return [
            "Agent.Name", "User.Name", "User.Location",
            "Project.Name", "Project.Stack.Backend",
            "Project.Stack.Frontend", "Project.Stack.Database",
        ]

    def _build_system_prompt(self) -> str:
        """Build the agent system prompt.

        When trained memory experts exist, uses the winning config pattern:
        memory cortex framing FIRST (dominant), then identity, then tools.
        This matches stress_test_winner.py / stress_test_agentic_v5.py.
        """
        if self._has_trained_memory():
            return self._build_memory_system_prompt()
        return self._build_base_system_prompt()

    def _ensure_cryptex_populated(self) -> None:
        """Populate Cryptex rings with identity + behavioral slots on first call."""
        wm = self.working_memory
        if wm is None or not hasattr(wm, "populate_genesis_identity"):
            return
        try:
            if not getattr(self, "_genesis_populated", False):
                today = datetime.now().strftime("%A, %B %d, %Y")
                tool_dir = self._get_tool_directory()
                wm.populate_genesis_identity(
                    agent_name=self.agent_name or "",
                    enabled_tools_list=tool_dir,
                    today_date=today,
                )
                wm.populate_behavioral_defaults()
                self._genesis_populated = True
                logger.info(
                    "[Agent] agent=%s Cryptex genesis slots populated", self.agent_id,
                )
            # Idempotent — picks up new profile slots on existing agents.
            wm.populate_agentic_supplement()
            self.sync_job_trust()
        except Exception as exc:
            logger.warning(
                "[Agent] agent=%s Cryptex population failed: %s", self.agent_id, exc,
            )

    def sync_job_trust(self, squad: Any | None = None) -> int:
        """Load job.json/trust.json and squad context into Cryptex SYSTEM slots."""
        wm = self.working_memory
        if wm is None:
            return 0
        from nls.runtime.job_trust import (
            load_job,
            load_trust,
            sync_job_trust_to_cryptex,
        )

        job = load_job(self.agent_dir)
        trust = load_trust(self.agent_dir)
        squad_ctx = ""
        try:
            from server.main import app as _app

            sm = getattr(_app.state, "squad_manager", None)
            if sm is not None:
                squad_ctx = sm.build_squad_context_block(self.agent_id)
        except Exception:
            pass
        if squad is not None and not squad_ctx:
            lead = getattr(squad, "lead_agent_id", "")
            name = getattr(squad, "name", "")
            sid = getattr(squad, "id", "")
            if self.agent_id == lead:
                squad_ctx = f"SQUAD LEAD: You lead squad '{name}' ({sid})."
            else:
                squad_ctx = f"SQUAD MEMBER: Squad '{name}' ({sid}). Lead: {lead}"
        n = sync_job_trust_to_cryptex(
            wm,
            job=job,
            trust=trust,
            squad_context=squad_ctx,
        )
        return n

    def _build_base_system_prompt(self) -> str:
        """Standard system prompt (no trained memory experts).

        V5: condensed identity + tool directory + agentic supplement.
        All behavioral instructions live here — nothing in user messages.
        """
        inference = self.config.get("inference", {})
        prompt = inference.get(
            "system_prompt_v5",
            inference.get("system_prompt", "You are a helpful AI assistant."),
        )

        prompt = apply_name_prompt_placeholders(prompt, self.agent_name or "")

        today = datetime.now().strftime("%A, %B %d, %Y")
        prompt = prompt.replace("{today_date}", today)
        if today not in prompt:
            prompt += f"\n\nToday's date is {today}."

        tool_dir = self._get_tool_directory()
        if "{enabled_tools_list}" in prompt:
            prompt = prompt.replace("{enabled_tools_list}", tool_dir)
        elif tool_dir:
            prompt += f"\n\nAvailable tools: {tool_dir}"

        # Behavioral directives now live on Cryptex rings (populated by
        # populate_agentic_supplement) and rendered by compose_context().
        # Only add a minimal bootstrap supplement as fallback for the
        # first iteration before compose_context() replaces this message.
        from nls.agentic.types import _AGENTIC_SYSTEM_SUPPLEMENT
        prompt += f"\n\n{_AGENTIC_SYSTEM_SUPPLEMENT}"

        prompt += NLS_SIGNAL_TOOL_TEXT
        return prompt

    def build_composed_context(
        self,
        render_mode: str = "chat",
        token_budget: int = 55_000,
        state: dict[str, Any] | None = None,
    ) -> list[dict[str, str]] | None:
        """Build context via Cryptex compose_context() if available.

        Returns None if the working memory doesn't support composition
        (backward compat: falls back to legacy _build_system_prompt).
        """
        wm = self.working_memory
        if wm is None or not hasattr(wm, "compose_context"):
            return None

        self._ensure_cryptex_populated()

        try:
            messages = wm.compose_context(
                render_mode=render_mode,
                token_budget=token_budget,
                state=state,
            )
            if messages:
                messages[-1]["content"] += NLS_SIGNAL_TOOL_TEXT
            return messages
        except Exception as exc:
            logger.warning(
                "[Agent] agent=%s compose_context failed, falling back: %s",
                self.agent_id, exc,
            )
            return None

    def _build_memory_system_prompt(self) -> str:
        """Winning config system prompt — memory cortex framing FIRST.

        Structure (stress_test_winner.py / stress_test_agentic_v5.py):
          1. Agent identity (short — just name)
          2. Memory cortex intro + introspective recall process
          3. Dynamic field list with signal strengths from DomainDB
          4. Memory cortex footer (trust your intuition)
          5. NLS signal tool description
          6. Date
        """
        if self.agent_name:
            prompt = (
                f"You are {self.agent_name}, a personal AI assistant with an "
                "integrated neural memory cortex.\n"
                "The human chose this name when creating you. Do not ask what "
                "they would like to call you.\n\n"
            )
        else:
            prompt = (
                "You are a personal AI assistant with an integrated neural memory cortex.\n"
                "You do NOT have a name yet. When greeting the user for the first time, "
                "ask what they would like to call you.\n\n"
            )
        prompt += _MEMORY_CORTEX_INTRO

        fields = self._get_memory_fields()
        for field in fields:
            prompt += f"  - {field}: encoded\n"

        prompt += _MEMORY_CORTEX_FOOTER

        prompt += NLS_SIGNAL_TOOL_TEXT

        tool_dir = self._get_tool_directory()
        if tool_dir:
            prompt += f"\n\nAvailable tools: {tool_dir}"

        today = datetime.now().strftime("%A, %B %d, %Y")
        prompt += f"\n\nToday's date is {today}."

        return prompt

    def _build_passive_dream_system_prompt(self) -> str:
        """System prompt for passive DMN dreams (single text generate, no tool runner).

        The normal chat prompt includes tool directories and the V5 agentic supplement
        ("you MUST use tools").  Passive dreams do not execute tools, so that
        combination produces spurious ``<tool_call>`` / todo XML in dream text.
        """
        dmn_note = (
            "\n\n[DMN passive daydream] This is private inner reflection, not a live "
            "chat turn. Write only prose and any [LEARN:Domain.Path|insight] tags "
            "the user message asks for. Do not emit tool calls, <tool_call> blocks, "
            "```tool_call``` fences, or pretend to invoke APIs — tools are "
            "unavailable in this mode."
        )
        if self._has_trained_memory():
            name = self.agent_name or "a personal AI assistant"
            prompt = (
                f"You are {name}, with an integrated "
                "neural memory cortex.\n\n"
            )
            prompt += _MEMORY_CORTEX_INTRO
            fields = self._get_memory_fields()
            for field in fields:
                prompt += f"  - {field}: encoded\n"
            prompt += _MEMORY_CORTEX_FOOTER
            prompt += NLS_SIGNAL_TOOL_TEXT
            today = datetime.now().strftime("%A, %B %d, %Y")
            prompt += f"\n\nToday's date is {today}."
            return prompt + dmn_note

        inference = self.config.get("inference", {})
        prompt = inference.get(
            "system_prompt_v5",
            inference.get("system_prompt", "You are a helpful AI assistant."),
        )
        prompt = apply_name_prompt_placeholders(prompt, self.agent_name or "")
        today = datetime.now().strftime("%A, %B %d, %Y")
        prompt = prompt.replace("{today_date}", today)
        if today not in prompt:
            prompt += f"\n\nToday's date is {today}."
        if "{enabled_tools_list}" in prompt:
            prompt = prompt.replace("{enabled_tools_list}", "")
        prompt += NLS_SIGNAL_TOOL_TEXT
        return prompt + dmn_note

    def _get_tool_directory(self) -> str:
        """Build the tool directory string for the system prompt.

        Tools are eagerly initialized in ``__init__`` so this always
        returns the real tool directory with full descriptions.
        """
        if self._agent_tools:
            try:
                from nls.tools.agent_tools.base import tools_to_directory
                return tools_to_directory(self._agent_tools)
            except Exception:
                pass
        return ""

    def _get_enabled_skills(self) -> list[str]:
        """Read enabled skills from agent_dir/enabled_skills.json."""
        from nls.tools.skill_manager import get_enabled_skills
        return get_enabled_skills(self.agent_dir)

    def _initialize_tools(self) -> None:
        """Eagerly create the full v2 tool set.

        Called once during ``__init__`` so that ``_agent_tools`` and
        ``_openai_tools`` are always populated.  This ensures:

        1. The system prompt always lists real tool names + descriptions.
        2. Adapter injection (Google Workspace, email, etc.) can
           replace/append at any time — ``_agent_tools`` is never None.
        3. The agentic loop never encounters uninitialized tools.
        """
        from nls.tools.tool_setup import setup_tools

        skill_loader = None
        try:
            from server.main import app as _app
            skill_loader = getattr(_app.state, "skill_loader", None)
        except Exception:
            pass

        enabled_skills = self._get_enabled_skills()

        try:
            self._agent_tools, self._openai_tools, self._scheduler_manager, self._team_manager = (
                setup_tools(
                    self.agent_id, self.agent_dir, self,
                    self.config, skill_loader=skill_loader,
                    ans=self.ans, calibrator=self.calibrator,
                    working_memory=self.working_memory,
                    dual_wm=self.dual_wm,
                    enabled_skills=enabled_skills,
                )
            )
            self._sync_adapter_tools()
            self._restore_adapter_tools()
            self._populate_skills_ring()
            self._populate_channels_ring()
            self._wire_bash_process_tracking()
            self.sync_squad_tools()
            logger.info(
                "[Agent] agent=%s: initialized %d tools (%d openai schemas)",
                self.agent_id,
                len(self._agent_tools or []),
                len(self._openai_tools or []),
            )
        except Exception as exc:
            logger.warning(
                "[Agent] agent=%s: tool initialization failed (%s) "
                "— will retry on first agentic entry",
                self.agent_id, exc,
            )
            self._agent_tools = None
            self._openai_tools = None

    _SQUAD_TOOL_NAMES = frozenset({
        "squad", "squad_escalate", "squad_message", "squad_report_done",
    })
    _SQUAD_SETUP_TOOL_NAMES = frozenset({"squad_setup"})
    _SET_JOB_TOOL_NAMES = frozenset({"set_job"})

    def sync_squad_tools(self) -> None:
        """Add/remove squad or bootstrap tools when membership changes."""
        if self._agent_tools is None:
            try:
                self._initialize_tools()
            except Exception:
                return
        if self._agent_tools is None:
            return
        in_squad = False
        sm = None
        try:
            from server.main import app as _app

            sm = getattr(_app.state, "squad_manager", None)
            if sm is not None:
                in_squad = sm.get_squad_for_agent(self.agent_id) is not None
        except Exception:
            pass

        has_squad_tools = any(
            getattr(t, "name", "") in self._SQUAD_TOOL_NAMES
            for t in self._agent_tools
        )
        has_setup = any(
            getattr(t, "name", "") in self._SQUAD_SETUP_TOOL_NAMES
            for t in self._agent_tools
        )
        has_set_job = any(
            getattr(t, "name", "") in self._SET_JOB_TOOL_NAMES
            for t in self._agent_tools
        )

        if in_squad:
            if has_setup:
                self._remove_tools_by_name(self._SQUAD_SETUP_TOOL_NAMES)
            if has_set_job:
                self._remove_tools_by_name(self._SET_JOB_TOOL_NAMES)
            if not has_squad_tools:
                self._register_squad_tools()
        else:
            if has_squad_tools:
                self._remove_tools_by_name(self._SQUAD_TOOL_NAMES)
            if not has_setup and sm is not None:
                self._register_squad_setup_tool(sm)
            if not has_set_job:
                self._register_set_job_tool()

    def _remove_tools_by_name(self, names: frozenset[str]) -> None:
        if self._agent_tools is None:
            return
        self._agent_tools = [
            t for t in self._agent_tools
            if getattr(t, "name", "") not in names
        ]
        try:
            from nls.tools.tool_setup import _populate_tools_ring

            _populate_tools_ring(self.working_memory, self._agent_tools)
            self.refresh_tools()
            logger.info("Agent %s: removed tools %s", self.agent_id, sorted(names))
        except Exception as exc:
            logger.warning(
                "Agent %s: tool removal failed: %s",
                self.agent_id, exc,
            )

    def _register_squad_setup_tool(self, sm: Any) -> None:
        if self._agent_tools is None:
            return
        if any(getattr(t, "name", "") == "squad_setup" for t in self._agent_tools):
            return
        try:
            from nls.tools.agent_tools.squad import SquadSetupTool
            from nls.tools.tool_setup import _populate_tools_ring

            self._agent_tools.append(SquadSetupTool(sm, self.agent_id))
            _populate_tools_ring(self.working_memory, self._agent_tools)
            self.refresh_tools()
            logger.info("Agent %s: squad_setup tool registered", self.agent_id)
        except Exception as exc:
            logger.warning("Agent %s: squad_setup skipped: %s", self.agent_id, exc)

    def _register_set_job_tool(self) -> None:
        if self._agent_tools is None:
            return
        if any(getattr(t, "name", "") == "set_job" for t in self._agent_tools):
            return
        try:
            from nls.tools.agent_tools.set_job import create_set_job_tool
            from nls.tools.tool_setup import _populate_tools_ring

            self._agent_tools.append(
                create_set_job_tool(self.agent_dir, self.agent_id),
            )
            _populate_tools_ring(self.working_memory, self._agent_tools)
            self.refresh_tools()
            logger.info("Agent %s: set_job tool registered", self.agent_id)
        except Exception as exc:
            logger.warning("Agent %s: set_job skipped: %s", self.agent_id, exc)

    def _register_squad_tools(self) -> None:
        """Attach squad tools when this agent belongs to a squad."""
        if self._agent_tools is None:
            return
        names = {t.name for t in self._agent_tools}
        if "squad" in names:
            return
        try:
            from server.main import app as _app

            sm = getattr(_app.state, "squad_manager", None)
            if sm is None or sm.get_squad_for_agent(self.agent_id) is None:
                return
            from nls.tools.agent_tools.squad import (
                SquadEscalateTool,
                SquadMessageTool,
                SquadReportDoneTool,
                SquadTool,
            )
            from nls.tools.tool_setup import _populate_tools_ring

            self._agent_tools.extend([
                SquadTool(sm, self.agent_id),
                SquadEscalateTool(sm, self.agent_id),
                SquadMessageTool(sm, self.agent_id),
                SquadReportDoneTool(sm, self.agent_id),
            ])
            _populate_tools_ring(self.working_memory, self._agent_tools)
            self.refresh_tools()
            logger.info("Agent %s: squad tools registered", self.agent_id)
        except Exception as exc:
            logger.debug("Agent %s: squad tools skipped: %s", self.agent_id, exc)

    def _sync_adapter_tools(self) -> None:
        """Deprecated — replaced by refresh_tools(). Kept as no-op for compatibility."""
        pass

    def _restore_adapter_tools(self) -> None:
        """Re-inject tools from connected skill adapters after runtime init.

        The Google Workspace adapter calls _inject_tools(agent_id) during
        server startup, but at that point the agent runtime may not yet be
        loaded (get_runtime returns None), so the injection silently fails.
        Calling this after _initialize_tools ensures any already-connected
        adapters (GW, email, etc.) get their tools wired in immediately.
        """
        try:
            from server.main import app as _app
            skill_loader = getattr(_app.state, "skill_loader", None)
            if skill_loader is None:
                return
            for skill_name, sk in skill_loader.skills.items():
                adapter = getattr(getattr(sk, "context", None), "adapter", None)
                if adapter is None:
                    continue
                inject_fn = getattr(adapter, "_inject_tools", None)
                if callable(inject_fn):
                    try:
                        inject_fn(self.agent_id)
                        logger.debug(
                            "[Agent] agent=%s: restored adapter tools from skill '%s'",
                            self.agent_id, skill_name,
                        )
                    except Exception as _exc:
                        logger.debug(
                            "[Agent] agent=%s: adapter restore skipped for '%s': %s",
                            self.agent_id, skill_name, _exc,
                        )
        except Exception:
            pass

    def _populate_skills_ring(self) -> None:
        """Populate the cryptex Skills ring (Ring 10) from loaded skills."""
        try:
            from nls.brain.cryptex import CryptexMemory, RING_SKILLS
        except ImportError:
            return
        wm = getattr(self, "working_memory", None)
        if not isinstance(wm, CryptexMemory):
            return
        ring = wm.get_ring(RING_SKILLS)
        if ring is None:
            return

        skill_loader = None
        try:
            from server.main import app as _app
            skill_loader = getattr(_app.state, "skill_loader", None)
        except Exception:
            return

        if skill_loader is None:
            return

        enabled = self._get_enabled_skills()
        enabled_set = set(enabled) if enabled != ["*"] else None
        seen_names: set[str] = set()

        for name, sk in skill_loader.skills.items():
            if sk.status != "loaded" or not sk.meta:
                continue
            schema = getattr(sk.meta, "config_schema", None) or []
            if not schema:
                continue
            from nls.skills_setup_policy import (
                bundled_skill_ring_guidance,
                is_pre_shipped_channel_skill,
            )

            is_enabled = enabled_set is None or name in enabled_set
            agent_installed = (sk.path / ".creator").is_file()
            configured = False
            if is_pre_shipped_channel_skill(name):
                platform = name.replace("-channel", "")
                configured = self._channel_is_connected(platform)
            headline, guidance = bundled_skill_ring_guidance(
                name,
                sk.meta.description or "",
                enabled=is_enabled,
                config_schema=schema,
                agent_installed=agent_installed,
                configured=configured,
            )
            salience = 0.7 if is_enabled else 0.85
            if agent_installed and not is_pre_shipped_channel_skill(name):
                salience = 0.9
            domain_area = skill_loader.get_skill_domain(name, sk.meta.description or "")
            from nls.brain.working_memory import WMSlot

            ring.upsert_slot(
                domain=f"skill.{name}",
                content=headline,
                slot_type="skill",
                salience=salience,
                source="skill_loader",
                position=domain_area,
                metadata={
                    "full_instructions": guidance,
                    "skill_name": name,
                    "configurable": True,
                    "enabled": is_enabled,
                    "agent_installed": agent_installed,
                    "pre_shipped": is_pre_shipped_channel_skill(name),
                },
            )
            seen_names.add(name)

        skill_tuples = skill_loader.instructions_for(enabled)

        for name, description, instructions in skill_tuples:
            if name in seen_names:
                continue
            domain_area = skill_loader.get_skill_domain(name, description)
            from nls.brain.working_memory import WMSlot
            ring.upsert_slot(
                domain=f"skill.{name}",
                content=f"{name}: {description}",
                slot_type="skill",
                salience=0.7,
                source="skill_loader",
                position=domain_area,
                metadata={"full_instructions": instructions, "skill_name": name},
            )
            seen_names.add(name)

        logger.info(
            "[Agent] agent=%s: populated skills ring with %d skills across %s",
            self.agent_id, len(seen_names), ring.position_ids,
        )

    _CHANNEL_SKILL_DIRS: dict[str, str] = {
        "whatsapp": "whatsapp-channel",
        "telegram": "telegram-channel",
        "email": "email-channel",
        "discord": "discord-channel",
        "slack": "slack-channel",
    }

    def _load_channel_agent_config(self, channel: str) -> dict | None:
        """Load per-agent channel skill config from data/skills/{skill}/agents/{id}.json."""
        from pathlib import Path

        from nls.runtime.channel_agent_config import (
            data_root_from_agent_dir,
            load_agent_channel_config,
        )

        if not getattr(self, "agent_dir", None):
            return None
        return load_agent_channel_config(
            data_root_from_agent_dir(self.agent_dir),
            self.agent_id,
            channel,
        )

    @staticmethod
    def _discord_config_summary(cfg: dict) -> str:
        scoped = cfg.get("scoped_channels") or {}
        guilds = scoped.get("guilds") or {}
        channels = scoped.get("channels") or {}
        guild_names = [
            str(g.get("name", "")).strip()
            for g in guilds.values()
            if isinstance(g, dict) and g.get("name")
        ]
        channel_names = sorted(
            str(c.get("name", "")).strip()
            for c in channels.values()
            if isinstance(c, dict) and c.get("effective_enabled") and c.get("name")
        )
        parts: list[str] = []
        if guild_names:
            parts.append(f"guild: {', '.join(guild_names[:2])}")
        if channel_names:
            shown = ", ".join(channel_names[:10])
            if len(channel_names) > 10:
                shown += f", +{len(channel_names) - 10} more"
            parts.append(f"channels: {shown}")
        return "; ".join(parts) if parts else "bot connected"

    def _channel_is_connected(self, channel: str) -> bool:
        """True when an outbound channel skill is paired (not just installed)."""
        skill_dir = self._CHANNEL_SKILL_DIRS.get(channel)
        if not skill_dir:
            return True
        if not getattr(self, "agent_dir", None):
            return False
        cfg = self._load_channel_agent_config(channel)
        if not cfg:
            return False
        if channel == "whatsapp":
            return bool(str(cfg.get("linked_phone", "")).strip())
        if channel == "telegram":
            return bool(
                str(cfg.get("bot_token", "")).strip()
                or str(cfg.get("linked_id", "")).strip()
            )
        if channel == "email":
            return bool(str(cfg.get("connected_email", "")).strip())
        if channel == "discord":
            return bool(
                cfg.get("enabled")
                and str(cfg.get("bot_token", "")).strip()
            )
        if channel == "slack":
            return bool(
                cfg.get("enabled")
                and str(cfg.get("bot_token", "")).strip()
            )
        return False

    def _channel_status_for_triage(self) -> str:
        """Factual connected-channel block for turn triage (not intent heuristics)."""
        lines: list[str] = []
        for channel in ("discord", "slack", "telegram", "whatsapp", "email"):
            if not self._channel_is_connected(channel):
                continue
            extra = ""
            if channel == "discord":
                cfg = self._load_channel_agent_config("discord") or {}
                extra = f" ({self._discord_config_summary(cfg)})"
            lines.append(
                f"- {channel}: CONNECTED{extra} — credentials already stored; "
                "do not ask the user for bot token or call *_setup"
            )
        if not lines:
            base = ""
        else:
            base = (
                "INSTALLED CHANNEL STATUS (factual — trust this over assumptions):\n"
                + "\n".join(lines)
            )
        try:
            from server.main import app as _app
            from nls.runtime.fleet_channel_topology import (
                build_fleet_topology_snapshot,
                render_topology_guidance,
            )

            _reg = getattr(_app.state, "squad_registry", None)
            _squad = _reg.get_for_agent(self.agent_id) if _reg else None
            _snap = build_fleet_topology_snapshot(
                agent_id=self.agent_id,
                agent_dir=self.agent_dir,
                app=_app,
                squad=_squad,
            )
            _topo = ""
            if _squad is not None and _snap.mode != "none":
                _topo = render_topology_guidance(_snap, compact=bool(base))
            if _topo:
                return f"{base}\n\n{_topo}".strip() if base else _topo
        except Exception:
            pass
        return base

    def _triage_continuation_context(
        self,
        user_input: str,
        *,
        history: list[dict] | None = None,
    ) -> str:
        from nls.agentic.plan_triage_policy import build_plan_triage_continuation_block
        from nls.agentic.profile_guard_policy import build_triage_continuation_context

        plan_block = build_plan_triage_continuation_block(
            self._plan_store(),
            self._team_manager,
        )
        base = build_triage_continuation_context(
            user_input,
            history=history,
            working_memory=self._ring_working_memory(),
        )
        if plan_block and base:
            return f"{plan_block}\n\n{base}"
        return plan_block or base

    def _plan_store(self) -> Any | None:
        for _t in self._agent_tools or []:
            if getattr(_t, "name", "") == "plan":
                if hasattr(_t, "get_store"):
                    try:
                        return _t.get_store()
                    except Exception:
                        pass
                return getattr(_t, "_store", None)
        return None

    def _upsert_fleet_topology_ring(self, ring: Any) -> None:
        try:
            from server.main import app as _app
            from nls.runtime.fleet_channel_topology import (
                build_fleet_topology_snapshot,
                render_topology_guidance,
            )

            _reg = getattr(_app.state, "squad_registry", None)
            _squad = _reg.get_for_agent(self.agent_id) if _reg else None
            if _squad is None:
                return
            snap = build_fleet_topology_snapshot(
                agent_id=self.agent_id,
                agent_dir=self.agent_dir,
                app=_app,
                squad=_squad,
            )
            content = render_topology_guidance(snap)
            if not content.strip():
                return
            ring.upsert_slot(
                domain="channel.fleet_topology",
                content=content,
                slot_type="fact",
                salience=0.92,
                source="channel_registration",
                position="communication",
            )
        except Exception:
            logger.debug(
                "Agent %s: fleet topology ring upsert failed",
                self.agent_id, exc_info=True,
            )

    def _channel_skill_enabled(self, channel: str) -> bool:
        """True when the bundled channel skill is enabled for this agent."""
        skill_name = self._CHANNEL_SKILL_DIRS.get(channel)
        if not skill_name:
            return False
        enabled = self._get_enabled_skills()
        if enabled == ["*"]:
            return True
        return skill_name in enabled

    def _refresh_channel_awareness(self) -> None:
        """Re-read channel configs from disk into Cryptex channels + skills rings."""
        try:
            self._populate_channels_ring()
        except Exception:
            logger.debug(
                "Agent %s: channels ring refresh failed",
                self.agent_id, exc_info=True,
            )
        try:
            self._populate_skills_ring()
        except Exception:
            logger.debug(
                "Agent %s: skills ring refresh failed",
                self.agent_id, exc_info=True,
            )

    def _populate_channels_ring(self) -> None:
        """Populate the Cryptex Channels ring from registered channel adapters."""
        try:
            from nls.brain.cryptex import CryptexMemory, RING_CHANNELS
        except ImportError:
            return
        wm = getattr(self, "working_memory", None)
        if not isinstance(wm, CryptexMemory):
            return
        ring = wm.get_ring(RING_CHANNELS)
        if ring is None:
            return

        channel_mgr = getattr(self, "channel_manager", None)
        if channel_mgr is None:
            try:
                from server.main import app as _app
                channel_mgr = getattr(_app.state, "channel_manager", None)
            except Exception:
                pass

        tool_names = {getattr(t, "name", "") for t in (self._agent_tools or [])}
        has_whatsapp_tool = (
            "whatsapp_send" in tool_names
            or self._channel_skill_enabled("whatsapp")
        )
        has_whatsapp = has_whatsapp_tool and self._channel_is_connected("whatsapp")
        has_telegram_tool = (
            "telegram_send" in tool_names
            or self._channel_skill_enabled("telegram")
        )
        has_telegram = has_telegram_tool and self._channel_is_connected("telegram")
        has_email_tool = (
            "email_send" in tool_names
            or self._channel_skill_enabled("email")
        )
        has_email = has_email_tool and self._channel_is_connected("email")
        has_calendar = any(t in tool_names for t in (
            "calendar_list", "calendar_create", "calendar_update",
        ))

        if has_whatsapp_tool and not has_whatsapp:
            ring.upsert_slot(
                domain="channel.whatsapp",
                content=(
                    "WhatsApp: NOT CONNECTED (skill enabled but not paired). "
                    "Do NOT mention WhatsApp in status updates and do NOT tell the "
                    "user you sent WhatsApp messages. Use communicate() in chat."
                ),
                slot_type="fact",
                salience=0.5,
                source="channel_registration",
                position="communication",
            )
        elif has_whatsapp:
            ring.upsert_slot(
                domain="channel.whatsapp",
                content=(
                    "WhatsApp: CONNECTED. Use whatsapp_send(phone=\"+...\", text=\"...\") "
                    "to send messages. Parameter names: phone (E.164), text (message body). "
                    "Look up contacts first with contacts tool."
                ),
                slot_type="fact",
                salience=0.9,
                source="channel_registration",
                position="communication",
            )
        if has_telegram_tool and not has_telegram:
            ring.upsert_slot(
                domain="channel.telegram",
                content=(
                    "Telegram: NOT CONNECTED (skill enabled but not configured). "
                    "Do NOT mention Telegram in status updates and do NOT claim "
                    "you sent Telegram messages. Use communicate() in chat."
                ),
                slot_type="fact",
                salience=0.5,
                source="channel_registration",
                position="communication",
            )
        elif has_telegram:
            ring.upsert_slot(
                domain="channel.telegram",
                content=(
                    "Telegram: CONNECTED. Use telegram_send to send messages. "
                    "Look up contacts first when needed."
                ),
                slot_type="fact",
                salience=0.9,
                source="channel_registration",
                position="communication",
            )
        if has_email_tool and not has_email:
            ring.upsert_slot(
                domain="channel.email",
                content=(
                    "Email: NOT CONNECTED (skill enabled but no mailbox linked). "
                    "Do NOT mention email delivery in status updates and do NOT "
                    "claim you sent email. Use communicate() in chat."
                ),
                slot_type="fact",
                salience=0.5,
                source="channel_registration",
                position="communication",
            )
        elif has_email:
            ring.upsert_slot(
                domain="channel.email",
                content=(
                    "Email: CONNECTED. Use email_send to send emails when the "
                    "user requested email delivery."
                ),
                slot_type="fact",
                salience=0.9,
                source="channel_registration",
                position="communication",
            )
        if has_calendar:
            ring.upsert_slot(
                domain="channel.google_calendar",
                content=(
                    "Google Calendar: ACTIVE. "
                    "Use calendar_list() to view events, "
                    "calendar_create() to add events, "
                    "calendar_update() to modify events."
                ),
                slot_type="fact",
                salience=0.9,
                source="channel_registration",
                position="communication",
            )

        has_discord_tool = (
            "discord_send" in tool_names
            or self._channel_skill_enabled("discord")
        )
        has_discord = has_discord_tool and self._channel_is_connected("discord")
        has_slack_tool = (
            "slack_send" in tool_names
            or self._channel_skill_enabled("slack")
        )
        has_slack = has_slack_tool and self._channel_is_connected("slack")

        if has_discord_tool and not has_discord:
            ring.upsert_slot(
                domain="channel.discord",
                content=(
                    "Discord: NOT CONNECTED (skill enabled but bot token not configured). "
                    "Use skill_configure(skill_name='discord-channel') or ask_user for "
                    "bot token only when the user has not linked Discord in Babo yet."
                ),
                slot_type="fact",
                salience=0.5,
                source="channel_registration",
                position="communication",
            )
        elif has_discord:
            _dcfg = self._load_channel_agent_config("discord") or {}
            _dsummary = self._discord_config_summary(_dcfg)
            ring.upsert_slot(
                domain="channel.discord",
                content=(
                    f"Discord: CONNECTED ({_dsummary}). "
                    "Bot token is already configured in Babo — do NOT ask the user "
                    "for a token and do NOT call discord_setup. "
                    "Use discord_send / slack_send for messages; channel_manage(channel=..., "
                    "action=...) for sync/scope/admin; channel_inspect for read-only detail. "
                    "NEVER bash/python/curl with bot tokens."
                ),
                slot_type="fact",
                salience=0.95,
                source="channel_registration",
                position="communication",
            )
        if has_slack_tool and not has_slack:
            ring.upsert_slot(
                domain="channel.slack",
                content=(
                    "Slack: NOT CONNECTED (skill enabled but not configured). "
                    "Configure via skill_configure(skill_name='slack-channel') when needed."
                ),
                slot_type="fact",
                salience=0.5,
                source="channel_registration",
                position="communication",
            )
        elif has_slack:
            ring.upsert_slot(
                domain="channel.slack",
                content=(
                    "Slack: CONNECTED. Bot token already configured — do NOT ask for "
                    "credentials again. Use slack_send for messages; "
                    "channel_inspect(action='get', channel='slack') for scope detail."
                ),
                slot_type="fact",
                salience=0.9,
                source="channel_registration",
                position="communication",
            )

        self._upsert_fleet_topology_ring(ring)

        if (
            has_whatsapp or has_telegram or has_email or has_calendar
            or has_discord or has_slack
        ):
            logger.info(
                "[Agent] agent=%s: populated channels ring "
                "(whatsapp=%s, telegram=%s, email=%s, calendar=%s, "
                "discord=%s, slack=%s)",
                self.agent_id,
                has_whatsapp,
                has_telegram,
                has_email,
                has_calendar,
                has_discord,
                has_slack,
            )

    def _has_trained_memory(self) -> bool:
        """Weight expert slots are not used in the product runtime."""
        return False

    def _has_learned_facts(self) -> bool:
        """Check whether DomainDB has any genuinely learned facts.

        Returns False for fresh agents where all facts are just genesis
        placeholders (block_height=0, values like 'Awaiting...').
        Injecting placeholders as context confuses the model.
        """
        if self.domain_db is None:
            return False
        try:
            facts = self._get_scoped_facts(100)
            for f in facts:
                bh = getattr(f, "block_height", 0) or 0
                if bh > 0:
                    return True
                val = getattr(f, "current_value", "") or ""
                if val and "awaiting" not in val.lower():
                    fc = getattr(f, "flip_count", 0) or 0
                    if fc > 0:
                        return True
        except Exception:
            pass
        return False

    def _build_scaffold_text(
        self,
        msgs: list[dict[str, str]],
        extra_preceding_text: str = "",
    ) -> tuple[str, list[int] | None]:
        """Build memory scaffold text and compute hippocampus positions.

        Returns ``(scaffold_text, positions)`` — the scaffold is NOT
        appended to *msgs*; the caller merges it into the user message.
        *extra_preceding_text* accounts for context already placed before
        the scaffold inside the user message (facts, WM, etc.).
        """
        if self._has_trained_memory():
            return self._build_winning_scaffold_text(msgs, extra_preceding_text)
        return self._build_basic_fact_scaffold_text(msgs, extra_preceding_text)

    def _count_preceding_tokens(
        self,
        msgs: list[dict[str, str]],
        extra_text: str = "",
    ) -> int:
        """Count tokens preceding the scaffold position.

        Uses vLLM's /tokenize endpoint for exact counts (includes chat
        template overhead).  Falls back to word×1.3 heuristic if vLLM
        is unreachable or returns 0.
        """
        cached = getattr(self, "_token_count_cache", None)
        if cached is None:
            self._token_count_cache: dict[int, int] = {}
            cached = self._token_count_cache

        cache_key = hash(
            tuple((m.get("role", ""), m.get("content", "")) for m in msgs)
            + (extra_text,)
        )
        if cache_key in cached:
            return cached[cache_key]

        vllm_count = 0
        _vllm, _adapter = self.inference_pipeline()
        if _vllm is not None:
            try:
                import httpx as _httpx
                base = _vllm.base_url
                model = _adapter or _vllm.default_model
                tok_msgs = list(msgs)
                if extra_text:
                    tok_msgs.append({"role": "user", "content": extra_text})
                resp = _httpx.post(
                    f"{base}/tokenize",
                    json={"model": model, "messages": tok_msgs},
                    timeout=5.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    vllm_count = data.get("count", 0) or len(data.get("tokens", []))
            except Exception:
                vllm_count = 0

        if vllm_count > 0:
            cached[cache_key] = vllm_count
            return vllm_count

        count = 0
        for msg in msgs:
            count += int(len((msg.get("content") or "").split()) * 1.3)
            count += 4  # chat template overhead per message
        if extra_text:
            count += int(len(extra_text.split()) * 1.3)
        cached[cache_key] = count
        return count

    def _count_text_tokens(self, text: str) -> int:
        """Approximate token count for a plain text string.

        Uses the word×1.3 heuristic (no chat template wrapping needed
        since this is for *inner* text fragments, not full messages).
        """
        return max(1, int(len(text.split()) * 1.3))

    def _build_winning_scaffold_text(
        self,
        msgs: list[dict[str, str]],
        extra_preceding_text: str = "",
    ) -> tuple[str, list[int] | None]:
        """Winning config scaffold: field names for neural recall."""
        fields = self._get_memory_fields()

        scaffold_text = (
            "Your memory cortex has been activated for the current user. "
            "The following fields are available for neural recall \u2014 focus on each "
            "one and let the stored value surface:\n"
        )
        for field in fields:
            scaffold_text += f"  \u2022 {field}\n"

        preceding_tokens = self._count_preceding_tokens(msgs, extra_preceding_text)
        scaffold_tokens = self._count_text_tokens(scaffold_text)

        positions = list(range(
            preceding_tokens,
            preceding_tokens + scaffold_tokens,
        ))

        logger.info(
            "[Agent] agent=%s: winning scaffold (%d fields, "
            "positions %d-%d)",
            self.agent_id, len(fields),
            positions[0] if positions else 0,
            positions[-1] if positions else 0,
        )
        return scaffold_text, positions

    def _build_basic_fact_scaffold_text(
        self,
        msgs: list[dict[str, str]],
        extra_preceding_text: str = "",
    ) -> tuple[str, list[int] | None]:
        """Basic DomainDB fact scaffold (pre-training fallback)."""
        if self.domain_db is None:
            return "", None

        try:
            facts = self._get_scoped_facts(20)
        except Exception:
            facts = []

        if not facts:
            return "", None

        parts = []
        for fact in facts:
            key = (
                fact.domain_path.split(".")[-1]
                if hasattr(fact, "domain_path") else ""
            )
            val = (
                fact.current_value
                if hasattr(fact, "current_value") else str(fact)
            )
            if "\n" in val:
                val = val.split("\n")[0]
            if len(val) > 60:
                val = val[:60]
            parts.append(f"{key}: {val}")

        scaffold_text = "[Memory cortex activating] " + " | ".join(parts)

        preceding_tokens = self._count_preceding_tokens(msgs, extra_preceding_text)
        scaffold_tokens = self._count_text_tokens(scaffold_text)
        positions = list(range(preceding_tokens, preceding_tokens + scaffold_tokens))

        return scaffold_text, positions

    def _get_memory_fields(self) -> list[str]:
        """Build the memory field list for the scaffold.

        Pulls domain paths from DomainDB if available, otherwise falls
        back to generic field names derived from slot group domains.
        Applies sub-path deduplication and noise filtering via
        ``_dedup_scaffold_fields``.
        """
        raw: list[str] = []
        if self.domain_db is not None:
            try:
                facts = self._get_scoped_facts(40)
                logger.info(
                    "[Agent] agent=%s: scoped facts returned %d items "
                    "(type=%s)",
                    self.agent_id, len(facts) if facts else 0,
                    type(facts[0]).__name__ if facts else "N/A",
                )
                for fact in facts:
                    path = getattr(fact, "domain_path", "")
                    if path and path not in raw:
                        raw.append(path)
            except Exception as exc:
                logger.warning(
                    "[Agent] agent=%s: _get_memory_fields error: %s",
                    self.agent_id, exc,
                )
        else:
            logger.info(
                "[Agent] agent=%s: domain_db is None in _get_memory_fields",
                self.agent_id,
            )
        if raw:
            fields = self._dedup_scaffold_fields(raw)
            logger.info(
                "[Agent] agent=%s: scaffold %d raw → %d deduped",
                self.agent_id, len(raw), len(fields),
            )
            return fields
        logger.info(
            "[Agent] agent=%s: falling back to generic memory fields",
            self.agent_id,
        )
        return [
            "user.name", "user.preferences", "user.relationships",
        ]


    # ------------------------------------------------------------------
    # 3. Generate — single vLLM call matching stress-test format
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_no_think(messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """Prepend ``/no_think`` to the last user message (Qwen3 soft switch).

        When the template hard-switch ``enable_thinking=False`` is active,
        the model won't produce ``<think>`` blocks but may still dump
        reasoning as visible text.  The ``/no_think`` soft-switch is a
        training-level signal that actually suppresses reasoning behaviour.
        Combining both gives reliable System 1 (fast, direct) responses.
        """
        msgs = [m.copy() for m in messages]
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") == "user":
                content = msgs[i].get("content") or ""
                if not content.startswith("/no_think"):
                    msgs[i]["content"] = f"/no_think\n{content}"
                break
        return msgs

    def _get_babo_cloud_vllm_client(self) -> Any | None:
        if self._babo_cloud_vllm_client is not None:
            return self._babo_cloud_vllm_client
        url = _babo_cloud_inference_url_from_env()
        if not url:
            return None
        from server.services.vllm_client import VLLMInferenceClient

        api_key = os.environ.get("NLS_INFERENCE_API_KEY", "").strip() or None
        hf = os.environ.get("NLS_HF_MODEL", "gpt-4o-mini")
        self._babo_cloud_vllm_client = VLLMInferenceClient(
            base_url=url,
            default_model=hf,
            api_key=api_key,
        )
        logger.info(
            "[Agent] agent=%s Babo Cloud inference relay at %s",
            self.agent_id,
            url,
        )
        return self._babo_cloud_vllm_client

    def _model_served_by_local_vllm(self, model_id: str) -> bool:
        """True when the install-default LAN client serves this model id."""
        if not self.vllm_client or not model_id:
            return False
        mid = model_id.strip()
        local_default = (
            getattr(self.vllm_client, "default_model", "") or ""
        ).strip()
        install_default = os.environ.get("NLS_HF_MODEL", "").strip()
        return mid == local_default or (
            bool(install_default) and mid == install_default
        )

    def _vllm_for_message(
        self, model_override: str | None
    ) -> tuple[Any, str | None]:
        """Pick LAN install-default vs Babo Cloud relay for this agent's model."""
        adapter = (model_override or "").strip() or None
        if adapter and _is_openai_api_model_id(adapter):
            if self._model_served_by_local_vllm(adapter):
                return self.vllm_client, adapter
            cloud = self._get_babo_cloud_vllm_client()
            if cloud is not None:
                return cloud, adapter
            logger.warning(
                "[Agent] agent=%s cloud model %r requested but "
                "Babo Cloud inference relay is not configured",
                self.agent_id,
                adapter,
            )
            return None, adapter
        if self.vllm_client is None:
            return None, adapter
        return self.vllm_client, adapter

    def inference_pipeline(
        self, model_override: str | None = None,
    ) -> tuple[Any, str | None]:
        """Resolve vLLM client + adapter for any turn on this agent."""
        model = self.resolve_orchestrator_model(model_override)
        return self._vllm_for_message(model)

    def inference_available(
        self, model_override: str | None = None,
    ) -> bool:
        """True when a routed inference client exists for this agent."""
        client, _ = self.inference_pipeline(model_override)
        return client is not None

    def resolve_orchestrator_model(
        self, request_override: str | None
    ) -> str | None:
        """Per-request override, then agent session default, else None (install default)."""
        override = (request_override or "").strip() or None
        if override:
            return override
        session = (self.session_orchestrator_model or "").strip()
        return session or None

    def resolve_delegate_adapter(
        self, orchestrator_adapter: str | None
    ) -> str | None:
        """Sub-agent model: lock to orchestrator, global delegate, or session delegate."""
        if self.session_delegate_lock_orchestrator:
            return orchestrator_adapter
        if self.delegate_model:
            return self.delegate_model
        session = (self.session_delegate_model or "").strip()
        return session or orchestrator_adapter

    def update_session_inference(
        self,
        *,
        orchestrator_model: str | None = None,
        delegate_model: str | None = None,
        delegate_lock_orchestrator: bool | None = None,
        clear_orchestrator: bool = False,
        clear_delegate: bool = False,
    ) -> dict[str, Any]:
        """Persist per-agent inference defaults in session_meta.json."""
        if clear_orchestrator:
            self.session_orchestrator_model = None
        elif orchestrator_model is not None:
            val = orchestrator_model.strip()
            self.session_orchestrator_model = val or None

        if clear_delegate:
            self.session_delegate_model = None
        elif delegate_model is not None:
            val = delegate_model.strip()
            self.session_delegate_model = val or None

        if delegate_lock_orchestrator is not None:
            self.session_delegate_lock_orchestrator = delegate_lock_orchestrator

        self.save_state()
        return self.session_inference_snapshot()

    def session_inference_snapshot(self) -> dict[str, Any]:
        return {
            "orchestrator_model": self.session_orchestrator_model,
            "delegate_model": self.session_delegate_model,
            "delegate_lock_orchestrator": self.session_delegate_lock_orchestrator,
        }

    async def generate(
        self,
        messages: list[dict[str, str]],
        xargs: dict[str, Any],
        scaffold_positions: list[int] | None = None,
        *,
        thinking_mode: bool = True,
        model_override: str | None = None,
    ) -> tuple[str, str]:
        """Call vLLM and return (full_text, thinking_text).

        Parameters
        ----------
        thinking_mode : bool
            System 2 (True) enables ``<think>`` chains with 4096 max tokens.
            System 1 (False) disables thinking for fast, direct responses
            with 2048 max tokens.  Also injects the Qwen3 ``/no_think``
            soft-switch to suppress reasoning in visible text.

        Sampling follows Qwen3.5 team recommendations per mode:
        - Thinking/general:  temp=1.0 top_p=0.95 top_k=20 pres_pen=1.5 rep_pen=1.0
        - Non-thinking/general: temp=0.7 top_p=0.8 top_k=20 pres_pen=1.5 rep_pen=1.0

        When memory experts are active (scaffold_positions non-empty),
        temperature is lowered to reduce confabulation (KL #242-244):
        - Thinking + recall: temp=0.3, top_p=0.8
        - No-think + recall: temp=0.3, top_p=0.8
        """
        _recall_mode = bool(scaffold_positions)
        if scaffold_positions:
            xargs["hippocampus_strength"] = str(3.0)
            xargs["hippocampus_positions"] = json.dumps(scaffold_positions)

        if not thinking_mode:
            messages = self._inject_no_think(messages)

        _max_tokens = 8192 if thinking_mode else 2048
        if getattr(self, "_max_tokens_override", None):
            _max_tokens = self._max_tokens_override

        if thinking_mode:
            temperature = 0.3 if _recall_mode else 1.0
            top_p = 0.8 if _recall_mode else 0.95
        else:
            temperature = 0.3 if _recall_mode else 0.7
            top_p = 0.8

        extra_body: dict[str, Any] = {
            "chat_template_kwargs": {"enable_thinking": thinking_mode},
            "top_k": 20,
            "min_p": 0.0,
            "repetition_penalty": 1.0,
        }
        if xargs:
            extra_body["vllm_xargs"] = xargs

        logger.info(
            "[Agent] agent=%s generate: thinking=%s temp=%.2f top_p=%.2f "
            "max_tokens=%d xargs_keys=%s msgs=%d",
            self.agent_id, thinking_mode,
            temperature, top_p, _max_tokens,
            list(xargs.keys()),
            len(messages),
        )

        _vllm, _adapter = self.inference_pipeline(model_override)
        if _vllm is None:
            raise RuntimeError(
                f"Agent {self.agent_id}: no inference client for generate()"
            )
        result = await _vllm.generate(
            messages=messages,
            max_tokens=_max_tokens,
            temperature=temperature,
            top_p=top_p,
            extra_body=extra_body,
            adapter_name=_adapter,
        )

        raw_text = result.text if hasattr(result, "text") else str(result)

        # vLLM bakes reasoning into text as <think>...</think> prefix.
        # Split it out so we can return clean content + thinking separately.
        thinking = ""
        full_text = raw_text
        if "</think>" in raw_text:
            think_part, full_text = raw_text.split("</think>", 1)
            thinking = think_part.replace("<think>", "").strip()

        # Rescue: if the model put its entire reply inside the <think>
        # block (visible text empty, thinking non-empty), promote the
        # thinking content to visible response — mirrors the streaming
        # path's rescue logic in generate_stream_async().
        if not full_text.strip() and thinking.strip():
            logger.warning(
                "[Agent] agent=%s generate: model produced no visible "
                "text (thinking_len=%d) — rescuing thinking as response",
                self.agent_id, len(thinking),
            )
            full_text = thinking
            thinking = ""

        _prompt_tokens = getattr(result, "prompt_tokens", 0)
        _completion_tokens = getattr(result, "completion_tokens", 0)

        logger.info(
            "[Agent] agent=%s generate result: text_len=%d thinking_len=%d "
            "prompt_tokens=%d completion_tokens=%d",
            self.agent_id, len(full_text), len(thinking),
            _prompt_tokens, _completion_tokens,
        )

        return full_text, thinking, _prompt_tokens, _completion_tokens

    # ------------------------------------------------------------------
    # 3b. Streaming Generation — async token-by-token from vLLM
    # ------------------------------------------------------------------

    async def generate_stream_async(
        self,
        messages: list[dict[str, str]],
        xargs: dict[str, Any],
        scaffold_positions: list[int] | None = None,
        *,
        thinking_mode: bool = True,
        tools: list[dict] | None = None,
        model_override: str | None = None,
    ) -> AsyncIterator[str | tuple[str, str]]:
        """Stream tokens from vLLM.

        Yields either plain ``str`` tokens (visible response text) or
        ``("thinking", text)`` / ``("thinking_end", "")`` tuples for
        progressive reasoning display.  When ``thinking_mode=False``
        (System 1), no thinking tuples are produced — the model
        responds directly.  The Qwen3 ``/no_think`` soft-switch is
        injected to suppress reasoning in visible text.

        After the iterator is exhausted, call :pyattr:`last_stream_result`
        to obtain ``(full_text, thinking)``.
        """
        _recall_mode = bool(scaffold_positions)
        if scaffold_positions:
            xargs["hippocampus_strength"] = str(3.0)
            xargs["hippocampus_positions"] = json.dumps(scaffold_positions)

        if not thinking_mode:
            messages = self._inject_no_think(messages)

        _max_tokens = 8192 if thinking_mode else 2048
        if getattr(self, "_max_tokens_override", None):
            _max_tokens = self._max_tokens_override

        if thinking_mode:
            temperature = 0.3 if _recall_mode else 1.0
            top_p = 0.8 if _recall_mode else 0.95
        else:
            temperature = 0.3 if _recall_mode else 0.7
            top_p = 0.8

        from nls.runtime.inference_compat import (
            cloud_safe_extra_body,
            resolve_tool_choice,
        )

        extra_body: dict[str, Any] = {
            "top_k": 20,
            "min_p": 0.0,
            "repetition_penalty": 1.0,
        }
        if xargs:
            extra_body["vllm_xargs"] = xargs

        accumulated_text = ""
        thinking_text = ""
        in_thinking = False
        past_thinking = False
        self._last_stream_thinking_rescued = False

        _vllm, _adapter = self.inference_pipeline(model_override)
        if _vllm is None:
            raise RuntimeError(
                f"Agent {self.agent_id}: no inference client for generate_stream()"
            )
        _upstream = getattr(_vllm, "base_url", "") or ""
        _model = model_override or _adapter or getattr(_vllm, "default_model", "") or ""
        extra_body = cloud_safe_extra_body(
            _upstream,
            extra_body,
            thinking=thinking_mode,
            is_continuation=False,
        )
        _tool_choice = resolve_tool_choice(
            _upstream, has_tools=bool(tools), model=_model,
        )

        logger.info(
            "[Agent] agent=%s generate_stream: thinking=%s temp=%.2f top_p=%.2f "
            "max_tokens=%d xargs_keys=%s msgs=%d tools=%d tool_choice=%s",
            self.agent_id, thinking_mode, temperature, top_p, _max_tokens,
            list(xargs.keys()), len(messages), len(tools or []),
            _tool_choice,
        )

        async for token in _vllm.generate_stream(
            messages=messages,
            max_tokens=_max_tokens,
            temperature=temperature,
            top_p=top_p,
            tools=tools or None,
            tool_choice=_tool_choice,
            extra_body=extra_body,
            adapter_name=_adapter,
        ):
            if isinstance(token, dict):
                continue

            accumulated_text += token

            if not past_thinking:
                if "<think>" in accumulated_text and not in_thinking:
                    in_thinking = True
                if in_thinking:
                    if "</think>" in accumulated_text:
                        parts = accumulated_text.split("</think>", 1)
                        thinking_text = parts[0].replace("<think>", "").strip()
                        remainder = parts[1]
                        accumulated_text = remainder
                        in_thinking = False
                        past_thinking = True
                        yield ("thinking_end", "")
                        if remainder:
                            yield remainder
                    else:
                        # Strip <think> tag from yielded thinking tokens
                        _clean_tok = token.replace("<think>", "")
                        if _clean_tok:
                            yield ("thinking", _clean_tok)
                    continue

            yield token

        # Safety net: if the model composed its entire reply inside the
        # <think> block (text_len=0, thinking_len>0), rescue the thinking
        # content as visible response text.  The reasoning tokens were
        # already streamed to the frontend, so we also yield the text as
        # a plain token so the frontend receives a visible response.
        if not accumulated_text.strip() and thinking_text.strip():
            logger.warning(
                "[Agent] agent=%s generate_stream: model produced no visible "
                "text (thinking_len=%d) — rescuing thinking as response",
                self.agent_id, len(thinking_text),
            )
            self._last_stream_thinking_rescued = True
            accumulated_text = thinking_text
            thinking_text = ""
            yield accumulated_text

        # Store result for post_process() after the stream is consumed
        # ──────────────────────────────────────────────────────────────────
        # Clean up orphan </think> markers.  When enable_thinking=False the
        # model sometimes emits "thinking content </think> actual response"
        # as plain text (no reasoning_content field).  The in_thinking flag
        # never fires (no <think> was seen), so </think> passes through.
        # Strip everything up-to-and-including the last </think> so that
        # what we save to history (and feed back as context) is clean.
        # IMPORTANT: do NOT populate thinking_text with the pre-</think>
        # content — it is a model generation artifact, not real reasoning.
        # Populating it causes the frontend to show a spurious "Reasoned"
        # block with the same visible text (triple-display bug).
        if not past_thinking and "</think>" in accumulated_text:
            accumulated_text = accumulated_text.replace("</think>", "").replace("<think>", "").strip()
            # Qwen3 sometimes generates the full response twice around the stray
            # </think>: "Hello!...\n\n</think>\n\nHello!...".  After tag removal
            # both copies remain.  Deduplicate: if there are exactly two
            # paragraph-separated blocks and they are identical, keep one.
            _paras = [p.strip() for p in accumulated_text.split("\n\n") if p.strip()]
            if len(_paras) == 2 and _paras[0] == _paras[1]:
                accumulated_text = _paras[0]
                logger.info(
                    "[Agent] agent=%s generate_stream: duplicate response "
                    "collapsed to single copy (%d chars)",
                    self.agent_id, len(accumulated_text),
                )
            else:
                logger.info(
                    "[Agent] agent=%s generate_stream: orphan </think> stripped",
                    self.agent_id,
                )
            # thinking_text intentionally left as "" — not a real think block

        self._last_stream_accumulated = accumulated_text
        self._last_stream_thinking = thinking_text

        _su = getattr(_vllm, "last_stream_usage", {}) or {}
        self._last_stream_prompt_tokens = _su.get("prompt_tokens", 0)
        self._last_stream_completion_tokens = _su.get("completion_tokens", 0)

        logger.info(
            "[Agent] agent=%s generate_stream complete: text_len=%d "
            "thinking_len=%d prompt_tokens=%d completion_tokens=%d",
            self.agent_id, len(accumulated_text), len(thinking_text),
            self._last_stream_prompt_tokens, self._last_stream_completion_tokens,
        )
        if thinking_text:
            _preview = thinking_text[:500].replace("\n", " ")
            logger.info(
                "[Agent] agent=%s thinking preview (first 500 chars): %s",
                self.agent_id, _preview,
            )

    @property
    def last_stream_result(self) -> tuple[str, str]:
        """Return (full_text, thinking) from the last streaming generation."""
        return (
            getattr(self, "_last_stream_accumulated", ""),
            getattr(self, "_last_stream_thinking", ""),
        )

    # ------------------------------------------------------------------
    # 4. Post-process — strip thinking, extract signals, schedule ANS
    # ------------------------------------------------------------------

    def post_process(
        self,
        full_text: str,
        thinking: str,
        user_input: str,
        history: list[dict] | None = None,
        *,
        model_override: str | None = None,
    ) -> tuple[str, list[NerveSignal]]:
        """Extract signals and clean the response for the user.

        Returns (clean_response, signals).
        """
        response = full_text

        # Strip inline tool_call debris and legacy signal syntax from visible text.
        response = _TOOL_CALL_STRIP_RE.sub("", response).strip()
        from nls.runtime.response_cleanup import strip_nls_artifacts

        response = strip_nls_artifacts(response)

        # Learning is handled by the ANS safety net (async), not inline nls_signal
        # tool calls or [LEARN:...] tags in the visible reply.  The UI receives
        # learnings via the safety_net_learned WebSocket event.
        signals: list[NerveSignal] = []

        # Extract reasoning schemas from thinking chain (M-023)
        if self.reasoning_distiller is not None and thinking:
            try:
                _distill_vllm, _distill_adapter = self.inference_pipeline(
                    model_override,
                )
                self.reasoning_distiller.distill_async(
                    thinking_chain=thinking,
                    user_input=user_input,
                    response=response,
                    domain_db=self.domain_db,
                    vllm_client=_distill_vllm,
                )
            except Exception:
                pass

        # Feed signals to ANS
        if self.ans is not None:
            self.ans.on_response(user_input, response, self.hypothalamus)

        self._schedule_safety_net(
            user_input, response, history, model_override=model_override,
        )

        # Update NarrativeSelf episode tracking
        if self.narrative_self is not None:
            try:
                _hypo = self.hypothalamus
                _valence = 0.0
                _arousal = 0.3
                _cortisol = 0.0
                _mood = "neutral"
                if _hypo is not None:
                    _hs = getattr(_hypo, "hormones", {})
                    if hasattr(_hs, "get"):
                        _cort = _hs.get("cortisol", None)
                        _sero = _hs.get("serotonin", None)
                        _dopa = _hs.get("dopamine", None)
                        if _cort is not None:
                            _cortisol = getattr(_cort, "level", 0.0)
                        if _sero is not None:
                            _valence = getattr(_sero, "level", 0.5) - 0.5
                        if _dopa is not None:
                            _arousal = getattr(_dopa, "level", 0.3)
                        _mood = "stressed" if _cortisol > 0.5 else (
                            "engaged" if _arousal > 0.5 else "neutral"
                        )
                self.narrative_self.record_turn(
                    turn_number=self._turn_count,
                    valence=_valence,
                    arousal=_arousal,
                    mood_label=_mood,
                    cortisol=_cortisol,
                    is_user_turn=True,
                )
            except Exception:
                pass

        # Update TheoryOfMind user model
        if self.theory_of_mind is not None:
            try:
                self.theory_of_mind.update_from_turn(
                    user_input=user_input,
                    response=response,
                )
            except Exception:
                pass

        return response, signals

    def _extract_inline_tool_calls(
        self, response_text: str,
    ) -> list[dict[str, Any]]:
        """Parse inline tool_call blocks (markdown fences or XML tags)."""
        if not response_text:
            return []
        tool_calls: list[dict[str, Any]] = []
        for m in _TOOL_CALL_EXTRACT_RE.finditer(response_text):
            raw = (m.group(1) or m.group(2) or "").strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            name = payload.get("name", "")
            args = payload.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    continue
            tool_calls.append({"name": name, "arguments": args})
        if tool_calls:
            logger.info(
                "[Agent] agent=%s: extracted %d inline tool_call blocks",
                self.agent_id, len(tool_calls),
            )
        return tool_calls

    def _parse_nls_signals(
        self, tool_calls: list[dict[str, Any]],
    ) -> list[NerveSignal]:
        """Convert extracted nls_signal tool calls to NerveSignal objects.

        Deduplicates by (signal_type, domain, content) to guard against
        repetition loops where the model emits the same tool_call many times.
        """
        signals: list[NerveSignal] = []
        seen: set[tuple[str, str, str]] = set()

        for tc in tool_calls:
            if tc.get("name") != "nls_signal":
                continue
            args = tc.get("arguments", {})
            signal_type = (args.get("signal_type") or args.get("signal") or "").upper()
            if not signal_type:
                continue

            domain = args.get("domain") or ""
            content = args.get("content") or ""
            subtype = args.get("subtype") or ""

            dedup_key = (signal_type, domain, content)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            if signal_type == "EVALUATE" and subtype:
                meta_layer = f"EVALUATE:{subtype}"
            elif signal_type == "RECALL" and subtype:
                meta_layer = f"RECALL:{subtype}"
            elif signal_type == "PLAN" and subtype:
                meta_layer = f"PLAN:{subtype}"
            else:
                meta_layer = ""

            pipe_fact = content if signal_type == "LEARN" else None

            hormonal_snapshot: dict[str, float] = {}
            if self.hypothalamus is not None:
                hormonal_snapshot = {
                    name: h.level
                    for name, h in self.hypothalamus.hormones.items()
                }

            signals.append(NerveSignal(
                signal_type=signal_type,
                domain_path=domain or None,
                content=content or None,
                pipe_fact=pipe_fact,
                meta_layer=meta_layer,
                source="tool_call",
                timestamp=datetime.utcnow(),
                hormonal_snapshot=hormonal_snapshot,
                turn_index=self._turn_count,
            ))

        n_raw = sum(1 for tc in tool_calls if tc.get("name") == "nls_signal")
        if signals:
            logger.info(
                "[Agent] agent=%s: parsed %d nls_signal(s) (deduped from %d): %s",
                self.agent_id, len(signals), n_raw,
                [s.signal_type for s in signals],
            )
        return signals

    def _store_learn_signals(
        self,
        signals: list[NerveSignal],
        user_input: str,
    ) -> None:
        """Delegate to the shared FactStore (M-011)."""
        if self._fact_store is None:
            from nls.knowledge.fact_store import FactStore
            self._fact_store = FactStore(
                domain_db=self.domain_db,
                hypothalamus=self.hypothalamus,
                ans=self.ans,
                taxonomy=self._taxonomy,
                self_state=self.self_state,
                working_memory=self.dual_wm or self.working_memory,
                agent_id=self.agent_id,
            )
        self._fact_store.store_learn_signals(
            signals, user_input, sleep_count=self._sleep_count,
        )

    def _schedule_safety_net(
        self,
        user_input: str,
        response: str,
        history: list[dict] | None = None,
        *,
        model_override: str | None = None,
    ) -> None:
        """Schedule the ANS safety net + frontal lobe verification.

        When the safety net discovers LEARN signals, broadcasts a
        ``safety_net_learned`` event via the ConnectionManager — the
        same message type the old ServerRuntime uses, so the frontend
        renders signal pills without changes.
        """
        if self.ans is None:
            return
        if getattr(self, "_safety_net_disabled", False):
            logger.info("[Agent] ANS safety net: SKIPPED (disabled for testing)")
            return

        _ans = self.ans
        _vllm, _adapter = self.inference_pipeline(model_override)
        if _vllm is None:
            return
        _hypo = self.hypothalamus
        _domain_db = self.domain_db
        _agent_id = self.agent_id
        _ans_state_path = self.agent_dir / "ans_state.json"
        _user_input = user_input
        _sn_prompt = user_input[:1500]
        _sn_response = response[:3000]
        _sn_history = list(history) if history else []
        _sn_project_id = self._get_active_project_id()
        _store = self._store_learn_signals
        _fl_verify = self._frontal_lobe_verify

        async def _safety_net_extract():
            try:
                sn_signals = await _ans.safety_net_extract_async(
                    _vllm,
                    hypothalamus=_hypo,
                    prompt_override=_sn_prompt,
                    response_override=_sn_response,
                    history=_sn_history,
                    domain_db=_domain_db,
                    project_id=_sn_project_id,
                    adapter_name=_adapter,
                )
                if sn_signals and _domain_db is not None:
                    _store(sn_signals, _user_input)
                if sn_signals or _ans.signal_count > 0:
                    _ans.save_state(_ans_state_path)
                emotions = getattr(_ans, "_last_emotion_result", {}) or {}
                if sn_signals:
                    logger.info(
                        "[Agent] ANS safety net: +%d learnings",
                        len(sn_signals),
                    )
                facts = [
                    s.pipe_fact or s.content
                    for s in (sn_signals or [])
                    if s.signal_type == "LEARN"
                ]
                if facts:
                    from nls.runtime.learn_dedup import (
                        collect_known_keys_from_ans,
                        filter_new_learn_facts,
                        learning_dedup_key,
                        merge_known_from_broadcast_cache,
                        remember_broadcast_keys,
                    )

                    _known = collect_known_keys_from_ans(_ans)
                    _known.update(
                        merge_known_from_broadcast_cache(
                            _ans._ui_broadcast_learn_keys,
                        ),
                    )
                    _before = len(facts)
                    facts = filter_new_learn_facts(facts, _known)
                    if facts:
                        remember_broadcast_keys(
                            _ans._ui_broadcast_learn_keys,
                            [learning_dedup_key(f) for f in facts],
                        )
                    if _before != len(facts):
                        logger.info(
                            "[Agent] ANS safety net: deduped broadcast "
                            "(%d -> %d facts)",
                            _before, len(facts),
                        )
                if facts or emotions:
                    try:
                        from server.main import app
                        cm = getattr(
                            app.state, "connection_manager", None,
                        )
                        if cm is not None:
                            payload: dict[str, Any] = {
                                "type": "safety_net_learned",
                                "facts": facts,
                            }
                            if emotions:
                                payload["emotions"] = emotions
                            await cm.broadcast(_agent_id, payload)
                    except Exception as broadcast_exc:
                        logger.warning(
                            "[Agent] ANS safety net: UI broadcast failed: %s",
                            broadcast_exc,
                        )
            except Exception as e:
                logger.warning("[Agent] ANS safety net failed: %s", e)

            # Frontal lobe: verify recalled facts against DomainDB
            try:
                _fl_verify(_sn_prompt, _sn_response)
            except Exception as e:
                logger.debug("[Agent] frontal lobe check failed: %s", e)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_safety_net_extract())
            logger.info("[Agent] ANS safety net: scheduled (parallel)")
        except RuntimeError:
            try:
                from server.main import app
                main_loop = getattr(app.state, "loop", None)
                if main_loop is not None and main_loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        _safety_net_extract(), main_loop,
                    )
                    logger.info("[Agent] ANS safety net: scheduled via main loop")
            except Exception as exc:
                logger.warning("[Agent] ANS safety net: could not schedule: %s", exc)

    # ------------------------------------------------------------------
    # Frontal Lobe — recall verification against DomainDB (M-007)
    # ------------------------------------------------------------------

    def _frontal_lobe_verify(
        self, user_input: str, response: str,
    ) -> None:
        """Cross-reference the model's response against DomainDB facts.

        Fully dynamic — derives all matching criteria from DomainDB
        itself (domain_path components, canonical_question, and
        current_value).  No hard-coded domain maps or stop-word lists.

        For each fact, relevance is determined by:
          1. canonical_question token overlap with user input (best)
          2. domain_path component overlap with user input (fallback)

        If the question is relevant but the response doesn't contain
        any distinctive token from the ground-truth value, an
        EVALUATE:incorrect signal is injected for the next sleep cycle.
        """
        if self.domain_db is None or self.ans is None:
            return

        try:
            all_facts = self._get_scoped_facts(100)
        except Exception:
            return
        if not all_facts:
            return

        response_lower = response.lower()
        input_lower = user_input.lower()
        input_tokens = set(self._tokenize(input_lower))
        corrections = 0

        for fact in all_facts:
            path = getattr(fact, "domain_path", "") or ""
            value = getattr(fact, "current_value", "") or ""
            if not path or not value:
                continue

            # Skip genesis placeholder facts — they contain no real data
            bh = getattr(fact, "block_height", 0) or 0
            if bh == 0 and "awaiting" in value.lower():
                continue

            # --- Relevance: is the user asking about this fact? ---
            relevance = self._fact_relevance(fact, input_lower, input_tokens)
            if relevance < 0.3:
                continue

            # --- Truth check: does the response contain the answer? ---
            truth_tokens = self._extract_value_tokens(value, path)
            if not truth_tokens:
                continue

            has_correct = any(
                tok.lower() in response_lower for tok in truth_tokens
            )
            if has_correct:
                continue

            self.ans.inject_signal(
                signal_type="EVALUATE:incorrect",
                domain_path=path,
                content=(
                    f"Recall mismatch: model was asked about {path} "
                    f"but did not produce the correct value. "
                    f"Ground truth: {value}"
                ),
                hypothalamus=self.hypothalamus,
                source="frontal_lobe",
                prompt=user_input[:300],
                response=response[:300],
            )
            corrections += 1
            logger.info(
                "[Agent] frontal lobe: correction for %s "
                "(truth: %s, relevance=%.2f)",
                path, truth_tokens, relevance,
            )

        if corrections:
            logger.info(
                "[Agent] frontal lobe: %d correction(s) injected for agent %s",
                corrections, self.agent_id,
            )

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Split text into lowercase alpha tokens (3+ chars)."""
        import re
        return re.findall(r"[a-z\u00e0-\u024f]{3,}", text.lower())

    @staticmethod
    def _fact_relevance(
        fact: Any, input_lower: str, input_tokens: set[str],
    ) -> float:
        """Score how relevant a DomainDB fact is to the user's question.

        Uses canonical_question (if available) and domain_path
        components — both derived from DomainDB, nothing hard-coded.
        Returns 0.0 (irrelevant) to 1.0 (strong match).
        """
        import re
        scores: list[float] = []

        # Source 1: canonical_question overlap (strongest signal)
        cq = getattr(fact, "canonical_question", "") or ""
        if cq:
            cq_tokens = set(re.findall(r"[a-z\u00e0-\u024f]{3,}", cq.lower()))
            # Remove ultra-common question words
            cq_tokens -= {"what", "who", "where", "how", "the", "does", "is"}
            if cq_tokens:
                overlap = len(cq_tokens & input_tokens)
                scores.append(overlap / len(cq_tokens))

        # Source 2: domain_path components (e.g. "User.Pets.Dog.Name")
        path = getattr(fact, "domain_path", "") or ""
        path_parts = set(re.findall(r"[a-z]{3,}", path.lower()))
        path_parts -= {"user", "general", "meta", "info", "data"}
        if path_parts:
            overlap = len(path_parts & input_tokens)
            scores.append(overlap / len(path_parts))

        return max(scores) if scores else 0.0

    @staticmethod
    def _extract_value_tokens(value: str, domain_path: str) -> list[str]:
        """Extract distinctive tokens from a fact value for matching.

        Dynamically builds the exclusion set from the domain_path
        itself (its components are generic labels, not answer content)
        plus minimal English function words. No hard-coded stop lists.
        """
        import re
        words = re.findall(r"[A-Za-z\u00C0-\u024F]{3,}", value)

        # Exclude: domain_path components (generic labels like "User",
        # "Name", "Pets") and minimal function words.
        _FUNCTION_WORDS = frozenset({
            "the", "that", "this", "with", "from", "for", "and", "are",
            "was", "has", "his", "her", "its", "they", "she", "who",
            "what", "how", "not", "but", "also", "very", "about",
        })
        path_components = frozenset(
            re.findall(r"[a-z]{3,}", domain_path.lower())
        )
        exclude = _FUNCTION_WORDS | path_components

        tokens = [w for w in words if w.lower() not in exclude]
        return tokens[:5]

    # ------------------------------------------------------------------
    # 4b. Sleep trigger — check after each turn
    # ------------------------------------------------------------------

    def _check_sleep_trigger(self) -> None:
        """Check whether the ANS wants to trigger sleep after this turn.

        Mirrors the legacy sleep-check logic from ServerRuntime:
        - Skipped when circadian rhythm handles sleep timing (InnerLoop)
        - Skipped when active WebSocket sessions exist
        - Skipped during education
        """
        if self.ans is None:
            return

        circadian_enabled = (
            getattr(self.ans, "circadian", None) is not None
            and self.ans.circadian.enabled
        )
        if circadian_enabled:
            return
        if self._active_sessions > 0:
            return
        if self.education_active:
            return

        should_sleep, reason = self.ans.check_sleep_trigger(
            self.hypothalamus,
        )
        if not should_sleep:
            return

        hormones: dict[str, float] = {}
        if self.hypothalamus is not None:
            hormones = {
                n: round(h.level, 3)
                for n, h in self.hypothalamus.hormones.items()
            }

        from nls.models import SleepRequest

        sleep_request = SleepRequest(
            agent_id=self.agent_id,
            reason=reason,
            signal_count=self.ans.get_buffer_summary().get(
                "learnable_signals", 0,
            ),
            hormones=hormones,
        )
        logger.info(
            "[Agent] agent=%s: sleep requested (reason=%s, signals=%d)",
            self.agent_id, reason, sleep_request.signal_count,
        )
        if self._on_sleep_requested is not None:
            self._on_sleep_requested(sleep_request)

    def notify_sleep_complete(self, **kwargs: Any) -> None:
        """Called by the sleep scheduler after a training cycle finishes.

        Mirrors the legacy ServerRuntime: restores energy, wakes the ANS,
        resets hormones, and runs sleep/wake hooks on all front-brain
        modules so the agent starts fresh.
        """
        sleep_type = kwargs.get("sleep_type", "sleep")
        self._sleep_count += 1

        try:
            from nls.ledger.chain_sleep import record_consolidation_epoch

            record_consolidation_epoch(
                Path(self.agent_dir),
                sleep_index=self._sleep_count,
                aku_count=int(kwargs.get("signals_processed", 0) or 0),
                summary=str(kwargs.get("consolidation_summary", "") or ""),
                sleep_type=sleep_type,
            )
        except Exception as exc:
            logger.warning(
                "[Agent] agent=%s: chain epoch block failed: %s",
                self.agent_id, exc,
            )

        # 1. ANS: transition back to AWAKE
        if self.ans is not None:
            try:
                report = self.ans.wake(self.hypothalamus)
                logger.info(
                    "[Agent] agent=%s: ANS woke up (duration=%.1fs, signals=%d)",
                    self.agent_id,
                    report.duration_seconds,
                    report.total_signals_processed,
                )
            except Exception as exc:
                logger.debug(
                    "[Agent] agent=%s: ANS wake failed, forcing AWAKE: %s",
                    self.agent_id, exc,
                )
                try:
                    from nls.brain.autonomic import AgentState
                    self.ans._state = AgentState.AWAKE
                except Exception:
                    pass

        # 2. DMN: reset replay memory so consolidated facts can be re-explored
        if self.dmn is not None:
            try:
                self.dmn.reset_replay_memory()
            except Exception:
                pass

        # 3. Temporal Self: restore energy
        if self.temporal_self is not None:
            try:
                if sleep_type == "nightly":
                    restore = self.temporal_self.cfg.energy_restore_full_sleep
                else:
                    restore = self.temporal_self.cfg.energy_restore_per_sleep
                self.temporal_self.restore_energy(restore)
                logger.info(
                    "[Agent] agent=%s: energy restored +%.0f%% -> %.0f%%",
                    self.agent_id,
                    restore * 100,
                    self.temporal_self.energy * 100,
                )
                if self.self_state is not None:
                    self.self_state.energy = self.temporal_self.energy
            except Exception as exc:
                logger.debug(
                    "[Agent] agent=%s: energy restore failed: %s",
                    self.agent_id, exc,
                )

        # Back-fill energy_after on latest SleepReport
        if self.ans is not None and getattr(self.ans, "_sleep_reports", None):
            try:
                latest = self.ans._sleep_reports[-1]
                if self.temporal_self is not None:
                    latest.energy_after = self.temporal_self.energy
            except Exception:
                pass

        # 4. Hypothalamus: reset hormones toward baseline
        if self.hypothalamus is not None:
            try:
                if sleep_type == "nightly":
                    self.hypothalamus.full_reset()
                else:
                    self.hypothalamus.gentle_reset()
            except Exception as exc:
                logger.debug(
                    "[Agent] agent=%s: hypothalamus reset failed: %s",
                    self.agent_id, exc,
                )

        # 5. Agency: post-sleep announcement
        if self.agency is not None:
            try:
                sleep_report = None
                if self.ans is not None and getattr(self.ans, "_sleep_reports", None):
                    sleep_report = self.ans._sleep_reports[-1]
                self.agency.on_wake(sleep_report)
            except Exception:
                pass

        # 6. Working Memory: consolidate and clear session-scoped slots
        try:
            if self.dual_wm is not None:
                self.dual_wm.on_sleep()
                self.dual_wm.on_wake()
            elif self.working_memory is not None:
                self.working_memory.on_sleep()
                self.working_memory.on_wake()
        except Exception as exc:
            logger.debug(
                "[Agent] agent=%s: WM sleep/wake skipped: %s",
                self.agent_id, exc,
            )

        # 6b. Rehydrate credentials from vault into Cryptex
        try:
            self.rehydrate_credentials()
        except Exception:
            pass

        # 7. Narrative Self
        if self.narrative_self is not None:
            try:
                self.narrative_self.on_sleep()
                self.narrative_self.on_wake()
            except Exception:
                pass

        # 8. Theory of Mind: reset temperature for fresh session
        if self.theory_of_mind is not None:
            try:
                self.theory_of_mind.on_sleep()
                self.theory_of_mind.on_wake()
            except Exception:
                pass

        # 9. Predictive Processing
        if self.predictive is not None:
            try:
                self.predictive.on_sleep()
                self.predictive.on_wake()
            except Exception:
                pass

        # 10. Network Dynamics: reset to neutral
        if self.network_dynamics is not None:
            try:
                self.network_dynamics.on_sleep()
                self.network_dynamics.on_wake()
            except Exception:
                pass

        # Save all state
        try:
            self.save_state()
        except Exception as exc:
            logger.debug(
                "[Agent] agent=%s: save_state after sleep skipped: %s",
                self.agent_id, exc,
            )
        logger.info(
            "[Agent] agent=%s: sleep complete (sleep_count=%d, type=%s)",
            self.agent_id, self._sleep_count, sleep_type,
        )

    # ------------------------------------------------------------------
    # 4b-2. Post-sleep day narrative via vLLM
    # ------------------------------------------------------------------

    _DAY_NARRATIVE_PROMPT = (
        "You are summarizing an AI agent's waking session into a day narrative. "
        "Produce a dense, first-person narrative (under {target} chars) covering:\n"
        "- What tasks were worked on and their outcomes\n"
        "- Key decisions, mistakes, and recoveries\n"
        "- Important facts, patterns, and architectural knowledge gained\n"
        "- Emotional arc (stress peaks, flow states)\n"
        "Write as a reflective journal entry. Omit credentials and secrets."
    )

    async def synthesize_day_narrative(self) -> str | None:
        """Generate a day narrative via vLLM and persist to Cryptex consolidation."""
        wm = getattr(self, "dual_wm", None) or getattr(self, "working_memory", None)
        if wm is None:
            return None

        # Gather available context
        ctx_parts: list[str] = []
        try:
            consol = wm.get_consolidation_context()
            if consol:
                ctx_parts.append(f"SESSION PROGRESS:\n{consol}")
        except Exception:
            pass

        ns = getattr(self, "narrative_self", None)
        if ns is not None:
            try:
                narrative_text = ns.get_narrative_for_sleep()
                if narrative_text:
                    ctx_parts.append(f"NARRATIVE EPISODES:\n{narrative_text[:1500]}")
            except Exception:
                pass
            try:
                soul = getattr(ns, "soul_wish", "")
                if soul:
                    ctx_parts.append(f"SOUL WISH: {soul}")
            except Exception:
                pass

        # Emotional state
        if self.temporal_self is not None:
            try:
                energy = self.temporal_self.energy
                ctx_parts.append(f"ENERGY: {energy:.0%}")
            except Exception:
                pass

        if not ctx_parts:
            return None

        context_text = "\n\n".join(ctx_parts)[:4000]
        target = 1200

        try:
            import re as _re
            from nls.runtime.inference_compat import micro_inference_extra_body

            system_msg = self._DAY_NARRATIVE_PROMPT.format(target=target)
            _vllm, _adapter = self.inference_pipeline()
            if _vllm is None:
                return None
            _upstream = getattr(_vllm, "base_url", "") or ""
            result = await _vllm.generate(
                adapter_name=_adapter,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": context_text},
                ],
                max_tokens=500,
                temperature=0.4,
                extra_body=micro_inference_extra_body(_upstream, thinking=False),
            )
            narrative = (
                result.text if hasattr(result, "text") else str(result or "")
            ).strip()
            if "<think>" in narrative:
                narrative = _re.sub(
                    r"<think>.*?</think>", "", narrative, flags=_re.DOTALL,
                ).strip()

            narrative = narrative[:target + 200]
            if not narrative or len(narrative) < 50:
                return None

            # Persist to Cryptex consolidation ring
            try:
                wm.upsert_fact(
                    domain="Consolidation.DayNarrative",
                    content=narrative,
                    source="sleep_narrative",
                    salience=0.95,
                )
            except Exception:
                pass

            # Also persist to NarrativeSelf as a narrative block
            if ns is not None:
                try:
                    ns.append_block(
                        block_type="day_narrative",
                        content=narrative,
                        source_episode=f"sleep-{self._sleep_count}",
                        domains=["sleep", "consolidation"],
                        coherence_delta=0.05,
                    )
                except Exception:
                    pass

            logger.info(
                "[Agent] agent=%s: day narrative synthesized (%d chars)",
                self.agent_id, len(narrative),
            )
            return narrative

        except Exception as exc:
            logger.warning(
                "[Agent] agent=%s: day narrative synthesis failed: %s",
                self.agent_id, exc,
            )
            return None

    # ------------------------------------------------------------------
    # 4c. Persistent state — session_meta.json
    # ------------------------------------------------------------------

    def _load_session_meta(self) -> None:
        """Restore turn_count, sleep_count, last_interaction from disk."""
        import time
        meta_path = self.agent_dir / "session_meta.json"
        if not meta_path.exists():
            return
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            self._turn_count = meta.get("turn_count", 0)
            self._sleep_count = meta.get("sleep_count", 0)
            saved = meta.get("last_interaction")
            if saved is not None:
                self._last_interaction = float(saved)
            _orch = (meta.get("orchestrator_model") or "").strip()
            self.session_orchestrator_model = _orch or None
            _del = (meta.get("delegate_model") or "").strip()
            self.session_delegate_model = _del or None
            if "delegate_lock_orchestrator" in meta:
                self.session_delegate_lock_orchestrator = bool(
                    meta.get("delegate_lock_orchestrator")
                )
            logger.info(
                "[Agent] agent=%s: restored meta (turns=%d, sleeps=%d)",
                self.agent_id, self._turn_count, self._sleep_count,
            )
        except Exception as exc:
            logger.debug("[Agent] agent=%s: meta load failed: %s", self.agent_id, exc)

    def save_state(self) -> None:
        """Persist all component states to disk."""
        import time
        _saves: list[tuple[Any, str | Path]] = [
            (self.hypothalamus, "hypothalamus_state.json"),
            (self.ans, "ans_state.json"),
            (self.self_state, "self_state.json"),
            (self.temporal_self, "temporal_self_state.json"),
            (self.ofc, "ofc_state.json"),
            (self.narrative_self, "narrative_self_state.json"),
            (self.theory_of_mind, "theory_of_mind_state.json"),
            (self.predictive, "predictive_state.json"),
            (self.network_dynamics, "network_dynamics_state.json"),
        ]
        for component, filename in _saves:
            if component is not None:
                try:
                    save_fn = getattr(component, "save_state", None) or getattr(component, "save", None)
                    if save_fn:
                        save_fn(self.agent_dir / filename if isinstance(filename, str) else filename)
                except Exception:
                    pass

        if self.calibrator is not None:
            try:
                self.calibrator.save_state(self.agent_dir)
            except Exception:
                pass
        if self.working_memory is not None:
            try:
                self.working_memory.save(
                    self.agent_dir / "working_memory_state.json",
                )
            except Exception:
                pass
        if self.dual_wm is not None:
            try:
                self.dual_wm.save(self.agent_dir)
            except Exception:
                pass
        if self.drive_engine is not None:
            try:
                self.drive_engine.save_state(self.agent_dir)
            except Exception:
                pass
        if self.delegate_manager is not None:
            try:
                self.delegate_manager.save_state(self.agent_dir / "delegates.json")
            except Exception:
                pass

        self._last_interaction = time.time()
        meta = {
            "agent_id": self.agent_id,
            "turn_count": self._turn_count,
            "sleep_count": self._sleep_count,
            "last_interaction": self._last_interaction,
            "last_session": datetime.utcnow().isoformat(),
        }
        if self.session_orchestrator_model:
            meta["orchestrator_model"] = self.session_orchestrator_model
        if self.session_delegate_model:
            meta["delegate_model"] = self.session_delegate_model
        meta["delegate_lock_orchestrator"] = self.session_delegate_lock_orchestrator
        meta_path = self.agent_dir / "session_meta.json"
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 5. Process Message — full orchestrator
    # ------------------------------------------------------------------

    async def process_message_async(
        self,
        user_input: str,
        history: list[dict] | None = None,
        *,
        model_override: str | None = None,
        memory_test_mode: bool = False,
        no_deltanet: bool = False,
    ) -> AgentTurnResult:
        """Process a single chat turn through the agent pipeline.

        This is the main entry point — async because vLLM generation
        is async. The caller (chat.py) runs this in the event loop.

        When *memory_test_mode* is True, the prompt is stripped to
        match the original stress-test setup: memory cortex framing +
        scaffold field names only — no Cryptex, no tool directory, no
        NLS signal text.  This isolates expert-weight recall from
        context contamination.
        """
        self._foreground_processing += 1
        self._foreground_source = "user"
        try:
            import time as _time
            self._turn_count += 1
            self._last_interaction = _time.time()

            if not memory_test_mode:
                # SN spike on user input (ported from legacy server_runtime)
                if self.network_dynamics is not None:
                    try:
                        self.network_dynamics.on_user_input()
                    except Exception:
                        pass

                # 0. Working memory: activate + check deferred intentions
                if self.working_memory is not None:
                    if hasattr(self.working_memory, "activate"):
                        self.working_memory.activate("user")
                    try:
                        triggered = self.working_memory.check_intentions(user_input)
                        if triggered:
                            for intn in triggered:
                                self.working_memory.add(intn)
                    except Exception:
                        pass

            # 1. Thalamic route — compute bias + xargs.
            # Use agentic=True to reduce prefill/decode scaling, same as the
            # agentic loop (KL #371).  This path generates with tools=54 and
            # must not over-perturb generation with full thalamic bias — especially
            # when DeltaNet is active post-sleep (memory=1.00, norm≈160) where
            # agentic=False caused 8192-token thinking spirals (KL #402).
            xargs, meta_weight, _thal_thinking = self.thalamic_route(
                agentic=True, query_context=user_input,
            )

            if no_deltanet:
                for _dn_key in (
                    "deltanet_inject_path", "deltanet_inject_paths",
                    "deltanet_inject_scales",
                ):
                    xargs.pop(_dn_key, None)
                logger.info("[Agent] no_deltanet=True — stripped DN injection xargs")

            # 1b. Thinking gate — micro-inference classifier.
            # Always let classify_thinking_need decide; do NOT bypass with
            # _thal_thinking.  When the calibrator sees memory=1.00 it sets
            # thinking=True, but "hey babo good morning" must still resolve to
            # CHAT_NOTHINK (System 1) to avoid being bootstrapped into a spiral.
            if memory_test_mode:
                thinking_mode = True
            else:
                thinking_mode = await self.classify_thinking_need(
                    user_input, history, model_override=model_override,
                )

            # 2. Format prompt — build messages with scaffold
            messages, scaffold_positions = self.format_prompt(
                user_input, history, memory_test_mode=memory_test_mode,
            )

            # 3. Generate — single vLLM call
            full_text, thinking, _prompt_tok, _compl_tok = await self.generate(
                messages, xargs, scaffold_positions,
                thinking_mode=thinking_mode,
                model_override=model_override,
            )
            self._last_thinking = thinking

            # 4. Post-process — extract signals, clean response, schedule ANS
            response, signals = self.post_process(
                full_text, thinking, user_input, history,
                model_override=model_override,
            )

            # 5. Hormonal feedback from signals
            if self.hypothalamus is not None and signals:
                for sig in signals:
                    if sig.signal_type == "LEARN":
                        self.hypothalamus.on_signal("new_learning")
                    elif sig.signal_type == "RECALL":
                        self.hypothalamus.on_signal("successful_recall")

            # 6. Sleep check
            self._check_sleep_trigger()

            # 7. Persist brain component states
            if self.hypothalamus is not None:
                try:
                    self.hypothalamus.save_state(
                        self.agent_dir / "hypothalamus_state.json",
                    )
                except Exception:
                    pass
            if self.working_memory is not None:
                try:
                    self.working_memory.save(
                        self.agent_dir / "working_memory_state.json",
                    )
                except Exception:
                    pass

            # Refresh unified self-representation
            if self.self_state is not None:
                self.self_state.turns_since_input = 0
                if self.temporal_self is not None:
                    self.temporal_self.mark_user_input()
                self.self_state.collect_all(
                    hypothalamus=self.hypothalamus,
                    thalamus_meta_weight=meta_weight,
                    drive_engine=self.drive_engine,
                    ans=self.ans,
                    working_memory=self.working_memory,
                    predictive=self.predictive,
                )

            logger.info(
                "[Agent] agent=%s turn=%d: response=%d chars, "
                "signals=%d, meta_weight=%.2f, tokens=%d/%d",
                self.agent_id, self._turn_count, len(response),
                len(signals), meta_weight,
                _prompt_tok, _compl_tok,
            )

            return AgentTurnResult(
                response=response,
                signals=signals,
                meta_weight=meta_weight,
                thinking=thinking,
                prompt_tokens=_prompt_tok,
                completion_tokens=_compl_tok,
            )
        finally:
            self._foreground_processing = max(0, self._foreground_processing - 1)
            if self._foreground_processing == 0:
                self._foreground_source = "idle"

    async def process_message_stream_async(
        self,
        user_input: str,
        history: list[dict] | None = None,
        *,
        model_override: str | None = None,
        force_thinking: bool | None = None,
        memory_test_mode: bool = False,
        no_deltanet: bool = False,
        include_tools: bool = True,
        orchestration_profile: str | None = None,
        tool_hints: list[str] | None = None,
    ) -> AsyncIterator[str | tuple[str, str]]:
        """Streaming variant — yields tokens (str or thinking tuples), then does post-process.

        After the iterator is exhausted the caller can access the
        full :class:`AgentTurnResult` via :pyattr:`last_stream_turn_result`.

        Parameters
        ----------
        force_thinking
            Override the thinking classifier.  ``False`` forces System 1
            (no ``<think>`` blocks, fast direct response).  ``None`` (default)
            lets the classifier decide.
        memory_test_mode
            When True, use the stripped Verbal V2 prompt for pure
            weights-only recall testing (no Cryptex, no tools, no
            focused facts).  Mirrors ``process_message_async`` behavior.
        no_deltanet
            When True, strip all DeltaNet injection xargs before
            sending to vLLM.  Used to isolate expert-only recall.
        include_tools
            When False, omit tools (e.g. birth greeting — not an agentic turn).
        orchestration_profile
            When set with ``include_tools``, filter schemas to this profile's
            allowed tool surface (conversational / solo / orchestrated).
        tool_hints
            Structured triage hints (e.g. ``forbid:tools``) applied on top.
        """
        self._foreground_processing += 1
        self._foreground_source = "user"
        try:
            import time as _time
            self._turn_count += 1
            self._last_interaction = _time.time()

            if not memory_test_mode:
                if self.working_memory is not None:
                    if hasattr(self.working_memory, "activate"):
                        self.working_memory.activate("user")
                    try:
                        triggered = self.working_memory.check_intentions(user_input)
                        if triggered:
                            for intn in triggered:
                                self.working_memory.add(intn)
                    except Exception:
                        pass

            # 1. Thalamic route — compute bias + xargs.
            # Use agentic=True: this path generates with tools=54, same as the
            # agentic loop.  agentic=False caused 8192-token thinking spirals
            # post-sleep (memory=1.00, norm≈160) because full prefill_bias_scale
            # (1.0) + full decode_layer_scales + DeltaNet injection combined to
            # destabilize generation (KL #402).  agentic=True brings this path
            # in line with run_agentic_loop_v4 which never spiraled overnight.
            xargs, meta_weight, _thal_thinking = self.thalamic_route(
                agentic=True, query_context=user_input,
            )

            if no_deltanet:
                for _dn_key in (
                    "deltanet_inject_path", "deltanet_inject_paths",
                    "deltanet_inject_scales",
                ):
                    xargs.pop(_dn_key, None)
                logger.info("[Agent] no_deltanet=True — stripped DN injection xargs")

            # 1b. Thinking gate — micro-inference classifier.
            # Do NOT bypass with _thal_thinking: when memory=1.00 (post-sleep)
            # the calibrator sets thinking=True, but a greeting like "hey babo
            # good morning" must classify as CHAT_NOTHINK to avoid a spiral.
            if memory_test_mode:
                thinking_mode = True
            elif force_thinking is not None:
                thinking_mode = force_thinking
            else:
                thinking_mode = await self.classify_thinking_need(
                    user_input, history, model_override=model_override,
                )

            self._maybe_apply_user_assigned_name(user_input)

            messages, scaffold_positions = self.format_prompt(
                user_input, history, memory_test_mode=memory_test_mode,
            )

            _stream_tools = self._openai_tools_for_turn(
                include_tools=include_tools,
                orchestration_profile=orchestration_profile,
                tool_hints=tool_hints,
                memory_test_mode=memory_test_mode,
            )
            async for token in self.generate_stream_async(
                messages, xargs, scaffold_positions,
                thinking_mode=thinking_mode,
                tools=_stream_tools,
                model_override=model_override,
            ):
                yield token

            full_text, thinking = self.last_stream_result
            self._last_thinking = thinking

            response, signals = self.post_process(
                full_text, thinking, user_input, history,
                model_override=model_override,
            )

            if self.hypothalamus is not None and signals:
                for sig in signals:
                    if sig.signal_type == "LEARN":
                        self.hypothalamus.on_signal("new_learning")
                    elif sig.signal_type == "RECALL":
                        self.hypothalamus.on_signal("successful_recall")

            self._check_sleep_trigger()
            if self.hypothalamus is not None:
                try:
                    self.hypothalamus.save_state(
                        self.agent_dir / "hypothalamus_state.json",
                    )
                except Exception:
                    pass
            if self.working_memory is not None:
                try:
                    self.working_memory.save(
                        self.agent_dir / "working_memory_state.json",
                    )
                except Exception:
                    pass

            # Refresh unified self-representation (heartbeat, valence, etc.)
            if self.self_state is not None:
                self.self_state.turns_since_input = 0
                if self.temporal_self is not None:
                    self.temporal_self.mark_user_input()
                self.self_state.collect_all(
                    hypothalamus=self.hypothalamus,
                    thalamus_meta_weight=meta_weight,
                    drive_engine=self.drive_engine,
                    ans=self.ans,
                    working_memory=self.working_memory,
                    predictive=self.predictive,
                )

            # Name detection (M-027) — check if the user named the agent
            from nls.identity.agent_identity import (
                detect_name_from_signals,
                save_agent_name,
            )
            detected_name = detect_name_from_signals(
                signals, user_input, response,
                agent_id=self.agent_id,
                domain_db=self.domain_db,
            )
            if detected_name:
                self.agent_name = detected_name
                save_agent_name(self.agent_dir, detected_name, self.agent_id)
                self._last_name_update = detected_name
            else:
                self._last_name_update = None

            logger.info(
                "[Agent] agent=%s turn=%d stream: response=%d chars, "
                "signals=%d, meta_weight=%.2f, name_update=%s",
                self.agent_id, self._turn_count, len(response),
                len(signals), meta_weight, detected_name,
            )

            self._last_stream_turn_result = AgentTurnResult(
                response=response,
                signals=signals,
                meta_weight=meta_weight,
                thinking=thinking,
                prompt_tokens=getattr(self, "_last_stream_prompt_tokens", 0),
                completion_tokens=getattr(self, "_last_stream_completion_tokens", 0),
            )
        finally:
            self._foreground_processing = max(0, self._foreground_processing - 1)
            if self._foreground_processing == 0:
                self._foreground_source = "idle"

    @property
    def last_stream_turn_result(self) -> AgentTurnResult | None:
        """Full result from the last streaming turn (available after stream ends)."""
        return getattr(self, "_last_stream_turn_result", None)

    # ------------------------------------------------------------------
    # 5b. Intent Gate — TASK vs CHAT classification (M-017)
    # ------------------------------------------------------------------

    _TASK_PATTERNS = re.compile(
        r"(?i)"
        r"\b(?:create|build|make|write|implement|deploy|fix|debug|install|"
        r"run|execute|test|setup|configure|download|upload|clone|"
        r"refactor|migrate|update|upgrade|delete|remove|add|push|pull|"
        r"commit|merge|revert|browse|navigate|open|search|find|"
        r"schedule|poll|monitor|check|verify|validate)\b"
    )

    @staticmethod
    def classify_intent(user_input: str) -> tuple[bool, bool]:
        """Classify message as TASK or CHAT.

        Uses regex fast-path only — no LLM classification.
        The model itself decides by emitting (or not emitting) tool calls.

        Returns ``(is_task, needs_thinking)``.
        """
        if AgentRuntime._TASK_PATTERNS.search(user_input):
            return True, True
        if user_input.strip().startswith("/"):
            return True, False
        return False, True

    def _wire_orchestration_notify_gate(self) -> None:
        """Outbound ledger gate + inner-loop check-back drain for teams."""
        if self._team_manager is None:
            return
        if self._team_manager._connection_manager is None:
            try:
                from server.main import app as _app

                cm = getattr(_app.state, "connection_manager", None)
                if cm is not None:
                    self._team_manager._connection_manager = cm
            except Exception:
                pass
        _plan_store = None
        for _t in self._agent_tools or []:
            if getattr(_t, "name", "") == "plan":
                _plan_store = getattr(_t, "_store", None)
                break
        if self._outbound_gate is None:
            from nls.agentic.outbound_notify import OutboundNotifyGate

            self._outbound_gate = OutboundNotifyGate(
                self.agent_dir,
                team_manager=self._team_manager,
                plan_store=_plan_store,
                delegate_manager=self.delegate_manager,
            )

        def _drain_dispatch(source_exact: str) -> int:
            try:
                from server.main import app as _app

                cs = getattr(_app.state, "consciousness_scheduler", None)
                if cs is None:
                    return 0
                il = cs.get_inner_loop(self.agent_id)
                if il is None:
                    return 0
                return il.drain_pending_dispatches(source_exact=source_exact)
            except Exception:
                return 0

        self._team_manager.set_dispatch_drain(_drain_dispatch)

        def _has_dispatch_prefix(prefix: str) -> bool:
            try:
                from server.main import app as _app

                cs = getattr(_app.state, "consciousness_scheduler", None)
                if cs is None:
                    return False
                il = cs.get_inner_loop(self.agent_id)
                if il is None:
                    return False
                return any(
                    s.startswith(prefix)
                    for _, s in getattr(il, "_pending_dispatches", [])
                )
            except Exception:
                return False

        self._team_manager.set_dispatch_has_prefix(_has_dispatch_prefix)

        def _schedule_orchestration_wake(prompt: str, source: str) -> None:
            try:
                from server.main import app as _app

                cs = getattr(_app.state, "consciousness_scheduler", None)
                if cs is None:
                    return
                il = cs.get_inner_loop(self.agent_id)
                if il is None:
                    logger.warning(
                        "Agent %s: orchestration wake not enqueued — "
                        "no inner loop (source=%s)",
                        self.agent_id, source,
                    )
                    return
                il.enqueue_autonomous_dispatch(prompt, source)
            except Exception:
                logger.warning(
                    "Agent %s: enqueue orchestration wake failed (source=%s)",
                    self.agent_id, source, exc_info=True,
                )

        self._team_manager.set_schedule_orchestration_wake(
            _schedule_orchestration_wake,
        )

    # ------------------------------------------------------------------
    # 5c. Agentic Loop — tool-use with cognitive hooks (M-016)
    # ------------------------------------------------------------------

    def _clear_agentic_stall_suppression(self) -> None:
        """Reset stall timestamps so background drives resume after user input."""
        self._last_agentic_stall_ts = 0.0
        try:
            from server.main import app as _app

            cs = getattr(_app.state, "consciousness_scheduler", None)
            if cs is None:
                return
            il = cs.get_inner_loop(self.agent_id)
            if il is not None:
                il._last_agentic_stall_ts = 0.0
        except Exception:
            pass

    async def process_message_agentic_async(
        self,
        user_input: str,
        history: list[dict] | None = None,
        *,
        model_override: str | None = None,
        on_event: Any | None = None,
        abort_signal: Any | None = None,
        source: str = "user",
        on_bash_output: Any | None = None,
        on_browser_navigation: Any | None = None,
        on_browser_auth_request: Any | None = None,
        browser_emit_and_wait: Any | None = None,
        copilot_queue: Any | None = None,
        emit_set_cookies: Any | None = None,
        first_response: str | None = None,
        first_tool_calls: list | None = None,
        shared_context: Any | None = None,
        checkpoint_callback: Any | None = None,
        enable_thinking: bool = True,
        pre_extracted_goals: list[str] | None = None,
        pre_extracted_hints: list[str] | None = None,
        pre_triage: Any | None = None,
        context_id: str | None = None,
        session_key: str | None = None,
    ) -> Any:
        """Run the agentic loop (v2/v3/v5) through AgentRuntime."""
        # Rotate cryptex to the project's context if provided
        if context_id is not None:
            try:
                from nls.brain.cryptex import CryptexMemory
                wm = getattr(self, "working_memory", None)
                if isinstance(wm, CryptexMemory):
                    wm.activate(source, project_id=context_id)
            except Exception:
                pass
            # Use context-specific deep slot when available
            from nls.engine.execution_slots import _DeepContext
            _deep_slot = self._slot_manager.get_deep_for_context(context_id)
            _deep_ctx = _DeepContext(_deep_slot, source=source)
        else:
            _deep_ctx = self._slot_manager.acquire_deep(source=source)

        from nls.runtime.dispatch_sources import is_orchestration_dispatch_source
        if not is_orchestration_dispatch_source(source):
            self._clear_agentic_stall_suppression()

        async with _deep_ctx:
            return await self._run_agentic_locked(
                user_input, history,
                model_override=model_override,
                on_event=on_event, abort_signal=abort_signal,
                source=source, on_bash_output=on_bash_output,
                on_browser_navigation=on_browser_navigation,
                on_browser_auth_request=on_browser_auth_request,
                browser_emit_and_wait=browser_emit_and_wait,
                copilot_queue=copilot_queue,
                emit_set_cookies=emit_set_cookies,
                first_response=first_response,
                first_tool_calls=first_tool_calls,
                shared_context=shared_context,
                checkpoint_callback=checkpoint_callback,
                enable_thinking=enable_thinking,
                pre_extracted_goals=pre_extracted_goals,
                pre_extracted_hints=pre_extracted_hints,
                pre_triage=pre_triage,
                session_key=session_key,
            )

    async def _run_agentic_locked(
        self,
        user_input: str,
        history: list[dict] | None = None,
        *,
        model_override: str | None = None,
        on_event: Any | None = None,
        abort_signal: Any | None = None,
        source: str = "user",
        on_bash_output: Any | None = None,
        on_browser_navigation: Any | None = None,
        on_browser_auth_request: Any | None = None,
        browser_emit_and_wait: Any | None = None,
        copilot_queue: Any | None = None,
        emit_set_cookies: Any | None = None,
        first_response: str | None = None,
        first_tool_calls: list | None = None,
        shared_context: Any | None = None,
        checkpoint_callback: Any | None = None,
        enable_thinking: bool = True,
        pre_extracted_goals: list[str] | None = None,
        pre_extracted_hints: list[str] | None = None,
        pre_triage: Any | None = None,
        session_key: str | None = None,
    ) -> Any:
        """Inner agentic loop body, serialized by _agentic_lock."""
        import time as _time
        from nls.agentic.bridge import build_hooks, build_config

        _fg_session = (session_key or "").strip() or "websocket:main"
        _prev_copilot = self._foreground_copilot_queue
        self._foreground_processing += 1
        self._foreground_source = source
        self._foreground_session_key = _fg_session
        if copilot_queue is not None:
            self._foreground_copilot_queue = copilot_queue
        try:
            self._turn_count += 1
            self._last_interaction = _time.time()

            # SN spike on user input (ported from legacy server_runtime)
            if source == "user" and self.network_dynamics is not None:
                try:
                    self.network_dynamics.on_user_input()
                except Exception:
                    pass

            # Retry tool init if __init__ failed (e.g. server not ready yet)
            if self._agent_tools is None:
                self._initialize_tools()

            # Wire per-request bash / browser callbacks
            from nls.tools.agent_tools.bash import BashTool
            for tool in self._agent_tools:
                if isinstance(tool, BashTool):
                    tool._on_output = on_bash_output
                    tool._on_processes_changed = self._emit_project_processes_changed
                    break
            try:
                from nls.tools.agent_tools.browser_adapter import BrowserAdapterTool
                for tool in self._agent_tools:
                    if isinstance(tool, BrowserAdapterTool):
                        tool._on_navigation = on_browser_navigation
                        tool._request_auth = on_browser_auth_request
                        tool._copilot_queue = copilot_queue
                        tool._emit_set_cookies = emit_set_cookies
                        tool._emit_and_wait = browser_emit_and_wait
                        # Wire Visual Cortex so the browser channel sees live screenshots
                        if self.visual_cortex is not None:
                            tool.set_visual_cortex(self.visual_cortex)
                        break
            except ImportError:
                pass

            # Wire LiveBrowserTool into Visual Cortex for Electron webview capture
            if self.visual_cortex is not None:
                try:
                    from nls.tools.agent_tools.browser_live import LiveBrowserTool as _LiveBrowserTool
                    for tool in self._agent_tools:
                        if isinstance(tool, _LiveBrowserTool):
                            tool.set_visual_cortex(self.visual_cortex)
                            break
                except ImportError:
                    pass

            # Register screenshot tool when Visual Cortex is available.
            # Added lazily per-turn so it's always in sync with the active VC
            # instance; the tool is idempotent to add (same name, skip if
            # already present).
            if self.visual_cortex is not None:
                try:
                    from nls.tools.agent_tools.screenshot import create_screenshot_tool
                    _existing_names = {t.name for t in self._agent_tools}
                    if "screenshot" not in _existing_names:
                        _ss_tool = create_screenshot_tool(self.visual_cortex)
                        self._agent_tools.append(_ss_tool)
                        logger.info("Agent %s: screenshot tool registered", self.agent_id)
                except Exception as _ss_err:
                    logger.debug("screenshot tool registration failed: %s", _ss_err)

            if self.visual_cortex is not None:
                try:
                    from nls.tools.agent_tools.eyes import create_eyes_tool
                    _existing_names = {t.name for t in self._agent_tools}
                    if "eyes" not in _existing_names:
                        _eyes_tool = create_eyes_tool(self.visual_cortex)
                        self._agent_tools.append(_eyes_tool)
                        logger.info("Agent %s: eyes tool registered", self.agent_id)
                except Exception as _eyes_err:
                    logger.debug("eyes tool registration failed: %s", _eyes_err)

            # Notify Visual Cortex that an agentic turn is active
            if self.visual_cortex is not None:
                self.visual_cortex.set_agent_active(True)

            # Decay any persisted high error rate at the start of every
            # intentional dispatch (user, scheduler check-backs, channel
            # messages).  This prevents a previous vLLM outage from blocking
            # a fresh task — DMN/autonomous daydreams are excluded because
            # they are speculative and should still respect the error ceiling.
            _intentional = (
                source in ("user", "channel")
                or source.startswith("scheduler")
                or source.startswith("delegate")
            )
            if _intentional and self.ans is not None:
                if hasattr(self.ans, "decay_error_rate"):
                    self.ans.decay_error_rate()
                # Also refresh the ANS user-activity timestamp so the circadian
                # bedtime guard ("user was active in last 5 min") does not
                # re-trigger sleep immediately after the agent completes
                # background work when the user is AFK.  Without this, the
                # agent wakes up, sees _last_interaction_at is stale, and the
                # bedtime check sends it straight back to sleep.
                if hasattr(self.ans, "_last_interaction_at"):
                    from datetime import datetime as _dt_now
                    self.ans._last_interaction_at = _dt_now.utcnow()

            agentic_config = build_config(self.config)
            if shared_context is not None:
                agentic_config.shared_context = shared_context
            if checkpoint_callback is not None:
                agentic_config.checkpoint_callback = checkpoint_callback
    
            # Activate the correct WM workspace before building hooks so that
            # all closures capture the right active workspace (personal for
            # autonomous/background tasks, professional for user tasks).
            _active_wm = self.working_memory
            if self.dual_wm is not None and source != "user":
                workspace_name = self.dual_wm.activate(source)
                _active_wm = self.dual_wm.active
            elif self.dual_wm is not None:
                self.dual_wm.activate("user")
                _active_wm = self.dual_wm.active

            _orch_vllm, _orch_adapter = self.inference_pipeline(model_override)

            hooks = build_hooks(
                agent_id=self.agent_id,
                agent_dir=self.agent_dir,
                working_memory=_active_wm,
                dual_wm=self.dual_wm,
                hypothalamus=self.hypothalamus,
                domain_db=self.domain_db,
                ans=self.ans,
                temporal_self=self.temporal_self,
                vllm_client=_orch_vllm,
                inference_adapter=_orch_adapter,
                store_learn_signals=self._store_learn_signals,
                config=self.config,
                predictive=self.predictive,
                self_state=self.self_state,
                calibrator=self.calibrator,
                ofc=self.ofc,
                theory_of_mind=self.theory_of_mind,
                narrative_self=self.narrative_self,
                event_logger=self._event_logger,
                agent_tools=self._agent_tools,
                network_dynamics=self.network_dynamics,
            )
    
            system_prompt = self._build_system_prompt()
            reflect_prompt = system_prompt
    
            # Qwen3.5 recommended sampling (thinking/general mode).
            # The V5 agentic loop reads these from LoopConfig directly.
            gen_config = {
                "max_new_tokens": 8192,
                "temperature": 1.0,
                "top_p": 0.95,
                "top_k": 20,
                "min_p": 0.0,
                "presence_penalty": 1.5,
                "repetition_penalty": 1.3,
            }
    
            agency_cfg = self.config.get("agency", {})
            loop_version = agency_cfg.get("agentic_loop_version", "v5")
            if loop_version not in ("v5", "v4"):
                logger.warning(
                    "Agent %s: unsupported agentic_loop_version=%s, using v5",
                    self.agent_id, loop_version,
                )
                loop_version = "v5"

            if loop_version in ("v5", "v4"):
                from nls.agentic.loop import run_loop
                from nls.agentic.bridge import build_config_v4, build_hooks_v4
    
                v4_config = build_config_v4(self.config)
                v4_config.agent_id = self.agent_id
                v4_config.session_log_dir = str(
                    self.agent_dir / "agentic_logs"
                )
                if shared_context is not None:
                    v4_config.shared_context = shared_context
                if checkpoint_callback is not None:
                    v4_config.checkpoint_callback = checkpoint_callback

                # Detached delegates: enable for orchestrator path
                _enable_detached = self.config.get(
                    "agency", {},
                ).get("agentic_loop", {}).get("enable_detached_delegates", True)
                v4_config.enable_detached_delegates = _enable_detached
                if _enable_detached and self.delegate_manager is None:
                    from nls.agentic.delegate_manager import DelegateManager
                    self.delegate_manager = DelegateManager()
                    _dm_state_path = self.agent_dir / "delegates.json"
                    if _dm_state_path.exists():
                        self.delegate_manager.load_state(_dm_state_path)
                    try:
                        from server.services.delegate_batch_hooks import (
                            wire_runtime_batch_complete,
                        )

                        wire_runtime_batch_complete(
                            self.delegate_manager,
                            self.agent_id,
                            get_copilot_queue=lambda: self._foreground_copilot_queue,
                        )
                    except Exception:
                        logger.debug(
                            "[Agent] agent=%s: runtime batch-complete hook skipped",
                            self.agent_id,
                            exc_info=True,
                        )
                elif self.delegate_manager is not None:
                    try:
                        from server.services.delegate_batch_hooks import (
                            wire_runtime_batch_complete,
                        )

                        wire_runtime_batch_complete(
                            self.delegate_manager,
                            self.agent_id,
                            get_copilot_queue=lambda: self._foreground_copilot_queue,
                        )
                    except Exception:
                        logger.debug(
                            "[Agent] agent=%s: runtime batch-complete hook skipped",
                            self.agent_id,
                            exc_info=True,
                        )

                # Wire delegate_manager into delegate_ring tool
                if self.delegate_manager is not None:
                    _dr_tool = next(
                        (t for t in self._agent_tools
                         if getattr(t, "name", "") == "delegate_ring"),
                        None,
                    )
                    if _dr_tool is not None and _dr_tool._dm is None:
                        _dr_tool._dm = self.delegate_manager

                # Wire late-bound deps into TeamManager
                if self._team_manager is not None:
                    if self._team_manager._delegate_manager is None and self.delegate_manager:
                        self._team_manager._delegate_manager = self.delegate_manager
                    if (
                        self.delegate_manager is not None
                        and self.delegate_manager._on_delegate_progress is None
                        and self._team_manager is not None
                    ):
                        _tm_ref = self._team_manager
                        async def _on_progress(status):
                            try:
                                await _tm_ref.on_delegate_progress(status.delegate_number, status)
                            except Exception as _exc:
                                logger.warning(
                                    "[Agent] on_delegate_progress FAILED for delegate #%d: %s",
                                    getattr(status, "delegate_number", "?"), _exc,
                                )
                        self.delegate_manager._on_delegate_progress = _on_progress
                    if (
                        self.delegate_manager is not None
                        and self.delegate_manager._on_delegate_complete is None
                        and self._team_manager is not None
                    ):
                        _tm_ref2 = self._team_manager
                        _self_ref = self
                        async def _on_complete(delegate_number, ds):
                            try:
                                await _tm_ref2.on_delegate_complete(delegate_number, ds)
                            except Exception as _exc:
                                logger.error(
                                    "[Agent] on_delegate_complete FAILED for delegate #%d: %s",
                                    delegate_number, _exc, exc_info=True,
                                )
                            # Absorb SubCryptex digest into orchestrator's Cryptex
                            try:
                                _cryptex_wm = getattr(_self_ref, "working_memory", None) or getattr(_self_ref, "dual_wm", None)
                                if (
                                    _cryptex_wm is not None
                                    and hasattr(_cryptex_wm, "absorb_delegate_digest")
                                    and hasattr(ds, "summary")
                                    and ds.summary
                                ):
                                    import json as _json_oc
                                    _dtxt = ds.summary
                                    _ds_start = _dtxt.find("[DELEGATE KNOWLEDGE DIGEST]")
                                    _ds_end = _dtxt.find("[END DIGEST]")
                                    if _ds_start != -1 and _ds_end != -1:
                                        _djson_str = _dtxt[_ds_start + len("[DELEGATE KNOWLEDGE DIGEST]"):_ds_end].strip()
                                        _digest_oc = _json_oc.loads(_djson_str)
                                        _cryptex_wm.absorb_delegate_digest(_digest_oc)
                                        logger.info(
                                            "[Agent] absorbed delegate #%d knowledge digest",
                                            delegate_number,
                                        )
                            except Exception as _abs_exc:
                                logger.debug(
                                    "[Agent] delegate #%d digest absorption failed: %s",
                                    delegate_number, _abs_exc,
                                )
                            try:
                                _ans = getattr(_self_ref, "ans", None)
                                _wm = getattr(_self_ref, "working_memory", None) or getattr(_self_ref, "dual_wm", None)
                                if _ans and _wm and hasattr(_ans, "absorb_signals_to_rings"):
                                    _ans.absorb_signals_to_rings(_wm)
                            except Exception:
                                pass
                        self.delegate_manager._on_delegate_complete = _on_complete
                    if self._team_manager._connection_manager is None:
                        try:
                            from server.main import app as _app
                            cm = getattr(_app.state, "connection_manager", None)
                            if cm is not None:
                                self._team_manager._connection_manager = cm
                        except Exception:
                            pass
                    if self._team_manager._scheduler_manager is None and self._scheduler_manager is not None:
                        self._team_manager._scheduler_manager = self._scheduler_manager
                    if self.delegate_manager is not None:
                        _reconciled = self._team_manager.reconcile_with_delegates()
                        logger.info(
                            "Agent %s: delegate reconcile on loop start (%d team(s) updated)",
                            self.agent_id, _reconciled,
                        )
                    self._wire_orchestration_notify_gate()
                    if self._team_manager is not None:
                        _unreported = (
                            self._team_manager.reconcile_unreported_terminal_teams()
                        )
                        if _unreported:
                            logger.info(
                                "Agent %s: queued EM review for %d terminal "
                                "team(s) missing team(advance)",
                                self.agent_id, _unreported,
                            )
                        _unlaunched = (
                            self._team_manager.enqueue_unlaunched_for_auto_launch()
                        )
                        if _unlaunched:
                            logger.info(
                                "Agent %s: queued auto-launch for %d "
                                "unlaunched wave team(s)",
                                self.agent_id, _unlaunched,
                            )
                        _pending_reviews = (
                            self._team_manager.reconcile_pending_completion_reviews(
                                current_dispatch_source=source,
                            )
                        )
                        if _pending_reviews:
                            logger.info(
                                "Agent %s: re-queued completion-review wake "
                                "for %d delegate(s)",
                                self.agent_id, _pending_reviews,
                            )

                # Register primary orchestration context (Phase 5)
                if not self._orch_registry.get("primary"):
                    _cryptex = getattr(self, "working_memory", None) or getattr(self, "dual_wm", None)
                    self._orch_registry.register_primary(
                        team_manager=self._team_manager,
                        delegate_manager=self.delegate_manager,
                        cryptex=_cryptex,
                    )

                try:
                    xargs, _meta_w, _think = self.thalamic_route(agentic=True)
                    v4_config.vllm_xargs = xargs
                except Exception:
                    logger.debug("thalamic_route before v4 loop failed", exc_info=True)
    
                v4_hooks = build_hooks_v4(
                    agent_id=self.agent_id,
                    agent_dir=self.agent_dir,
                    working_memory=_active_wm,
                    dual_wm=self.dual_wm,
                    hypothalamus=self.hypothalamus,
                    domain_db=self.domain_db,
                    ans=self.ans,
                    vllm_client=_orch_vllm,
                    inference_adapter=_orch_adapter,
                    store_learn_signals=self._store_learn_signals,
                    config=self.config,
                    event_logger=self._event_logger,
                    agent_tools=self._agent_tools,
                    thalamic_route_fn=self.thalamic_route,
                    narrative_self=self.narrative_self,
                    theory_of_mind=self.theory_of_mind,
                    source=source,
                    self_state=self.self_state,
                    network_dynamics=self.network_dynamics,
                    outbound_gate=self._outbound_gate,
                    foreground_session_key=_fg_session,
                )
    
                # Expose the accumulator on the runtime so the inner loop
                # heartbeat can trigger wall-clock flushes.
                self._learning_accumulator = getattr(v4_hooks, "_accumulator", None)

                # Wire orch hooks into TeamManager so orchestration events
                # update WM (teams, decisions, escalations).
                if self._team_manager is not None:
                    self._team_manager.set_hooks(v4_hooks)

                self._agentic_hooks = v4_hooks
                try:
                    from server.main import app as _app

                    _sm = getattr(_app.state, "squad_manager", None)
                    if _sm is not None:
                        _sq = _sm.get_squad_for_agent(self.agent_id)
                        if _sq is not None and _sq.is_lead(self.agent_id):
                            _sm.set_hooks(v4_hooks)
                except Exception:
                    pass

                _fleet_hints = list(pre_extracted_hints or [])
                if not _fleet_hints and pre_triage is not None:
                    _fleet_hints = list(getattr(pre_triage, "hints", None) or [])
                try:
                    from nls.agentic.fleet_triage_policy import fleet_hint_active
                    from nls.agentic.job_triage_policy import (
                        job_active_tool_names,
                        job_hint_active,
                    )

                    if fleet_hint_active(_fleet_hints):
                        self.sync_squad_tools()
                except Exception:
                    pass

                try:
                    from server.main import app as _app

                    _sm = getattr(_app.state, "squad_manager", None)
                    if (
                        _sm is not None
                        and _sm.get_squad_for_agent(self.agent_id) is not None
                    ):
                        self.sync_squad_tools()
                except Exception:
                    pass

                tool_dict = {t.name: t for t in self._agent_tools}

                # Deferred tool loading: the executor gets the FULL tool
                # dict (so it can run any tool the model discovers), but we
                # build a reduced _active_tool_names set so only core +
                # predicted tools get schemas sent to the LLM initially.
                _active_tool_names: set[str] | None = None
                try:
                    from nls.engine.thalamic_router import CORE_TOOLS, predict_tools as _predict_tools
                    from nls.agentic.fleet_triage_policy import (
                        fleet_active_tool_names,
                        fleet_hint_active,
                    )
                    from nls.agentic.job_triage_policy import (
                        job_active_tool_names,
                        job_hint_active,
                    )

                    _predicted = _predict_tools(user_input)
                    if fleet_hint_active(_fleet_hints):
                        _predicted |= set(fleet_active_tool_names(self.agent_id))
                    if job_hint_active(_fleet_hints):
                        _predicted |= set(job_active_tool_names())
                    _active_tool_names = set(CORE_TOOLS) | _predicted
                    _dt = tool_dict.get("discover_tools")
                    if _dt is not None and hasattr(_dt, "set_registry"):
                        _dt.set_registry(tool_dict)
                    logger.debug(
                        "[Agent] deferred loading: %d core + %d predicted of %d total tools",
                        len(CORE_TOOLS), len(_predicted), len(tool_dict),
                    )
                except Exception:
                    pass
    
                # --- V5: Clean message architecture ---
                # Each role has one purpose:
                #   system[0] = identity + instructions + tool directory
                #   system[1] = working memory (professional/personal context)
                #   history   = prior turns (with thinking preserved)
                #   user      = raw user request only
                logger.info(
                    "[Agent] agent=%s v5 agentic: building context — "
                    "sys_prompt_len=%d history_len=%d user_input_len=%d "
                    "enable_thinking=%s",
                    self.agent_id, len(system_prompt),
                    len(history) if history else 0,
                    len(user_input), enable_thinking,
                )
    
                self._ensure_cryptex_populated()

                context: list[dict] = [
                    {"role": "system", "content": system_prompt},
                ]
    
                # Working memory as separate system message
                wm_context: str = ""
                if self.working_memory:
                    try:
                        wm_str = self.working_memory.to_context_string(render_context=source)
                    except TypeError:
                        wm_str = self.working_memory.to_context_string()
                    if wm_str:
                        wm_context = wm_str
    
                # ANS context and preflight knowledge folded into WM message
                _wm_parts: list[str] = []
                if wm_context:
                    _wm_parts.append(wm_context)
                if self.ans:
                    try:
                        ans_ctx = self.ans.get_context_summary()
                        if ans_ctx:
                            _wm_parts.append(ans_ctx)
                    except Exception:
                        pass
                    try:
                        task_ctx = self.ans.get_recent_tasks_context()
                        if task_ctx:
                            _wm_parts.append(task_ctx)
                    except Exception:
                        pass
    
                preflight = v4_hooks.get_preflight_knowledge
                if preflight:
                    pk = preflight(user_input)
                    if pk:
                        _wm_parts.append(
                            f"[RELEVANT KNOWLEDGE]\n{pk}\n[/RELEVANT KNOWLEDGE]"
                        )
    
                if _wm_parts:
                    _wm_system_msg = "\n\n".join(_wm_parts)
                    context.append({"role": "system", "content": _wm_system_msg})
    
                # History with artifact stripping
                if history:
                    import re as _re
                    _ARTIFACT_RE = _re.compile(
                        r"</?tool_code>|</?tool_call>|```tool_call.*?```",
                        _re.DOTALL,
                    )
                    for _hm in history:
                        _hc = _hm.get("content") or ""
                        if _hm.get("role") == "assistant" and _ARTIFACT_RE.search(_hc):
                            _cleaned = _ARTIFACT_RE.sub("", _hc).strip()
                            if not _cleaned:
                                continue
                            _hm = {**_hm, "content": _cleaned}
                        context.append(_hm)

                # Visual Cortex — ambient turn-start snapshot.
                # Injected as a user message (not system) so it lands correctly
                # in the Qwen chat template.  Mid-conversation system messages
                # are undefined behaviour in most chat templates and may be
                # silently dropped or folded.  One frame per turn — not per tool.
                _skip_ambient_vc = "team" in tool_dict
                if self.visual_cortex is not None and not _skip_ambient_vc:
                    try:
                        _vc_snap = self.visual_cortex.get_visual_context(channel="user")
                        if _vc_snap:
                            _vc_lower = _vc_snap.lower()
                            if (
                                "windrose" in _vc_lower
                                and len(user_input) > 80
                                and "windrose" not in user_input.lower()
                            ):
                                _vc_snap = ""
                        if _vc_snap:
                            context.append({
                                "role": "user",
                                "content": f"[VISUAL CONTEXT — current screen]\n{_vc_snap}",
                            })
                            logger.debug(
                                "[Agent] agent=%s: VC turn-start snapshot injected (%d chars)",
                                self.agent_id, len(_vc_snap),
                            )
                    except Exception as _vc_err:
                        logger.debug("VC turn-start snapshot failed: %s", _vc_err)

                # Clean user message — no preamble, no sandwich
                context.append({"role": "user", "content": user_input})
    
                logger.info(
                    "[Agent] agent=%s v5: launching loop — "
                    "context_msgs=%d user_input_len=%d wm_len=%d tools=%d "
                    "first_resp=%s first_tc=%d enable_thinking=%s",
                    self.agent_id,
                    len(context),
                    len(user_input),
                    len(wm_context),
                    len(tool_dict),
                    "yes" if first_response else "no",
                    len(first_tool_calls) if first_tool_calls else 0,
                    enable_thinking,
                )
                for _ci, _cm in enumerate(context):
                    logger.debug(
                        "[Agent] context[%d] role=%s len=%d",
                        _ci, _cm.get("role"), len(_cm.get("content") or ""),
                    )
    
                _vllm, _adapter = _orch_vllm, _orch_adapter
                v4_config.delegate_adapter_name = self.resolve_delegate_adapter(
                    _adapter
                )
                result = await run_loop(
                    context=context,
                    tools=tool_dict,
                    config=v4_config,
                    hooks=v4_hooks,
                    vllm_client=_vllm,
                    abort_signal=abort_signal,
                    on_event=on_event,
                    user_input=user_input,
                    adapter_name=_adapter,
                    copilot_queue=copilot_queue,
                    first_response=first_response,
                    first_tool_calls=first_tool_calls,
                    enable_thinking=enable_thinking,
                    pre_extracted_goals=pre_extracted_goals,
                    pre_extracted_hints=pre_extracted_hints,
                    pre_triage=pre_triage,
                    visual_cortex=self.visual_cortex,
                    delegate_manager=self.delegate_manager,
                    active_tool_names=_active_tool_names,
                    dispatch_source=source,
                    session_key=_fg_session,
                )
    
            # Schedule safety net on the final agentic exchange.
            # Include a digest of tool actions so the extraction LLM sees
            # project-level facts (tech stack, file structure, configs)
            # that only appear in tool call results.
            _final_resp = getattr(result, "final_response", "") or ""
            if _final_resp:
                _action_digest = ""
                _events = getattr(result, "events", []) or []
                _action_lines: list[str] = []
                for ev in _events[-10:]:
                    for tr in (ev.tool_results or [])[-3:]:
                        _tname = tr.get("tool_name", "")
                        _preview = tr.get("result_preview", "")[:200]
                        if _tname and _preview:
                            _action_lines.append(f"[{_tname}] {_preview}")
                if _action_lines:
                    _action_digest = (
                        "\n\n[Agentic tool outcomes]\n"
                        + "\n".join(_action_lines[-8:])
                    )
                _enriched_resp = _final_resp + _action_digest
                self._schedule_safety_net(
                    user_input, _enriched_resp, history,
                    model_override=model_override,
                )

            # Counteract accumulated errors so the agent doesn't immediately
            # fall asleep after productive (or even aborted) work.
            _result_aborted = getattr(result, "aborted", False)
            if self.ans is not None:
                if not _result_aborted and hasattr(self.ans, "record_task_success"):
                    self.ans.record_task_success()
                elif _result_aborted and hasattr(self.ans, "decay_error_rate"):
                    self.ans.decay_error_rate()

            self._check_sleep_trigger()
            self.save_state()

            return result
        finally:
            self._foreground_processing = max(0, self._foreground_processing - 1)
            if self._foreground_processing == 0:
                self._foreground_source = "idle"
                self._foreground_session_key = ""
            self._foreground_copilot_queue = _prev_copilot
            if self.visual_cortex is not None:
                self.visual_cortex.set_agent_active(False)

    # ------------------------------------------------------------------
    # 5d. Channel-aware session routing (M-019)
    # ------------------------------------------------------------------

    def load_session_history(
        self, session_key: str | None = None, max_turns: int = 20,
    ) -> list[dict]:
        """Load conversation history, optionally for a specific channel session."""
        if session_key and self.channel_registry is not None:
            try:
                return self.channel_registry.session_router.load_history(
                    session_key, max_turns=max_turns,
                )
            except Exception:
                pass
        history_path = self.agent_dir / "conversation_history.json"
        if not history_path.exists():
            return []
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            messages = data if isinstance(data, list) else data.get("messages", [])
            return messages[-max_turns * 2:] if max_turns else messages
        except Exception:
            return []

    def save_session_history(
        self,
        history: list[dict],
        session_key: str | None = None,
        max_turns: int = 20,
        metadata: dict | None = None,
    ) -> None:
        """Save conversation history, optionally for a specific channel session."""
        if session_key and self.channel_registry is not None:
            try:
                self.channel_registry.session_router.save_history(
                    session_key, history,
                    max_turns=max_turns, metadata=metadata,
                )
                return
            except Exception:
                pass
        history_path = self.agent_dir / "conversation_history.json"
        trimmed = history[-max_turns * 2:] if max_turns else history
        try:
            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(trimmed, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Delegation stubs — thin wrappers to domain modules.
    # Keep AgentRuntime lean; logic lives in nls/runtime/, nls/brain/.
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Post-construction hook (called by AgentManager)."""
        if self._agent_tools is None:
            self._initialize_tools()
        else:
            self._wire_bash_process_tracking()
        logger.info("[Agent] agent=%s: initialized", self.agent_id)

    def _get_bash_tool(self) -> Any | None:
        from nls.tools.agent_tools.bash import BashTool

        for tool in self._agent_tools or []:
            if isinstance(tool, BashTool):
                return tool
        return None

    def _wire_bash_process_tracking(self) -> None:
        bash = self._get_bash_tool()
        if bash is None:
            return
        bash._on_processes_changed = self._emit_project_processes_changed

    def _get_connection_manager(self) -> Any | None:
        """Resolve WS broadcast manager (desktop may not import server.main)."""
        tm = getattr(self, "_team_manager", None)
        if tm is not None:
            cm = getattr(tm, "_connection_manager", None)
            if cm is not None:
                return cm
        try:
            from server.main import app as _app

            return getattr(_app.state, "connection_manager", None)
        except Exception:
            return None

    async def _emit_project_processes_changed(self) -> None:
        processes = self.list_project_processes()
        cm = self._get_connection_manager()
        if cm is None:
            return
        try:
            await cm.broadcast(self.agent_id, {
                "type": "project_processes_changed",
                "processes": processes,
            })
        except Exception:
            logger.debug(
                "[Agent] agent=%s: project_processes broadcast failed",
                self.agent_id,
                exc_info=True,
            )

    def list_project_processes(self) -> list[dict[str, Any]]:
        bash = self._get_bash_tool()
        if bash is None:
            return []
        return bash.list_detached_processes()

    async def kill_project_process(self, pid: int) -> bool:
        bash = self._get_bash_tool()
        if bash is None:
            return False
        return await bash.kill_detached(pid)

    async def shutdown_async(self) -> None:
        """Graceful cleanup — save state, close resources, kill child processes."""
        self.save_state()

        # Kill any processes spawned by the bash tool (dev servers, bundlers)
        for tool in (getattr(self, "_agent_tools", None) or []):
            if hasattr(tool, "cleanup") and callable(tool.cleanup):
                try:
                    tool.cleanup()
                except Exception:
                    pass

        vc = self.visual_cortex
        if vc is not None and getattr(vc, "_running", False):
            try:
                await vc.stop()
            except Exception:
                logger.warning(
                    "[Agent] agent=%s: Visual Cortex stop failed",
                    self.agent_id,
                    exc_info=True,
                )

        if self.domain_db is not None:
            try:
                self.domain_db.close()
            except Exception:
                pass
        if self._event_logger is not None:
            try:
                self._event_logger.log(
                    "session_end", agent_id=self.agent_id,
                    turns=self._turn_count, sleep_cycles=self._sleep_count,
                )
                self._event_logger.close()
            except Exception:
                pass
        logger.info("[Agent] agent=%s: shutdown complete", self.agent_id)

    def shutdown(self) -> None:
        """Sync wrapper for :meth:`shutdown_async` (blocks until VC is released)."""
        try:
            asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            in_loop = False

        if not in_loop:
            asyncio.run(self.shutdown_async())
            return

        # Called from an async context without ``await shutdown_async()`` —
        # run teardown on a side thread so we can block without deadlocking.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(asyncio.run, self.shutdown_async()).result(timeout=45.0)

    @property
    def is_busy(self) -> bool:
        """True while a chat or agentic turn is actively running.

        ``_active_sessions`` counts open WebSocket clients (sleep deferral);
        long-lived desktop connections must not block idle autonomous work.
        """
        return self._foreground_processing > 0

    @property
    def is_user_busy(self) -> bool:
        """True only when a USER or CHANNEL initiated turn is running.

        Orchestration dispatches (scheduler, delegate_batch_complete,
        team_checkback, DMN, drives) are NOT user-busy — they must not
        abort themselves via ``_watch_interrupt``.  Real user/channel turns
        still preempt background work.
        """
        if self._foreground_processing <= 0:
            return False
        from nls.runtime.dispatch_sources import is_orchestration_dispatch_source

        return not is_orchestration_dispatch_source(self._foreground_source)

    def get_status(self, sections: set[str] | None = None) -> dict[str, Any]:
        from nls.runtime.status import get_status
        status = get_status(
            agent_id=self.agent_id,
            agent_name=self.agent_name,
            config=self.config,
            hypothalamus=self.hypothalamus,
            ans=self.ans,
            calibrator=self.calibrator,
            domain_db=self.domain_db,
            self_state=self.self_state,
            working_memory=self.working_memory,
            narrative_self=self.narrative_self,
            theory_of_mind=self.theory_of_mind,
            predictive=self.predictive,
            network_dynamics=self.network_dynamics,
            turn_count=self._turn_count,
            sleep_count=self._sleep_count,
            last_interaction=self._last_interaction,
            sections=sections,
        )
        if sections is None or "activity" in sections:
            snap = self.session_inference_snapshot()
            status["activity"] = {
                "busy": self.is_busy,
                "user_busy": self.is_user_busy,
                "foreground_source": self._foreground_source,
                "orchestrator_model": snap.get("orchestrator_model"),
                "delegate_model": snap.get("delegate_model"),
            }
            if snap.get("orchestrator_model"):
                status["orchestrator_model"] = snap["orchestrator_model"]
            if snap.get("delegate_model"):
                status["delegate_model"] = snap["delegate_model"]
        return status

    def get_wake_prompt(self) -> str | None:
        from nls.runtime.status import get_wake_prompt
        return get_wake_prompt(self.config, self.agent_name)

    def is_agentic_enabled(self) -> bool:
        from nls.runtime.status import is_agentic_enabled
        return is_agentic_enabled(self.config)

    def get_agentic_config(self) -> Any:
        from nls.runtime.status import get_agentic_config
        return get_agentic_config(self.config)

    def get_agentic_config_v2(self) -> Any:
        return self.get_agentic_config()

    def load_conversation_history(self, max_turns: int = 20) -> list[dict]:
        from nls.runtime.session import load_conversation_history
        return load_conversation_history(self.agent_dir, max_turns)

    def save_conversation_history(
        self, history: list[dict], max_turns: int = 20,
    ) -> None:
        from nls.runtime.session import save_conversation_history
        save_conversation_history(self.agent_dir, history, max_turns)

    def load_chat_transcript(self, max_turns: int = 200) -> list[dict]:
        from nls.runtime.session import load_chat_transcript
        limit = None if max_turns <= 0 else max_turns * 2
        return load_chat_transcript(self.agent_dir, limit=limit)

    def record_chat_turn(
        self,
        *,
        user: str | None = None,
        assistant: str | None = None,
        reasoning: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        from nls.runtime.session import append_chat_transcript_turn
        append_chat_transcript_turn(
            self.agent_dir,
            user=user,
            assistant=assistant,
            reasoning=reasoning,
            metadata=metadata,
        )

    def load_autonomous_history(self, max_turns: int = 10) -> list[dict]:
        from nls.runtime.session import load_autonomous_history
        return load_autonomous_history(self.agent_dir, max_turns)

    def save_autonomous_history(
        self, history: list[dict], max_turns: int = 10,
    ) -> None:
        from nls.runtime.session import save_autonomous_history
        save_autonomous_history(self.agent_dir, history, max_turns)

    def _maybe_apply_user_assigned_name(self, user_input: str) -> str | None:
        """Apply a user-assigned name before prompt build (avoids re-greet)."""
        from nls.identity.agent_identity import (
            detect_name_from_user_input,
            save_agent_name,
            sync_identity_name_in_working_memory,
        )

        name = detect_name_from_user_input(user_input)
        if not name:
            return None
        if self.agent_name and self.agent_name.lower() == name.lower():
            return name
        self.agent_name = name
        save_agent_name(self.agent_dir, name, self.agent_id)
        sync_identity_name_in_working_memory(self.working_memory, name)
        logger.info(
            "Agent %s: applied user-assigned name '%s' before generation",
            self.agent_id, name,
        )
        return name

    def _save_agent_name(self, name: str) -> None:
        from nls.runtime.session import save_agent_name
        save_agent_name(self.agent_dir, name)
        self.agent_name = name

    def build_fact_memory_context(self) -> str:
        """Build a summary of known facts for context injection."""
        if self.domain_db is None:
            return ""
        try:
            facts = self._get_scoped_facts(30)
            parts = []
            for f in facts:
                path = getattr(f, "domain_path", "")
                val = getattr(f, "current_value", "")
                if path and val:
                    parts.append(f"{path}: {val[:100]}")
            return "\n".join(parts)
        except Exception:
            return ""

    async def triage_user_turn(
        self,
        user_input: str,
        *,
        history: list[dict] | None = None,
        model_override: str | None = None,
        profile_override: str | None = None,
    ) -> Any:
        """Unified turn triage: intent, thinking, profile, goals, hints, deferred."""
        from nls.agentic.goals import TurnTriage, triage_turn, _heuristic_triage

        if not user_input.strip():
            return TurnTriage(
                intent="CHAT_NOTHINK",
                thinking=False,
                profile="conversational",
            )
        _vllm, _adapter = self.inference_pipeline(model_override)
        if _vllm is None:
            return TurnTriage(
                intent="CHAT_THINK",
                thinking=True,
                profile="solo_structured",
            )
        try:
            self._refresh_channel_awareness()
        except Exception:
            pass
        try:
            triage = await triage_turn(
                _vllm, user_input, history=history, adapter_name=_adapter,
                tool_catalog=self._tool_catalog_for_triage(),
                environment_context=self._channel_status_for_triage(),
                continuation_context=self._triage_continuation_context(
                    user_input, history=history,
                ),
            )
        except Exception:
            logger.warning(
                "Agent %s: turn triage failed", self.agent_id, exc_info=True,
            )
            triage = _heuristic_triage(user_input)
            triage.classifier_inferred = False

        from nls.agentic.profile_guard_policy import boost_triage_for_work_continuation

        boost_triage_for_work_continuation(
            triage, user_input, history=history,
        )
        from nls.agentic.job_triage_policy import boost_job_charter_continuation

        boost_job_charter_continuation(
            triage,
            user_input,
            history=history,
            agent_id=self.agent_id,
        )
        from nls.agentic.job_triage_policy import job_hint_active, resolve_job_candidate

        if job_hint_active(triage.hints) and not triage.job_candidate:
            triage.job_candidate = resolve_job_candidate(
                None,
                hints=triage.hints,
                working_memory=self._ring_working_memory(),
            )
        from nls.agentic.profile_guard_policy import reconcile_triage_continuation_phase

        reconcile_triage_continuation_phase(
            triage,
            user_input,
            history=history,
            working_memory=self._ring_working_memory(),
        )
        from nls.agentic.plan_triage_policy import (
            apply_user_profile_override,
            boost_triage_for_active_plan,
        )

        boost_triage_for_active_plan(
            triage,
            user_input,
            plan_store=self._plan_store(),
            team_manager=self._team_manager,
        )
        triage.reconcile_orchestration_depth()
        triage.reconcile_fleet_vs_skill_hints(agent_id=self.agent_id)
        triage.reconcile_job_charter_hints(agent_id=self.agent_id)
        apply_user_profile_override(
            triage,
            profile_override,
            plan_store=self._plan_store(),
            team_manager=self._team_manager,
        )
        self._apply_triage_to_working_memory(triage)
        logger.info(
            "Agent %s: triage intent=%s profile=%s thinking=%s goals=%s hints=%s job=%s",
            self.agent_id,
            triage.intent,
            triage.profile,
            triage.thinking,
            triage.goals,
            triage.hints,
            bool(getattr(triage, "job_candidate", None)),
        )
        return triage

    def _tool_catalog_for_triage(self) -> str:
        """Compact AVAILABLE TOOLS block for the turn triage classifier."""
        from nls.agentic.goals import summarize_tools_for_triage

        if not self._agent_tools:
            try:
                self._initialize_tools()
            except Exception:
                pass
        return summarize_tools_for_triage(self._agent_tools)

    def _openai_tools_for_turn(
        self,
        *,
        include_tools: bool,
        orchestration_profile: str | None,
        tool_hints: list[str] | None,
        memory_test_mode: bool,
    ) -> list[dict] | None:
        """Resolve OpenAI tool schemas for a chat stream turn."""
        if memory_test_mode or not self._openai_tools:
            return None
        if not include_tools:
            return None
        if not orchestration_profile:
            return self._openai_tools
        from nls.agentic.orchestration_profile_spec import (
            apply_tool_deny,
            normalize_profile,
        )
        from nls.agentic.profile_guard_policy import tools_denied_by_hints

        names = frozenset(
            schema.get("function", {}).get("name", "")
            for schema in self._openai_tools
            if schema.get("function", {}).get("name")
        )
        allowed = apply_tool_deny(names, normalize_profile(orchestration_profile))
        allowed = allowed - tools_denied_by_hints(tool_hints)
        if not allowed:
            return None
        filtered = [
            schema for schema in self._openai_tools
            if schema.get("function", {}).get("name", "") in allowed
        ]
        return filtered or None

    def _ring_working_memory(self) -> Any | None:
        return self.dual_wm or self.working_memory

    def _apply_triage_to_working_memory(self, triage: Any) -> None:
        from nls.agentic.profile_guard_policy import wm_get_tactical_goal_strings

        wm = self._ring_working_memory()
        goals = getattr(triage, "goals", None) or []
        hints = getattr(triage, "hints", None) or []
        if goals and wm is not None:
            wm_goals = wm_get_tactical_goal_strings(wm)
            if wm_goals != goals[:5]:
                wm.clear_goals("tactical")
                for g in goals:
                    wm.add_goal(
                        level="tactical", content=g, source="task_extract",
                    )
        if hints and wm is not None:
            _clean_hints: list[str] = []
            for _h in hints:
                if _HINT_CREDENTIAL_RE.search(_h):
                    _hl = _h.lower()
                    _dom = "Project.Credential.Detected"
                    if "ghp_" in _hl or "gho_" in _hl or "github" in _hl:
                        _dom = "Project.Credential.GitHub"
                    elif "sk-ant-" in _hl or "anthropic" in _hl:
                        _dom = "Project.Credential.Anthropic"
                    elif "sk-" in _hl or "openai" in _hl:
                        _dom = "Project.Credential.OpenAI"
                    elif "postgres" in _hl:
                        _dom = "Project.Credential.Database"
                    elif "assembly" in _hl:
                        _dom = "Project.Credential.AssemblyAI"
                    try:
                        wm.upsert_credential(
                            domain=_dom, content=_h,
                            source="task_hints", salience=1.0,
                        )
                    except Exception:
                        _clean_hints.append(_h)
                else:
                    _clean_hints.append(_h)
            if _clean_hints:
                wm.upsert_fact(
                    domain="Task.Hints",
                    content=" | ".join(_clean_hints),
                )
        job_candidate = getattr(triage, "job_candidate", None) or {}
        if job_candidate and wm is not None:
            try:
                import json as _json

                wm.upsert_fact(
                    domain="Task.JobCandidate",
                    content=_json.dumps(job_candidate, ensure_ascii=False),
                )
            except Exception:
                pass

    async def extract_task_goals(
        self, user_input: str,
        *,
        history: list[dict] | None = None,
        model_override: str | None = None,
    ) -> tuple[list[str], list[str]]:
        """Pre-extract task goals and hints (wrapper over triage_user_turn)."""
        triage = await self.triage_user_turn(
            user_input, history=history, model_override=model_override,
        )
        return triage.goals, triage.hints

    def get_enabled_skills(self) -> list[str]:
        return self._get_enabled_skills()

    def enable_skill(self, skill_setup: Any) -> None:
        """Enable a skill at runtime (callable register hook or skill name string)."""
        try:
            if isinstance(skill_setup, str):
                from nls.tools.skill_manager import enable_skill as persist_enable_skill

                persist_enable_skill(self.agent_dir, skill_setup, refresh_fn=None)
            elif callable(skill_setup):
                skill_setup(self)
            self.refresh_tools()
            try:
                self._refresh_channel_awareness()
            except Exception:
                pass
        except Exception as exc:
            logger.warning("[Agent] agent=%s: enable_skill failed: %s", self.agent_id, exc)

    def reload_tools(self, tool_name: str | None = None) -> None:
        """Re-initialize tools (called from admin panel)."""
        self._initialize_tools()
        logger.info("[Agent] agent=%s: tools reloaded", self.agent_id)

    def refresh_tools(self) -> None:
        """Rebuild _openai_tools from _agent_tools and invalidate prompt cache.

        Called by skill adapters after injecting/replacing tools so the
        LLM sees the updated tool directory in the system prompt.
        """
        if self._agent_tools:
            try:
                from nls.tools.agent_tools.base import tools_to_openai_schema
                self._openai_tools = tools_to_openai_schema(self._agent_tools)
            except Exception as exc:
                logger.warning("[Agent] agent=%s: refresh_tools schema rebuild failed: %s",
                               self.agent_id, exc)
        try:
            self._refresh_channel_awareness()
        except Exception:
            pass
        logger.info(
            "[Agent] agent=%s: tools refreshed (%d tools, %d schemas)",
            self.agent_id,
            len(self._agent_tools or []),
            len(self._openai_tools or []),
        )

    # -- Brain delegation (DMN / Drives) --------------------------------

    def tick_dmn(self, skip_hypo_tick: bool = False) -> Any:
        """Tick DMN and return a DreamJob if activated."""
        if self.dmn is None:
            return None

        self.dmn.tick()

        if not skip_hypo_tick and self.hypothalamus is not None:
            tick_seconds = self.config.get("drives", {}).get(
                "tick_interval", 30.0,
            )
            self.hypothalamus.tick(tick_seconds)

        ach_level = 0.30
        if (
            self.hypothalamus is not None
            and "acetylcholine" in self.hypothalamus.hormones
        ):
            ach_level = self.hypothalamus.hormones["acetylcholine"].level

        if not self.dmn.should_activate(ach_level):
            return None

        from server.services.dream_job import DreamJob

        if self.dmn.should_active_dream():
            conversation_history = self.load_autonomous_history(max_turns=5)
            active_result = self.dmn.build_active_dream(
                conversation_history=conversation_history,
                recent_files=self._recent_files[-10:],
                recent_errors=self._recent_errors[-5:],
                self_state=self.self_state,
                theory_of_mind=self.theory_of_mind,
                working_memory=self.working_memory,
                predictive=self.predictive,
            )
            if active_result is not None:
                wonder_prompt, dream_type, type_config = active_result
                mode = f"active_{dream_type}"
                logger.info(
                    "Agent %s: DMN active dream (ach=%.3f, type=%s)",
                    self.agent_id, ach_level, dream_type,
                )
                job = DreamJob(
                    agent_id=self.agent_id,
                    prompt=wonder_prompt,
                    facts=[],
                    mode=mode,
                )
                job.dream_type_config = type_config
                job.wonder_prompt = wonder_prompt
                return job

        facts, prompt, mode = self.dmn.build_dream(
            self_state=self.self_state,
            theory_of_mind=self.theory_of_mind,
            working_memory=self.working_memory,
            predictive=self.predictive,
            narrative_self=self.narrative_self,
        )
        if not prompt:
            return None

        logger.info(
            "Agent %s: DMN activated (ach=%.3f, facts=%d, mode=%s)",
            self.agent_id, ach_level, len(facts), mode,
        )
        return DreamJob(
            agent_id=self.agent_id,
            prompt=prompt,
            facts=facts,
            mode=mode,
        )

    def process_dream_result(
        self, dream_response: str, mode: str = "replay",
    ) -> dict[str, Any]:
        """Process a completed daydream — extract signals and store facts."""
        result: dict[str, Any] = {
            "signals_extracted": 0,
            "facts_stored": 0,
            "sleep_triggered": False,
            "mode": mode,
        }

        signals_raw: list = []
        if self.ans is not None:
            mode_label = {
                "replay": "hippocampal replay",
                "seeded": "knowledge exploration (seeded)",
                "pure": "knowledge exploration (spontaneous)",
                "enriched": "enriched (model-generated)",
                "social_simulation": "social simulation",
            }.get(mode, mode)
            dmn_context = f"[DMN daydream -- {mode_label}]"
            signals_raw = self.ans.on_response(
                dmn_context, dream_response, self.hypothalamus,
            )
            result["signals_extracted"] = len(signals_raw)

            if mode == "replay":
                source_tag = "dmn"
            elif mode == "enriched":
                source_tag = "dmn_enriched"
            elif mode == "social_simulation":
                source_tag = "dmn_social"
            else:
                source_tag = "dmn_explore"
            for sig in signals_raw:
                sig.source = source_tag

            if self.domain_db is not None and signals_raw:
                facts_before = self.domain_db.fact_count()
                self._store_dream_facts(signals_raw)
                facts_after = self.domain_db.fact_count()
                result["facts_stored"] = facts_after - facts_before

        if (
            self.network_dynamics is not None
            and result["signals_extracted"] > 0
        ):
            relevance = min(1.0, result["signals_extracted"] * 0.2)
            self.network_dynamics.on_dmn_finding(relevance)

        if self.hypothalamus is not None:
            if result["signals_extracted"] > 0:
                self.hypothalamus.on_signal("dream_success")
            else:
                self.hypothalamus.on_signal("dream_failure")

        if self.dmn is not None:
            self.dmn.record_activation(mode=mode)

        _circ_enabled = (
            self.ans is not None
            and getattr(self.ans, "circadian", None) is not None
            and self.ans.circadian.enabled
        )
        if (
            self.ans is not None
            and self._on_sleep_requested is not None
            and not _circ_enabled
            and self._active_sessions == 0
            and not self.education_active
        ):
            from nls.models import SleepRequest
            hormones: dict[str, float] = {}
            if self.hypothalamus is not None:
                hormones = {
                    name: round(h.level, 3)
                    for name, h in self.hypothalamus.hormones.items()
                }
            should_sleep, reason = self.ans.check_sleep_trigger(
                self.hypothalamus,
            )
            if should_sleep:
                sleep_request = SleepRequest(
                    agent_id=self.agent_id,
                    reason=f"dream:{reason}",
                    signal_count=self.ans.get_buffer_summary().get(
                        "learnable_signals", 0,
                    ),
                    hormones=hormones,
                )
                logger.info(
                    "Agent %s: dream-triggered sleep (reason=%s, signals=%d)",
                    self.agent_id, reason, sleep_request.signal_count,
                )
                self._on_sleep_requested(sleep_request)
                result["sleep_triggered"] = True

        if self._event_logger is not None:
            self._event_logger.log(
                "dmn_dream",
                agent_id=self.agent_id,
                signals_extracted=result["signals_extracted"],
                facts_stored=result["facts_stored"],
                sleep_triggered=result["sleep_triggered"],
                preview=dream_response[:200],
            )

        logger.info(
            "Agent %s: dream processed (signals=%d, facts=%d, sleep=%s)",
            self.agent_id,
            result["signals_extracted"],
            result["facts_stored"],
            result["sleep_triggered"],
        )
        return result

    def _store_dream_facts(self, signals: list) -> None:
        """Store LEARN signals from dream processing into DomainDB.

        All DMN-sourced facts are namespaced under ``Autonomous.*`` so they
        never contaminate the ``Project.*`` / ``Code.*`` rings that belong to
        the user's actual work.  They remain fully searchable and available as
        seed context for subsequent DMN cycles (the ``Autonomous`` prefix is
        included in the DMN's own ``project_fact_domains``).
        """
        if self.domain_db is None:
            return
        for sig in signals:
            if sig.signal_type == "LEARN" and sig.content:
                domain = sig.domain_path or "Autonomous.Inference"
                # Remap to Autonomous.* unless already namespaced there.
                if not domain.startswith("Autonomous."):
                    domain = f"Autonomous.{domain}"
                self.domain_db.update_fact(
                    domain_path=domain,
                    new_value=sig.content,
                    block_height=self._sleep_count,
                )

    async def dream_generate_async(
        self, prompt: str,
        worker_model: Any = None, worker_tokenizer: Any = None,
    ) -> str:
        """Generate a daydream via the agent's orchestrator inference pipeline."""
        from nls.brain.thinking import strip_thinking

        gen_cfg = self.config.get("inference", {}).get("generation", {})

        _vllm, _adapter = self.inference_pipeline()
        if _vllm is None:
            raise RuntimeError(
                "dream_generate requires an inference client on AgentRuntime",
            )

        temperature = gen_cfg.get("temperature", 0.7)
        if not gen_cfg.get("do_sample", True):
            temperature = 0.0

        messages = [
            {"role": "system", "content": self._build_passive_dream_system_prompt()},
            {"role": "user", "content": prompt},
        ]
        from nls.runtime.inference_compat import micro_inference_extra_body

        _upstream = getattr(_vllm, "base_url", "") or ""
        result = await _vllm.generate(
            adapter_name=_adapter,
            max_tokens=gen_cfg.get("max_new_tokens", 1024),
            temperature=temperature,
            top_p=gen_cfg.get("top_p", 0.9),
            messages=messages,
            extra_body=micro_inference_extra_body(_upstream, thinking=False),
        )

        raw = result.text.strip()
        response, _ = strip_thinking(raw)
        return response

    def dream_generate(
        self, prompt: str,
        worker_model: Any = None, worker_tokenizer: Any = None,
    ) -> str:
        """Sync wrapper — always schedules on the server event loop."""
        coro = self.dream_generate_async(
            prompt,
            worker_model=worker_model,
            worker_tokenizer=worker_tokenizer,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=300.0)

        from server.main import app

        main_loop = getattr(app.state, "loop", None)
        if main_loop is not None and main_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, main_loop)
            return future.result(timeout=300.0)

        raise RuntimeError(
            "dream_generate requires a running asyncio event loop",
        )

    def add_dream_finding(self, finding: Any) -> None:
        self._dream_findings.append(finding)
        if len(self._dream_findings) > 50:
            self._dream_findings = self._dream_findings[-50:]
        if self.network_dynamics is not None:
            relevance = getattr(finding, "relevance_score", 0.0)
            if relevance > 0.5:
                self.network_dynamics.on_dmn_finding(relevance)

    def tick_drives(self) -> Any:
        """Evaluate drives and return a DriveGoal if the agent should act."""
        if self.drive_engine is None or self.hypothalamus is None:
            return None

        drives_cfg = self.config.get("drives", {})
        if not drives_cfg.get("autonomous_actions_enabled", True):
            return None

        tick_seconds = self.drive_engine.tick_interval
        self.hypothalamus.tick(tick_seconds)

        enrichment_parts: list[str] = []
        if self.self_state is not None:
            mood = getattr(self.self_state, "mood_label", "")
            energy = getattr(self.self_state, "energy", 1.0)
            if mood and mood != "neutral":
                enrichment_parts.append(f"Mood: {mood}.")
            if energy < 0.5:
                enrichment_parts.append(f"Energy: {energy:.0%}.")

        if self.theory_of_mind is not None:
            try:
                user = self.theory_of_mind.get_user()
                if user.interests:
                    top = sorted(
                        user.interests.items(),
                        key=lambda x: x[1], reverse=True,
                    )[:3]
                    if top:
                        enrichment_parts.append(
                            "User interests: "
                            + ", ".join(k for k, _ in top) + "."
                        )
            except Exception:
                pass

        if self.working_memory is not None:
            try:
                goals = self.working_memory.get_goal_stack(limit=2)
                if goals:
                    enrichment_parts.append(
                        "Goals: "
                        + "; ".join(g.content[:60] for g in goals)
                        + "."
                    )
            except Exception:
                pass

        if self.narrative_self is not None:
            try:
                if self.narrative_self.soul_wish:
                    enrichment_parts.append(
                        f"Soul wish: {self.narrative_self.soul_wish[:150]}."
                    )
                compound = self.narrative_self.get_compound_narrative(
                    max_recent=3,
                )
                if compound and "Narrative thread:" in compound:
                    thread_part = compound.split("Narrative thread:\n", 1)
                    if len(thread_part) > 1:
                        enrichment_parts.append(
                            f"Narrative thread: {thread_part[1][:200]}"
                        )
            except Exception:
                pass

        if enrichment_parts:
            ctx_str = " ".join(enrichment_parts)[:600]
        else:
            ctx_str = None
        self.drive_engine.set_enrichment_context(ctx_str)

        front_brain: dict | None = None
        if any(
            x is not None
            for x in (self.working_memory, self.theory_of_mind, self.predictive)
        ):
            front_brain = {}
            if self.working_memory is not None:
                try:
                    front_brain["wm_goals"] = [
                        g.content
                        for g in self.working_memory.get_goal_stack(limit=5)
                    ]
                except Exception:
                    pass
            if self.theory_of_mind is not None:
                try:
                    user = self.theory_of_mind.get_user()
                    front_brain["user_interests"] = list(
                        user.interests.keys(),
                    )
                except Exception:
                    pass
            if self.predictive is not None:
                try:
                    front_brain["high_uncertainty"] = [
                        d for d, _ in self.predictive.get_high_uncertainty_domains()
                    ]
                except Exception:
                    pass

        return self.drive_engine.tick(
            self.hypothalamus,
            calibrator=self.calibrator,
            ans=self.ans,
            front_brain_context=front_brain,
        )

    def generate_drive_query(
        self, goal: Any,
        worker_model: Any = None, worker_tokenizer: Any = None,
    ) -> str:
        """Generate a natural-language query for a drive goal via vLLM."""
        import re as _re
        from nls.brain.thinking import strip_thinking

        domain = getattr(goal, "domain", "")
        action = getattr(goal, "action_type", "reflect")
        human_domain = domain.replace(".", " ").replace(
            "User ", "their ",
        ).replace("Agent ", "my ").strip().lower()

        context_hint = ""
        if self.domain_db is not None:
            fact = self.domain_db.get_fact(domain)
            if fact and fact.current_value:
                val = fact.current_value
                if "\n[context:" in val:
                    val = val.split("\n[context:")[0].strip()
                context_hint = f' You already know: "{val}".'

        somatic_hint = ""
        if self.ofc is not None and self.self_state is not None:
            bias = self.ofc.somatic_evaluate(
                action_type=action,
                domain=domain,
                hormones=self.self_state.hormones,
                energy=self.self_state.energy,
            )
            if bias > 0.15:
                somatic_hint = " You feel confident about this."
            elif bias < -0.15:
                somatic_hint = " You feel hesitant about this."
            if self.hypothalamus is not None:
                self.hypothalamus.anticipate(bias)

        enrichment_parts: list[str] = []
        if self.self_state is not None:
            mood = getattr(self.self_state, "mood_label", "")
            energy = getattr(self.self_state, "energy", 1.0)
            momentum = getattr(self.self_state, "momentum", "")
            if mood and mood != "neutral":
                enrichment_parts.append(f"Your mood is {mood}.")
            if energy < 0.5:
                enrichment_parts.append(f"Energy is low ({energy:.0%}).")
            if momentum and momentum not in ("stable", ""):
                enrichment_parts.append(f"Your trajectory is {momentum}.")

        if self.predictive is not None:
            high_unc = self.predictive.get_high_uncertainty_domains()
            if high_unc:
                unc_str = ", ".join(d for d, _ in high_unc[:3])
                enrichment_parts.append(
                    f"You're uncertain about: {unc_str}.",
                )

        if self.theory_of_mind is not None:
            try:
                user = self.theory_of_mind.get_user()
                if user.interests:
                    top = sorted(
                        user.interests.items(),
                        key=lambda x: x[1], reverse=True,
                    )[:3]
                    if top:
                        enrichment_parts.append(
                            "The user cares about: "
                            + ", ".join(k for k, _ in top) + "."
                        )
            except Exception:
                pass

        if self.working_memory is not None:
            try:
                goals = self.working_memory.get_goal_stack(limit=2)
                if goals:
                    enrichment_parts.append(
                        "Active goals: "
                        + "; ".join(g.content[:60] for g in goals)
                        + "."
                    )
            except Exception:
                pass

        if self.narrative_self is not None:
            try:
                if self.narrative_self.soul_wish:
                    enrichment_parts.append(
                        f"Your purpose: {self.narrative_self.soul_wish[:150]}."
                    )
            except Exception:
                pass

        drive_gen_cfg = self.config.get("drives", {}).get(
            "drive_generation", {},
        )
        max_ctx = drive_gen_cfg.get("context_max_length", 500)
        enrichment = ""
        if enrichment_parts:
            enrichment = " " + " ".join(enrichment_parts)[:max_ctx]

        if action == "web_search":
            if domain.startswith("User."):
                prompt = (
                    f"You want to learn about {human_domain}. "
                    f"You don't know this yet."
                    f"{context_hint}{somatic_hint}{enrichment} "
                    f"Write ONE short, natural question to ask them. "
                    f"Just the question, no tags, no preamble."
                )
            else:
                prompt = (
                    f"You are curious about {human_domain}."
                    f"{context_hint}{somatic_hint}{enrichment} "
                    f"Write a concise web search query to learn more. "
                    f"Just the search query, nothing else."
                )
        elif action == "self_test":
            prompt = (
                f"You want to test your knowledge of {human_domain}."
                f"{context_hint}{somatic_hint}{enrichment} "
                f"Write a specific question to quiz yourself. "
                f"Just the question, nothing else."
            )
        elif action == "reach_out":
            prompt = (
                f"You've been thinking about {human_domain}."
                f"{context_hint}{somatic_hint}{enrichment} "
                f"Write a short, warm message to start a conversation. "
                f"Just the message, no tags, no preamble."
            )
        else:
            return getattr(goal, "query", "") or domain

        try:
            _vllm, _adapter = self.inference_pipeline()
            if _vllm is None:
                return getattr(goal, "query", "") or domain

            from nls.runtime.inference_compat import micro_inference_extra_body

            messages = [
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": prompt},
            ]
            _upstream = getattr(_vllm, "base_url", "") or ""
            _gen_kwargs = {
                "messages": messages,
                "adapter_name": _adapter,
                "max_tokens": 64,
                "temperature": 0.7,
                "top_p": 0.9,
                "extra_body": micro_inference_extra_body(_upstream, thinking=False),
            }

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    _vllm.generate(**_gen_kwargs), loop,
                )
                result = future.result(timeout=30.0)
            else:
                result = asyncio.run(_vllm.generate(**_gen_kwargs))

            raw = result.text.strip()
            raw, _ = strip_thinking(raw)

            query = raw.strip().strip('"\'').strip()
            query = _re.sub(
                r'\[(?:LEARN|LOOKUP|EVALUATE|UNKNOWN):[^\]]*\]\s*',
                '', query,
            ).strip()
            if query and len(query) < 300:
                return query
        except Exception as exc:
            logger.warning(
                "Agent %s: drive query generation failed: %s",
                self.agent_id, exc,
            )

        return getattr(goal, "query", "") or domain

    def execute_drive_action(self, goal: Any) -> dict[str, Any] | None:
        """Execute a drive action via the AgencyEngine."""
        if self.agency is None:
            return None

        result = self.agency.execute_drive_goal(
            goal=goal,
            tools=getattr(self, "_agent_tools", None),
            hypothalamus=self.hypothalamus,
        )

        if self.drive_engine is not None:
            self.drive_engine.experience.record_outcome(
                getattr(goal, "domain", ""),
                result.get("success", False),
            )
            action_type = getattr(goal, "action_type", "")
            if action_type in (
                "web_search", "read_page", "deep_browse", "disconfirm",
            ):
                self.drive_engine.experience.mark_searched(
                    getattr(goal, "domain", ""),
                )

        if result.get("success") and self.hypothalamus is not None:
            self.hypothalamus.on_signal("task_completed")

        return result

    def check_proactive_initiative(self) -> dict[str, Any] | None:
        """Check if the agent should proactively reach out."""
        import time
        findings = self.pop_dream_findings(max_count=1)
        if findings:
            finding = findings[0]
            return {
                "type": "dream_finding",
                "message": finding.to_reach_out_message()
                if hasattr(finding, "to_reach_out_message")
                else str(getattr(finding, "summary", "")),
                "finding": finding.to_broadcast()
                if hasattr(finding, "to_broadcast")
                else {},
            }

        if self.agency is None:
            return None

        idle_time = time.time() - (self._last_interaction or time.time())
        return self.agency.check_initiative(
            hypothalamus=self.hypothalamus,
            ans=self.ans,
            idle_seconds=idle_time,
        )

    def pop_dream_findings(self, max_count: int = 3) -> list:
        """Pop undelivered dream findings for user notification."""
        undelivered = [
            f for f in self._dream_findings
            if not getattr(f, "delivered", False)
        ]
        if not undelivered:
            return []
        undelivered.sort(
            key=lambda f: getattr(f, "relevance_score", 0.0),
            reverse=True,
        )
        batch = undelivered[:max_count]
        for f in batch:
            f.delivered = True
        return batch

    def process_message(
        self,
        user_input: str,
        history: list[dict] | None = None,
    ) -> AgentTurnResult:
        """Sync wrapper for process_message_async.

        Runs the async pipeline in the current or a new event loop.
        """
        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures
            future = asyncio.run_coroutine_threadsafe(
                self.process_message_async(user_input, history), loop,
            )
            return future.result(timeout=300)
        except RuntimeError:
            return asyncio.run(
                self.process_message_async(user_input, history),
            )
