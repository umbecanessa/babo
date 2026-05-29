"""v4 goal extraction and evaluation.

Extracted from loop_v3.py — same LLM prompts and logic, cleaner interface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

OrchestrationProfile = Literal[
    "conversational", "direct_tool", "solo_structured", "orchestrated",
]
IntentLabel = Literal[
    "CHAT_NOTHINK", "CHAT_THINK", "TASK_NOTHINK", "TASK_THINK",
]

_VALID_PROFILES = frozenset({
    "conversational", "direct_tool", "solo_structured", "orchestrated",
})
_VALID_INTENTS = frozenset({
    "CHAT_NOTHINK", "CHAT_THINK", "TASK_NOTHINK", "TASK_THINK",
})

_SUBSTANTIAL_ANSWER_CHARS = 200


@dataclass
class TurnTriage:
    """Unified upfront classification for a user turn."""

    intent: str = "CHAT_THINK"
    thinking: bool = True
    profile: str = "solo_structured"
    goals: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    deferred: list[dict] = field(default_factory=list)

    @property
    def is_conversational(self) -> bool:
        return self.profile == "conversational"

    @property
    def needs_tools(self) -> bool:
        return self.profile != "conversational"

    @property
    def allows_orchestration(self) -> bool:
        return self.profile == "orchestrated"

    def cap_profile_from_hints(self) -> None:
        """Downgrade profile using structured hint tokens from triage (language-agnostic)."""
        from nls.agentic.profile_guard_policy import (
            HINT_FORBID_TOOLS,
            apply_structured_hint_caps,
        )

        tokens = {h.strip().lower() for h in self.hints if h and h.strip()}
        if tokens & HINT_FORBID_TOOLS:
            self.profile = "conversational"
            self.goals = []
            return
        capped = apply_structured_hint_caps(self.profile, self.hints)
        if capped == "conversational":
            self.profile = "conversational"
            self.goals = []
        else:
            self.profile = capped

_THINKING_BLOCK_RE = re.compile(
    r"<think>.*?</think>",
    re.DOTALL,
)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

_MAX_GOALS = 5

_TASK_EXTRACT_SYSTEM = (
    "You extract discrete sub-tasks from a user message.\n"
    "Return a JSON object with three fields:\n"
    '  {"goals": [...], "hints": [...], "deferred": [...]}\n\n'
    "Rules for GOALS:\n"
    "- Extract WHAT the user wants done as short imperative phrases "
    "(< 15 words each).\n"
    "- Keep goals coarse — group closely related steps into a single "
    "goal. For example, 'login and list repos' is TWO goals (login, "
    "list repos), but 'list repos and tell me which are private' is "
    "ONE goal (the telling is part of the listing).\n"
    "- If the message contains multiple distinct goals (e.g. 'set up X AND "
    "then do Y'), list each as a separate sub-task.\n"
    "- If the message is a single simple task, return just that one goal.\n"
    "- For greetings, casual chat, recap/clarification of information "
    "the assistant likely already provided (e.g. 'what did you find?', "
    "'list the repos again', 'which repos?'), or other questions that "
    "need NO new tool use — return empty goals [].\n"
    "- Do NOT turn pure information/recap questions into fake 'tasks' "
    "like 'Check the time' or 'List repositories' when the user is only "
    "asking for a summary or repetition.\n\n"
    "Rules for DEFERRED:\n"
    "- Any instruction that should happen AFTER the main task is done.\n"
    "- Common patterns: 'send me on WhatsApp when done', "
    "'email me the results', 'message me on Telegram', "
    "'notify me when finished', 'ping me on ...', "
    "'let me know via email'.\n"
    "- Each deferred action is an object with 'channel' and 'instruction'.\n"
    "- channel must be one of: whatsapp, telegram, email, chat.\n"
    "- If no deferred actions, return empty deferred [].\n\n"
    "Rules for HINTS:\n"
    "- Suggestions about HOW to do the task go here. Phrases like "
    "'you can use X', 'feel free to Y', 'spin up sub-agents if needed', "
    "'take your time', 'be thorough' are methodology hints or permissions.\n"
    "- These are NOT goals — they are context the agent can use.\n"
    "- If no hints, return empty hints [].\n\n"
    "Return ONLY the JSON object. No explanation, no markdown fences, "
    "no thinking tags.\n\n"
    "Examples:\n"
    'User: "Log into GitHub and clone the repo then analyze the project"\n'
    'Output: {"goals": ["Log into GitHub", "Clone the repo", '
    '"Analyze the project"], "hints": [], "deferred": []}\n\n'
    'User: "Analyze the codebase, you can spin sub-agents if you need"\n'
    'Output: {"goals": ["Analyze the codebase"], '
    '"hints": ["Can use sub-agents for parallel exploration"], '
    '"deferred": []}\n\n'
    'User: "Do a deep analysis of the repo. Send me a summary on '
    'WhatsApp when done, I\'m going AFK"\n'
    'Output: {"goals": ["Deep analysis of the repo"], '
    '"hints": [], "deferred": [{"channel": "whatsapp", '
    '"instruction": "Send summary of analysis results"}]}\n\n'
    'User: "What time is it?"\n'
    'Output: {"goals": [], "hints": [], "deferred": []}\n\n'
    'User: "What repos are available?"\n'
    'Output: {"goals": [], "hints": [], "deferred": []}\n\n'
    'User: "Hey, how are you?"\n'
    'Output: {"goals": [], "hints": [], "deferred": []}\n'
)

_TURN_TRIAGE_SYSTEM = (
    "Classify the user's LATEST message and extract task structure.\n"
    "Return ONE JSON object with these fields:\n"
    '  {"intent": "...", "thinking": true|false, "profile": "...", '
    '"goals": [...], "hints": [...], "deferred": [...]}\n\n'
    "INTENT (exactly one):\n"
    "  CHAT_NOTHINK — greeting, thanks, name-setting, casual chat, "
    "confirmations, NO action needed.\n"
    "  CHAT_THINK — thoughtful advice/explanation/comparison with NO "
    "external tools (draft email, career advice, pros/cons).\n"
    "  TASK_NOTHINK — simple DO action: lookup URL, search online, "
    "open page, quick fetch, one command.\n"
    "  TASK_THINK — complex multi-step work: build, architect, deep "
    "research report, end-to-end project.\n\n"
    "THINKING: true for CHAT_THINK and TASK_THINK; false for *_NOTHINK.\n\n"
    "PROFILE (orchestration depth — how much machinery to use):\n"
    "  conversational — answer in prose only; goals=[]; no plan/team/todo.\n"
    "  direct_tool — 1-3 tools max (web_search, browser, read); NO "
    "plan, team, todo, delegate.\n"
    "  solo_structured — you execute (write/bash/plan/todo); NO team waves.\n"
    "  orchestrated — full EM stack allowed (plan + team + delegates).\n\n"
    "GOALS: short imperative phrases (<15 words). Empty [] for chat/recap "
    "('what did you find?', 'list again'). Group related steps into one goal.\n"
    "HINTS: methodology permissions — NOT goals. Prefer machine-readable tokens "
    "when constraints are clear:\n"
    "  forbid:team — user forbids teams/sub-agents/delegates (any language)\n"
    "  forbid:tools — user wants prose only, no tools\n"
    "  orchestration:solo — execute solo, no wave orchestration\n"
    "Also plain-language hints are allowed ('be thorough', etc.).\n"
    "DEFERRED: post-task channel delivery "
    '{"channel":"whatsapp|telegram|email|chat","instruction":"..."}.\n\n'
    "Rules:\n"
    "- 'Plan my week' / 'help dad think through careers' → conversational "
    "or solo_structured, NOT orchestrated (no team).\n"
    "- 'Check Wikipedia for X' / 'price of Y online' → direct_tool.\n"
    "- 'Build ICF end-to-end' / monorepo / waves → orchestrated.\n"
    "- User forbids teams/sub-agents/delegates (any language) → solo_structured "
    "or direct_tool, NEVER orchestrated; add hint forbid:team.\n"
    "- User forbids tools / wants chat only (any language) → conversational; "
    "add hint forbid:tools.\n"
    "- Multi-step solo task (git repo, file write, todos): ONE coarse goal, "
    "profile solo_structured — do NOT split into 3+ micro-goals.\n"
    "- Recap/clarification of prior assistant output → goals=[].\n"
    "- User sharing credentials to USE → TASK, not CHAT.\n\n"
    "Examples:\n"
    'User: "Hey, how are you?"\n'
    '{"intent":"CHAT_NOTHINK","thinking":false,"profile":"conversational",'
    '"goals":[],"hints":[],"deferred":[]}\n\n'
    'User: "Check Wikipedia — what year was the Eiffel Tower built?"\n'
    '{"intent":"TASK_NOTHINK","thinking":false,"profile":"direct_tool",'
    '"goals":["Look up Eiffel Tower construction year on Wikipedia"],'
    '"hints":[],"deferred":[]}\n\n'
    'User: "Draft a short email to my landlord about the leak"\n'
    '{"intent":"CHAT_THINK","thinking":true,"profile":"conversational",'
    '"goals":[],"hints":[],"deferred":[]}\n\n'
    'User: "Deep relocation research — send report on WhatsApp when done"\n'
    '{"intent":"TASK_THINK","thinking":true,"profile":"solo_structured",'
    '"goals":["Research relocation options and compile report"],'
    '"hints":[],"deferred":[{"channel":"whatsapp",'
    '"instruction":"Send full relocation research report"}]}\n\n'
    'User: "Build the ICF platform end-to-end with teams"\n'
    '{"intent":"TASK_THINK","thinking":true,"profile":"orchestrated",'
    '"goals":["Build ICF platform end-to-end"],'
    '"hints":["May use teams for parallel work"],"deferred":[]}\n\n'
    "Return ONLY the JSON object. No markdown fences or explanation.\n"
)

_GOAL_EVAL_SYSTEM = (
    "You evaluate which sub-tasks from a task list have been completed "
    "based on the conversation so far.\n"
    "You receive:\n"
    "- GOALS: a JSON array of sub-task strings\n"
    "- HINTS: methodology hints from the user (optional)\n"
    "- SUMMARY: a brief summary of actions taken so far\n\n"
    'Return a JSON object: {"done": [<indices of completed goals>], '
    '"pending": [<indices of still-pending goals>]}\n'
    "A goal is 'done' if the actions clearly addressed it — even if "
    "the method differed from the goal's wording. For example, if the "
    "goal says 'log in via CLI' but the agent used an API call with "
    "the same credentials and achieved the same result, that counts.\n"
    "If the agent attempted the goal and produced a reasonable answer "
    "or output, mark it done.\n"
    "Return ONLY the JSON object. No explanation."
)


def _generation_text(result: Any) -> str:
    return (result.text if hasattr(result, "text") else str(result or "")).strip()


def _json_parse_surface(text: str) -> str:
    text = _THINKING_BLOCK_RE.sub("", text).strip()
    text = _FENCE_RE.sub("", text).strip()
    return text


def _heuristic_task_goals(user_input: str) -> list[str]:
    """Fallback when the extractor model returns non-JSON (common on cloud relays)."""
    low = user_input.lower()
    if "[the user attached" in low:
        return ["Complete the user's request"]
    if len(user_input.strip()) < 20:
        return []
    if re.match(
        r"^\s*(hi|hello|hey|thanks|thank you|your name is|good morning)\b",
        low,
    ):
        return []
    task_markers = (
        "build", "create", "deploy", "implement", "monorepo", "github",
        "install", "set up", "setup", "analyze", "refactor", "write",
        "run ", "execute", "scaffold", "migration", "end-to-end",
        "platform", "repository", "repo ",
    )
    if any(m in low for m in task_markers):
        return ["Complete the user's request"]
    return []


def _heuristic_triage(user_input: str) -> TurnTriage:
    """Fallback when triage JSON parse fails."""
    low = user_input.lower()
    goals = _heuristic_task_goals(user_input)
    if "[the user attached" in low:
        return TurnTriage(
            intent="TASK_THINK",
            thinking=True,
            profile="direct_tool",
            goals=["Complete the user's request"],
        )
    if not goals and len(user_input.strip()) < 25:
        if re.match(
            r"^\s*(hi|hello|hey|thanks|thank you|your name is|good morning)\b",
            low,
        ):
            return TurnTriage(
                intent="CHAT_NOTHINK",
                thinking=False,
                profile="conversational",
            )
    orchestration_markers = (
        "monorepo", "end-to-end", "wave", "scaffold", "deploy",
        "engineering manager", "sub-agent", "sub agent",
    )
    if any(m in low for m in orchestration_markers):
        return TurnTriage(
            intent="TASK_THINK",
            thinking=True,
            profile="orchestrated",
            goals=goals or ["Complete the user's request"],
        )
    lookup_markers = (
        "wikipedia", "look up", "lookup", "search online", "check online",
        "open browser", "what is the price", "how much does",
        "find online", "google ",
    )
    if any(m in low for m in lookup_markers):
        return TurnTriage(
            intent="TASK_NOTHINK",
            thinking=False,
            profile="direct_tool",
            goals=goals or ["Look up the requested information"],
        )
    if goals:
        return TurnTriage(
            intent="TASK_THINK",
            thinking=True,
            profile="solo_structured",
            goals=goals,
        )
    return TurnTriage(
        intent="CHAT_THINK",
        thinking=True,
        profile="conversational",
    )


def _parse_triage_dict(parsed: dict) -> TurnTriage:
    intent = str(parsed.get("intent", "CHAT_THINK")).upper().strip()
    if intent not in _VALID_INTENTS:
        for label in _VALID_INTENTS:
            if label in intent:
                intent = label
                break
        else:
            intent = "CHAT_THINK" if parsed.get("goals") else "CHAT_NOTHINK"

    thinking = parsed.get("thinking")
    if not isinstance(thinking, bool):
        thinking = intent.endswith("THINK") and "NOTHINK" not in intent

    profile = str(parsed.get("profile", "solo_structured")).strip().lower()
    if profile not in _VALID_PROFILES:
        profile = "solo_structured"

    goals = [
        str(g).strip()
        for g in parsed.get("goals", [])
        if str(g).strip()
    ][:_MAX_GOALS]
    hints = [
        str(h).strip()
        for h in parsed.get("hints", [])
        if str(h).strip()
    ]
    deferred = [
        d for d in parsed.get("deferred", [])
        if isinstance(d, dict) and d.get("channel")
    ]

    triage = TurnTriage(
        intent=intent,
        thinking=thinking,
        profile=profile,
        goals=goals,
        hints=hints,
        deferred=deferred,
    )
    triage.cap_profile_from_hints()
    return triage


def cap_triage_profile_for_tools(
    triage: TurnTriage,
    allowed_tools: frozenset[str],
) -> None:
    """Structural cap: profile depth cannot exceed available tool surface."""
    from nls.agentic.orchestration_profile_spec import cap_profile_for_tool_surface

    capped = cap_profile_for_tool_surface(triage.profile, allowed_tools)
    if capped != triage.profile:
        triage.profile = capped


def _parse_triage_blob(blob: str) -> TurnTriage | None:
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        goals_m = re.search(r'"goals"\s*:\s*\[(.*?)\]', blob, re.DOTALL)
        if goals_m:
            inner = "[" + goals_m.group(1) + "]"
            try:
                goals = json.loads(inner)
                if isinstance(goals, list):
                    return TurnTriage(
                        goals=[
                            str(g).strip() for g in goals if str(g).strip()
                        ][:_MAX_GOALS],
                    )
            except json.JSONDecodeError:
                pass
        return None
    if isinstance(parsed, dict):
        return _parse_triage_dict(parsed)
    return None


async def triage_turn(
    vllm_client: Any,
    user_input: str,
    *,
    history: list[dict] | None = None,
    adapter_name: str | None = None,
) -> TurnTriage:
    """Single micro-inference: intent, thinking, profile, goals, hints, deferred."""
    if not (user_input or "").strip():
        return TurnTriage(
            intent="CHAT_NOTHINK",
            thinking=False,
            profile="conversational",
        )
    try:
        msgs: list[dict] = [
            {"role": "system", "content": _TURN_TRIAGE_SYSTEM},
        ]
        if history:
            for turn in history[-6:]:
                role = turn.get("role", "user")
                content = turn.get("content") or ""
                if role in ("user", "assistant") and content:
                    msgs.append({"role": role, "content": content[:300]})
        msgs.append({"role": "user", "content": user_input})

        result = await asyncio.wait_for(
            vllm_client.generate(
                messages=msgs,
                adapter_name=adapter_name,
                max_tokens=384,
                temperature=0.1,
                extra_body=_micro_extra_body(vllm_client),
            ),
            timeout=15,
        )
        text = _json_parse_surface(_generation_text(result))
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            triage = _parse_triage_blob(text[start : end + 1])
            if triage is not None:
                logger.info(
                    "Turn triage: intent=%s thinking=%s profile=%s goals=%d",
                    triage.intent,
                    triage.thinking,
                    triage.profile,
                    len(triage.goals),
                )
                return triage
    except Exception:
        logger.warning("Turn triage failed", exc_info=True)

    fallback = _heuristic_triage(user_input)
    logger.info(
        "Turn triage heuristic: intent=%s profile=%s goals=%d",
        fallback.intent,
        fallback.profile,
        len(fallback.goals),
    )
    return fallback


def substantial_answer(text: str, *, min_chars: int = _SUBSTANTIAL_ANSWER_CHARS) -> bool:
    return len((text or "").strip()) >= min_chars


def deferred_actions_to_goal_strings(deferred: list[dict]) -> list[str]:
    """Turn deferred channel actions into tactical goal strings."""
    out: list[str] = []
    for da in deferred:
        if not isinstance(da, dict):
            continue
        _ch = da.get("channel", "")
        _instr = da.get("instruction", "")
        if not _ch:
            continue
        if _ch in ("whatsapp", "telegram", "email"):
            out.append(
                f"DELIVER full results via {_ch}: {_instr} "
                f"(user is AFK — {_ch} is the primary output channel)"
            )
        else:
            out.append(f"Send results via {_ch}: {_instr}")
    return out


def _micro_extra_body(vllm_client: Any) -> dict[str, Any]:
    from nls.runtime.inference_compat import micro_inference_extra_body

    base = getattr(vllm_client, "base_url", "") or ""
    return micro_inference_extra_body(base, thinking=False)


async def extract_goals(
    vllm_client: Any,
    user_input: str,
    *,
    adapter_name: str | None = None,
    history: list[dict] | None = None,
) -> tuple[list[str], list[str], list[dict]]:
    """Extract goals, hints, and deferred via unified turn triage."""
    triage = await triage_turn(
        vllm_client,
        user_input,
        history=history,
        adapter_name=adapter_name,
    )
    return triage.goals, triage.hints, triage.deferred


async def evaluate_goals(
    vllm_client: Any,
    goals: list[str],
    action_summary: str,
    *,
    previous_pending: list[int] | None = None,
    hints: list[str] | None = None,
    adapter_name: str | None = None,
) -> list[int]:
    """Return indices of goals that are still pending.

    Retries once on transient errors. On failure returns *previous_pending*
    to preserve last-known state.
    """
    fallback = (
        previous_pending
        if previous_pending is not None
        else list(range(len(goals)))
    )
    hints_block = ""
    if hints:
        hints_block = f"\nHINTS: {json.dumps(hints)}\n"
    prompt = (
        f"GOALS: {json.dumps(goals)}\n"
        f"{hints_block}\n"
        f"SUMMARY OF ACTIONS TAKEN:\n{action_summary}"
    )
    for attempt in range(2):
        try:
            result = await asyncio.wait_for(
                vllm_client.generate(
                    messages=[
                        {"role": "system", "content": _GOAL_EVAL_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    adapter_name=adapter_name,
                    max_tokens=128,
                    temperature=0.1,
                    extra_body=_micro_extra_body(vllm_client),
                ),
                timeout=15,
            )
            text = (result.text or "").strip()
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                parsed = json.loads(text[start : end + 1])
                pending = parsed.get("pending", [])
                if isinstance(pending, list):
                    return [int(i) for i in pending if 0 <= int(i) < len(goals)]
        except Exception as exc:
            err_str = str(exc).lower()
            if attempt == 0 and any(
                t in err_str for t in ("event loop", "timeout", "connection")
            ):
                logger.warning(
                    "Goal eval transient error (attempt %d): %s — retrying",
                    attempt + 1,
                    str(exc)[:120],
                )
                await asyncio.sleep(1)
                continue
            logger.warning("Goal evaluation failed", exc_info=True)
            break
    return fallback
