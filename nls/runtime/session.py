"""Session management — history persistence and state I/O.

Provides standalone functions that operate on an agent_dir (Path) so that
any runtime can use them without inheritance.  AgentRuntime delegates here
instead of carrying the logic inline.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONV_TOOL_MAX = 300
_AUTO_TOOL_MAX = 200


# ── Conversation history ──────────────────────────────────────────

def load_conversation_history(
    agent_dir: Path,
    max_turns: int = 20,
) -> list[dict]:
    """Load persisted conversation history, filtering autonomous messages."""
    history_path = agent_dir / "conversation_history.json"
    if not history_path.exists():
        return []
    try:
        with open(history_path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return []
        conversation = json.loads(raw)
        if not isinstance(conversation, list):
            return []
        filtered = [
            msg for msg in conversation
            if not (msg.get("metadata") or {}).get("autonomous")
            and not str(msg.get("content") or "").startswith("[Autonomous task")
        ]
        if len(filtered) > max_turns * 2:
            filtered = filtered[-(max_turns * 2):]
        return filtered
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Agent %s: failed to load conversation history: %s",
                        agent_dir.name, exc)
        return []


def save_conversation_history(
    agent_dir: Path,
    history: list[dict],
    max_turns: int = 20,
) -> None:
    """Persist conversation history with tool-result truncation."""
    conversation: list[dict] = []
    for msg in history:
        role = msg.get("role")
        if role in ("user", "assistant"):
            conversation.append(msg)
        elif role == "tool":
            entry = {**msg}
            content = msg.get("content") or ""
            if len(content) > _CONV_TOOL_MAX:
                entry["content"] = content[:_CONV_TOOL_MAX] + "\n... (truncated)"
            conversation.append(entry)
    if len(conversation) > max_turns * 2:
        conversation = conversation[-(max_turns * 2):]

    history_path = agent_dir / "conversation_history.json"
    try:
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(conversation, f, indent=2, ensure_ascii=False)
    except (OSError, FileNotFoundError) as exc:
        logger.warning("Agent %s: failed to save conversation history: %s",
                        agent_dir.name, exc)


# ── Autonomous history ────────────────────────────────────────────

def load_autonomous_history(
    agent_dir: Path,
    max_turns: int = 10,
) -> list[dict]:
    """Load autonomous/drive conversation history."""
    history_path = agent_dir / "autonomous_history.json"
    if not history_path.exists():
        return []
    try:
        with open(history_path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return []
        conversation = json.loads(raw)
        if not isinstance(conversation, list):
            return []
        if len(conversation) > max_turns * 2:
            conversation = conversation[-(max_turns * 2):]
        return conversation
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Agent %s: failed to load autonomous history: %s",
                        agent_dir.name, exc)
        return []


def save_autonomous_history(
    agent_dir: Path,
    history: list[dict],
    max_turns: int = 10,
) -> None:
    """Persist autonomous history with tool-result truncation."""
    conversation: list[dict] = []
    for msg in history:
        role = msg.get("role")
        if role in ("user", "assistant"):
            conversation.append(msg)
        elif role == "tool":
            entry = {**msg}
            content = msg.get("content") or ""
            if len(content) > _AUTO_TOOL_MAX:
                entry["content"] = content[:_AUTO_TOOL_MAX] + "\n... (truncated)"
            conversation.append(entry)
    if len(conversation) > max_turns * 2:
        conversation = conversation[-(max_turns * 2):]

    history_path = agent_dir / "autonomous_history.json"
    try:
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(conversation, f, indent=2, ensure_ascii=False)
    except (OSError, FileNotFoundError) as exc:
        logger.warning("Agent %s: failed to save autonomous history: %s",
                        agent_dir.name, exc)


# ── Agent name persistence ────────────────────────────────────────

def save_agent_name(agent_dir: Path, name: str) -> None:
    """Persist agent name to agent_meta.json."""
    meta_path = agent_dir / "agent_meta.json"
    try:
        meta: dict = {}
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        meta["agent_name"] = name
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        logger.info("Agent %s: name set to '%s'", agent_dir.name, name)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Agent %s: failed to save name: %s", agent_dir.name, exc)


def load_agent_name(agent_dir: Path) -> str | None:
    """Read agent name from agent_meta.json."""
    meta_path = agent_dir / "agent_meta.json"
    if not meta_path.exists():
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f).get("agent_name")
    except Exception:
        return None
