"""v4 goal extraction and evaluation.

Extracted from loop_v3.py — same LLM prompts and logic, cleaner interface.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

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
    "Return ONLY the JSON object. No explanation.\n\n"
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


async def extract_goals(
    vllm_client: Any,
    user_input: str,
) -> tuple[list[str], list[str], list[dict]]:
    """Extract coarse-grained goals, methodology hints, and deferred actions.

    Returns (goals, hints, deferred). Max ``_MAX_GOALS`` goals per extraction.
    Deferred actions are ``{"channel": "whatsapp", "instruction": "..."}`` dicts.
    """
    try:
        result = await asyncio.wait_for(
            vllm_client.generate(
                messages=[
                    {"role": "system", "content": _TASK_EXTRACT_SYSTEM},
                    {"role": "user", "content": user_input},
                ],
                adapter_name=None,
                max_tokens=256,
                temperature=0.1,
                extra_body={
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            ),
            timeout=15,
        )
        text = (result.text or "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
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
                return goals, hints, deferred
        # Fallback: plain array
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            goals = json.loads(text[start : end + 1])
            if isinstance(goals, list):
                return (
                    [str(g).strip() for g in goals if str(g).strip()][:_MAX_GOALS],
                    [],
                    [],
                )
    except Exception:
        logger.warning("Goal extraction failed", exc_info=True)
    return [], [], []


async def evaluate_goals(
    vllm_client: Any,
    goals: list[str],
    action_summary: str,
    *,
    previous_pending: list[int] | None = None,
    hints: list[str] | None = None,
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
                    adapter_name=None,
                    max_tokens=128,
                    temperature=0.1,
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
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
