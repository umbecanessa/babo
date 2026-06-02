"""Pure helper functions and constants for the chat module."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_FRONT_BRAIN_KEYS = (
    "working_memory", "narrative", "theory_of_mind",
    "predictive_processing", "network_dynamics",
)

_TOOLCALL_BLOCK_RE = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)
_INLINE_JSON_TOOLCALL_RE = re.compile(
    r"\{\s*[\"']name[\"']\s*:\s*[\"'][^\"']+[\"']\s*,\s*"
    r"[\"']arguments[\"']\s*:\s*"
    r"\{(?:[^{}]|\{[^{}]*\})*\}\s*\}",
    re.DOTALL,
)

# Pseudo tool calls the model emits as prose instead of API tool_calls.
_PSEUDO_TOOL_CALL_RE = re.compile(
    r"\b(?:clawhub|discover_tools|skill_configure|crystallize_skill|mcp_manage|"
    r"web_search|web_fetch|browser)\(\s*action\s*=",
    re.IGNORECASE,
)

_CHAT_TOOLCALL_NUDGE = (
    "[System: Your previous response looked like a raw tool call in plain text. "
    "Use the tool API (structured tool_calls), not prose syntax like "
    "toolname(action='...'). If you already have the answer, reply in natural "
    "language.]"
)

_SIGNAL_TAG_RE = re.compile(r"\[([A-Za-z_]+)(?:[:.]([^\]]*))?\]")

from nls.runtime.response_cleanup import strip_nls_artifacts, strip_nls_signal_calls

_TASK_PATTERNS = re.compile(
    r"\b("
    r"build|create|make|setup|set up|install|deploy|implement|write|"
    r"fix|debug|run|execute|start|configure|scaffold|generate|"
    r"refactor|migrate|update|upgrade|connect|integrate|"
    r"add|remove|delete|modify|change|edit|replace|"
    r"test|check|verify|validate|analyze|scan|"
    r"download|upload|fetch|pull|push|clone|commit|"
    r"boot|launch|spin up|provision|initialize|"
    r"clean|organize|plan|schedule|book|find|search|look up|"
    r"sort|backup|restore|send|forward|sync|automate|"
    r"monitor|track|scrape|crawl|summarize|translate|convert|"
    r"compile|format|parse|extract|merge|split|resize|compress|"
    r"encrypt|decrypt|publish|host|serve|open|enable|disable"
    r")\b",
    re.IGNORECASE,
)

_INTENT_PATTERNS = re.compile(
    r"^\s*("
    r"(i\s+want\s+to|i\s+wanna|i('d|\s+would)\s+like\s+to|i\s+need\s+to|i\s+need\s+you\s+to)\b|"
    r"(can\s+you|could\s+you|would\s+you|will\s+you)\b.{5,}|"
    r"(help\s+me|show\s+me\s+how\s+to|set\s+me\s+up|get\s+me|hook\s+me\s+up)\b|"
    r"(how\s+(can|do)\s+i)\b.{5,}|"
    r"(is\s+there\s+a\s+way\s+to|is\s+it\s+possible\s+to)\b|"
    r"(please|pls)\b.{5,}|"
    r"(i\s+wish\s+i\s+could|if\s+only\s+i\s+could)\b"
    r")",
    re.IGNORECASE,
)

# Task cues anywhere in the message (compound turns: greeting + real ask).
_COMPOUND_TASK_RE = re.compile(
    r"\b("
    r"help me|how can we|how do we|how can i|how do i|how can you|"
    r"give you access|admin access|set it up|set up|created a|"
    r"discord server|discord bot|install a skill|search clawhub"
    r")\b",
    re.IGNORECASE,
)

_CONVERSATIONAL_PATTERNS = re.compile(
    r"^\s*("
    r"(hi|hello|hey|good morning|good evening|how are you|what's up|sup)\b|"
    r"(who are you|what is your name|what can you do|tell me about)\b|"
    r"(your name is|call yourself|i('ll| will) call you|you are called)\b|"
    r"(thanks|thank you|bye|goodbye|see you|later)\b|"
    r"(yes|no|ok|okay|sure|yep|nah|nope)\s*[.!?]*\s*$"
    r")",
    re.IGNORECASE,
)

_IMAGE_MIMES = frozenset({
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/bmp", "image/tiff", "image/svg+xml",
})

_AUDIO_MIMES = frozenset({
    "audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4", "audio/flac",
    "audio/aac", "audio/webm", "audio/x-wav", "audio/x-m4a",
})

_AUDIO_EXTS = frozenset({
    ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".wma", ".webm",
})

_ARCHIVE_MIMES = frozenset({
    "application/zip", "application/x-tar", "application/gzip",
    "application/x-7z-compressed", "application/x-rar-compressed",
    "application/x-bzip2", "application/x-xz",
})

_ARCHIVE_EXTS = frozenset({
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
})

_OFFICE_EXT_MAP: dict[str, str] = {
    ".docx": "document", ".doc": "document",
    ".xlsx": "spreadsheet", ".xls": "spreadsheet",
    ".pptx": "presentation", ".ppt": "presentation",
    ".rtf": "document",
}

_CLASSIFY_PROMPT = (
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
    "\u2014 that is CHAT. But if the user asks the agent to USE info "
    "(credentials, tokens, accounts) or DO something with it, "
    "that is ALWAYS TASK.\n\n"
    "Reply with exactly one label: TASK_THINK, TASK_NOTHINK, "
    "CHAT_THINK, or CHAT_NOTHINK."
)


def _build_nls_metadata(status: dict, **extra: Any) -> dict:
    """Build NLS metadata dict with front-brain sections included."""
    _facts = status.get("facts_in_memory")
    if _facts is None:
        _facts = status.get("fact_count", 0)
    nls: dict = {
        "hormones": status.get("hormones", {}),
        "ans": status.get("ans", {}),
        "facts_in_memory": _facts,
        "turn_count": status.get("turn_count", 0),
        "sleep_count": status.get("sleep_count", 0),
        "heartbeat": status.get("heartbeat", {}),
    }
    for key in _FRONT_BRAIN_KEYS:
        val = status.get(key)
        if not val and key == "narrative":
            val = status.get("narrative_self")
        if not val and key == "predictive_processing":
            val = status.get("predictive")
        if val:
            nls[key] = val
    nls.update(extra)
    return nls


def _format_file_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def _file_kind(mime: str, name: str) -> str:
    """Classify a file into a kind for the attachment context hint."""
    nl = name.lower()
    ext = "." + nl.rsplit(".", 1)[-1] if "." in nl else ""
    if mime in _IMAGE_MIMES:
        return "image"
    if mime == "application/pdf" or ext == ".pdf":
        return "document"
    if ext in _OFFICE_EXT_MAP:
        return _OFFICE_EXT_MAP[ext]
    if mime in _AUDIO_MIMES or ext in _AUDIO_EXTS:
        return "audio"
    if mime in _ARCHIVE_MIMES or ext in _ARCHIVE_EXTS:
        return "archive"
    return "text"


def _augment_with_attachments(
    user_input: str,
    attachments: list[dict],
    workspace_dir: str = "",
) -> str:
    """Prepend file-attachment context to the user message."""
    if not attachments:
        return user_input

    lines: list[str] = []
    kinds: set[str] = set()
    for att in attachments:
        name = att.get("name", "file")
        rel_path = att.get("path", "")
        mime = att.get("mime_type", "")
        size = att.get("size", 0)
        kind = _file_kind(mime, name)
        kinds.add(kind)
        if workspace_dir and rel_path:
            from pathlib import Path as _P
            abs_path = str(_P(workspace_dir) / rel_path)
        else:
            abs_path = rel_path
        lines.append(
            f"  - {name} ({kind}, {_format_file_size(size)})\n"
            f"    read(path=\"{abs_path}\")"
        )

    hint_parts: list[str] = []
    readable = kinds & {"text", "document", "spreadsheet", "presentation", "archive"}
    if readable:
        hint_parts.append(
            "Use the read tool with the EXACT path shown above to examine "
            "text, PDF, Word, Excel, PowerPoint, RTF, and archive files. "
            "Do NOT shorten or guess the path \u2014 use the full path as given."
        )
    if "audio" in kinds:
        hint_parts.append(
            "Use the read tool on audio files to get a transcript."
        )
    if "image" in kinds:
        hint_parts.append(
            "Use the vision tool to analyze images (describe or ask)."
        )

    header = (
        f"[The user attached {len(attachments)} file(s):\n"
        + "\n".join(lines)
        + "\n" + " ".join(hint_parts) + "]\n\n"
    )
    return header + user_input


def _is_task_message(text: str) -> bool:
    """Detect whether a user message is a task request vs conversation."""
    if not text or len(text.strip()) < 5:
        return False
    stripped = text.strip()
    # Task/intent markers win over a conversational opener ("Hi! Your name is X — set up Y").
    if _TASK_PATTERNS.search(stripped):
        return True
    if _INTENT_PATTERNS.search(stripped):
        return True
    if _COMPOUND_TASK_RE.search(stripped):
        return True
    if _CONVERSATIONAL_PATTERNS.match(stripped):
        return False
    return False


def response_has_pseudo_tool_call(text: str) -> bool:
    """True when the model wrote a tool invocation as prose instead of API tool_calls."""
    return bool(_PSEUDO_TOOL_CALL_RE.search(text or ""))


def _message_implies_agentic_work(text: str) -> bool:
    """Whether the user message should enter the agentic loop even without inline tool calls."""
    if not text:
        return False
    if "[the user attached" in text.lower():
        return True
    return _is_task_message(text)


def _runtime_uses_local_vllm(runtime: Any) -> bool:
    """True when this agent's orchestrator pipeline points at local/LAN vLLM."""
    try:
        from nls.runtime.inference_compat import inference_host_is_local

        client, _adapter = runtime.inference_pipeline()
        if client is None:
            return False
        base = (getattr(client, "base_url", "") or "").strip()
        if not base:
            return False
        return inference_host_is_local(base)
    except Exception:
        return False


async def _classify_intent(
    vllm_client,
    message: str,
    history: list[dict] | None = None,
    *,
    adapter_name: str | None = None,
) -> str:
    """Classify intent and thinking need in a single LLM call.

    Returns one of: task_think, task_nothink, chat_think, chat_nothink.
    """
    try:
        msgs: list[dict] = [{"role": "system", "content": _CLASSIFY_PROMPT}]
        if history:
            for turn in history[-6:]:
                role = turn.get("role", "user")
                content = turn.get("content", "")
                if role in ("user", "assistant") and content:
                    msgs.append({
                        "role": role,
                        "content": content[:300],
                    })
        msgs.append({"role": "user", "content": message})
        from nls.runtime.inference_compat import prepare_micro_inference

        _micro_msgs, _micro_body = prepare_micro_inference(
            msgs, vllm_client=vllm_client, adapter_name=adapter_name,
        )
        result = await vllm_client.generate(
            adapter_name=adapter_name,
            messages=_micro_msgs,
            max_tokens=64,
            temperature=0.0,
            extra_body=_micro_body,
        )
        raw = (result.text if hasattr(result, "text") else str(result or "")).upper().strip()
        for label in ("TASK_THINK", "TASK_NOTHINK", "CHAT_THINK", "CHAT_NOTHINK"):
            if label in raw:
                return label.lower()
        if "TASK" in raw:
            return "task_think"
        if not raw and _is_task_message(message):
            return "task_think"
        return "chat_nothink"
    except Exception:
        logger.exception("LLM intent classifier failed")
        return "chat_nothink"


def _dedup_signal_tags(text: str) -> str:
    """Remove duplicate signal tags and other NLS artifacts from model output."""
    return strip_nls_artifacts(text)


def _build_agentic_metadata(result) -> dict:
    """Build frontend-compatible metadata from an AgenticResult."""
    from nls.agentic.types import EventType

    step_data: dict = {}
    for ev in (result.events or []):
        d = ev.data if hasattr(ev, "data") else {}
        if ev.type == EventType.TOOL_EXECUTION_END:
            step = d.get("iteration", 0)
            if step not in step_data:
                step_data[step] = {
                    "step": step, "tool_calls": [],
                    "tool_results": [], "hormones": {},
                    "duration_ms": 0,
                }
            tc_name = d.get("tool_name", d.get("name", "tool"))
            step_data[step]["tool_calls"].append({"name": tc_name})
            step_data[step]["tool_results"].append({
                "success": not d.get("is_error", False),
            })
            step_data[step]["duration_ms"] += d.get("duration_ms", 0)

    events_summary = list(step_data.values())

    plan_steps_meta: list[dict] | None = None
    try:
        _last_plan_snapshot: list[dict] | None = None
        _step_status_patches: dict[int, str] = {}
        for ev in (result.events or []):
            d = ev.data if hasattr(ev, "data") else {}
            if ev.type == EventType.AGENTIC_PLAN:
                raw = d.get("steps", [])
                if raw:
                    _last_plan_snapshot = [
                        {
                            "label": (
                                s.get("label", s.get("step", ""))
                                if isinstance(s, dict) else str(s)
                            ),
                            "status": (
                                s.get("status", "pending")
                                if isinstance(s, dict) else "pending"
                            ),
                        }
                        for s in raw
                    ]
                    _step_status_patches.clear()
            elif ev.type == EventType.PLAN_STEP_UPDATE:
                idx = d.get("step_index", -1)
                st = d.get("status", "done")
                if idx >= 0:
                    _step_status_patches[idx] = st

        if _last_plan_snapshot:
            for idx, st in _step_status_patches.items():
                if idx < len(_last_plan_snapshot):
                    _last_plan_snapshot[idx]["status"] = st
            plan_steps_meta = _last_plan_snapshot
    except Exception:
        pass

    meta: dict = {
        "agentic": True,
        "iterations": result.iterations,
        "tool_calls": result.total_tool_calls,
        "aborted": result.aborted,
        "abort_reason": result.abort_reason,
        "events": events_summary,
    }
    if plan_steps_meta:
        meta["plan_steps"] = plan_steps_meta
    return meta


def _build_activity_status(
    tool_names: list[str],
    tool_calls: list[dict],
) -> str:
    """Build a context-aware activity status from tool calls."""
    labels = {
        "browser": "Browsing",
        "terminal": "Running command",
        "bash": "Running command",
        "file_read": "Reading",
        "file_write": "Writing",
        "file_edit": "Editing",
        "read": "Reading",
        "write": "Writing",
        "edit": "Editing",
        "file_search": "Searching files",
        "git": "Git",
        "web_search": "Searching the web",
        "web_fetch": "Fetching page",
        "wikipedia": "Reading Wikipedia",
        "arxiv_search": "Searching Arxiv",
        "test_runner": "Running tests",
        "code_analyze": "Analyzing code",
    }

    if not tool_names:
        return "Working..."

    name = tool_names[0]
    label = labels.get(name, name.replace("_", " ").title())

    if tool_calls:
        args = tool_calls[0].get("arguments", {})
        if name == "browser":
            url = args.get("url", "")
            action = args.get("action", "")
            if url:
                short_url = url[:50] + ("..." if len(url) > 50 else "")
                return f"{label}: {short_url}"
            if action:
                return f"{label}: {action}"
        elif name in ("terminal", "bash"):
            cmd = args.get("command", "")
            if cmd:
                return f"{label}: {cmd[:40]}"
        elif name in ("read", "write", "edit") or name.startswith("file_"):
            path = args.get("path", "")
            if path:
                return f"{label}: {path}"
        elif name in ("web_search", "wikipedia", "arxiv_search"):
            query = args.get("query", "")
            if query:
                return f"{label}: {query[:40]}"
        elif name == "web_fetch":
            url = args.get("url", "")
            if url:
                short_url = url[:50] + ("..." if len(url) > 50 else "")
                return f"{label}: {short_url}"
        elif name == "git":
            cmd = args.get("command", "")
            if cmd:
                return f"{label}: {cmd[:40]}"

    return f"{label}..."
