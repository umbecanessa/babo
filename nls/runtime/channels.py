"""NLS Channel System -- Multi-channel communication for agents.

Provides the core abstractions for external communication channels
(Telegram, WhatsApp, Email, etc.):

    * **ChannelMessage** -- Normalized inbound message from any channel.
    * **ChannelAdapter** -- Protocol that channel skill implementations
      must satisfy (send, receive, connect/disconnect).
    * **SessionRouter** -- Per-thread conversation history isolation.
      Each channel conversation gets its own history file while sharing
      the agent's brain state (hormones, ANS, knowledge).
    * **ChannelRegistry** -- Thin coordination layer that channel skills
      register into.  Used by the reach-out system and sub-agents to
      discover available channels and route outbound messages.

Channel implementations live in ``data/skills/`` as self-contained
skill packages.  This module only defines the shared contracts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------


class MessageType(str, Enum):
    CHAT = "chat"
    TASK = "task"
    CONTENT = "content"
    LINK = "link"
    COMMAND = "command"
    FORWARD = "forward"


# ---------------------------------------------------------------------------
# Normalized inbound message
# ---------------------------------------------------------------------------


@dataclass
class ChannelMessage:
    """Platform-agnostic representation of an inbound message.

    Every channel adapter normalizes raw platform payloads into this
    format before handing off to the session router / process pipeline.
    """

    channel: str
    session_key: str

    sender_id: str
    sender_name: str
    content: str

    is_group: bool = False
    group_id: str | None = None
    is_mention: bool = False
    is_forwarded: bool = False
    is_reply_to_bot: bool = False
    message_type: MessageType = MessageType.CHAT

    message_id: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Channel adapter protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ChannelAdapter(Protocol):
    """Contract that every channel skill must satisfy."""

    @property
    def name(self) -> str:
        """Unique channel identifier (e.g. 'telegram', 'whatsapp')."""
        ...

    async def send(
        self,
        target: str,
        message: str,
        **kwargs: Any,
    ) -> bool:
        """Send *message* to *target* (chat ID, phone number, email).

        Returns True on success.
        """
        ...

    async def is_connected(self) -> bool:
        """Whether the channel has an active connection."""
        ...

    def get_config(self) -> dict[str, Any]:
        """Return the channel's current configuration (policies, etc.)."""
        ...

    def get_status(self) -> dict[str, Any]:
        """Return connection status metadata for reach-out decisions."""
        ...


# ---------------------------------------------------------------------------
# Session router
# ---------------------------------------------------------------------------


def _session_filename(session_key: str) -> str:
    """Deterministic filename from a session key.

    ``agent::telegram:dm:491234567`` -> ``telegram__dm__491234567.json``
    """
    clean = session_key.split("::", 1)[-1] if "::" in session_key else session_key
    return re.sub(r"[^a-zA-Z0-9_.\-+@]", "__", clean) + ".json"


class SessionRouter:
    """Manages per-thread conversation history isolation.

    Each (agent, channel, thread) triple gets its own history file under
    ``data/agents/{id}/sessions/``.  The shared brain state (ANS,
    hypothalamus, DomainDB) remains global.
    """

    MAIN_SESSION = "websocket:main"

    def __init__(self, agent_dir: Path) -> None:
        self._agent_dir = agent_dir
        self._sessions_dir = agent_dir / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

        self._index_path = self._sessions_dir / "_session_index.json"
        self._index: dict[str, dict[str, Any]] = self._load_index()

    # -- index persistence --------------------------------------------------

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if self._index_path.exists():
            try:
                return json.loads(
                    self._index_path.read_text(encoding="utf-8"),
                )
            except Exception:
                pass
        return {}

    def _save_index(self) -> None:
        self._index_path.write_text(
            json.dumps(self._index, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # -- session key builder ------------------------------------------------

    @staticmethod
    def build_key(
        channel: str,
        chat_type: str = "main",
        identifier: str = "",
    ) -> str:
        parts = [channel, chat_type]
        if identifier:
            parts.append(identifier)
        return ":".join(parts)

    # -- history load / save ------------------------------------------------

    def _history_path(self, session_key: str) -> Path:
        return self._sessions_dir / _session_filename(session_key)

    def load_history(
        self,
        session_key: str,
        max_turns: int = 20,
    ) -> list[dict[str, Any]]:
        """Load conversation history for *session_key*."""
        path = self._history_path(session_key)
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if not raw:
                return []
            conversation = json.loads(raw)
            if not isinstance(conversation, list):
                return []
            if len(conversation) > max_turns * 2:
                conversation = conversation[-(max_turns * 2):]
            return conversation
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Session %s: load failed: %s", session_key, exc)
            return []

    @staticmethod
    def _normalize_session_message(msg: dict[str, Any]) -> dict[str, Any] | None:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            return None
        row: dict[str, Any] = {
            "role": role,
            "content": msg.get("content") or "",
        }
        for key in ("reasoning", "metadata", "pre_agentic_reasoning", "timestamp"):
            if msg.get(key) is not None:
                row[key] = msg[key]
        return row

    def save_history(
        self,
        session_key: str,
        history: list[dict[str, Any]],
        max_turns: int = 20,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist conversation history for *session_key*."""
        conversation = [
            row for msg in history
            if (row := self._normalize_session_message(msg)) is not None
        ]
        if len(conversation) > max_turns * 2:
            conversation = conversation[-(max_turns * 2):]

        path = self._history_path(session_key)
        try:
            path.write_text(
                json.dumps(conversation, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Session %s: save failed: %s", session_key, exc)

        entry = self._index.get(session_key, {})
        entry.update({
            "last_updated": time.time(),
            "turn_count": len(conversation),
            "file": path.name,
        })
        if metadata:
            for k in (
                "channel", "sender", "subject", "reply_target", "channel_name",
                "guild_name", "label",
            ):
                if k in metadata:
                    entry[k] = metadata[k]
        elif "channel" not in entry:
            # Infer channel from session_key
            entry["channel"] = session_key.split(":")[0]
        self._index[session_key] = entry
        self._save_index()

    def update_session_meta(
        self,
        session_key: str,
        **fields: Any,
    ) -> bool:
        """Update index metadata (e.g. branch label)."""
        if not fields:
            return False
        entry = dict(self._index.get(session_key) or {})
        entry.update(fields)
        self._index[session_key] = entry
        self._save_index()
        return True

    def list_sessions(self) -> dict[str, dict[str, Any]]:
        """Return the session index (key -> metadata)."""
        return dict(self._index)

    def delete_session(self, session_key: str) -> bool:
        deleted = False
        path = self._history_path(session_key)
        if path.exists():
            path.unlink()
            deleted = True
        ui_path = self._sessions_dir / (
            _session_filename(session_key).replace(".json", "") + "_ui.jsonl"
        )
        if ui_path.exists():
            ui_path.unlink()
            deleted = True
        if self._index.pop(session_key, None) is not None:
            self._save_index()
            deleted = True
        return deleted


# ---------------------------------------------------------------------------
# Message classifier
# ---------------------------------------------------------------------------

_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_TASK_PATTERNS = re.compile(
    r"\b(please|could you|can you|fix|create|build|implement|deploy|"
    r"run|execute|install|update|change|modify|delete|remove|add|"
    r"write|refactor|debug|analyze|investigate|research)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Guard rails -- reusable policy enforcement
# ---------------------------------------------------------------------------


class PolicyEnforcer:
    """Shared access control logic for all channel adapters.

    Implements OpenClaw-style DM policies, group allowlists, and
    mention gating.  Channel adapters delegate policy checks here.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    def check_dm(self, sender_id: str, sender_username: str = "") -> bool:
        """Check if a DM from *sender_id* / *sender_username* should be processed.

        Matches against ``allow_from`` by numeric ID **or** username
        (case-insensitive, with or without leading ``@``).
        """
        policy = self._config.get("dm_policy", "allowlist")
        if policy == "disabled":
            return False
        if policy == "open":
            return True
        allow = self._config.get("allow_from", [])
        norm_username = sender_username.lstrip("@").lower()
        for entry in allow:
            entry_norm = entry.lstrip("@").lower()
            if sender_id == entry:
                return True
            if norm_username and entry_norm == norm_username:
                return True
            if entry.startswith("*") and sender_id.endswith(entry.lstrip("*")):
                return True
        return False

    def check_group(
        self,
        group_id: str,
        sender_id: str,
        is_mention: bool = False,
    ) -> bool:
        """Check if a group message should be processed."""
        groups_cfg = self._config.get("groups", {})
        group_cfg = groups_cfg.get(group_id, groups_cfg.get("*", {}))

        allowed = group_cfg.get("allow_from", ["*"])
        if "*" not in allowed and sender_id not in allowed:
            return False

        if group_cfg.get("require_mention", True):
            return is_mention

        return True

    def get_group_system_prompt(self, group_id: str) -> str | None:
        """Return the per-group system prompt override, if any."""
        groups_cfg = self._config.get("groups", {})
        group_cfg = groups_cfg.get(group_id, groups_cfg.get("*", {}))
        return group_cfg.get("system_prompt")

    def check_mention(
        self,
        text: str,
        patterns: list[str] | None = None,
    ) -> bool:
        """Check if *text* contains a mention matching configured patterns."""
        patterns = patterns or self._config.get("mention_patterns", [])
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False


class MessageClassifier:
    """Classify inbound messages to determine the processing pipeline."""

    @staticmethod
    def classify(msg: ChannelMessage) -> MessageType:
        if msg.content.startswith("/"):
            return MessageType.COMMAND

        if msg.is_forwarded:
            if len(msg.content) > 500 or _URL_PATTERN.search(msg.content):
                return MessageType.CONTENT
            return MessageType.FORWARD

        if msg.channel == "email":
            headers = msg.metadata.get("email_headers", {})
            if headers.get("List-Unsubscribe") or headers.get("X-Forwarded-For"):
                return MessageType.CONTENT

        urls = _URL_PATTERN.findall(msg.content)
        non_url_text = _URL_PATTERN.sub("", msg.content).strip()
        if urls and len(non_url_text) < 30:
            return MessageType.LINK

        if _TASK_PATTERNS.search(msg.content):
            return MessageType.TASK

        return MessageType.CHAT


# ---------------------------------------------------------------------------
# Channel registry
# ---------------------------------------------------------------------------


class ChannelRegistry:
    """Central registry that channel skills register into.

    Provides outbound routing, session management, and channel
    discovery for the reach-out system.
    """

    def __init__(self, agent_dir: Path) -> None:
        self._channels: dict[str, ChannelAdapter] = {}
        self._session_router = SessionRouter(agent_dir)

    @property
    def session_router(self) -> SessionRouter:
        return self._session_router

    # -- registration -------------------------------------------------------

    def register(self, name: str, adapter: ChannelAdapter) -> None:
        self._channels[name] = adapter
        logger.info("ChannelRegistry: registered '%s'", name)

    def unregister(self, name: str) -> None:
        self._channels.pop(name, None)
        logger.info("ChannelRegistry: unregistered '%s'", name)

    def get(self, name: str) -> ChannelAdapter | None:
        return self._channels.get(name)

    # -- discovery ----------------------------------------------------------

    async def list_connected(self) -> list[dict[str, Any]]:
        """Return metadata for all connected channels."""
        result = []
        for name, adapter in self._channels.items():
            try:
                connected = await adapter.is_connected()
            except Exception:
                connected = False
            result.append({
                "name": name,
                "connected": connected,
                **adapter.get_status(),
            })
        return result

    def channel_names(self) -> list[str]:
        return list(self._channels.keys())

    # -- outbound routing ---------------------------------------------------

    async def send(
        self,
        channel: str,
        target: str,
        message: str,
        **kwargs: Any,
    ) -> bool:
        """Send a message through a specific channel."""
        adapter = self._channels.get(channel)
        if adapter is None:
            logger.warning("ChannelRegistry.send: unknown channel '%s'", channel)
            return False
        try:
            return await adapter.send(target, message, **kwargs)
        except Exception as exc:
            logger.error(
                "ChannelRegistry.send failed on '%s': %s", channel, exc,
            )
            return False

    def reconstruct_reply(
        self,
        channel_name: str,
        reply_target: str,
        agent_id: str,
    ) -> Any:
        """Build an async reply callable from serializable metadata.

        Returns an ``async def _reply(text)`` closure that routes through
        the registered adapter, or a no-op if the channel is unknown.
        """
        async def _reply(text: str) -> None:
            if not channel_name or not reply_target:
                return
            await self.send(channel_name, reply_target, text, agent_id=agent_id)

        return _reply

    # -- channel monitor sub-agent -----------------------------------------

    def build_monitor_delegation(
        self,
        channel_name: str,
        pending_messages: list[ChannelMessage],
    ) -> dict[str, Any] | None:
        """Build a delegation payload for the channel monitor sub-agent.

        Returns a dict suitable for injection into the agentic loop's
        ``delegate()`` virtual tool.  The parent agent can call
        ``delegate(task=...)`` with this payload to spawn a focused
        sub-agent that triages channel messages.

        Returns None if the channel is not connected or there are
        no pending messages.
        """
        adapter = self._channels.get(channel_name)
        if adapter is None:
            return None
        if not pending_messages:
            return None

        summaries = []
        for msg in pending_messages[:10]:
            summaries.append(
                f"- [{msg.sender_name}] {msg.content[:200]}"
            )
        message_list = "\n".join(summaries)

        return {
            "task": (
                f"You are monitoring the {channel_name} channel. "
                f"Triage these {len(pending_messages)} pending messages:\n\n"
                f"{message_list}\n\n"
                f"For each message, decide:\n"
                f"1. RESPOND - if you can handle it directly (simple questions, acknowledgments)\n"
                f"2. ESCALATE - if it needs the main agent's attention (complex tasks, decisions)\n"
                f"3. INGEST - if it's content to study (articles, links, newsletters)\n\n"
                f"Use the {channel_name}_send tool to respond directly when appropriate. "
                f"For ESCALATE items, describe what needs attention."
            ),
            "channel": channel_name,
            "message_count": len(pending_messages),
        }

    async def route_outbound(
        self,
        session_key: str,
        message: str,
        **kwargs: Any,
    ) -> bool:
        """Send a message to the channel implied by *session_key*."""
        from nls.skills.surface_send import resolve_surface_target, get_session_meta

        runtime_agent_id = kwargs.pop("agent_id", None)
        meta: dict[str, Any] = {}
        if runtime_agent_id:
            try:
                from server.main import app
                am = getattr(app.state, "agent_manager", None)
                rt = am.get_runtime(runtime_agent_id) if am else None
                if rt is not None:
                    meta = get_session_meta(rt, session_key)
            except Exception:
                pass

        target = resolve_surface_target(session_key, meta)
        if target is None:
            parts = session_key.split(":")
            if len(parts) < 2:
                return False
            channel = parts[0]
            target_id = parts[-1] if len(parts) >= 3 else ""
            return await self.send(channel, target_id, message, **kwargs)

        return await self.send(
            target.channel,
            target.reply_target,
            message,
            agent_id=runtime_agent_id,
            **target.send_kwargs,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Channel relay WebSocket client
# ---------------------------------------------------------------------------


class ChannelRelayClient:
    """Outbound WebSocket client that connects to the NestJS relay.

    The desktop runtime is behind NAT so NestJS cannot reach it.
    Instead, the runtime opens a WS connection **to** NestJS and
    NestJS pushes inbound webhook payloads through it.

    Lifecycle is tied to channel relay registration:
      - ``connect()`` after a channel skill registers its webhook
      - ``disconnect()`` on adapter shutdown
      - auto-reconnect with exponential backoff on drop
    """

    def __init__(
        self,
        nestjs_url: str,
        agent_id: str,
        secret: str = "",
        agent_name: str = "",
        genesis_version: str = "",
    ) -> None:
        self._nestjs_url = nestjs_url.rstrip("/")
        self._agent_id = agent_id
        self._secret = secret
        self._agent_name = agent_name
        self._genesis_version = genesis_version
        self._ws: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stop = False
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        """Start the relay connection (non-blocking)."""
        if self._task is not None and not self._task.done():
            return
        self._stop = False
        self._task = asyncio.ensure_future(self._run_loop())
        logger.info("ChannelRelay [%s]: starting", self._agent_id)

    async def disconnect(self) -> None:
        """Tear down the relay connection."""
        self._stop = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._connected = False
        logger.info("ChannelRelay [%s]: disconnected", self._agent_id)

    async def _run_loop(self) -> None:
        """Connect, listen, auto-reconnect with backoff."""
        try:
            import websockets
        except ImportError:
            logger.error(
                "ChannelRelay: 'websockets' package not installed — "
                "relay disabled. Run: pip install websockets"
            )
            return

        backoff = 1.0
        max_backoff = 60.0

        ws_base = self._nestjs_url.replace("https://", "wss://").replace("http://", "ws://")
        url = f"{ws_base}/api/channels/relay/{self._agent_id}"
        if self._secret:
            url += f"?secret={self._secret}"

        while not self._stop:
            try:
                async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                    self._ws = ws
                    self._connected = True
                    backoff = 1.0
                    logger.info("ChannelRelay [%s]: connected to %s", self._agent_id, self._nestjs_url)

                    async for raw in ws:
                        if self._stop:
                            break
                        try:
                            msg = json.loads(raw)
                            await self._handle_message(msg)
                        except Exception as exc:
                            logger.warning("ChannelRelay [%s]: message error: %s", self._agent_id, exc)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                exc_s = str(exc)
                if "401" in exc_s or "403" in exc_s or "Unauthorized" in exc_s:
                    logger.warning(
                        "ChannelRelay [%s]: auth rejected — ensure RUNTIME_SHARED_SECRET "
                        "matches NestJS (relay URL uses ?secret=...): %s",
                        self._agent_id, exc_s[:200],
                    )
                else:
                    logger.warning(
                        "ChannelRelay [%s]: connection lost: %s",
                        self._agent_id, exc,
                    )

            self._connected = False
            self._ws = None

            if self._stop:
                return

            logger.info("ChannelRelay [%s]: reconnecting in %.0fs", self._agent_id, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    async def send(self, msg: dict[str, Any]) -> bool:
        """Send a message back through the relay WebSocket."""
        if self._ws is None or not self._connected:
            return False
        try:
            await self._ws.send(json.dumps(msg, ensure_ascii=False, default=str))
            return True
        except Exception as exc:
            logger.warning("ChannelRelay [%s]: send failed: %s", self._agent_id, exc)
            return False

    async def broadcast_event(self, event: dict[str, Any]) -> bool:
        """Forward a ConnectionManager broadcast through the relay.

        NestJS routes these to all phone/browser clients connected
        to this agent (daydreams, sleep status, hormone updates, etc.).
        """
        return await self.send({"type": "broadcast", "event": event})

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Route an inbound message from NestJS.

        Handles four message types:
        - ``connected``: handshake acknowledgement
        - ``channel_message``: webhook payload for a channel skill
        - ``chat_request``: chat message from a remote browser/phone
        - ``http_proxy``: HTTP request forwarded through relay
        """
        msg_type = msg.get("type", "")

        if msg_type == "connected":
            logger.info("ChannelRelay [%s]: handshake OK", self._agent_id)
            await self.send({
                "type": "agent_info",
                "agent_id": self._agent_id,
                "name": self._agent_name,
                "genesis_version": self._genesis_version,
            })
            return

        if msg_type == "chat_request":
            await self._handle_chat_request(msg)
            return

        if msg_type == "http_proxy":
            await self._handle_http_proxy(msg)
            return

        if msg_type == "skill_install":
            await self._handle_skill_install(msg)
            return

        if msg_type != "channel_message":
            return

        channel = msg.get("channel", "")
        payload = msg.get("payload", {})

        if not channel or not payload:
            return

        port = os.environ.get("NLS_PORT", "9222")
        endpoint = f"http://127.0.0.1:{port}/skills/{channel}-channel/webhook/{self._agent_id}"

        logger.info(
            "ChannelRelay [%s]: routing %s message locally -> %s "
            "(payload_keys=%s, size=%d)",
            self._agent_id, channel, endpoint,
            list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
            len(json.dumps(payload)) if payload else 0,
        )

        try:
            import httpx
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(endpoint, json=payload)
                if resp.status_code >= 400:
                    logger.warning(
                        "ChannelRelay [%s]: local %s webhook returned %d",
                        self._agent_id, channel, resp.status_code,
                    )
        except Exception as exc:
            logger.error(
                "ChannelRelay [%s]: failed to route %s message locally: %r",
                self._agent_id, channel, exc,
                exc_info=True,
            )

    async def _handle_skill_install(self, msg: dict[str, Any]) -> None:
        """Receive a skill bundle pushed from NestJS and write to data/skills/."""
        slug = msg.get("slug", "")
        files: dict[str, str] = msg.get("files", {})

        if not slug or not files:
            await self.send({
                "type": "skill_install_ack",
                "slug": slug,
                "status": "error",
                "error": "Missing slug or files",
            })
            return

        data_dir = os.environ.get("NLS_DATA_DIR", "data")
        skill_dir = Path(data_dir) / "skills" / slug
        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            for rel_path, content in files.items():
                dest = skill_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")

            (skill_dir / ".clawhub").write_text(slug, encoding="utf-8")

            logger.info(
                "ChannelRelay [%s]: installed skill '%s' (%d files)",
                self._agent_id, slug, len(files),
            )
            await self.send({
                "type": "skill_install_ack",
                "slug": slug,
                "status": "ok",
            })
        except Exception as exc:
            logger.error(
                "ChannelRelay [%s]: skill install failed for '%s': %s",
                self._agent_id, slug, exc,
            )
            await self.send({
                "type": "skill_install_ack",
                "slug": slug,
                "status": "error",
                "error": str(exc),
            })

    async def _handle_http_proxy(self, msg: dict[str, Any]) -> None:
        """Forward an HTTP request from NestJS to the local runtime and return the response."""
        request_id = msg.get("request_id", "")
        method = msg.get("method", "GET").upper()
        path = msg.get("path", "")
        body = msg.get("body")

        port = os.environ.get("NLS_PORT", "9222")
        url = f"http://127.0.0.1:{port}{path}"

        logger.info(
            "ChannelRelay [%s]: http_proxy %s %s",
            self._agent_id, method, path,
        )

        try:
            import httpx
            async with httpx.AsyncClient(timeout=25.0) as client:
                if method == "POST":
                    resp = await client.post(url, json=body)
                elif method == "PATCH":
                    resp = await client.patch(url, json=body)
                elif method == "PUT":
                    resp = await client.put(url, json=body)
                elif method == "DELETE":
                    resp = await client.delete(url)
                else:
                    resp = await client.get(url)

                if resp.status_code < 400:
                    try:
                        resp_body = resp.json()
                    except Exception:
                        resp_body = resp.text
                    await self.send({
                        "type": "http_proxy_response",
                        "request_id": request_id,
                        "body": resp_body,
                    })
                else:
                    await self.send({
                        "type": "http_proxy_response",
                        "request_id": request_id,
                        "error": f"Runtime returned {resp.status_code}",
                    })
        except Exception as exc:
            logger.error(
                "ChannelRelay [%s]: http_proxy failed: %s", self._agent_id, exc,
            )
            await self.send({
                "type": "http_proxy_response",
                "request_id": request_id,
                "error": str(exc),
            })

    async def _handle_chat_request(self, msg: dict[str, Any]) -> None:
        """Process a chat message from a remote browser/phone.

        Routes the message through the local runtime's process pipeline
        with session isolation via the channel system, then sends the
        response back through the relay.
        """
        content = msg.get("content") or ""
        session_key = msg.get("session_key", "web:remote:default")
        request_id = msg.get("request_id", "")

        if not content:
            return

        port = os.environ.get("NLS_PORT", "9222")
        endpoint = f"http://127.0.0.1:{port}/chat/relay"

        logger.info(
            "ChannelRelay [%s]: chat_request from remote (session=%s, len=%d)",
            self._agent_id, session_key, len(content),
        )

        try:
            import httpx
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(endpoint, json={
                    "agent_id": self._agent_id,
                    "content": content,
                    "session_key": session_key,
                })
                if resp.status_code == 200:
                    result = resp.json()
                    await self.send({
                        "type": "chat_response",
                        "request_id": request_id,
                        "session_key": session_key,
                        "content": result.get("response", ""),
                        "agent_id": self._agent_id,
                        "nls": result.get("nls"),
                    })
                else:
                    await self.send({
                        "type": "chat_response",
                        "request_id": request_id,
                        "session_key": session_key,
                        "content": "",
                        "error": f"Runtime returned {resp.status_code}",
                    })
        except Exception as exc:
            logger.error(
                "ChannelRelay [%s]: chat_request processing failed: %s",
                self._agent_id, exc,
            )
            await self.send({
                "type": "chat_response",
                "request_id": request_id,
                "session_key": session_key,
                "content": "",
                "error": str(exc),
            })


# ---------------------------------------------------------------------------
# Channel progress reporting (shared by inner loop + channel processing)
# ---------------------------------------------------------------------------

_TOOL_PROGRESS_LABELS: dict[str, str] = {
    "gmail_search": "Searching your emails...",
    "gmail_read": "Reading an email...",
    "gmail_send": "Sending an email...",
    "gmail_draft": "Drafting an email...",
    "calendar_list": "Checking your calendar...",
    "calendar_create": "Creating a calendar event...",
    "web_search": "Looking something up online...",
    "web_fetch": "Fetching a webpage...",
    "bash": "Running a command...",
    "browser": "Using the browser...",
    "read": "Reading a file...",
    "write": "Writing a file...",
    "edit": "Editing a file...",
    "delegate": "Delegating a sub-task...",
    "plan": "Updating the plan...",
    "skill": "Using a skill...",
    "ask_user": "Asking you a question...",
    "loop_budget_prompt": "Need your decision to continue...",
    "escalate": "Asking orchestrator for help...",
}

_SILENT_TOOLS = frozenset({
    "bash", "read", "write", "edit", "todo", "plan",
    "skill", "screenshot", "codebase_index",
})

_MIN_TOOL_PROGRESS_INTERVAL_S = 30.0
_MIN_STEP_PROGRESS_INTERVAL_S = 60.0
_MILESTONE_EVERY_N_ITERATIONS = 10


def _rich_tool_label(tool_name: str, args: dict) -> str | None:
    """Build a context-aware progress label from tool name + arguments."""
    if tool_name in _SILENT_TOOLS:
        return None
    if tool_name in ("web_search", "web_fetch"):
        q = args.get("query", "") or args.get("url", "")
        if q:
            short = q[:60] + ("..." if len(q) > 60 else "")
            return f"Searching: {short}"
    if tool_name in ("gmail_search", "gmail_read"):
        q = args.get("query", "") or args.get("subject", "") or args.get("message_id", "")
        if q:
            short = q[:60] + ("..." if len(q) > 60 else "")
            return f"Email: {short}"
    if tool_name == "delegate":
        task = args.get("task", "")
        if task:
            short = task[:50] + ("..." if len(task) > 50 else "")
            return f"Delegating: {short}"
    return _TOOL_PROGRESS_LABELS.get(tool_name)


_CREDENTIAL_PATTERNS = re.compile(
    r"|".join([
        r"sk-[A-Za-z0-9_\-]{20,}",           # OpenAI / Anthropic style
        r"ghp_[A-Za-z0-9]{36,}",              # GitHub PAT
        r"gho_[A-Za-z0-9]{36,}",              # GitHub OAuth
        r"github_pat_[A-Za-z0-9_]{22,}",      # GitHub fine-grained PAT
        r"glpat-[A-Za-z0-9\-_]{20,}",         # GitLab PAT
        r"xox[bpsar]-[A-Za-z0-9\-]{10,}",     # Slack tokens
        r"AKIA[0-9A-Z]{16}",                   # AWS access key
        r"postgresql://\S+",                    # connection strings
        r"mongodb(\+srv)?://\S+",
        r"mysql://\S+",
        r"redis://\S+",
        r"Bearer\s+[A-Za-z0-9\-._~+/]+=*",    # Bearer tokens
        r"[A-Fa-f0-9]{32,64}",                 # generic hex keys (32-64 chars)
    ]),
)


def _scrub_credentials(text: str) -> str:
    """Mask strings that look like API keys, tokens, or connection strings."""
    if not text:
        return text
    return _CREDENTIAL_PATTERNS.sub("[REDACTED]", text)


class ChannelProgressReporter:
    """Sends throttled progress messages to a channel during agentic loops."""

    def __init__(
        self,
        adapter: Any,
        reply_target: str,
        agent_id: str,
    ) -> None:
        self._adapter = adapter
        self._target = reply_target
        self._agent_id = agent_id
        self._last_send = 0.0
        self._plan_announced = False
        self._iteration = 0
        self._max_iterations = 40
        self._last_tool_label = ""
        self._current_plan_step = ""
        self._last_milestone_iter = 0

    def _throttled(self, interval: float = _MIN_TOOL_PROGRESS_INTERVAL_S) -> bool:
        return (time.monotonic() - self._last_send) < interval

    async def _send(self, text: str) -> None:
        from nls.runtime.response_cleanup import sanitize_channel_outbound

        text = sanitize_channel_outbound(_scrub_credentials(text))
        if not text:
            return
        try:
            await self._adapter.send(
                self._target, text, agent_id=self._agent_id,
            )
            self._last_send = time.monotonic()
        except Exception:
            logger.debug("Channel progress send failed", exc_info=True)

    async def on_event(self, event: Any) -> None:
        raw_type = event.type if hasattr(event, "type") else ""
        etype = raw_type.value if hasattr(raw_type, "value") else str(raw_type)
        data = event.data if hasattr(event, "data") else {}
        logger.debug("ChannelProgressReporter event: %s", etype)

        if etype == "agentic_plan" and not self._plan_announced:
            steps = data.get("steps", [])
            n_steps = len(steps) if isinstance(steps, list) else 0
            if n_steps:
                await self._send(
                    f"Working on it \u2014 I have a plan with {n_steps} steps."
                )
            else:
                await self._send("Working on it...")
            self._plan_announced = True
            return

        if etype == "agent_start":
            self._max_iterations = data.get("max_iterations", 40)
            return

        if etype == "communicate":
            message = data.get("message", "")
            if message:
                await self._send(message)
            return

        if etype == "ask_user":
            question = data.get("question", "")
            if question:
                await self._send(question)
            return

        if etype == "loop_budget_prompt":
            from nls.agentic.budget_prompt import format_channel_budget_prompt

            question = data.get("question", "")
            if not question:
                question = format_channel_budget_prompt(
                    data.get("reason", "max_iterations"),
                    iteration=int(data.get("iteration", 0) or 0),
                    max_iterations=int(data.get("max_iterations", 0) or 0),
                    options=data.get("options") or [10, 20, 40],
                    elapsed_seconds=data.get("elapsed_seconds"),
                    timeout_seconds=data.get("timeout_seconds"),
                )
            if question:
                await self._send(question)
            return

        if etype == "budget_decision":
            new_max = data.get("max_iterations")
            if new_max:
                try:
                    self._max_iterations = int(new_max)
                except (TypeError, ValueError):
                    pass
            return

        if etype == "tool_execution_start":
            tool_name = data.get("tool_name", "") or data.get("tool", "")
            args = data.get("arguments", {}) or {}
            label = _rich_tool_label(tool_name, args)
            if label:
                self._last_tool_label = label
                if not self._throttled(_MIN_TOOL_PROGRESS_INTERVAL_S):
                    await self._send(label)
            return

        if etype == "plan_step_update":
            new_status = data.get("status", "")
            step_label = data.get("label", "") or data.get("step_label", "")
            if step_label:
                self._current_plan_step = step_label
            if new_status == "done" and not self._throttled(_MIN_STEP_PROGRESS_INTERVAL_S):
                if step_label:
                    await self._send(f"Done: {step_label}")
                else:
                    await self._send("Making progress...")
            return

        if etype == "turn_end":
            self._iteration = data.get("iteration", self._iteration) + 1
            _resp_text = (data.get("response_text") or "").strip()
            if len(_resp_text) > 80 and not self._throttled(_MIN_STEP_PROGRESS_INTERVAL_S):
                await self._send(_resp_text[:3000])
                self._last_milestone_iter = self._iteration
                return

            since_last = self._iteration - self._last_milestone_iter
            if since_last >= _MILESTONE_EVERY_N_ITERATIONS and not self._throttled(_MIN_STEP_PROGRESS_INTERVAL_S):
                hint = self._current_plan_step or self._last_tool_label or ""
                msg = f"Progress: step {self._iteration}/{self._max_iterations}"
                if hint:
                    msg += f" \u2014 {hint}"
                await self._send(msg)
                self._last_milestone_iter = self._iteration
            return

        if etype == "agent_end":
            aborted = data.get("aborted", False)
            if aborted:
                reason = data.get("abort_reason", "")
                if "Max iterations" in reason:
                    await self._send(
                        "Wrapping up \u2014 reached the iteration limit. Preparing summary..."
                    )
            return
