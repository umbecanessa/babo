"""Telegram channel adapter -- send/receive via Telegram Bot API.

Supports two inbound modes:
  1. **Webhook relay** (preferred) -- NestJS cloud server receives
     Telegram webhook POSTs and relays them to the local desktop via WS.
  2. **Long-polling fallback** -- when no relay is configured, the
     adapter calls ``getUpdates`` periodically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from nls.tools.agent_tools.base import AgentTool, ToolResult
from nls.agentic.outbound_notify import FINAL_SUMMARY_SCHEMA_PROPERTY

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4096
_SIGNAL_TAG_RE = re.compile(r"\[(?:[A-Za-z_]+)(?:[:.](?:[^\]]*))?\]\s*")


def _strip_signal_tags(text: str) -> str:
    """Remove ANS behavioral tags (e.g. [EVALUATE:correct]) from outgoing text."""
    return _SIGNAL_TAG_RE.sub("", text).strip()
POLL_INTERVAL_SECONDS = 2.0


@dataclass
class TelegramSendTool:
    """Agent tool for sending Telegram messages.

    Each agent gets its own tool instance with ``_agent_id`` so the
    adapter resolves the correct bot token.
    """

    _adapter: TelegramAdapter
    _agent_id: str | None = None

    @property
    def name(self) -> str:
        return "telegram_send"

    @property
    def description(self) -> str:
        bot_user = self._adapter._bot_usernames.get(self._agent_id, "") if self._agent_id else ""
        base = (
            "Send a Telegram message. Use the contacts tool to look up chat_id first. "
            "Provide chat_id (user or group) and text. Supports Markdown formatting. "
            "To share documents/reports/images, use file_path with the workspace path — "
            "do NOT paste URLs or file contents into text."
        )
        if bot_user:
            base += f" This agent's Telegram bot username is @{bot_user}."
        return base

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "Telegram chat ID (user ID or group ID)",
                },
                "text": {
                    "type": "string",
                    "description": "Message text to send",
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "Workspace file path to attach as a document "
                        "(e.g. 'PROJECT_REPORT.md', 'data/results.csv'). "
                        "The file is sent as a Telegram attachment. "
                        "Do NOT paste file contents or URLs into text — "
                        "use this parameter instead."
                    ),
                },
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Multiple workspace file paths to send as separate "
                        "document attachments (e.g. ['report.pdf', 'chart.png'])"
                    ),
                },
                "parse_mode": {
                    "type": "string",
                    "enum": ["Markdown", "MarkdownV2", "HTML"],
                    "description": (
                        "Message formatting mode. Use 'Markdown' for *bold*, "
                        "_italic_, `code`. Use 'HTML' for <b>bold</b>, <i>italic</i>."
                    ),
                },
                "reply_to_message_id": {
                    "type": "string",
                    "description": (
                        "Message ID to reply to (for threading in groups). "
                        "The reply will be shown as a thread reply."
                    ),
                },
                "final_summary": FINAL_SUMMARY_SCHEMA_PROPERTY,
            },
            "required": ["chat_id", "text"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        chat_id = params.get("chat_id", "")
        text = params.get("text", "")
        file_path = params.get("file_path", "")
        file_paths = params.get("file_paths", []) or []
        parse_mode = params.get("parse_mode", "")
        reply_to = params.get("reply_to_message_id", "")
        if not chat_id:
            return ToolResult(content="Error: chat_id is required", is_error=True)
        if not text and not file_path and not file_paths:
            return ToolResult(content="Error: text, file_path, or file_paths is required", is_error=True)

        allowed = self._adapter.get_allowed_chat_ids(self._agent_id)
        if allowed and str(chat_id) not in allowed:
            known_list = ", ".join(sorted(allowed)[:3])
            logger.warning(
                "Telegram send BLOCKED: agent=%s tried to message %s "
                "(allowed: %s)", self._agent_id, chat_id, allowed,
            )
            return ToolResult(
                content=(
                    f"Cannot send to chat {chat_id} — that chat has not "
                    f"messaged you yet. To initiate contact, first save the "
                    f"person with contacts(action='add', name='...', "
                    f"telegram_id='{chat_id}'), then retry telegram_send. "
                    f"Known chats: {known_list}"
                ),
                is_error=True,
            )

        send_kwargs: dict[str, Any] = {}
        if parse_mode:
            send_kwargs["parse_mode"] = parse_mode
        if reply_to:
            send_kwargs["reply_to_message_id"] = reply_to

        if file_paths:
            results: list[str] = []
            for fp in file_paths:
                r = await self._adapter.send_file(
                    chat_id, fp, caption="", agent_id=self._agent_id,
                )
                results.append(r.content)
            if text:
                await self._adapter.send(chat_id, text, agent_id=self._agent_id, **send_kwargs)
            return ToolResult(content=f"Sent {len(file_paths)} file(s) to {chat_id}")

        if file_path:
            return await self._adapter.send_file(
                chat_id, file_path,
                caption=text,
                agent_id=self._agent_id,
            )

        ok = await self._adapter.send(chat_id, text, agent_id=self._agent_id, **send_kwargs)
        if ok:
            return ToolResult(content=f"Message sent to {chat_id}")
        return ToolResult(content="Failed to send message", is_error=True)


class TelegramSetupTool:
    """Agent tool for validating a Telegram bot token and configuring the connection.

    Used during conversational onboarding -- the agent calls this after
    the user pastes their token from @BotFather.
    """

    def __init__(self, _adapter: TelegramAdapter, agent_id: str | None = None) -> None:
        self._adapter = _adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "telegram_setup"

    @property
    def description(self) -> str:
        return (
            "Validate a Telegram bot token and configure the Telegram connection. "
            "Call this after the user provides their bot token from @BotFather."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "bot_token": {
                    "type": "string",
                    "description": "The Telegram bot token from @BotFather.",
                },
            },
            "required": ["bot_token"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        bot_token = params.get("bot_token", "").strip()
        agent_id = self._agent_id or ""

        if not bot_token:
            return ToolResult(
                content="Error: bot_token is required",
                is_error=True,
            )

        try:
            url = f"{TELEGRAM_API}/bot{bot_token}/getMe"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return ToolResult(
                content=f"Invalid token -- Telegram API returned an error: {exc}. "
                        "Ask the user to double-check their token from @BotFather.",
                is_error=True,
            )

        result = data.get("result", {})
        bot_username = result.get("username", "")
        bot_name = result.get("first_name", "")

        if agent_id:
            cfg_update: dict[str, Any] = {
                "bot_token": bot_token,
                "enabled": True,
            }
            self._adapter.update_config(cfg_update, agent_id=agent_id)
            self._adapter._bot_usernames[agent_id] = bot_username
            self._adapter._connected_agents.add(agent_id)
            self._adapter._register_with_agent(agent_id)
        else:
            logger.warning("TelegramSetupTool: no agent_id — cannot persist per-agent config")

        relay_note = ""
        relay_url = self._adapter._get_relay_base_url(agent_id)
        if relay_url and agent_id:
            ok = await self._adapter.register_webhook_relay(relay_url, agent_id)
            if ok:
                relay_note = " Webhook relay registered via cloud server."
            else:
                relay_note = " Webhook relay registration failed — using long-polling fallback."
                self._adapter.start_polling(agent_id)
        elif agent_id:
            self._adapter.start_polling(agent_id)

        return ToolResult(
            content=(
                f"Telegram connected! Bot: @{bot_username} ({bot_name}).{relay_note} "
                f"Now call skill_configure(skill_name='telegram-channel') to check "
                f"for any remaining required configuration."
            )
        )


class TelegramAdapter:
    """Telegram Bot API adapter implementing the ChannelAdapter protocol.

    Supports multi-agent operation: each agent gets its own config
    (bot_token, enabled, policies) stored at
    ``data/skills/telegram-channel/agents/{agent_id}.json``.
    """

    channel_name: str = "telegram"

    def __init__(self, global_config: dict[str, Any], ctx: Any) -> None:
        self._global_config = global_config
        self._ctx = ctx
        self._agent_configs: dict[str, dict[str, Any]] = {}
        self._bot_usernames: dict[str, str] = {}
        self._connected_agents: set[str] = set()
        self._known_senders: dict[str, set[str]] = {}
        self._poll_tasks: dict[str, asyncio.Task[None]] = {}
        self._poll_offsets: dict[str, int] = {}
        self._relay_clients: dict[str, Any] = {}
        self._load_all_agent_configs()
        self._load_known_senders()

    def _load_all_agent_configs(self) -> None:
        for agent_id, cfg in self._ctx.load_all_agent_configs().items():
            self._agent_configs[agent_id] = cfg

    def _agent_cfg(self, agent_id: str | None) -> dict[str, Any]:
        if agent_id:
            from nls.runtime.channel_agent_config import merge_global_and_agent_channel_config

            return merge_global_and_agent_channel_config(
                self._global_config,
                self._agent_configs.get(agent_id, {}),
            )
        return self._global_config

    @property
    def name(self) -> str:
        return "telegram"

    # -- ChannelAdapter protocol -------------------------------------------

    async def send(
        self,
        target: str,
        message: str,
        **kwargs: Any,
    ) -> bool:
        agent_id = kwargs.pop("agent_id", None)
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token:
            logger.warning("Telegram: no bot_token configured (agent=%s)", agent_id)
            return False

        chunks = _chunk_message(message)
        url = f"{TELEGRAM_API}/bot{token}/sendMessage"

        async with httpx.AsyncClient(timeout=10.0) as client:
            for i, chunk in enumerate(chunks):
                payload: dict[str, Any] = {
                    "chat_id": target,
                    "text": chunk,
                }
                if kwargs.get("parse_mode"):
                    payload["parse_mode"] = kwargs["parse_mode"]
                if kwargs.get("reply_to_message_id") and i == 0:
                    payload["reply_to_message_id"] = int(kwargs["reply_to_message_id"])
                try:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                except Exception as exc:
                    logger.error("Telegram send failed: %s", exc)
                    return False
        return True

    async def is_connected(self, agent_id: str | None = None) -> bool:
        if agent_id:
            return agent_id in self._connected_agents
        return bool(self._connected_agents)

    def get_config(self, agent_id: str | None = None) -> dict[str, Any]:
        safe = dict(self._agent_cfg(agent_id))
        if safe.get("bot_token"):
            safe["bot_token"] = "***masked***"
        return safe

    def get_status(self, agent_id: str | None = None) -> dict[str, Any]:
        cfg = self._agent_cfg(agent_id)
        connected = agent_id in self._connected_agents if agent_id else bool(self._connected_agents)
        return {
            "channel": "telegram",
            "connected": connected,
            "bot_username": self._bot_usernames.get(agent_id or "", ""),
            "enabled": cfg.get("enabled", False),
        }

    def channel_manage_actions(self) -> list[str]:
        return ["list"]

    def channel_remote_actions(self) -> list[str]:
        return ["delete", "send"]

    async def delete_channel_message(
        self,
        agent_id: str,
        channel_id: str,
        message_id: str,
    ) -> tuple[bool, str]:
        cfg = self._agent_cfg(agent_id)
        token = str(cfg.get("bot_token") or "").strip()
        if not token:
            return False, "Error: no bot_token configured."
        url = f"{TELEGRAM_API}/bot{token}/deleteMessage"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    json={
                        "chat_id": channel_id,
                        "message_id": int(message_id),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            if not data.get("ok"):
                return False, f"Telegram delete failed: {data.get('description', data)}"
        except Exception as exc:
            return False, f"Telegram delete failed: {exc}"
        return True, f"Deleted message {message_id} in chat {channel_id}."

    async def manage_channel(
        self,
        agent_id: str,
        action: str,
        params: dict[str, Any],
    ) -> tuple[bool, str]:
        from nls.runtime.channel_manage import format_simple_channel_status

        act = (action or "").strip().lower()
        if act == "list":
            status = self.get_status(agent_id=agent_id)
            cfg = self._agent_cfg(agent_id)
            lines = [format_simple_channel_status("Telegram", status)]
            groups = cfg.get("groups") or {}
            if groups:
                lines.append(f"  groups policy keys: {', '.join(groups.keys())}")
            return True, "\n".join(lines)
        return False, "Telegram supports action=list only (use skill_configure for policy)."

    # -- per-agent config --------------------------------------------------

    def update_config(self, new_config: dict[str, Any], agent_id: str) -> None:
        self._agent_configs.setdefault(agent_id, {}).update(new_config)
        self._ctx.save_config(self._agent_configs[agent_id], agent_id=agent_id)

    # -- lifecycle ---------------------------------------------------------

    async def startup(self) -> None:
        """Start all previously-activated agents."""
        for agent_id, cfg in list(self._agent_configs.items()):
            if cfg.get("enabled") and cfg.get("bot_token"):
                await self._startup_agent(agent_id)

        if not self._connected_agents:
            logger.info("Telegram channel: no agents with Telegram enabled")

    async def startup_agent(self, agent_id: str) -> None:
        await self._startup_agent(agent_id)

    async def _startup_agent(self, agent_id: str) -> None:
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token or not cfg.get("enabled", False):
            logger.info("Telegram channel [%s]: disabled", agent_id)
            return

        try:
            url = f"{TELEGRAM_API}/bot{token}/getMe"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                self._bot_usernames[agent_id] = data.get("result", {}).get("username", "")
                self._connected_agents.add(agent_id)
                logger.info(
                    "Telegram channel [%s]: connected as @%s",
                    agent_id, self._bot_usernames[agent_id],
                )
        except Exception as exc:
            logger.error("Telegram channel [%s]: startup failed: %s", agent_id, exc)
            return

        self._register_with_agent(agent_id)

        if cfg.get("webhook_relay_url"):
            await self._ensure_relay(agent_id)
        else:
            self.start_polling(agent_id)

    async def shutdown(self) -> None:
        for agent_id in list(self._poll_tasks):
            self.stop_polling(agent_id)
        for agent_id, relay in list(self._relay_clients.items()):
            await relay.disconnect()
        self._relay_clients.clear()
        self._connected_agents.clear()
        logger.info("Telegram channel: disconnected all agents")

    # -- long-polling fallback ---------------------------------------------

    def start_polling(self, agent_id: str) -> None:
        task = self._poll_tasks.get(agent_id)
        if task is not None and not task.done():
            return
        self._poll_tasks[agent_id] = asyncio.ensure_future(self._poll_loop(agent_id))
        logger.info("Telegram [%s]: started getUpdates polling", agent_id)

    def stop_polling(self, agent_id: str) -> None:
        task = self._poll_tasks.pop(agent_id, None)
        if task is not None:
            task.cancel()
            logger.info("Telegram [%s]: stopped polling", agent_id)

    async def _poll_loop(self, agent_id: str) -> None:
        """Long-poll ``getUpdates`` for a specific agent's bot."""
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        url = f"{TELEGRAM_API}/bot{token}/getUpdates"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{TELEGRAM_API}/bot{token}/deleteWebhook")
        except Exception:
            pass

        while True:
            try:
                offset = self._poll_offsets.get(agent_id, 0)
                async with httpx.AsyncClient(timeout=35.0) as client:
                    resp = await client.get(url, params={
                        "offset": offset,
                        "timeout": 25,
                    })
                    resp.raise_for_status()
                    data = resp.json()

                updates = data.get("result", [])
                for update in updates:
                    update_id = update.get("update_id", 0)
                    if update_id >= self._poll_offsets.get(agent_id, 0):
                        self._poll_offsets[agent_id] = update_id + 1
                    await self._handle_polled_update(update, agent_id)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("Telegram [%s] poll error: %s", agent_id, exc)
                await asyncio.sleep(5.0)
                continue

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _handle_polled_update(self, update: dict[str, Any], agent_id: str) -> None:
        """Route a single polled update for a specific agent."""
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        normalized = self.normalize(update, agent_id)
        if normalized is None:
            return

        try:
            from server.main import app
            agent_manager = getattr(app.state, "agent_manager", None)
            runtime = (
                agent_manager.get_runtime(agent_id)
                if agent_manager is not None
                else None
            )
        except ImportError:
            app = None
            agent_manager = None
            runtime = None

        will_respond = self.should_respond(message, agent_id)
        if runtime is not None or app is not None:
            from nls.skills.channel_ambient import record_inbound_ambient
            record_inbound_ambient(
                runtime, normalized, triggered=will_respond,
                app=app, agent_id=agent_id,
            )

        if not will_respond:
            logger.debug("Telegram [%s] poll: policy rejected from %s", agent_id, normalized["sender_id"])
            if normalized.get("is_group") and app is not None:
                from nls.skills.channel_adapter_util import broadcast_group_ambient_inbound
                broadcast_group_ambient_inbound(app, agent_id, "telegram", normalized)
            return

        self.register_known_sender(normalized["metadata"]["chat_id"], agent_id)

        try:
            if agent_manager is None:
                return

            if runtime is None:
                logger.warning("Telegram [%s] poll: runtime not found", agent_id)
                return

            session_key = normalized["session_key"]
            text = normalized["content"]
            chat_id = normalized["metadata"]["chat_id"]
            sender_name = normalized["sender_name"]
            attachments = normalized.get("attachments") or []

            history = runtime.load_session_history(session_key)

            from nls.skills.surface_send import channel_session_metadata
            session_meta = channel_session_metadata(normalized)

            from nls.skills.channel_adapter_util import channel_history_content

            runtime.save_session_history(
                history + [{"role": "user", "content": channel_history_content(text, attachments)}],
                session_key=session_key,
                metadata=session_meta,
            )

            from nls.skills.channel_processing import (
                process_channel_message,
                try_feed_pending_answer_async,
            )

            if await try_feed_pending_answer_async(
                agent_id, session_key, text, attachments=attachments, app=app,
            ):
                return

            from nls.skills.channel_attachments import (
                deliver_channel_reply,
                note_attachment_download_gaps,
                telegram_inbound_media_count,
            )

            user_input = f"[{sender_name} via Telegram]: {text}" if text else f"[{sender_name} via Telegram]:"
            user_input = note_attachment_download_gaps(
                user_input,
                expected=telegram_inbound_media_count(message),
                saved=len(attachments),
                labels=[a.get("name", "file") for a in attachments],
            )
            response_text = await process_channel_message(
                app, runtime, agent_id, user_input, history,
                channel_adapter=self,
                reply_target=chat_id,
                session_key=session_key,
                attachments=attachments,
            )

            clean_response = _strip_signal_tags(response_text) if response_text else ""

            if not clean_response and response_text:
                logger.warning(
                    "Telegram [%s] poll: response became empty after "
                    "signal-tag stripping — using original",
                    agent_id,
                )
                clean_response = response_text.strip()

            if clean_response:
                user_content = channel_history_content(text, attachments)
                history.append({"role": "user", "content": user_content})
                history.append({"role": "assistant", "content": clean_response})
                runtime.save_session_history(
                    history, session_key=session_key,
                    metadata=session_meta,
                )
                await deliver_channel_reply(
                    self, chat_id, clean_response, response_text or "",
                    agent_id=agent_id,
                )
                from nls.skills.channel_ambient import record_outbound_ambient
                record_outbound_ambient(runtime, normalized, clean_response)

            _broadcast_channel_event(app, runtime, normalized, clean_response)

        except Exception as exc:
            logger.error("Telegram [%s] poll handler error: %s", agent_id, exc, exc_info=True)

    def _register_with_agent(self, agent_id: str) -> None:
        """Register this adapter in the agent's channel registry."""
        try:
            from server.main import app
            agent_manager = getattr(app.state, "agent_manager", None)
            if agent_manager is None:
                return
            runtime = agent_manager.get_runtime(agent_id)
            if runtime is not None and hasattr(runtime, "channel_registry"):
                cr = runtime.channel_registry
                if cr is not None:
                    cr.register("telegram", self)
        except Exception:
            pass

    def _get_relay_base_url(self, agent_id: str | None = None) -> str:
        """Return the NestJS relay base URL from environment or config."""
        cfg = self._agent_cfg(agent_id)
        if cfg.get("webhook_relay_base_url"):
            return cfg["webhook_relay_base_url"]

        import os
        env_url = os.environ.get("NESTJS_URL", "")
        if env_url:
            return env_url.rstrip("/")

        try:
            from server.main import app
            cfg_state = getattr(app.state, "config", {})
            nest_url = cfg_state.get("nestjs_url", "")
            if nest_url:
                return nest_url.rstrip("/")
        except Exception:
            pass

        return ""

    # -- workspace file resolution -----------------------------------------

    def _resolve_workspace_file(self, agent_id: str, file_path: str) -> Path | None:
        """Resolve a relative path inside the agent's workspace, preventing traversal."""
        try:
            from server.main import app
            am = getattr(app.state, "agent_manager", None)
            if am is None:
                return None
            workspace = am.agents_dir / agent_id / "workspace"
            resolved = (workspace / file_path).resolve()
            if not str(resolved).startswith(str(workspace.resolve())):
                logger.warning("Telegram: path traversal blocked: %s", file_path)
                return None
            if not resolved.is_file():
                return None
            return resolved
        except Exception:
            return None

    async def send_file(
        self,
        target: str,
        file_path: str,
        *,
        caption: str = "",
        agent_id: str | None = None,
    ) -> ToolResult:
        """Send a file from the agent's workspace via the Telegram Bot API."""
        if not agent_id:
            return ToolResult(content="No agent_id for file send", is_error=True)

        resolved = self._resolve_workspace_file(agent_id, file_path)
        if resolved is None:
            return ToolResult(
                content=f"File not found in workspace: {file_path}",
                is_error=True,
            )

        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token:
            return ToolResult(content="No bot_token configured", is_error=True)

        mime = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        is_image = mime.startswith("image/")
        endpoint = "sendPhoto" if is_image else "sendDocument"
        field_name = "photo" if is_image else "document"

        url = f"{TELEGRAM_API}/bot{token}/{endpoint}"
        data_fields: dict[str, Any] = {"chat_id": target}
        if caption:
            data_fields["caption"] = caption

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    data=data_fields,
                    files={field_name: (resolved.name, resolved.read_bytes(), mime)},
                )
                resp.raise_for_status()
                return ToolResult(
                    content=f"File '{resolved.name}' sent to {target} via Telegram",
                )
        except Exception as exc:
            logger.error("Telegram send_file failed: %s", exc)
            return ToolResult(content=f"Failed to send file: {exc}", is_error=True)

    # -- known-sender tracking (outbound guard) ----------------------------

    def _known_senders_path(self) -> Path:
        return self._ctx._skills_dir / "telegram-channel" / "known_senders.json"

    def _load_known_senders(self) -> None:
        path = self._known_senders_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for agent_id, ids in raw.items():
                self._known_senders[agent_id] = set(ids)
        except Exception:
            pass

    def _save_known_senders(self) -> None:
        path = self._known_senders_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {
            aid: sorted(ids) for aid, ids in self._known_senders.items()
        }
        path.write_text(
            json.dumps(serializable, indent=2), encoding="utf-8",
        )

    def register_known_sender(self, chat_id: str, agent_id: str) -> None:
        """Track a chat_id that has sent a message to this agent.

        Persisted across restarts.
        """
        cid = str(chat_id)
        senders = self._known_senders.setdefault(agent_id, set())
        if cid not in senders:
            senders.add(cid)
            self._save_known_senders()

    def get_allowed_chat_ids(self, agent_id: str | None) -> set[str]:
        """Return chat IDs this agent is allowed to message.

        Includes owner_identity, allow_from entries, and any chat that
        has contacted the agent.  Returns empty set when no data is
        available (backwards compat: allow all).
        """
        allowed: set[str] = set()
        if not agent_id:
            return allowed

        cfg = self._agent_configs.get(agent_id, {})

        owner = cfg.get("owner_identity", "")
        if owner:
            allowed.add(str(owner))

        for entry in cfg.get("allow_from", []):
            if entry:
                allowed.add(str(entry))

        known = self._known_senders.get(agent_id, set())
        allowed.update(known)

        # Personal contacts store (owner-authorised first-contact).
        # Any Telegram chat ID saved via contacts(action='add', telegram_id='...')
        # is treated as explicitly approved by the owner for outbound messaging.
        try:
            from server.main import app as _app  # noqa: PLC0415
            _am = getattr(_app.state, "agent_manager", None)
            if _am is not None:
                _contacts_path = _am.agents_dir / agent_id / "contacts.json"
                if _contacts_path.exists():
                    import json as _json
                    _cdata = _json.loads(_contacts_path.read_text(encoding="utf-8"))
                    for _c in _cdata.get("contacts", []):
                        _tid = str(_c.get("telegram_id") or "").strip()
                        if _tid:
                            allowed.add(_tid)
        except Exception:
            pass

        return allowed

    # -- tool factory ------------------------------------------------------

    def create_send_tool(self, agent_id: str | None = None) -> TelegramSendTool:
        return TelegramSendTool(_adapter=self, _agent_id=agent_id)

    def create_setup_tool(self, agent_id: str | None = None) -> TelegramSetupTool:
        return TelegramSetupTool(_adapter=self, agent_id=agent_id)

    # -- policy enforcement ------------------------------------------------

    def should_respond(self, message: dict[str, Any], agent_id: str | None = None) -> bool:
        """Check DM/group policies to decide if the agent should respond."""
        from nls.runtime.channels import PolicyEnforcer

        cfg = self._agent_cfg(agent_id)
        enforcer = PolicyEnforcer(cfg)
        chat = message.get("chat", {})
        chat_type = chat.get("type", "private")
        sender = message.get("from", {})
        sender_id = str(sender.get("id", ""))
        sender_username = sender.get("username", "")

        if chat_type == "private":
            return enforcer.check_dm(sender_id, sender_username=sender_username)

        group_id = str(chat.get("id", ""))
        is_mention = self._is_mention(message, message.get("text", ""), agent_id)
        return enforcer.check_group(group_id, sender_id, is_mention=is_mention)

    def _is_mention(self, message: dict[str, Any], text: str, agent_id: str | None = None) -> bool:
        bot_username = self._bot_usernames.get(agent_id or "", "")
        entities = message.get("entities", [])
        for ent in entities:
            ent_type = ent.get("type")
            if ent_type == "mention":
                mention_text = text[ent["offset"]:ent["offset"] + ent["length"]]
                if bot_username and mention_text.lstrip("@").lower() == bot_username.lower():
                    return True
            elif ent_type == "text_mention":
                user = ent.get("user") or {}
                if user.get("is_bot") and bot_username:
                    if user.get("username", "").lower() == bot_username.lower():
                        return True

        if bot_username and text:
            needle = f"@{bot_username}".lower()
            if needle in text.lower():
                return True

        reply_to = message.get("reply_to_message", {})
        reply_from = reply_to.get("from", {})
        if bot_username and reply_from.get("is_bot") and reply_from.get("username", "").lower() == bot_username.lower():
            return True

        cfg = self._agent_cfg(agent_id)
        for pattern in cfg.get("mention_patterns", []):
            if re.search(pattern, text, re.IGNORECASE):
                return True

        return False

    # -- message normalization ---------------------------------------------

    def normalize(self, update: dict[str, Any], agent_id: str | None = None) -> dict[str, Any] | None:
        """Normalize a Telegram update into a ChannelMessage-compatible dict.

        Detects media (photo, document, audio, voice, video) and downloads
        them via the Bot API, saving to the agent's workspace/uploads/.
        """
        message = update.get("message") or update.get("edited_message") or {}
        text = message.get("text", "") or message.get("caption", "")
        chat = message.get("chat", {})
        sender = message.get("from", {})

        has_media = any(
            k in message
            for k in ("document", "photo", "audio", "voice", "video", "video_note")
        )
        if (not text and not has_media) or not chat.get("id"):
            return None

        chat_id = str(chat["id"])
        chat_type = chat.get("type", "private")
        is_group = chat_type in ("group", "supergroup")
        sender_id = str(sender.get("id", ""))

        if is_group:
            session_key = f"telegram:group:{chat_id}"
        else:
            session_key = f"telegram:dm:{sender_id}"

        bot_username = self._bot_usernames.get(agent_id or "", "")

        attachments: list[dict[str, Any]] = []
        if has_media and agent_id:
            att = self._process_telegram_media(message, agent_id)
            if att:
                attachments.append(att)

        chat_title = (chat.get("title") or "").strip()
        metadata: dict[str, Any] = {
            "chat_id": chat_id,
            "chat_type": chat_type,
            "raw_message": message,
        }
        if chat_title:
            metadata["channel_name"] = chat_title

        return {
            "channel": "telegram",
            "session_key": session_key,
            "sender_id": sender_id,
            "sender_name": _build_display_name(sender),
            "content": text or "",
            "is_group": is_group,
            "group_id": chat_id if is_group else None,
            "is_mention": self._is_mention(message, text, agent_id),
            "is_forwarded": "forward_date" in message or "forward_origin" in message,
            "is_reply_to_bot": (
                bot_username and
                message.get("reply_to_message", {})
                .get("from", {})
                .get("username", "")
                .lower() == bot_username.lower()
            ),
            "message_id": str(message.get("message_id", "")),
            "attachments": attachments,
            "metadata": metadata,
        }

    def _process_telegram_media(
        self, message: dict[str, Any], agent_id: str,
    ) -> dict[str, Any] | None:
        """Download a media attachment from Telegram and save to workspace."""
        import time as _time

        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token:
            return None

        file_id = ""
        mime = "application/octet-stream"
        filename = ""
        is_voice = False

        if "document" in message:
            doc = message["document"]
            file_id = doc.get("file_id", "")
            mime = doc.get("mime_type", mime)
            filename = doc.get("file_name", f"document_{int(_time.time())}")
        elif "photo" in message:
            photos = message["photo"]
            if photos:
                biggest = photos[-1]
                file_id = biggest.get("file_id", "")
                mime = "image/jpeg"
                filename = f"photo_{int(_time.time())}.jpg"
        elif "voice" in message:
            voice = message["voice"]
            file_id = voice.get("file_id", "")
            mime = voice.get("mime_type", "audio/ogg")
            filename = f"voice_{int(_time.time())}.ogg"
            is_voice = True
        elif "audio" in message:
            audio = message["audio"]
            file_id = audio.get("file_id", "")
            mime = audio.get("mime_type", "audio/mpeg")
            filename = audio.get("file_name", f"audio_{int(_time.time())}.mp3")
        elif "video" in message:
            video = message["video"]
            file_id = video.get("file_id", "")
            mime = video.get("mime_type", "video/mp4")
            filename = video.get("file_name", f"video_{int(_time.time())}.mp4")
        elif "video_note" in message:
            vn = message["video_note"]
            file_id = vn.get("file_id", "")
            mime = "video/mp4"
            filename = f"videonote_{int(_time.time())}.mp4"

        if not file_id:
            return None

        try:
            import httpx as _httpx

            with _httpx.Client(timeout=15.0) as client:
                gf_resp = client.get(
                    f"{TELEGRAM_API}/bot{token}/getFile",
                    params={"file_id": file_id},
                )
                gf_resp.raise_for_status()
                file_path = gf_resp.json().get("result", {}).get("file_path", "")
                if not file_path:
                    return None

                dl_resp = client.get(
                    f"{TELEGRAM_API}/file/bot{token}/{file_path}",
                )
                dl_resp.raise_for_status()
                raw = dl_resp.content

            from server.main import app
            am = getattr(app.state, "agent_manager", None)
            if am is None:
                return None

            uploads = am.agents_dir / agent_id / "workspace" / "uploads"
            uploads.mkdir(parents=True, exist_ok=True)
            dest = uploads / filename
            dest.write_bytes(raw)

            return {
                "name": filename,
                "path": f"uploads/{filename}",
                "mime_type": mime,
                "size": len(raw),
                "is_voice": is_voice,
            }
        except Exception:
            logger.warning("Telegram: failed to download media", exc_info=True)
            return None

    # -- webhook relay integration -----------------------------------------

    async def register_webhook_relay(self, relay_base_url: str, agent_id: str) -> bool:
        """Register a Telegram webhook pointing to the NestJS relay."""
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token:
            return False

        webhook_url = f"{relay_base_url}/api/channels/webhook/telegram/{agent_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{TELEGRAM_API}/bot{token}/setWebhook",
                    json={"url": webhook_url},
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    logger.error("Telegram [%s] setWebhook failed: %s", agent_id, data)
                    return False
        except Exception as exc:
            logger.error("Telegram [%s] setWebhook error: %s", agent_id, exc)
            return False

        self.update_config({"webhook_relay_url": webhook_url}, agent_id=agent_id)
        self.stop_polling(agent_id)
        logger.info("Telegram [%s]: webhook relay registered at %s", agent_id, webhook_url)

        await self._ensure_relay(agent_id)
        return True

    async def _ensure_relay(self, agent_id: str) -> None:
        """Open relay WS to NestJS if not already connected."""
        if agent_id in self._relay_clients:
            existing = self._relay_clients[agent_id]
            if existing.connected:
                return
            await existing.disconnect()

        relay_url = self._get_relay_base_url(agent_id)
        if not relay_url:
            logger.warning("Telegram [%s]: no NESTJS_URL — relay WS skipped", agent_id)
            return

        import os
        secret = os.environ.get("RUNTIME_SHARED_SECRET", "") or os.environ.get("NLS_SHARED_SECRET", "")

        from nls.runtime.channels import ChannelRelayClient
        client = ChannelRelayClient(relay_url, agent_id, secret)
        self._relay_clients[agent_id] = client
        await client.connect()


def _broadcast_channel_event(
    app: Any,
    runtime: Any,
    normalized: dict[str, Any],
    response: str,
) -> None:
    """Notify the frontend about inbound/outbound channel messages."""
    cm = getattr(app.state, "connection_manager", None)
    if cm is None:
        return
    agent_id = getattr(runtime, "agent_id", "")
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(cm.broadcast(agent_id, {
            "type": "channel_event",
            "channel": "telegram",
            "direction": "inbound",
            "sender": normalized["sender_name"],
            "content": normalized["content"],
            "content_preview": normalized["content"][:100],
            "session_key": normalized["session_key"],
            "response": response,
            "response_preview": response[:100] if response else "",
        }))
    except Exception:
        pass


def _chunk_message(text: str) -> list[str]:
    """Split long messages into Telegram-safe chunks."""
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]
    chunks = []
    while text:
        if len(text) <= MAX_MESSAGE_LENGTH:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, MAX_MESSAGE_LENGTH)
        if split_at < MAX_MESSAGE_LENGTH // 2:
            split_at = MAX_MESSAGE_LENGTH
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def _build_display_name(sender: dict[str, Any]) -> str:
    first = sender.get("first_name", "")
    last = sender.get("last_name", "")
    username = sender.get("username", "")
    name = f"{first} {last}".strip()
    return name or username or "Unknown"
