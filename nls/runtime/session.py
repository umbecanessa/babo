"""Session management — history persistence and state I/O.

Provides standalone functions that operate on an agent_dir (Path) so that
any runtime can use them without inheritance.  AgentRuntime delegates here
instead of carrying the logic inline.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONV_TOOL_MAX = 300
_AUTO_TOOL_MAX = 200
_TRANSCRIPT_MAX_TURNS = 200
_TRANSCRIPT_FILENAME = "chat_transcript.jsonl"
_LEGACY_TRANSCRIPT_JSON = "chat_transcript.json"


def _transcript_path(agent_dir: Path) -> Path:
    return agent_dir / _TRANSCRIPT_FILENAME


def _read_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return []
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", path.name, exc)
        return []


def _write_json_list(path: Path, items: list[dict]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
    except (OSError, FileNotFoundError) as exc:
        logger.warning("Failed to write %s: %s", path.name, exc)


def _trim_model_history(conversation: list[dict], max_entries: int) -> list[dict]:
    """Drop oldest tool noise first so user/assistant turns survive longer."""
    trimmed = list(conversation)
    while len(trimmed) > max_entries:
        removed = False
        for i, msg in enumerate(trimmed):
            if msg.get("role") == "tool":
                trimmed.pop(i)
                removed = True
                break
        if not removed:
            trimmed.pop(0)
    return trimmed


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
        conversation = _trim_model_history(conversation, max_turns * 2)

    history_path = agent_dir / "conversation_history.json"
    try:
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(conversation, f, indent=2, ensure_ascii=False)
    except (OSError, FileNotFoundError) as exc:
        logger.warning("Agent %s: failed to save conversation history: %s",
                        agent_dir.name, exc)


# ── UI chat transcript (user-visible turns only) ─────────────────
# Append-only JSONL — same collection pattern as agentic loop logs and
# read_index.jsonl (one structured event per line, crash-safe append).

def _transcript_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl_messages(path: Path) -> list[dict]:
    if not path.exists():
        return []
    messages: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    messages.append(row)
    except OSError as exc:
        logger.warning("Failed to read %s: %s", path.name, exc)
    return messages


def _append_jsonl_message(path: Path, entry: dict[str, Any]) -> None:
    row = dict(entry)
    row.setdefault("ts", _transcript_ts())
    if "timestamp" not in row:
        row["timestamp"] = time.time()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except (OSError, FileNotFoundError) as exc:
        logger.warning("Failed to append %s: %s", path.name, exc)


def _read_last_transcript_message(agent_dir: Path) -> dict | None:
    path = _transcript_path(agent_dir)
    if not path.exists():
        return None
    last: dict | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    last = row
    except OSError:
        return None
    return last


def _ensure_transcript_jsonl(agent_dir: Path) -> Path:
    """Ensure chat transcript exists as JSONL; migrate legacy JSON once."""
    path = _transcript_path(agent_dir)
    if path.exists():
        return path

    legacy = agent_dir / _LEGACY_TRANSCRIPT_JSON
    if legacy.exists():
        for row in _read_json_list(legacy):
            _append_jsonl_message(path, row)
        return path

    return path


def _transcript_visible_message(msg: dict) -> bool:
    role = msg.get("role")
    if role not in ("user", "assistant"):
        return False
    content = (msg.get("content") or "").strip()
    if role == "user":
        return bool(content)
    if content:
        return True
    meta = msg.get("metadata") or {}
    return bool(meta.get("agentic"))


def _migrate_transcript_from_conversation(agent_dir: Path) -> list[dict]:
    """One-time backfill when chat_transcript.jsonl is missing."""
    path = _ensure_transcript_jsonl(agent_dir)
    if path.exists() and path.stat().st_size > 0:
        return _read_jsonl_messages(path)

    migrated: list[dict] = []
    for msg in load_conversation_history(agent_dir, max_turns=_TRANSCRIPT_MAX_TURNS):
        if not _transcript_visible_message(msg):
            continue
        entry: dict[str, Any] = {
            "role": msg["role"],
            "content": msg.get("content") or "",
        }
        if msg.get("reasoning"):
            entry["reasoning"] = msg["reasoning"]
        if msg.get("metadata"):
            entry["metadata"] = msg["metadata"]
        if msg.get("timestamp"):
            entry["timestamp"] = msg["timestamp"]
        _append_jsonl_message(path, entry)
        migrated.append(entry)
    return migrated


def load_chat_transcript(
    agent_dir: Path,
    *,
    limit: int | None = 400,
) -> list[dict]:
    """Load user-visible chat for UI restore.

    ``limit`` caps how many messages are returned (newest tail).  Pass ``None``
    for the full append-only log (used by ``chat_history`` search).
    """
    _ensure_transcript_jsonl(agent_dir)
    path = _transcript_path(agent_dir)
    transcript = _read_jsonl_messages(path)
    if not transcript:
        transcript = _migrate_transcript_from_conversation(agent_dir)
    if limit is not None and len(transcript) > limit:
        transcript = transcript[-limit:]
    return transcript


def query_chat_transcript(
    agent_dir: Path,
    *,
    query: str = "",
    role: str = "",
    limit: int | None = 20,
    offset: int = 0,
    line_start: int | None = None,
    line_end: int | None = None,
    newest_first: bool = True,
) -> tuple[list[dict], int]:
    """Search the full chat transcript log.

    Returns ``(matches, total_lines)``.  Each match includes a 1-based
    ``line`` number matching the JSONL file order.
    """
    _ensure_transcript_jsonl(agent_dir)
    messages = _read_jsonl_messages(_transcript_path(agent_dir))
    if not messages:
        messages = _migrate_transcript_from_conversation(agent_dir)

    total = len(messages)
    indexed: list[dict] = []
    for i, msg in enumerate(messages, start=1):
        row = dict(msg)
        row["line"] = i
        indexed.append(row)

    if line_start is not None or line_end is not None:
        ls = max(1, line_start or 1)
        le = min(total, line_end or total)
        indexed = [m for m in indexed if ls <= m["line"] <= le]

    if role in ("user", "assistant"):
        indexed = [m for m in indexed if m.get("role") == role]

    q = (query or "").strip().lower()
    if q:
        def _matches(row: dict) -> bool:
            blob = " ".join(
                str(row.get(k) or "")
                for k in ("content", "reasoning")
            ).lower()
            meta = row.get("metadata") or {}
            if isinstance(meta, dict):
                blob += " " + json.dumps(meta, ensure_ascii=False).lower()
            return q in blob

        indexed = [m for m in indexed if _matches(m)]

    if q or (role and newest_first):
        if newest_first:
            indexed = list(reversed(indexed))

    if not q and not role and line_start is None and not newest_first:
        # Chronological tail (recent messages).
        end = total - offset if offset else total
        start = max(0, end - (limit or total))
        indexed = indexed[start:end]
    elif offset > 0:
        indexed = indexed[offset:]

    if limit is not None and limit > 0:
        indexed = indexed[:limit]
    return indexed, total


def session_ui_transcript_path(agent_dir: Path, session_key: str) -> Path:
    """Per-branch UI transcript (tool chips / agentic metadata)."""
    from nls.runtime.channels import _session_filename

    stem = _session_filename(session_key).removesuffix(".json")
    return agent_dir / "sessions" / f"{stem}_ui.jsonl"


def _read_last_message_at(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    last: dict[str, Any] | None = None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return None
    return last


def _append_transcript_turn_at(
    path: Path,
    *,
    last: dict[str, Any] | None,
    user: str | None = None,
    assistant: str | None = None,
    reasoning: str | None = None,
    metadata: dict | None = None,
    attachments: list | None = None,
) -> None:
    user_text = (user or "").strip()
    asst_text = (assistant or "").strip()
    if not user_text and not asst_text and not metadata:
        return

    if user_text:
        if not (
            last
            and last.get("role") == "user"
            and last.get("content") == user_text
        ):
            _append_jsonl_message(path, {
                "role": "user",
                "content": user_text,
                **({"attachments": attachments} if attachments else {}),
            })
            last = {"role": "user", "content": user_text}

    if asst_text or metadata:
        if (
            last
            and last.get("role") == "assistant"
            and (last.get("content") or "") == asst_text
            and not metadata
        ):
            return
        entry: dict[str, Any] = {
            "role": "assistant",
            "content": asst_text,
        }
        if reasoning:
            entry["reasoning"] = reasoning
        if metadata:
            entry["metadata"] = metadata
        _append_jsonl_message(path, entry)


def append_chat_transcript_turn(
    agent_dir: Path,
    *,
    user: str | None = None,
    assistant: str | None = None,
    reasoning: str | None = None,
    metadata: dict | None = None,
    attachments: list | None = None,
) -> None:
    """Append one visible chat turn to the append-only transcript log."""
    path = _ensure_transcript_jsonl(agent_dir)
    _append_transcript_turn_at(
        path,
        last=_read_last_transcript_message(agent_dir),
        user=user,
        assistant=assistant,
        reasoning=reasoning,
        metadata=metadata,
        attachments=attachments,
    )


def append_session_transcript_turn(
    agent_dir: Path,
    session_key: str,
    *,
    user: str | None = None,
    assistant: str | None = None,
    reasoning: str | None = None,
    metadata: dict | None = None,
    attachments: list | None = None,
) -> None:
    """Append a UI transcript row for a non-main chat branch."""
    sk = (session_key or "").strip()
    if not sk or sk == "websocket:main":
        append_chat_transcript_turn(
            agent_dir,
            user=user,
            assistant=assistant,
            reasoning=reasoning,
            metadata=metadata,
            attachments=attachments,
        )
        return

    path = session_ui_transcript_path(agent_dir, sk)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text("", encoding="utf-8")
    _append_transcript_turn_at(
        path,
        last=_read_last_message_at(path),
        user=user,
        assistant=assistant,
        reasoning=reasoning,
        metadata=metadata,
        attachments=attachments,
    )


def load_session_transcript(
    agent_dir: Path,
    session_key: str,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Load branch UI transcript rows for chat restore."""
    path = session_ui_transcript_path(agent_dir, session_key)
    if not path.is_file():
        return []
    messages = _read_jsonl_messages(path)
    if limit is not None and limit > 0:
        messages = messages[-limit:]
    return messages


def delete_session_transcript(agent_dir: Path, session_key: str) -> None:
    path = session_ui_transcript_path(agent_dir, session_key)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        logger.debug("delete_session_transcript failed for %s", session_key, exc_info=True)


def chat_transcript_stats(agent_dir: Path) -> dict[str, Any]:
    """Summary of the append-only chat transcript log."""
    _ensure_transcript_jsonl(agent_dir)
    rows = _read_jsonl_messages(_transcript_path(agent_dir))
    if not rows:
        rows = _migrate_transcript_from_conversation(agent_dir)
    path = _transcript_path(agent_dir)
    return {
        "total": len(rows),
        "user": sum(1 for r in rows if r.get("role") == "user"),
        "assistant": sum(1 for r in rows if r.get("role") == "assistant"),
        "first_ts": rows[0].get("ts") if rows else "",
        "last_ts": rows[-1].get("ts") if rows else "",
        "path": str(path),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


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
