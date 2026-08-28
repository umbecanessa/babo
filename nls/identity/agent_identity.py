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
    r"[\"']?([A-Za-z][A-Za-z0-9\-' ]{0,30})[\"']?",
    # Italian
    r"(?:ti chiami|il tuo nome (?:è|e)|ti do il nome)\s+"
    r"[\"']?([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ0-9\-' ]{0,30})[\"']?",
    # French
    r"(?:tu t['’]appelles|ton nom (?:est|sera)|je t['’]appelle)\s+"
    r"[\"']?([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ0-9\-' ]{0,30})[\"']?",
    # Spanish
    r"(?:te llamas|tu nombre (?:es|será|sera)|te llamo)\s+"
    r"[\"']?([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ0-9\-' ]{0,30})[\"']?",
    # German
    r"(?:du heißt|du heisst|dein name (?:ist|wird)|ich nenne dich)\s+"
    r"[\"']?([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ0-9\-' ]{0,30})[\"']?",
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


_INVALID_AGENT_NAMES = frozenset({
    "yours", "you", "me", "the", "a", "an", "is", "are", "called", "name",
    "agent", "user", "assistant", "bot", "hey", "hello", "thanks",
})


def _valid_agent_name(name: str) -> bool:
    cleaned = (name or "").strip().strip("\"'")
    if len(cleaned) < 2 or len(cleaned) > 40:
        return False
    if cleaned.lower() in _INVALID_AGENT_NAMES:
        return False
    # Allow lowercase ("your name is babo"); other patterns already require
    # an initial capital in the regex when the phrasing is ambiguous.
    if not cleaned[0].isalpha():
        return False
    return True


def detect_name_from_user_input(user_input: str) -> str | None:
    """Return a name the user explicitly assigned this turn, if any."""
    if not (user_input or "").strip():
        return None
    for pattern in _NAMING_PATTERNS:
        m = re.search(pattern, user_input, re.IGNORECASE)
        if not m:
            continue
        for g in m.groups():
            if g:
                name = g.strip("\"' ").strip()
                if _valid_agent_name(name):
                    return name
    return None


def detect_name_from_signals(
    signals: list,
    user_input: str,
    response: str,
    *,
    agent_id: str = "",
    domain_db: Any | None = None,
    current_name: str | None = None,
) -> str | None:
    """Detect if the agent accepted a name this turn.

    Returns the detected name string, or None.
    """
    established = (current_name or "").strip()

    # Step 1: User's explicit naming
    user_named = detect_name_from_user_input(user_input)

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
        if established and user_named.lower() == established.lower():
            return None
        if signal_name and signal_name.lower() != user_named.lower():
            logger.warning(
                "Agent %s: name conflict — user='%s' signal='%s'. Trusting user.",
                agent_id, user_named, signal_name,
            )
        return user_named

    # Never rename from LEARN alone once the agent already has a name.
    if established or not signal_name:
        return None

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
    if not _valid_agent_name(name):
        return None
    return name


def naming_turn_user_prefix(assigned_name: str) -> str:
    """Steer the model away from repeating the birth greeting after naming."""
    return (
        f"[User assigned your name: {assigned_name}]\n"
        "Reply in one or two short sentences only: thank them, confirm you "
        f"are {assigned_name}, and offer to help.\n"
        "Do NOT repeat your initialization or \"just came online\" greeting.\n"
        "Do NOT say you have no name or ask what to call you.\n\n"
    )


def sync_identity_name_in_working_memory(
    working_memory: Any | None,
    name: str,
) -> None:
    """Update Cryptex identity ring when the user names the agent mid-chat."""
    if working_memory is None or not name:
        return
    try:
        from datetime import datetime

        from nls.brain.identity_renderer import DOMAIN_UNNAMED_BLOCK

        if hasattr(working_memory, "populate_genesis_identity"):
            working_memory.populate_genesis_identity(
                agent_name=name,
                today_date=datetime.now().strftime("%A, %B %d, %Y"),
            )
            return
        ring = getattr(working_memory, "_rings", {}).get("identity")
        if ring is not None:
            ring.upsert_slot(
                domain="name",
                content=name,
                slot_type="identity",
                salience=1.0,
                source="user",
                access="malleable",
            )
            ring.remove_by_domain(DOMAIN_UNNAMED_BLOCK)
    except Exception:
        logger.debug(
            "sync_identity_name_in_working_memory failed for %r",
            name,
            exc_info=True,
        )


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
