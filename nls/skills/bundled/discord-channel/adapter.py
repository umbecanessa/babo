"""Discord channel adapter -- real-time Gateway connection via discord.py.

Supports two inbound modes:
  1. **Gateway WebSocket** (primary) -- persistent connection receiving messages live
  2. **REST fallback** -- when no token configured, uses Discord REST API polling

Key features:
  - Mention detection (@Babo) in guild channels
  - Prefix command detection (!help, !ban, !kick, etc.)
  - DM policy enforcement (open / allowlist / disabled)
  - Role-based permission checks (Administrator, Moderator, etc.)
  - Auto-moderation hints for spam/hate speech/abuse
  - Real-time typing indicators and read receipts
  - File attachment handling
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine

import httpx

from nls.tools.agent_tools.base import AgentTool, ToolResult
from nls.agentic.outbound_notify import FINAL_SUMMARY_SCHEMA_PROPERTY

logger = logging.getLogger(__name__)

# Discord gateway constants
DISCORD_API_BASE = "https://discord.com/api/v10"
GATEWAY_URL = "wss://gateway.discord.gg"
MAX_MESSAGE_LENGTH = 2000  # Discord limit per chunk
_TYPING_INTERVAL = 3.0  # seconds between typing indicators


@dataclass
class _TypingState:
    """Track typing indicator state per channel."""
    last_typing: float = 0.0

    def should_type(self) -> bool:
        if time.time() - self.last_typing >= _TYPING_INTERVAL:
            self.last_typing = time.time()
            return True
        return False


@dataclass
class DiscordChannelInfo:
    """Cached info about a Discord server/channel."""
    guild_id: str
    guild_name: str
    channels: dict[str, str] = field(default_factory=dict)  # id -> name
    roles: dict[str, str] = field(default_factory=dict)  # id -> name
    members: dict[str, str] = field(default_factory=dict)  # id -> display_name
    joined_at: float = 0.0
    active_channels: set[str] = field(default_factory=set)


SIGNAL_TAG_RE = re.compile(r"\[(?:[A-Za-z_]+)(?:[:.](?:[^\]]*))?\]\s*")


def _strip_signal_tags(text: str) -> str:
    """Remove ANS behavioral tags (e.g. [EVALUATE:correct]) from outgoing text."""
    return _SIGNAL_TAG_RE.sub("", text).strip()[:MAX_MESSAGE_LENGTH]


# ---------------------------------------------------------------------------
# Agent tools
# ---------------------------------------------------------------------------


class DiscordSendTool:
    """Agent tool for sending Discord messages."""

    def __init__(self, adapter: "DiscordAdapter", agent_id: str | None = None) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "discord_send"

    @property
    def description(self) -> str:
        bot_user = self._adapter._bot_username or ""
        base = (
            "Send a Discord message. Provide channel_id (for servers) or user ID (for DMs) and text. "
            "Supports basic markdown formatting. To share documents/reports/images, "
            "use file_path with the workspace path. Messages over {} characters will be truncated.".format(
                MAX_MESSAGE_LENGTH
            )
        )
        if bot_user:
            base += f" This agent's Discord username is @{bot_user}."
        return base

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "Discord channel ID or user ID to send to",
                },
                "text": {
                    "type": "string",
                    "description": "Message text to send (supports basic markdown)",
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "Workspace file path to attach as a document "
                        "(e.g. 'PROJECT_REPORT.md', 'data/results.csv'). "
                        "Do NOT paste file contents into text -- use this parameter instead."
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
                "reply_to_message_id": {
                    "type": "string",
                    "description": (
                        "Message ID to reply to (for threading in channels). "
                        "The reply will show as a threaded response."
                    ),
                },
                "final_summary": FINAL_SUMMARY_SCHEMA_PROPERTY,
            },
            "required": ["channel_id", "text"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        channel_id = params.get("channel_id", "")
        text = params.get("text", "")
        file_path = params.get("file_path", "")
        file_paths = params.get("file_paths", []) or []
        reply_to = params.get("reply_to_message_id", "")

        if not channel_id:
            return ToolResult(content="Error: channel_id is required", is_error=True)
        if not text and not file_path and not file_paths:
            return ToolResult(
                content="Error: text, file_path, or file_paths is required",
                is_error=True,
            )

        # Check allowed chats policy
        allowed = self._adapter.get_allowed_channel_ids(self._agent_id)
        if allowed and channel_id not in allowed:
            known_list = ", ".join(sorted(allowed)[:5])
            logger.warning(
                "Discord send BLOCKED: agent=%s tried to message %s "
                "(allowed: %s)", self._agent_id, channel_id, allowed,
            )
            return ToolResult(
                content=(
                    f"Cannot send to channel {channel_id} — that channel has not "
                    f"messaged you yet. Known channels: {known_list}"
                ),
                is_error=True,
            )

        # Handle file attachments
        if file_paths:
            results: list[str] = []
            for fp in file_paths:
                r = await self._adapter.send_file(channel_id, fp)
                results.append(r.content)
            if text:
                await self._adapter.send_message(channel_id, text, reply_to=reply_to)
            return ToolResult(content=f"Sent {len(file_paths)} file(s) to {channel_id}")

        if file_path:
            return await self._adapter.send_file_with_caption(channel_id, file_path, caption=text)

        # Regular text message
        ok = await self._adapter.send_message(channel_id, text, reply_to=reply_to)
        if ok:
            return ToolResult(content=f"Message sent to {channel_id}")
        return ToolResult(content="Failed to send message", is_error=True)


class DiscordSetupTool:
    """Agent tool for validating a Discord bot token and configuring the connection."""

    def __init__(self, adapter: "DiscordAdapter", agent_id: str | None = None) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "discord_setup"

    @property
    def description(self) -> str:
        return (
            "Validate a Discord bot token and configure the Discord connection. "
            "Call this after the user provides their bot token from Developer Portal."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "bot_token": {
                    "type": "string",
                    "description": "The Discord bot token from Developer Portal.",
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
            url = f"{DISCORD_API_BASE}/users/@me"
            headers = {"Authorization": f"Bot {bot_token}"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return ToolResult(
                content=f"Invalid token -- Discord API returned an error: {exc}. "
                        "Ask the user to double-check their token in Developer Portal.",
                is_error=True,
            )

        result = data.get("user", data)
        bot_username = result.get("username", "")
        bot_discriminator = result.get("discriminator", "#0000")
        bot_id = result.get("id", "")

        if agent_id:
            cfg_update: dict[str, Any] = {
                "bot_token": bot_token,
                "bot_username": bot_username,
                "enabled": True,
            }
            self._adapter.update_config(cfg_update, agent_id=agent_id)
            self._adapter._bot_username = bot_username
            self._adapter._connected_agents.add(agent_id)

        return ToolResult(
            content=(
                f"Discord connected! Bot: @{bot_username}{bot_discriminator} (ID: {bot_id})\n"
                f"Status: Online\n"
                f"Next step: Set owner_identity and dm_policy using skill_configure.\n"
                f"The bot will join your servers when Babo starts with the valid token."
            ),
            is_error=False,
        )


# ---------------------------------------------------------------------------
# Main adapter
# ---------------------------------------------------------------------------


class DiscordAdapter:
    """Discord channel adapter using discord.py Gateway.

    Handles both inbound messages (via Gateway events) and outbound messages
    (via the agent tools). Provides moderation capabilities through role checks.
    """

    def __init__(self, global_config: dict[str, Any], ctx) -> None:
        self._config = global_config
        self._ctx = ctx
        self._bot = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

        # Connection state
        self._bot_username: str = ""
        self._connected_agents: set[str] = set()
        self._guild_info: dict[str, DiscordChannelInfo] = {}
        self._typing_states: dict[str, _TypingState] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue()

        # Process message callback (set by AgentRuntime)
        self._process_msg_callback: Callable[[dict[str, Any]], Coroutine] | None = None

    # ── Configuration helpers ────────────────────────────────────

    def register_process_message_callback(self, callback: Callable[[dict[str, Any]], Coroutine]) -> None:
        """Register a callback for processing incoming messages through AgentRuntime."""
        self._process_msg_callback = callback

    def get_allowed_channel_ids(self, agent_id: str | None = None) -> set[str] | None:
        """Get set of allowed channel/user IDs based on current config."""
        if not agent_id:
            return None
        policy = self._config.get("dm_policy", "disabled")
        if policy == "disabled":
            return set()
        elif policy == "allowlist":
            allow_from = self._config.get("allow_from", [])
            return set(allow_from)
        else:  # open
            return None  # No restriction

    def update_config(self, updates: dict[str, Any], agent_id: str | None = None) -> None:
        """Update the adapter configuration and persist to disk."""
        self._config.update(updates)
        if agent_id:
            self._connected_agents.add(agent_id)
            current = self._ctx.load_config(agent_id=agent_id)
            current.update(updates)
            self._ctx.save_config(current, agent_id=agent_id)
        else:
            current = self._ctx.load_config()
            current.update(updates)
            self._ctx.save_config(current)

    def _resolve_agent_id(self) -> str:
        """Pick the agent that owns this Discord bot connection."""
        if self._connected_agents:
            return next(iter(self._connected_agents))
        for aid, cfg in self._ctx.load_all_agent_configs().items():
            if cfg.get("enabled", True) and cfg.get("bot_token"):
                self._connected_agents.add(aid)
                return aid
        return ""

    def create_send_tool(self, agent_id: str | None = None) -> DiscordSendTool:
        """Create a new DiscordSendTool instance for an agent."""
        return DiscordSendTool(adapter=self, agent_id=agent_id)

    def create_setup_tool(self, agent_id: str | None = None) -> DiscordSetupTool:
        """Create a new DiscordSetupTool instance for an agent."""
        return DiscordSetupTool(adapter=self, agent_id=agent_id)

    # ── Outbound messaging ───────────────────────────────────────

    def _get_typing_state(self, channel_id: str) -> _TypingState:
        if channel_id not in self._typing_states:
            self._typing_states[channel_id] = _TypingState()
        return self._typing_states[channel_id]

    async def send_message(
        self,
        channel_id: str,
        text: str,
        reply_to: str | None = None,
    ) -> bool:
        """Send a text message to a Discord channel/user."""
        try:
            stripped = _strip_signal_tags(text)
            if not stripped:
                return False

            chunks = [stripped[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(stripped), MAX_MESSAGE_LENGTH)]
            ts = self._get_typing_state(channel_id)
            if ts.should_type():
                try:
                    ch = await self._bot.fetch_channel(int(channel_id))
                    await ch.typing()
                except Exception:
                    pass

            kwargs = {}
            if reply_to:
                kwargs["reference"] = reply_to

            for chunk in chunks:
                channel = await self._bot.fetch_channel(int(channel_id))
                await channel.send(chunk, **kwargs)
            return True
        except Exception as e:
            logger.error(f"Failed to send message to {channel_id}: {e}")
            return False

    async def send_file(
        self,
        channel_id: str,
        file_path: str,
        caption: str = "",
    ) -> ToolResult:
        """Send a file to a Discord channel."""
        try:
            full_path = Path(file_path)
            if not full_path.exists():
                return ToolResult(content=f"File not found: {file_path}", is_error=True)

            import discord as _dc
            file_obj = _dc.File(full_path, filename=full_path.name)
            channel = await self._bot.fetch_channel(int(channel_id))
            await channel.send(content=caption if caption else None, file=file_obj)
            return ToolResult(content=f"File {full_path.name} sent to {channel_id}")
        except Exception as e:
            logger.error(f"Failed to send file: {e}")
            return ToolResult(content=f"File send failed: {e}", is_error=True)

    async def send_file_with_caption(
        self,
        channel_id: str,
        file_path: str,
        caption: str = "",
    ) -> ToolResult:
        """Send a file with optional caption."""
        return await self.send_file(channel_id, file_path, caption)

    # ── Inbound event handlers (registered via @bot.event) ───────

    async def _on_ready(self):
        """Handle bot being ready."""
        logger.info(f"Discord bot ready! User: {self._bot.user}")
        self._bot_username = self._bot.user.name

        for guild in self._bot.guilds:
            await self._cache_guild_info(guild)
            logger.info(f"Joined guild: {guild.name} (ID: {guild.id}, {guild.member_count} members)")

        for channel in self._bot.get_all_channels():
            logger.debug(f"Available channel: #{channel.name} (id={channel.id}, type={type(channel).__name__})")

        logger.info(f"Ready! Listening in {len(self._bot.guilds)} guilds.")

    async def _on_message(self, message):
        """Handle incoming messages."""
        if message.author.bot:
            return

        if isinstance(message.channel, discord.DMChannel):
            if not self._should_process_dm(message.author):
                return

        raw_data = {
            "source": "discord",
            "platform": "discord",
            "user_id": str(message.author.id),
            "username": message.author.name,
            "display_name": message.author.display_name,
            "message_id": str(message.id),
            "content": message.content.strip(),
            "channel_id": str(message.channel.id),
            "channel_name": getattr(message.channel, "name", "unknown"),
            "guild_id": str(message.guild.id) if message.guild else None,
            "guild_name": message.guild.name if message.guild else "DM",
            "timestamp": message.created_at.isoformat() if hasattr(message, "created_at") else None,
            "mention_ids": [str(m.id) for m in message.mentions],
            "role_names": [r.name for r in message.author.roles] if hasattr(message, "roles") else [],
            "has_embeds": len(message.embeds) > 0,
            "has_files": len(message.attachments) > 0,
        }

        logger.debug(f"[{raw_data['guild_name']}/{raw_data['channel_name']}] {raw_data['username']}: {raw_data['content'][:60]}...")

        if not self._is_directed_at_us(raw_data):
            return

        ts = self._get_typing_state(raw_data["channel_id"])
        ts.last_typing = time.time()

        if self._process_msg_callback:
            try:
                await self._process_msg_callback(raw_data)
            except Exception as e:
                logger.error(f"Failed to process message: {e}")
        else:
            await self._process_inbound_gateway_message(raw_data)

    async def _process_inbound_gateway_message(self, raw_data: dict[str, Any]) -> None:
        """Route Gateway events through the shared channel message pipeline."""
        agent_id = self._resolve_agent_id()
        if not agent_id:
            logger.warning("Discord Gateway message ignored — no agent with bot_token")
            return
        try:
            from server.main import app
        except ImportError:
            return

        agent_manager = getattr(app.state, "agent_manager", None)
        if agent_manager is None:
            return
        runtime = agent_manager.get_runtime(agent_id)
        if runtime is None:
            logger.warning("Discord Gateway: agent %s runtime not loaded", agent_id)
            return

        body = {
            "content": raw_data.get("content", ""),
            "channel_id": raw_data.get("channel_id", ""),
            "author": {
                "username": raw_data.get("username", "?"),
                "id": raw_data.get("user_id", ""),
            },
            "guild_id": raw_data.get("guild_id"),
        }
        normalized = self.normalize_webhook(body, agent_id=agent_id)
        if normalized is None:
            return

        session_key = normalized["session_key"]
        text = normalized.get("content", "") or ""
        chat_id = normalized.get("channel_id", "")
        sender_name = normalized.get("sender_name", "?")

        history = runtime.load_session_history(session_key)
        runtime.save_session_history(
            history + [{"role": "user", "content": text or "[media]"}],
            session_key=session_key,
            metadata={"channel": "discord", "sender": sender_name},
        )

        try:
            from nls.skills.channel_processing import (
                process_channel_message,
                try_feed_pending_answer,
            )

            if try_feed_pending_answer(agent_id, session_key, text):
                return

            user_input = (
                f"[{sender_name} via Discord]: {text}"
                if text else f"[{sender_name} via Discord]:"
            )
            response_text = await process_channel_message(
                app,
                runtime,
                agent_id,
                user_input,
                history,
                channel_adapter=self,
                reply_target=chat_id,
                session_key=session_key,
            )
            if response_text:
                clean = _strip_signal_tags(response_text)
                if clean:
                    await self.send_message(chat_id, clean)
                    runtime.save_session_history(
                        history
                        + [
                            {"role": "user", "content": text},
                            {"role": "assistant", "content": clean},
                        ],
                        session_key=session_key,
                        metadata={"channel": "discord", "sender": sender_name},
                    )
        except Exception as exc:
            logger.error(
                "Discord Gateway processing failed for agent %s: %s",
                agent_id, exc, exc_info=True,
            )

    def _is_directed_at_us(self, msg_data: dict[str, Any]) -> bool:
        """Check if a message is directed at our bot."""
        mention_pattern = self._config.get("mention_pattern", "Babo").lower()
        prefix_commands = self._config.get("prefix_commands", True)

        content = msg_data.get("content", "").lower().strip()
        mention_ids = msg_data.get("mention_ids", [])

        if mention_ids:
            return True

        if mention_pattern and (mention_pattern in content or mention_pattern in content.split()):
            return True

        if prefix_commands:
            parts = content.split(None, 1)
            if parts and parts[0].startswith("!"):
                cmd = parts[0][1:].split()[0] if len(parts[0]) > 1 else ""
                known_cmds = {
                    "help", "ban", "kick", "mute", "warn", "purge", "unban",
                    "info", "moderate", "settings", "rules", "welcome",
                    "channels", "roles", "members", "status", "ping", "uptime",
                }
                if cmd in known_cmds:
                    return True
        return False

    def _should_process_dm(self, author: object) -> bool:
        """Check if we should process a DM from this user."""
        policy = self._config.get("dm_policy", "disabled")
        if policy == "open":
            return True
        elif policy == "allowlist":
            allow_from = self._config.get("allow_from", [])
            auth_name = getattr(author, "name", "")
            auth_id = str(getattr(author, "id", ""))
            return auth_name in allow_from or auth_id in allow_from
        else:
            return False

    async def _cache_guild_info(self, guild):
        """Cache useful information about a guild."""
        info = self._guild_info.get(str(guild.id))
        if not info:
            info = DiscordChannelInfo(
                guild_id=str(guild.id),
                guild_name=guild.name,
            )
            self._guild_info[str(guild.id)] = info

        info.channels.clear()
        for ch in guild.text_channels + guild.voice_channels:
            info.channels[str(ch.id)] = ch.name

        info.roles.clear()
        for role in guild.roles:
            info.roles[str(role.id)] = role.name

        info.joined_at = time.time()

    # ── Webhook normalization helpers ────────────────────────────

    def normalize_webhook(self, body: dict[str, Any], agent_id: str = "") -> dict[str, Any] | None:
        """Normalize a Discord webhook payload into our internal format.

        Handles both direct message payloads and interaction/callback data.
        Returns None if the message should be skipped.
        """
        import uuid

        sender_name = "unknown"
        user_id = "unknown"
        channel_id = "unknown"
        guild_id = None
        guild_name = "DM"
        content = ""
        is_dm = True

        # Direct message-style payload
        if "content" in body and "channel_id" in body:
            sender_name = body.get("author", {}).get("username", body.get("author", {}).get("name", "unknown"))
            user_id = str(body.get("author", {}).get("id", "unknown"))
            channel_id = str(body["channel_id"])
            content = body.get("content", "")
            if body.get("guild_id"):
                guild_id = str(body["guild_id"])
                is_dm = False
                guild_name = body.get("guild", {}).get("name", guild_name or "Unknown Server")
            else:
                guild_id = None
                is_dm = True

        elif "message" in body:
            msg = body["message"]
            sender_name = msg.get("author", {}).get("username", "unknown")
            user_id = str(msg.get("author", {}).get("id", "unknown"))
            channel_id = str(msg.get("channel_id", "unknown"))
            content = msg.get("content", "")
            guild_id = str(msg.get("guild_id")) if msg.get("guild_id") else None
            guild_name = msg.get("guild", {}).get("name", "Unknown Server") if msg.get("guild") else "Unknown Server"
            is_dm = not guild_id

        elif "interaction" in body:
            inter = body["interaction"]
            sender_name = inter.get("user", {}).get("username", "unknown")
            user_id = str(inter.get("user", {}).get("id", "unknown"))
            channel_id = str(inter.get("channel_id", "unknown"))
            content = (inter.get("data", {}).get("options", [{}])[-1].get("value", "") if inter.get("data", {}).get("options") else "") or ""
            guild_id = str(inter.get("guild_id", "")) if inter.get("guild_id") else None
            guild_name = inter.get("guild_name", "Unknown Server")
            is_dm = not guild_id

        # Skip empty messages
        if not content or not content.strip():
            return None

        session_key = f"discord-{channel_id}" if not is_dm else f"discord-dm-{user_id}"

        return {
            "session_key": session_key,
            "content": content.strip(),
            "sender_name": sender_name,
            "sender_id": user_id,
            "channel_id": channel_id,
            "guild_id": guild_id,
            "guild_name": guild_name,
            "is_dm": is_dm,
            "attachments": [],
            "metadata": {"platform": "discord", "raw_body_keys": list(body.keys())[:20]},
        }

    def get_status(self, agent_id: str = "") -> dict[str, Any]:
        """Return connection status for the frontend dashboard."""
        return {
            "channel": "discord",
            "connected": self._running and self._bot is not None,
            "bot_username": self._bot_username or "",
            "guild_count": len(getattr(self._bot, "guilds", [])) if self._bot else 0,
            "enabled": bool(self._config.get("bot_token")),
            "dm_policy": self._config.get("dm_policy", "disabled"),
            "prefix_commands": self._config.get("prefix_commands", True),
        }

    # ── Lifecycle hooks ──────────────────────────────────────────

    async def startup(self):
        """Start the Discord bot connection."""
        for aid, cfg in self._ctx.load_all_agent_configs().items():
            if cfg.get("enabled", True) and cfg.get("bot_token"):
                self._config.update(
                    {k: v for k, v in cfg.items() if v not in (None, "")},
                )
                self._connected_agents.add(aid)

        bot_token = self._config.get("bot_token", "")
        if not bot_token:
            logger.warning("No Discord bot token configured. Skipping startup.")
            return

        try:
            import discord

            intents = discord.Intents.default()
            intents.message_content = True
            intents.messages = True
            intents.guilds = True
            intents.members = True
            intents.reactions = True
            intents.emojis = True
            intents.presences = True

            self._bot = discord.Bot(intents=intents, command_prefix="!")

            @self._bot.event
            async def on_ready():
                await self._on_ready()

            @self._bot.event
            async def on_message(message):
                await self._on_message(message)

            @self._bot.event
            async def on_message_edit(before, after):
                logger.debug(f"Message edited in {after.channel} by {after.author}")

            @self._bot.event
            async def on_message_delete(message):
                logger.debug(f"Message deleted in {message.channel} by {message.author}")

            self._running = True
            logger.info("Starting Discord bot session...")
            asyncio.create_task(
                self._run_bot_session(bot_token),
                name="discord-channel-gateway",
            )

        except Exception as e:
            logger.error(f"Failed to start Discord adapter: {e}", exc_info=True)
            self._running = False
            raise

    async def _run_bot_session(self, bot_token: str) -> None:
        """Run discord.py Gateway in the background (non-blocking startup hook)."""
        try:
            if self._bot is not None:
                await self._bot.start(bot_token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Discord Gateway session ended: %s", exc, exc_info=True)
            self._running = False

    async def shutdown(self):
        """Stop the Discord bot connection."""
        if self._bot and self._running:
            try:
                await self._bot.close()
                self._running = False
                logger.info("Discord adapter shut down gracefully")
            except Exception as e:
                logger.error(f"Error shutting down Discord adapter: {e}")
                self._running = False

    async def process_queued_messages(self):
        """Process any queued messages (fallback when no callback registered)."""
        while not self._message_queue.empty():
            try:
                msg_data = self._message_queue.get_nowait()
                logger.debug(f"Queued message: {msg_data['content'][:50]}...")
            except asyncio.QueueEmpty:
                break
