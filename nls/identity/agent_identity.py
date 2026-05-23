"""Agent Identity — name detection from signals (M-027).

Extracted from ServerRuntime._detect_name_from_signals.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NAMING_PATTERNS = [
    r"(?:your name (?:is|will be|shall be))\s+"
    r"[\"']?([A-Z][a-zA-Z\-']{1,30})[\"']?",
    r"(?:(?:i(?:'ll| will) )?call you|"
    r"(?:let(?:'s|s)) call you)\s+"
    r"[\"']?([A-Z][a-zA-Z\-']{1,30})[\"']?",
    r"you (?:are|shall be) called\s+"
    r"[\"']?([A-Z][a-zA-Z\-']{1,30})[\"']?",
    r"(?:i(?:'ll| will) )?name you\s+"
    r"[\"']?([A-Z][a-zA-Z\-']{1,30})[\"']?",
    r"(?:the name|I (?:thought|think)|how about)\s+"
    r"[\"']?([A-Z][a-zA-Z\-']{1,30})[\"']?"
    r"(?:\s+(?:fit|suits|works|sounds|is perfect|for you))?",
]


def detect_name_from_signals(
    signals: list,
    user_input: str,
    response: str,
    *,
    agent_id: str = "",
    domain_db: Any | None = None,
) -> str | None:
    """Detect if the agent accepted a name this turn.

    Returns the detected name string, or None.
    """
    # Step 1: User's explicit naming
    user_named: str | None = None
    for pattern in _NAMING_PATTERNS:
        m = re.search(pattern, user_input, re.IGNORECASE)
        if m:
            for g in m.groups():
                if g:
                    user_named = g.strip("\"' ")
                    break
            if user_named:
                break

    # Step 2: LEARN signals for Agent.*.Name
    signal_name: str | None = None
    for sig in signals:
        if isinstance(sig, dict):
            sig_type = sig.get("type", "") or ""
            domain = sig.get("domain", "") or ""
            content = sig.get("content", "") or ""
            pipe_fact = sig.get("pipe_fact", "") or ""
        else:
            sig_type = getattr(sig, "signal_type", "") or ""
            domain = getattr(sig, "domain_path", "") or ""
            content = getattr(sig, "content", "") or ""
            pipe_fact = getattr(sig, "pipe_fact", "") or ""

        if sig_type.upper() != "LEARN":
            continue
        if re.search(r"(?i)agent\.[^.]*\.(agent)?name", domain):
            name = _extract_name(pipe_fact or content)
            if name:
                signal_name = name
                break

    # Step 3: Resolve — user's naming always wins
    if user_named:
        if signal_name and signal_name.lower() != user_named.lower():
            logger.warning(
                "Agent %s: name conflict — user='%s' signal='%s'. Trusting user.",
                agent_id, user_named, signal_name,
            )
        return user_named

    return signal_name


def _extract_name(content: str) -> str | None:
    """Pull a proper-cased name from fact content."""
    content = content.strip()
    content = re.sub(
        r"^(?:The (?:agent'?s?|user'?s?|my) name is|"
        r"(?:Agent|User) name:?|My name is)\s*",
        "", content, flags=re.IGNORECASE,
    ).strip().strip("\"'.,!?")
    words = content.split()
    if not words:
        return None
    name = words[0] if len(words) <= 3 else " ".join(words[:2])
    name = name.strip()
    if len(name) < 2 or len(name) > 40:
        return None
    if not name[0].isupper():
        return None
    return name


def save_agent_name(agent_dir: Path, name: str, agent_id: str = "") -> None:
    """Persist the agent's name to agent_meta.json."""
    meta_path = agent_dir / "agent_meta.json"
    try:
        meta: dict = {}
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        meta["agent_name"] = name
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        logger.info("Agent %s: name set to '%s'", agent_id, name)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Agent %s: failed to save name: %s", agent_id, exc)
