"""v4 goal extraction and evaluation.

Extracted from loop_v3.py — same LLM prompts and logic, cleaner interface.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

OrchestrationProfile = Literal[
    "conversational", "solo_structured", "orchestrated", "squad_lead",
]
IntentLabel = Literal[
    "CHAT_NOTHINK", "CHAT_THINK", "TASK_NOTHINK", "TASK_THINK",
]

_VALID_PROFILES = frozenset({
    "conversational", "solo_structured", "orchestrated", "squad_lead",
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
    # True when goals/hints came from triage micro-inference; False for heuristic fallback.
    classifier_inferred: bool = True

    @property
    def is_conversational(self) -> bool:
        return self.profile == "conversational"

    @property
    def needs_tools(self) -> bool:
        """True when the turn should enter the agentic loop."""
        if (self.intent or "").upper().startswith("TASK"):
            return True
        return self.profile != "conversational"

    @property
    def allows_orchestration(self) -> bool:
        return self.profile in ("orchestrated", "squad_lead")

    def cap_profile_from_hints(self) -> None:
        """Downgrade profile using structured hint tokens from triage (language-agnostic)."""
        from nls.agentic.profile_guard_policy import (
            HINT_FORBID_TOOLS,
            apply_structured_hint_caps,
        )

        tokens = {h.strip().lower() for h in self.hints if h and h.strip()}
        if tokens & HINT_FORBID_TOOLS:
            self.profile = "conversational"
            return
        capped = apply_structured_hint_caps(self.profile, self.hints)
        if capped != self.profile:
            self.profile = capped

    def reconcile_orchestration_depth(self) -> None:
        """Resolve contradictory profile/goals/hints from the classifier."""
        from nls.agentic.profile_guard_policy import (
            reconcile_triage_orchestration_depth,
        )

        profile, hints = reconcile_triage_orchestration_depth(
            profile=self.profile,
            goals=self.goals,
            hints=self.hints,
            intent=self.intent,
        )
        self.profile = profile
        self.hints = hints

    def reconcile_fleet_vs_skill_hints(self, *, agent_id: str = "") -> None:
        """Strip conflicting skill-setup hints/goals when triage emitted fleet staffing."""
        from nls.agentic.fleet_triage_policy import apply_fleet_hint_policy

        self.goals, self.hints = apply_fleet_hint_policy(
            self.hints, self.goals, agent_id=agent_id,
        )

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

_TURN_TRIAGE_TOOL_CATALOG_PLACEHOLDER = "{tool_catalog}"

_TURN_TRIAGE_SYSTEM_BASE = (
    "Classify the user's LATEST message and extract task structure.\n"
    "Return ONE JSON object with these fields:\n"
    '  {"intent": "...", "thinking": true|false, "profile": "...", '
    '"goals": [...], "hints": [...], "deferred": [...]}\n\n'
    + _TURN_TRIAGE_TOOL_CATALOG_PLACEHOLDER
    + "\n\n"
    "INTENT (exactly one):\n"
    "  CHAT_NOTHINK — greeting, thanks, name-setting, casual chat, "
    "confirmations, NO action needed.\n"
    "  CHAT_THINK — thoughtful advice/explanation/comparison with NO "
    "external tools (draft email, career advice, pros/cons).\n"
    "  TASK_NOTHINK — simple DO action: lookup URL, search online, "
    "open page, quick fetch, one command.\n"
    "  TASK_THINK — complex multi-step work: build, architect, deep "
    "research report, end-to-end project, repo creation, deployment.\n\n"
    "THINKING: true for CHAT_THINK and TASK_THINK; false for *_NOTHINK.\n\n"
    "PROFILE (orchestration depth — how much machinery to use):\n"
    "  conversational — chat + quick tools (web_search, browser, read, "
    "clawhub, discover_tools). Answer in chat when possible; no plan, "
    "team, todo, delegate, bash, or file writes.\n"
    "  solo_structured — you execute (write/bash/plan/todo); NO team waves.\n"
    "  orchestrated — full EM stack (plan + team + delegates). DEFAULT for "
    "multi-phase builds (monorepo, backend+frontend+deploy, PRD implementation).\n"
    "  Triage profile is the starting depth only. Mid-loop, the agent may call "
    "adopt_orchestration_profile(profile='solo_structured'|orchestrated') when "
    "plan/todo/team/bash work needs deeper machinery — do not auto-bump profile "
    "on switch_mode(executing) alone.\n\n"
    "GOALS: short imperative phrases (<15 words). Empty [] for chat/recap "
    "('what did you find?', 'list again') and for pure CHAT_* intents with "
    "no action. For solo_structured/orchestrated TASK intents, goals MUST "
    "be non-empty.\n\n"
    "TOOL GATING (read AVAILABLE TOOLS above before choosing profile/hints):\n"
    "- conversational ALWAYS has lookup and discovery tools — use them for "
    "quick searches, fetches, and ClawHub skill discovery.\n"
    "- profile conversational + hint forbid:tools ONLY when the user "
    "explicitly wants prose-only with zero tool use.\n"
    "- When unsure between conversational and solo_structured, prefer "
    "conversational for single lookups and solo_structured for builds.\n"
    "- Match profile to depth: quick lookup / casual ask → conversational; "
    "single-step execution (files, shell) → solo_structured; PRD/platform/"
    "monorepo/deploy/multi-service → orchestrated.\n\n"
    "HINTS: methodology permissions — NOT goals. Machine-readable tokens ONLY "
    "when clearly applicable:\n"
    "  forbid:team — ONLY when the user's message explicitly forbids "
    "teams/sub-agents/delegates (quote their constraint). NEVER infer from "
    "task size or 'build it yourself' wording.\n"
    "  forbid:tools — prose-only answer; strip tool access via hint (keep profile)\n"
    "  orchestration:solo — user explicitly says work solo, no wave orchestration\n"
    "  setup:instruction_skill — configuring an installed ClawHub/AgentSkill "
    "(SKILL.md + bash; NOT skill_configure)\n"
    "  setup:configure_bundled — configure a pre-shipped Babo channel skill "
    "(telegram-channel, whatsapp-channel, email-channel) via skill_configure; "
    "NOT agent-authored skills (e.g. discord-channel is built via skill_install)\n"
    "  setup:interaction_policy — user sets who can reach the agent and where "
    "(private DMs, groups, channels, email threads with CC). ANY language/jargon. "
    "Use channel_inspect first, then skill_configure(interaction_mode= owner_private_only|"
    "shared_only|owner_plus_shared|trusted_allowlist|open_community OR interaction_intent=...) "
    "— never dm_policy='enabled'. Confirm with ask_user() when widening access.\n"
    "  setup:native_skill — authoring a NEW native Python NLS skill from scratch "
    "(nls/skills/bundled/ + register(); NOT skill_configure on existing bundled skills)\n"
    "  continuation:credential — user pasted a token/key after assistant asked; "
    "finish configuration, do NOT rebuild\n"
    "  continuation:configure_not_build — prior turn completed or paused waiting for "
    "credentials; configure existing bundled skill, not scaffold\n"
    "  lookup:chat_history — user references an EARLIER conversation turn, "
    "prior decision, or something said/discussed before (ANY language). "
    "Use when they ask what was discussed, what you said earlier, to continue "
    "a past topic, etc. NOT for repeating the immediately previous reply "
    "in the current short thread (goals=[] recap is enough).\n"
    "  fleet:squad_candidate — owner describes a multi-agent fleet (Discord mods + QA + "
    "lead, several agents with different roles, community server staffing). Includes "
    "'lead a team' when they mean mod agent + QA agent with different jobs — that is "
    "persistent squad_setup staffing, NOT the team() tool and NOT building code/bots. "
    "NEVER combine with setup:native_skill or setup:configure_bundled. If discord-channel "
    "is already connected, do NOT ask for bot token or scaffold skills. Emit when they "
    "want persistent agents working together; goals should mention squad_setup / "
    "set_member_job; agent confirms with ask_user() first.\n"
    "Also plain-language hints are allowed ('be thorough', etc.).\n"
    "DEFERRED: post-task channel delivery "
    '{"channel":"whatsapp|telegram|email|chat","instruction":"..."}.\n\n'
    "When conversational profile IS allowed:\n"
    "- CHAT_* intents, recap questions, advice, quick lookups.\n"
    "- TASK_NOTHINK: search online, open page, ClawHub search, one fetch.\n"
    "- TASK_THINK where the deliverable is prose in chat AND no file/shell "
    "work is needed (e.g. 'brainstorm names here', 'draft this paragraph').\n\n"
    "When conversational profile is FORBIDDEN (use solo_structured or orchestrated):\n"
    "- Attached PRD/spec + build, implement, create, deploy, scaffold, ship.\n"
    "- End-to-end platform, production app, monorepo, multi-service, repo + deploy.\n"
    "- Any request where available tools like read, write, bash, plan, team, "
    "browser, or web_search would help — including 'do it yourself' builds.\n"
    "Extract 2-5 coarse goals. NEVER forbid:tools or conversational for these.\n\n"
    "Other rules:\n"
    "- Never emit orchestration:solo or forbid:team unless the user explicitly "
    "forbids sub-agents or teams in their message.\n"
    "- PRD/spec + end-to-end build → orchestrated, hints=[], NEVER forbid:team.\n"
    "- Credentials/API keys in the message are for USE in the task → TASK, not CHAT.\n"
    "- User pastes bot token/API key alone after assistant asked for it → TASK_THINK, "
    "solo_structured. Pre-shipped channel (telegram/whatsapp/email/discord/slack): hints "
    "continuation:credential + setup:configure_bundled + skill_configure — "
    "NEVER setup:native_skill for these bundled channel plugins.\n"
    "- Configuring a pre-shipped Babo channel skill (telegram/whatsapp/email/discord/slack "
    "bot token) → solo_structured, hint setup:configure_bundled; use skill_configure.\n"
    "- When INSTALLED CHANNEL STATUS shows discord/slack CONNECTED, do NOT emit "
    "setup:configure_bundled or ask for bot token — use fleet:squad_candidate for "
    "multi-agent staffing instead.\n"
    "- When the user mentions Discord/Slack channel names, scope, or 'already connected', "
    "or you need to verify which channels are listening: goal should include "
    "channel_inspect(action='get', channel='discord'|'slack') — NOT ask_user for "
    "tokens or channel lists when inspect can answer.\n"
    "- Fleet Discord topology (ask owner via ask_user if unclear): SINGLE FACE = only "
    "lead's bot speaks in Discord, members use squad inbox; MULTI FACE = each agent "
    "that speaks in-channel needs its own bot token on that agent's Tools integration.\n"
    "- Configuring/setup of an installed ClawHub or AgentSkill package "
    "(bot token, env vars, running SKILL.md scripts) → solo_structured, "
    "hint setup:instruction_skill; goals mention read SKILL.md + verify, "
    "NOT skill_configure.\n"
    "- Building/creating a native Python NLS skill (register(), bundled layout) "
    "→ solo_structured, hint setup:native_skill; goals mention scaffold "
    "__init__.py + modules — NOT instruction-only SKILL.md.\n\n"
    "Profile selection:\n"
    "- 'Plan my week' / career advice → conversational.\n"
    "- 'Check Wikipedia for X' / 'search ClawHub for Discord' / quick lookup "
    "→ conversational (web_search/browser/clawhub).\n"
    "- Single-step execution (one file, one command, setup task) → solo_structured.\n"
    "- Recap/clarification of prior assistant output in the current thread "
    "→ CHAT, goals=[].\n"
    "- Reference to an earlier session/decision/topic not in context → "
    "CHAT or TASK as appropriate, goals=[] or task goals, "
    'hint lookup:chat_history (works in any language).\n'
    "- User starts conversational but will need SKILL.md + bash setup → conversational "
    "with hint setup:instruction_skill; agent may adopt solo_structured mid-loop.\n\n"
    "Examples:\n"
    'User: "Hey, how are you?"\n'
    '{"intent":"CHAT_NOTHINK","thinking":false,"profile":"conversational",'
    '"goals":[],"hints":[],"deferred":[]}\n\n'
    'User: "Check Wikipedia — what year was the Eiffel Tower built?" '
    '(web_search available)\n'
    '{"intent":"TASK_NOTHINK","thinking":false,"profile":"conversational",'
    '"goals":["Look up Eiffel Tower construction year on Wikipedia"],'
    '"hints":[],"deferred":[]}\n\n'
    'User: "Draft a short email to my landlord — just write it here, no commands"\n'
    '(no tool needed — answer is prose in chat)\n'
    '{"intent":"TASK_THINK","thinking":true,"profile":"conversational",'
    '"goals":["Draft landlord email about the leak"],'
    '"hints":["forbid:tools"],"deferred":[]}\n\n'
    'User: "[The user attached 1 file(s): prd.md ...] Read the PRD, create GitHub '
    'repo ICF-BenchBabo, build the full platform end-to-end (monorepo, Railway deploy)"\n'
    '(read, write, bash, plan, team available)\n'
    '{"intent":"TASK_THINK","thinking":true,"profile":"orchestrated",'
    '"goals":["Read PRD and extract requirements","Create GitHub repo and scaffold",'
    '"Build and deploy platform end-to-end"],'
    '"hints":[],"deferred":[]}\n\n'
    'User: "Deep relocation research — send report on WhatsApp when done"\n'
    '(web_search/browser available)\n'
    '{"intent":"TASK_THINK","thinking":true,"profile":"solo_structured",'
    '"goals":["Research relocation options and compile report"],'
    '"hints":[],"deferred":[{"channel":"whatsapp",'
    '"instruction":"Send full relocation research report"}]}\n\n'
    'User: "Build the ICF platform end-to-end but no sub-agents"\n'
    '{"intent":"TASK_THINK","thinking":true,"profile":"solo_structured",'
    '"goals":["Build ICF platform end-to-end"],'
    '"hints":["forbid:team","orchestration:solo"],"deferred":[]}\n\n'
    'User: "What did we decide about the Discord native skill?"\n'
    '{"intent":"CHAT_THINK","thinking":true,"profile":"conversational",'
    '"goals":[],"hints":["lookup:chat_history"],"deferred":[]}\n\n'
    'User: "Di cosa avevamo parlato ieri per il server Discord?"\n'
    '(Italian — same: references prior chat)\n'
    '{"intent":"CHAT_THINK","thinking":true,"profile":"conversational",'
    '"goals":[],"hints":["lookup:chat_history"],"deferred":[]}\n\n'
    'User: "MTA...xyz.AbC...defG" (prior assistant: "Paste your Telegram bot token")\n'
    '{"intent":"TASK_THINK","thinking":true,"profile":"solo_structured",'
    '"goals":["Configure telegram-channel with provided bot token via skill_configure",'
    '"Enable telegram-channel for this agent","Verify Telegram connection"],'
    '"hints":["continuation:credential","setup:configure_bundled",'
    '"continuation:configure_not_build"],"deferred":[]}\n\n'
    'User: "[attached prd.md] Read PRD, create repo, build full platform end-to-end"\n'
    'WRONG (do not output): profile solo_structured or hints forbid:team.\n'
    'RIGHT:\n'
    '{"intent":"TASK_THINK","thinking":true,"profile":"orchestrated",'
    '"goals":["Read PRD and extract requirements","Scaffold repo and monorepo",'
    '"Build and deploy platform end-to-end"],"hints":[],"deferred":[]}\n\n'
    'User: "I made a Discord server — I want a lead agent plus moderators and QA, '
    'each with their own role"\n'
    '{"intent":"CHAT_THINK","thinking":true,"profile":"solo_structured",'
    '"goals":[],"hints":["fleet:squad_candidate"],"deferred":[]}\n\n'
    'User: "I set up a Discord server — lead a team with one mod agent and one QA agent"\n'
    '(means persistent squad — NOT team() tool, NOT discord skill build)\n'
    '{"intent":"TASK_THINK","thinking":true,"profile":"solo_structured",'
    '"goals":["Propose lead + mod + QA squad roles","Confirm with ask_user",'
    '"Create squad via squad_setup after owner_confirmed=true"],'
    '"hints":["fleet:squad_candidate"],"deferred":[]}\n\n'
    'User: "I set up a Discord server — I want a lead agent plus moderators and QA, '
    'each with their own role"\n'
    '{"intent":"CHAT_THINK","thinking":true,"profile":"solo_structured",'
    '"goals":[],"hints":["fleet:squad_candidate"],"deferred":[]}\n\n'
    'User: (same thread, QA job details + "bot/discord already connected")\n'
    '{"intent":"TASK_THINK","thinking":true,"profile":"solo_structured",'
    '"goals":["Confirm single-face vs multi-face Discord with owner",'
    '"Define mod and QA member jobs","Create squad with squad_setup",'
    '"Spawn members and set_member_job"],"hints":["fleet:squad_candidate"],'
    '"deferred":[]}\n\n'
    'User: (after assistant asked for separate Mod/QA Discord bot tokens; '
    'discord already CONNECTED on lead; squad exists)\n'
    'User: "Mod bot token: … QA bot token: …"\n'
    '{"intent":"TASK_THINK","thinking":true,"profile":"solo_structured",'
    '"goals":["Spawn Mod and QA squad members if missing",'
    '"Configure each member via squad configure_member with its bot token",'
    '"Set member jobs and verify channel scope via channel_inspect target_agent_id"],'
    '"hints":["fleet:squad_candidate","continuation:credential"],'
    '"deferred":[]}\n'
    'NEVER setup:configure_bundled on the lead when INSTALLED CHANNEL STATUS '
    'shows discord CONNECTED and CONTINUATION CONTEXT mentions multi-face/squad.\n\n'
    'WRONG for squad staffing (never combine fleet + skill hints):\n'
    '{"hints":["fleet:squad_candidate","setup:native_skill"],'
    '"goals":["Scaffold native Discord skill"],...}\n\n'
    'User: "Build a native discord-channel skill from scratch"\n'
    '{"intent":"TASK_THINK","thinking":true,"profile":"solo_structured",'
    '"goals":["Scaffold discord-channel bundled skill"],'
    '"hints":["setup:native_skill"],"deferred":[]}\n\n'
    "Return ONLY the JSON object. No markdown fences, no thinking, no explanation.\n"
)


def summarize_tools_for_triage(tools: list[Any] | None) -> str:
    """Compact tool list for the triage classifier."""
    if not tools:
        return (
            "AVAILABLE TOOLS: (not loaded — assume read, write, bash, web_search, "
            "browser, plan, team, and related tools may exist.)"
        )
    lines: list[str] = ["AVAILABLE TOOLS (agent may call any that help):"]
    for tool in tools:
        name = (getattr(tool, "name", None) or "").strip()
        if not name:
            continue
        desc = (getattr(tool, "description", None) or "").strip().replace("\n", " ")
        if len(desc) > 100:
            desc = desc[:97] + "..."
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
        if len(lines) > 55:
            lines.append(f"- ... (+{len(tools) - 54} more tools)")
            break
    return "\n".join(lines)


def build_triage_system_prompt(*, tool_catalog: str | None = None) -> str:
    catalog = tool_catalog or summarize_tools_for_triage(None)
    return _TURN_TRIAGE_SYSTEM_BASE.replace(
        _TURN_TRIAGE_TOOL_CATALOG_PLACEHOLDER, catalog,
    )


_TURN_TRIAGE_SYSTEM = build_triage_system_prompt()

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

ProseVerdict = Literal[
    "awaiting_user_input",
    "deliverable_done",
    "should_continue",
    "duplicate",
]

_PROSE_EVAL_SYSTEM = (
    "You classify the agent's latest prose-only turn during a tool-using task.\n"
    "You receive GOALS, HINTS, recent ACTIONS, the latest PROSE text, and any "
    "recent TOOL ERRORS.\n\n"
    'Return JSON: {"prose_verdict": "<one of: awaiting_user_input | '
    "deliverable_done | should_continue | duplicate>\", "
    '"show_to_user": <true|false>}\n\n'
    "Verdict rules:\n"
    "- awaiting_user_input: agent is blocked on credentials, a choice, or "
    "missing info only the user can provide (401/unauthorized, invalid token, "
    "paste your API key). Exit the loop; show once.\n"
    "- duplicate: prose repeats a prior turn without new facts or asks. "
    "Do not show again; exit.\n"
    "- deliverable_done: agent reports verified success and the deliverable "
    "is complete (even if task_complete was not called).\n"
    "- should_continue: agent should keep working with tools; prose is "
    "premature status or incomplete. Set show_to_user to false to hold it.\n\n"
    "show_to_user: false when holding premature prose (should_continue) or "
    "duplicate; true when exiting on awaiting_user_input or deliverable_done.\n"
    "Return ONLY the JSON object."
)

_CREDENTIAL_BLOCK_MARKERS = (
    "401", "403", "unauthorized", "invalid token", "authentication failed",
    "forbidden", "bad credentials",
)
_AWAITING_USER_MARKERS = (
    "token", "api key", "password", "credential", "paste your", "provide your",
    "send me", "waiting for you", "need you to", "reset your", "regenerate",
    "invalidated", "expired", "bot token", "share the", "give me",
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
    task_markers = (
        "build", "create", "deploy", "implement", "monorepo", "github",
        "install", "set up", "setup", "analyze", "refactor", "write",
        "run ", "execute", "scaffold", "migration", "end-to-end",
        "platform", "repository", "repo ", "discord", "admin access",
    )
    if any(m in low for m in task_markers):
        return ["Complete the user's request"]
    if re.match(
        r"^\s*(hi|hello|hey|thanks|thank you|your name is|good morning)\b",
        low,
    ):
        return []
    return []


def _heuristic_triage(user_input: str) -> TurnTriage:
    """Fallback when triage JSON parse fails."""
    low = user_input.lower()
    goals = _heuristic_task_goals(user_input)
    if "[the user attached" in low:
        return TurnTriage(
            intent="TASK_THINK",
            thinking=True,
            profile="conversational",
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
            profile="conversational",
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
    if profile == "direct_tool":
        profile = "conversational"
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
    triage.reconcile_orchestration_depth()
    triage.reconcile_fleet_vs_skill_hints()
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


def _append_triage_history(
    msgs: list[dict],
    history: list[dict] | None,
) -> None:
    """History for triage: full last assistant turn, truncated earlier turns."""
    if not history:
        return
    last_asst_idx: int | None = None
    for idx in range(len(history) - 1, -1, -1):
        if history[idx].get("role") == "assistant":
            last_asst_idx = idx
            break
    tail = history[-6:]
    for rel_idx, turn in enumerate(tail):
        role = turn.get("role", "user")
        content = turn.get("content") or ""
        if role not in ("user", "assistant") or not content:
            continue
        abs_idx = len(history) - len(tail) + rel_idx
        cap = 2000 if abs_idx == last_asst_idx else 500
        msgs.append({"role": role, "content": content[:cap]})


async def triage_turn(
    vllm_client: Any,
    user_input: str,
    *,
    history: list[dict] | None = None,
    adapter_name: str | None = None,
    tool_catalog: str | None = None,
    environment_context: str | None = None,
    continuation_context: str | None = None,
) -> TurnTriage:
    """Single micro-inference: intent, thinking, profile, goals, hints, deferred."""
    if not (user_input or "").strip():
        return TurnTriage(
            intent="CHAT_NOTHINK",
            thinking=False,
            profile="conversational",
        )
    try:
        _system = build_triage_system_prompt(tool_catalog=tool_catalog)
        if environment_context:
            _system = f"{_system}\n\n{environment_context.strip()}"
        if continuation_context:
            _system = (
                f"{_system}\n\nCONTINUATION CONTEXT (trust over keyword guesses):\n"
                f"{continuation_context.strip()}"
            )
        msgs: list[dict] = [
            {"role": "system", "content": _system},
        ]
        _append_triage_history(msgs, history)
        msgs.append({"role": "user", "content": user_input})
        _micro_msgs, _micro_body = _prepare_micro_inference(
            msgs, vllm_client, adapter_name=adapter_name,
        )

        result = await asyncio.wait_for(
            vllm_client.generate(
                messages=_micro_msgs,
                adapter_name=adapter_name,
                max_tokens=384,
                temperature=0.1,
                extra_body=_micro_body,
            ),
            timeout=15,
        )
        text = _json_parse_surface(_generation_text(result))
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            triage = _parse_triage_blob(text[start : end + 1])
            if triage is not None:
                triage.classifier_inferred = True
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
    fallback.classifier_inferred = False
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


def _prepare_micro_inference(
    messages: list[dict],
    vllm_client: Any,
    *,
    adapter_name: str | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    from nls.runtime.inference_compat import prepare_micro_inference

    return prepare_micro_inference(
        messages,
        vllm_client=vllm_client,
        adapter_name=adapter_name,
    )


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
            _eval_msgs, _eval_body = _prepare_micro_inference(
                [
                    {"role": "system", "content": _GOAL_EVAL_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                vllm_client,
                adapter_name=adapter_name,
            )
            result = await asyncio.wait_for(
                vllm_client.generate(
                    messages=_eval_msgs,
                    adapter_name=adapter_name,
                    max_tokens=128,
                    temperature=0.1,
                    extra_body=_eval_body,
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


def prose_fingerprint(text: str) -> str:
    """Stable hash for duplicate-prose detection."""
    normalized = re.sub(r"\s+", " ", (text or "").lower().strip())
    return hashlib.md5(normalized.encode()).hexdigest()[:16]


def _heuristic_prose_verdict(
    prose: str,
    *,
    prior_hash: str = "",
    last_error: str = "",
    consecutive_text_only: int = 0,
) -> tuple[ProseVerdict, bool]:
    """Rule-based fallback when micro-inference is unavailable."""
    text = (prose or "").strip()
    if not text:
        return "should_continue", False

    fp = prose_fingerprint(text)
    if prior_hash and fp == prior_hash and consecutive_text_only >= 2:
        return "duplicate", False

    err_low = (last_error or "").lower()
    text_low = text.lower()
    if any(m in err_low for m in _CREDENTIAL_BLOCK_MARKERS):
        if any(m in text_low for m in _AWAITING_USER_MARKERS):
            return "awaiting_user_input", True
        if consecutive_text_only >= 2:
            return "awaiting_user_input", True

    if consecutive_text_only >= 2:
        if any(m in text_low for m in _AWAITING_USER_MARKERS):
            if "?" in text[-400:] or "please" in text_low:
                return "awaiting_user_input", True

    return "should_continue", False


async def evaluate_prose_turn(
    vllm_client: Any,
    *,
    goals: list[str],
    action_summary: str,
    prose: str,
    hints: list[str] | None = None,
    last_error: str = "",
    prior_prose_hash: str = "",
    consecutive_text_only: int = 0,
    adapter_name: str | None = None,
) -> tuple[ProseVerdict, bool]:
    """Classify a prose-only turn; returns (verdict, show_to_user)."""
    prose = (prose or "").strip()
    if not prose:
        return "should_continue", False

    fp = prose_fingerprint(prose)
    if prior_prose_hash and fp == prior_prose_hash and consecutive_text_only >= 2:
        return "duplicate", False

    if vllm_client is None:
        return _heuristic_prose_verdict(
            prose,
            prior_hash=prior_prose_hash,
            last_error=last_error,
            consecutive_text_only=consecutive_text_only,
        )

    hints_block = ""
    if hints:
        hints_block = f"HINTS: {json.dumps(hints)}\n"
    err_block = ""
    if last_error:
        err_block = f"RECENT TOOL ERROR:\n{last_error[:400]}\n\n"
    prompt = (
        f"GOALS: {json.dumps(goals)}\n"
        f"{hints_block}"
        f"{err_block}"
        f"ACTIONS:\n{action_summary[-3000:]}\n\n"
        f"PROSE:\n{prose[:2000]}"
    )
    for attempt in range(2):
        try:
            _eval_msgs, _eval_body = _prepare_micro_inference(
                [
                    {"role": "system", "content": _PROSE_EVAL_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                vllm_client,
                adapter_name=adapter_name,
            )
            result = await asyncio.wait_for(
                vllm_client.generate(
                    messages=_eval_msgs,
                    adapter_name=adapter_name,
                    max_tokens=96,
                    temperature=0.1,
                    extra_body=_eval_body,
                ),
                timeout=12,
            )
            text = (result.text or "").strip()
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                parsed = json.loads(text[start : end + 1])
                verdict = str(parsed.get("prose_verdict", "")).strip()
                show = parsed.get("show_to_user", True)
                if verdict in (
                    "awaiting_user_input",
                    "deliverable_done",
                    "should_continue",
                    "duplicate",
                ):
                    if verdict == "should_continue":
                        show = bool(parsed.get("show_to_user", False))
                    elif verdict == "duplicate":
                        show = False
                    else:
                        show = bool(parsed.get("show_to_user", True))
                    return verdict, show
        except Exception as exc:
            err_str = str(exc).lower()
            if attempt == 0 and any(
                t in err_str for t in ("event loop", "timeout", "connection")
            ):
                await asyncio.sleep(1)
                continue
            logger.warning("Prose evaluation failed", exc_info=True)
            break

    return _heuristic_prose_verdict(
        prose,
        prior_hash=prior_prose_hash,
        last_error=last_error,
        consecutive_text_only=consecutive_text_only,
    )
