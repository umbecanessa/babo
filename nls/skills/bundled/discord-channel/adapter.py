"""Discord channel adapter — REST send, NestJS Gateway relay, scoped channels."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from nls.skills.channel_adapter_util import (
    broadcast_channel_event,
    chunk_message,
    ensure_relay,
    get_relay_base_url,
    get_runtime_secret,
    register_with_agent,
    resolve_workspace_file,
    strip_signal_tags,
)
from nls.skills.channel_scope import (
    apply_desired_channel,
    compile_groups_policy,
    effective_channel_ids,
    finalize_scoped_config,
    list_scoped_channels,
    merge_observed_channels,
    reconcile_config,
    scoped_channels_from_config,
)
from nls.tools.agent_tools.base import AgentTool, ToolResult

logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"
MAX_MESSAGE_LENGTH = 2000
_MENTION_ID_RE = re.compile(r"<@!?(\d+)>")
_PERM_VIEW = 1024
_PERM_SEND = 2048
_PERM_HISTORY = 65536
_BOT_PERMS = _PERM_VIEW | _PERM_SEND | _PERM_HISTORY


def extract_discord_user_mention_ids(text: str) -> list[str]:
    """Snowflake user ids from ``<@123>`` / ``<@!123>`` mention markup."""
    return list(dict.fromkeys(_MENTION_ID_RE.findall(text or "")))


def discord_send_payload(content: str, *, reply_to: str | None = None) -> dict[str, Any]:
    """Build Discord message payload with allowed_mentions for user pings."""
    payload: dict[str, Any] = {"content": content}
    if reply_to:
        payload["message_reference"] = {"message_id": str(reply_to)}
    mention_ids = extract_discord_user_mention_ids(content)
    if mention_ids:
        payload["allowed_mentions"] = {"parse": [], "users": mention_ids}
    return payload


def discord_setup_gaps(cfg: dict[str, Any]) -> list[str]:
    """Human-readable blockers after token connect (Tools UI + agent follow-up)."""
    gaps: list[str] = []
    if not str(cfg.get("owner_identity", "")).strip():
        gaps.append("owner_identity")
    listening = effective_channel_ids(cfg)
    if not listening:
        gaps.append("at least one channel listening in scope")
    dm = str(cfg.get("dm_policy", "disabled")).lower()
    if dm == "disabled" and not listening:
        gaps.append("interaction policy (channels or DMs via interaction_mode)")
    return gaps


def _channel_display_name(cfg: dict[str, Any], channel_id: str) -> str:
    scoped = (cfg.get("scoped_channels") or {}).get("channels") or {}
    entry = scoped.get(channel_id)
    if isinstance(entry, dict) and entry.get("name"):
        return str(entry["name"])
    return channel_id


def broadcast_channel_policy_skip(
    app: Any,
    agent_id: str,
    normalized: dict[str, Any],
    *,
    reason: str,
) -> None:
    """Surface ignored inbound messages in Activity (channel not scoped / policy)."""
    cm = getattr(app.state, "connection_manager", None)
    if cm is None:
        return
    meta = normalized.get("metadata") or {}
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(cm.broadcast(agent_id, {
            "type": "channel_event",
            "channel": "discord",
            "direction": "skipped",
            "skip_reason": reason,
            "sender": normalized.get("sender_name", "?"),
            "content": normalized.get("content", ""),
            "content_preview": (normalized.get("content") or "")[:100],
            "session_key": normalized.get("session_key", ""),
            "channel_name": meta.get("channel_name", ""),
        }))
    except Exception:
        pass


class DiscordSendTool:
    def __init__(self, adapter: DiscordAdapter, agent_id: str | None = None) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "discord_send"

    @property
    def description(self) -> str:
        bot = self._adapter._bot_usernames.get(self._agent_id or "", "")
        base = (
            "Send a Discord message. Use contacts to look up discord_id first. "
            "Provide channel_id (text channel snowflake) or user id for DMs. "
            "To @mention users/bots use <@snowflake_id> in text (renders as a ping)."
        )
        if bot:
            base += f" Bot username: {bot}."
        return base

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "Discord channel or user snowflake ID",
                },
                "text": {"type": "string", "description": "Message text"},
                "file_path": {
                    "type": "string",
                    "description": "Workspace-relative file to attach",
                },
                "reply_to_message_id": {
                    "type": "string",
                    "description": "Message ID to reply to (threading)",
                },
            },
            "required": ["channel_id", "text"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        channel_id = str(params.get("channel_id") or "").strip()
        text = str(params.get("text") or "")
        file_path = str(params.get("file_path") or "").strip()
        reply_to = str(params.get("reply_to_message_id") or "").strip()
        if not channel_id:
            return ToolResult(content="Error: channel_id is required", is_error=True)
        if not text and not file_path:
            return ToolResult(content="Error: text or file_path required", is_error=True)

        allowed = self._adapter.get_allowed_target_ids(self._agent_id)
        if self._adapter._outbound_restricted(self._agent_id):
            if channel_id not in allowed:
                return ToolResult(
                    content=(
                        f"Cannot send to {channel_id} — not in allowed targets. "
                        "Save contact with discord_id, enable the channel in Tools, "
                        "or wait for them to message first."
                    ),
                    is_error=True,
                )
        elif allowed and channel_id not in allowed:
            return ToolResult(
                content=(
                    f"Cannot send to {channel_id} — not in allowed targets. "
                    "Save contact with discord_id or wait for them to message first."
                ),
                is_error=True,
            )

        if file_path:
            return await self._adapter.send_file(
                channel_id, file_path, caption=text, agent_id=self._agent_id,
                reply_to=reply_to or None,
            )
        ok = await self._adapter.send(
            channel_id, text, agent_id=self._agent_id, reply_to=reply_to or None,
        )
        if ok:
            return ToolResult(content=f"Message sent to {channel_id}")
        return ToolResult(content="Failed to send message", is_error=True)


class DiscordSetupTool:
    def __init__(self, adapter: DiscordAdapter, agent_id: str | None = None) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "discord_setup"

    @property
    def description(self) -> str:
        return (
            "Validate a Discord bot token and connect the Discord channel. "
            "Call after the user pastes their Developer Portal bot token."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "bot_token": {"type": "string", "description": "Discord bot token"},
            },
            "required": ["bot_token"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        token = str(params.get("bot_token") or "").strip()
        agent_id = self._agent_id or ""
        if not token:
            return ToolResult(content="Error: bot_token is required", is_error=True)
        if not agent_id:
            return ToolResult(content="Error: no agent context", is_error=True)

        try:
            me = await self._adapter._api_get(token, "/users/@me")
        except Exception as exc:
            return ToolResult(
                content=f"Invalid Discord token: {exc}",
                is_error=True,
            )

        bot_id = str(me.get("id", ""))
        bot_username = str(me.get("username", ""))
        self._adapter.update_config(
            {
                "bot_token": token,
                "enabled": True,
                "bot_id": bot_id,
                "bot_username": bot_username,
            },
            agent_id=agent_id,
        )
        self._adapter._bot_ids[agent_id] = bot_id
        self._adapter._bot_usernames[agent_id] = bot_username
        self._adapter._connected_agents.add(agent_id)
        register_with_agent(agent_id, self._adapter)

        relay_note = ""
        relay_url = get_relay_base_url(self._adapter._agent_cfg(agent_id))
        if relay_url:
            ok = await self._adapter.register_gateway_relay(relay_url, agent_id)
            relay_note = (
                " NestJS Gateway relay registered."
                if ok else " NestJS Gateway registration failed — using local Gateway fallback."
            )
            if not ok:
                self._adapter.start_local_gateway(agent_id)
        else:
            self._adapter.start_local_gateway(agent_id)
            relay_note = " Local Gateway started (no NESTJS_URL)."

        await self._adapter.sync_channels_from_platform(agent_id, auto_enable=True)

        cfg = self._adapter._agent_cfg(agent_id)
        gaps = discord_setup_gaps(cfg)
        if gaps:
            gap_text = "; ".join(gaps)
            return ToolResult(
                content=(
                    f"Discord token saved as @{bot_username} (id={bot_id}).{relay_note}\n"
                    "SETUP_INCOMPLETE — bot is connected but cannot reply in guild channels yet.\n"
                    f"Still needed: {gap_text}\n"
                    "Next: ask_user() for owner username, then skill_configure("
                    "skill_name='discord-channel', interaction_mode='owner_plus_shared' "
                    "or 'shared_only', config={'owner_identity': '...'}, owner_confirm=true).\n"
                    "Confirm channel scope in Tools → Discord if #general is not listening."
                ),
            )

        return ToolResult(
            content=(
                f"Discord connected as @{bot_username} (id={bot_id}).{relay_note} "
                "SETUP_COMPLETE for guild channels. "
                "Call skill_configure(skill_name='discord-channel') if owner or DM policy still missing."
            ),
        )


class DiscordManageTool:
    """Deprecated alias — use channel_manage(channel='discord', ...)."""

    def __init__(self, adapter: "DiscordAdapter", agent_id: str | None = None) -> None:
        self._agent_id = agent_id or ""

    @property
    def name(self) -> str:
        return "discord_manage"

    @property
    def description(self) -> str:
        return (
            "Deprecated — use channel_manage(channel='discord', action=...) instead. "
            "Same server-side token; never bash/python with tokens."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {"type": "string"},
                "channel_id": {"type": "string"},
                "bot_user_id": {"type": "string"},
                "enabled": {"type": "boolean"},
                "require_mention": {"type": "boolean"},
            },
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        from nls.runtime.channel_manage import dispatch_channel_manage

        action = str(params.get("action") or "").strip().lower()
        ok, msg = await dispatch_channel_manage(
            self._agent_id, "discord", action, dict(params),
        )
        return ToolResult(content=msg, is_error=not ok)


class DiscordAdapter:
    channel_name: str = "discord"

    def __init__(self, global_config: dict[str, Any], ctx: Any) -> None:
        self._global_config = global_config
        self._ctx = ctx
        self._agent_configs: dict[str, dict[str, Any]] = {}
        self._bot_ids: dict[str, str] = {}
        self._bot_usernames: dict[str, str] = {}
        self._connected_agents: set[str] = set()
        self._known_senders: dict[str, set[str]] = {}
        self._relay_clients: dict[str, Any] = {}
        self._gateway_tasks: dict[str, asyncio.Task[None]] = {}
        self._last_sync_error: dict[str, str] = {}
        self._load_all_agent_configs()
        self._load_known_senders()

    def _load_all_agent_configs(self) -> None:
        for agent_id, cfg in self._ctx.load_all_agent_configs().items():
            self._agent_configs[agent_id] = cfg
            if cfg.get("bot_id"):
                self._bot_ids[agent_id] = str(cfg["bot_id"])

    def _agent_cfg(self, agent_id: str | None) -> dict[str, Any]:
        if agent_id:
            from nls.runtime.channel_agent_config import merge_global_and_agent_channel_config

            merged = merge_global_and_agent_channel_config(
                self._global_config,
                self._agent_configs.get(agent_id, {}),
            )
            if "groups" not in merged or not merged.get("groups"):
                merged["groups"] = compile_groups_policy(merged)
            return merged
        return self._global_config

    @property
    def name(self) -> str:
        return "discord"

    def update_config(self, new_config: dict[str, Any], agent_id: str) -> None:
        merged = dict(self._agent_configs.get(agent_id, {}))
        merged.update(new_config)
        if "scoped_channels" in merged or any(
            k in new_config for k in ("enabled_desired", "channels")
        ):
            merged = reconcile_config(merged)
        elif "scoped_channels" in merged:
            merged["groups"] = compile_groups_policy(merged)
        self._agent_configs[agent_id] = merged
        self._ctx.save_config(merged, agent_id=agent_id)

    async def send(
        self,
        target: str,
        message: str,
        **kwargs: Any,
    ) -> bool:
        agent_id = kwargs.pop("agent_id", None)
        reply_to = kwargs.pop("reply_to", None)
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token:
            return False
        headers = {"Authorization": f"Bot {token}"}
        for chunk in chunk_message(message, MAX_MESSAGE_LENGTH):
            payload = discord_send_payload(chunk, reply_to=reply_to)
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        f"{DISCORD_API}/channels/{target}/messages",
                        headers=headers,
                        json=payload,
                    )
                    resp.raise_for_status()
            except Exception as exc:
                logger.error("Discord send failed: %s", exc)
                return False
        return True

    async def send_file(
        self,
        target: str,
        file_path: str,
        *,
        caption: str = "",
        agent_id: str | None = None,
        reply_to: str | None = None,
    ) -> ToolResult:
        if not agent_id:
            return ToolResult(content="No agent_id", is_error=True)
        resolved = resolve_workspace_file(agent_id, file_path)
        if resolved is None:
            return ToolResult(content=f"File not found: {file_path}", is_error=True)
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token:
            return ToolResult(content="No bot_token", is_error=True)
        headers = {"Authorization": f"Bot {token}"}
        payload: dict[str, Any] = {}
        if caption:
            payload["content"] = caption
            mention_ids = extract_discord_user_mention_ids(caption)
            if mention_ids:
                payload["allowed_mentions"] = {"parse": [], "users": mention_ids}
        if reply_to:
            payload["message_reference"] = {"message_id": str(reply_to)}
        data: dict[str, Any] = {}
        if payload:
            data["payload_json"] = json.dumps(payload)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{DISCORD_API}/channels/{target}/messages",
                    headers=headers,
                    data=data,
                    files={"files[0]": (resolved.name, resolved.read_bytes())},
                )
                resp.raise_for_status()
            return ToolResult(content=f"File sent to {target}")
        except Exception as exc:
            return ToolResult(content=f"Send file failed: {exc}", is_error=True)

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
        connected = False
        if agent_id:
            connected = agent_id in self._connected_agents
            if not connected:
                try:
                    from nls.runtime.channel_agent_config import agent_channel_is_configured

                    data_root = self._ctx._skills_dir.parent
                    connected = agent_channel_is_configured(data_root, agent_id, "discord")
                except Exception:
                    connected = False
        else:
            connected = bool(self._connected_agents)
        scoped = list_scoped_channels(cfg)
        effective = [c for c in scoped if c.get("effective_enabled")]
        aid = agent_id or ""
        return {
            "channel": "discord",
            "connected": connected,
            "bot_username": self._bot_usernames.get(aid, cfg.get("bot_username", "")),
            "bot_id": self._bot_ids.get(aid, cfg.get("bot_id", "")),
            "enabled": cfg.get("enabled", False),
            "scoped_channel_count": len(scoped),
            "active_channel_count": len(effective),
            "channels": scoped,
            "sync_error": self._last_sync_error.get(aid, ""),
        }

    def channel_manage_actions(self) -> list[str]:
        return ["sync", "list", "enable", "grant_bot_access", "squad_readiness"]

    async def manage_channel(
        self,
        agent_id: str,
        action: str,
        params: dict[str, Any],
    ) -> tuple[bool, str]:
        from nls.runtime.channel_manage import format_scoped_channel_status

        act = (action or "").strip().lower()
        if act in ("sync", "list"):
            if act == "sync":
                await self.sync_channels_from_platform(agent_id, auto_enable=True)
            status = self.get_status(agent_id=agent_id)
            return True, format_scoped_channel_status("Discord", status)

        channel_id = str(params.get("channel_id") or "").strip()
        if act == "squad_readiness":
            if not channel_id:
                return False, "Error: channel_id required"
            try:
                from nls.runtime.discord_squad_readiness import audit_squad_discord_channel

                _, report, _playbook = await audit_squad_discord_channel(agent_id, channel_id)
                return True, report
            except Exception as exc:
                return False, f"Error: {exc}"

        if act == "enable":
            if not channel_id:
                return False, "Error: channel_id required"
            enabled = params.get("enabled")
            if enabled is None:
                enabled = True
            require_mention = params.get("require_mention")
            rm = bool(require_mention) if require_mention is not None else None
            updated = await self.apply_channel_desired(
                agent_id, channel_id, enabled=bool(enabled), require_mention=rm,
            )
            warn = updated.pop("_permission_warning", "")
            lines = [f"Channel {channel_id} enabled={enabled} on this agent."]
            if warn:
                lines.append(f"Warning: {warn}")
            return True, "\n".join(lines)

        if act == "grant_bot_access":
            bot_user_id = str(params.get("bot_user_id") or "").strip()
            if not channel_id or not bot_user_id:
                return False, "Error: channel_id and bot_user_id required"
            ok, msg = await self.grant_channel_member_access(
                agent_id, channel_id, bot_user_id, grant=True,
            )
            if not ok:
                return False, f"Failed: {msg}"
            return True, (
                f"Granted channel {channel_id} access to user/bot {bot_user_id}. "
                "Run squad(action='sync_member_channels', ...) on members if scope empty."
            )

        supported = ", ".join(self.channel_manage_actions())
        return False, f"Unknown action '{action}'. Supported: {supported}"

    def create_send_tool(self, agent_id: str | None = None) -> DiscordSendTool:
        return DiscordSendTool(self, agent_id)

    def create_setup_tool(self, agent_id: str | None = None) -> DiscordSetupTool:
        return DiscordSetupTool(self, agent_id)

    def create_manage_tool(self, agent_id: str | None = None) -> DiscordManageTool:
        return DiscordManageTool(self, agent_id)

    def _active_runtime_agent_ids(self) -> set[str]:
        try:
            from server.main import app

            am = getattr(app.state, "agent_manager", None)
            if am is None:
                return set(self._agent_configs.keys())
            return {
                aid for aid in self._agent_configs
                if am.get_runtime(aid) is not None
            }
        except Exception:
            return set(self._agent_configs.keys())

    async def _retire_agent_gateway(self, agent_id: str) -> None:
        await self._unregister_gateway_relay(agent_id)
        self._connected_agents.discard(agent_id)
        cfg = self._agent_configs.get(agent_id)
        if not cfg:
            return
        if cfg.get("gateway_relay_registered"):
            updated = dict(cfg)
            updated["gateway_relay_registered"] = False
            self._agent_configs[agent_id] = updated
            self._ctx.save_config(updated, agent_id=agent_id)

    async def _retire_duplicate_token_agents(self, agent_id: str, token: str) -> None:
        for other_id, cfg in list(self._agent_configs.items()):
            if other_id == agent_id:
                continue
            other_token = str(cfg.get("bot_token") or "").strip()
            if other_token and other_token == token:
                logger.info(
                    "Discord [%s]: retiring stale gateway for duplicate bot token",
                    other_id,
                )
                await self._retire_agent_gateway(other_id)

    async def startup(self) -> None:
        active_ids = self._active_runtime_agent_ids()
        seen_tokens: dict[str, str] = {}
        for agent_id, cfg in list(self._agent_configs.items()):
            if active_ids and agent_id not in active_ids:
                logger.info(
                    "Discord [%s]: skipping startup — agent not loaded in runtime",
                    agent_id,
                )
                continue
            token = str(cfg.get("bot_token") or "").strip()
            if not cfg.get("enabled") or not token or "masked" in token:
                continue
            prev = seen_tokens.get(token)
            if prev and prev != agent_id:
                await self._retire_agent_gateway(prev)
            seen_tokens[token] = agent_id
            await self._startup_agent(agent_id)

    async def _startup_agent(self, agent_id: str) -> None:
        cfg = self._agent_cfg(agent_id)
        token = str(cfg.get("bot_token") or "").strip()
        if not token or "masked" in token:
            return
        await self._retire_duplicate_token_agents(agent_id, token)
        try:
            me = await self._api_get(token, "/users/@me")
            self._bot_ids[agent_id] = str(me.get("id", ""))
            self._bot_usernames[agent_id] = str(me.get("username", ""))
            self._connected_agents.add(agent_id)
        except Exception as exc:
            logger.error("Discord [%s] startup failed: %s", agent_id, exc)
            return
        register_with_agent(agent_id, self)
        relay_url = get_relay_base_url(cfg)
        if relay_url:
            if await self.register_gateway_relay(relay_url, agent_id):
                await ensure_relay(self._relay_clients, agent_id, relay_url)
            else:
                self.start_local_gateway(agent_id)
        else:
            self.start_local_gateway(agent_id)
        await self.sync_channels_from_platform(agent_id)

    async def shutdown(self) -> None:
        for agent_id in list(self._connected_agents):
            await self._unregister_gateway_relay(agent_id)
        for agent_id, task in list(self._gateway_tasks.items()):
            task.cancel()
        self._gateway_tasks.clear()
        for agent_id, relay in list(self._relay_clients.items()):
            await relay.disconnect()
        self._relay_clients.clear()
        self._connected_agents.clear()

    async def _unregister_gateway_relay(self, agent_id: str) -> None:
        cfg = self._agent_cfg(agent_id)
        relay_url = get_relay_base_url(cfg)
        if not relay_url:
            return
        url = f"{relay_url.rstrip('/')}/api/channels/discord/unregister/{agent_id}"
        secret = get_runtime_secret()
        headers: dict[str, str] = {}
        if secret:
            headers["x-runtime-secret"] = secret
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, headers=headers)
        except Exception as exc:
            logger.warning("Discord [%s] NestJS gateway unregister failed: %s", agent_id, exc)
        self.stop_local_gateway(agent_id)

    async def _api_get(self, token: str, path: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bot {token}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{DISCORD_API}{path}", headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def register_gateway_relay(self, relay_base_url: str, agent_id: str) -> bool:
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token:
            return False
        url = f"{relay_base_url.rstrip('/')}/api/channels/discord/register/{agent_id}"
        secret = get_runtime_secret()
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["x-runtime-secret"] = secret
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, headers=headers, json={"bot_token": token})
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("Discord [%s] NestJS gateway register failed: %s", agent_id, exc)
            return False
        if not data.get("ok") or not data.get("ready"):
            logger.warning(
                "Discord [%s] NestJS gateway not ready (ok=%s ready=%s)",
                agent_id, data.get("ok"), data.get("ready"),
            )
            return False
        self.update_config({"gateway_relay_registered": True}, agent_id=agent_id)
        self.stop_local_gateway(agent_id)
        await ensure_relay(self._relay_clients, agent_id, relay_base_url)
        logger.info("Discord [%s]: NestJS Gateway registered and ready", agent_id)
        return True

    def start_local_gateway(self, agent_id: str) -> None:
        task = self._gateway_tasks.get(agent_id)
        if task is not None and not task.done():
            return
        self._gateway_tasks[agent_id] = asyncio.create_task(
            self._local_gateway_loop(agent_id),
            name=f"discord-gateway-{agent_id}",
        )

    def stop_local_gateway(self, agent_id: str) -> None:
        task = self._gateway_tasks.pop(agent_id, None)
        if task is not None:
            task.cancel()

    async def _local_gateway_loop(self, agent_id: str) -> None:
        """Local discord.py Gateway when NestJS relay is unavailable."""
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token:
            return
        try:
            import discord
        except ImportError:
            logger.error("discord.py not installed — local Gateway unavailable")
            return

        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        parent = self

        class _Client(discord.Client):
            async def on_ready(self) -> None:
                logger.info(
                    "Discord local gateway [%s] ready as %s",
                    agent_id, self.user,
                )

            async def on_message(self, message: discord.Message) -> None:
                if message.author.bot:
                    return
                mention_ids = [str(m.id) for m in message.mentions]
                payload = {
                    "t": "MESSAGE_CREATE",
                    "d": {
                        "id": str(message.id),
                        "channel_id": str(message.channel.id),
                        "guild_id": str(message.guild.id) if message.guild else None,
                        "content": message.content or "",
                        "author": {
                            "id": str(message.author.id),
                            "username": message.author.name,
                            "bot": message.author.bot,
                        },
                        "mentions": [{"id": mid} for mid in mention_ids],
                        "mention_everyone": message.mention_everyone,
                    },
                }
                await parent._handle_gateway_payload(agent_id, payload)

        client = _Client(intents=intents)
        try:
            await client.start(token)
        except asyncio.CancelledError:
            await client.close()
        except Exception as exc:
            logger.error("Discord local gateway [%s]: %s", agent_id, exc)

    async def _handle_gateway_payload(self, agent_id: str, payload: dict[str, Any]) -> None:
        if payload.get("t") != "MESSAGE_CREATE":
            return
        message = payload.get("d") or {}
        normalized = self.normalize_gateway_message(message, agent_id)
        if normalized is None:
            return
        if not self.should_respond(message, agent_id=agent_id):
            reason = self.explain_policy_block(message, agent_id=agent_id) or "policy blocked"
            logger.info("Discord [%s]: policy skip — %s", agent_id, reason)
            try:
                from server.main import app
                broadcast_channel_policy_skip(app, agent_id, normalized, reason=reason)
            except Exception:
                pass
            return
        await self._process_inbound(agent_id, normalized, message)

    async def ensure_bot_identity(self, agent_id: str) -> str | None:
        cfg = self._agent_cfg(agent_id)
        token = str(cfg.get("bot_token") or "").strip()
        if not token or "masked" in token:
            self._last_sync_error[agent_id] = "Bot token missing — run Setup in Chat or save a valid token."
            return None
        bot_id = self._bot_ids.get(agent_id) or str(cfg.get("bot_id") or "")
        if bot_id:
            return bot_id
        try:
            me = await self._api_get(token, "/users/@me")
            bot_id = str(me.get("id", ""))
            username = str(me.get("username", ""))
            if not bot_id:
                return None
            self._bot_ids[agent_id] = bot_id
            self._bot_usernames[agent_id] = username
            self.update_config(
                {"bot_id": bot_id, "bot_username": username},
                agent_id=agent_id,
            )
            return bot_id
        except Exception as exc:
            self._last_sync_error[agent_id] = f"Discord API auth failed: {exc}"
            return None

    async def fetch_observed_channels(self, agent_id: str) -> list[dict[str, Any]]:
        cfg = self._agent_cfg(agent_id)
        token = str(cfg.get("bot_token") or "").strip()
        if not token or "masked" in token:
            self._last_sync_error[agent_id] = "Bot token missing or invalid."
            return []
        bot_id = await self.ensure_bot_identity(agent_id)
        if not bot_id:
            return []
        headers = {"Authorization": f"Bot {token}"}
        observed: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                guilds_resp = await client.get(f"{DISCORD_API}/users/@me/guilds", headers=headers)
                guilds_resp.raise_for_status()
                guilds = guilds_resp.json()
                if not guilds:
                    self._last_sync_error[agent_id] = (
                        "Bot is not in any Discord servers yet — invite it, then sync again."
                    )
                    return []
                for guild in guilds:
                    gid = str(guild.get("id", ""))
                    gname = str(guild.get("name", gid))
                    ch_resp = await client.get(
                        f"{DISCORD_API}/guilds/{gid}/channels", headers=headers,
                    )
                    if ch_resp.status_code == 403:
                        errors.append(f"No channel list access in {gname} (403)")
                        continue
                    ch_resp.raise_for_status()
                    guild_perms = int(guild.get("permissions") or 0)
                    platform_access = bool(guild_perms & _PERM_VIEW)
                    for ch in ch_resp.json():
                        if ch.get("type") not in (0, 5, 15):
                            continue
                        cid = str(ch.get("id", ""))
                        observed.append({
                            "id": cid,
                            "name": ch.get("name", cid),
                            "guild_id": gid,
                            "guild_name": gname,
                            "platform_access": platform_access,
                        })
        except Exception as exc:
            logger.warning("Discord [%s] fetch channels failed: %s", agent_id, exc)
            self._last_sync_error[agent_id] = str(exc)
            return []
        if observed:
            self._last_sync_error.pop(agent_id, None)
        elif errors:
            self._last_sync_error[agent_id] = "; ".join(errors)
        else:
            self._last_sync_error[agent_id] = "No text channels found in servers the bot can see."
        return observed

    async def fetch_guild_roles(self, agent_id: str) -> list[dict[str, Any]]:
        cfg = self._agent_cfg(agent_id)
        token = str(cfg.get("bot_token") or "").strip()
        if not token or "masked" in token:
            return []
        if not await self.ensure_bot_identity(agent_id):
            return []
        headers = {"Authorization": f"Bot {token}"}
        out: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                guilds_resp = await client.get(f"{DISCORD_API}/users/@me/guilds", headers=headers)
                guilds_resp.raise_for_status()
                for guild in guilds_resp.json():
                    gid = str(guild.get("id", ""))
                    gname = str(guild.get("name", gid))
                    roles_resp = await client.get(
                        f"{DISCORD_API}/guilds/{gid}/roles", headers=headers,
                    )
                    if roles_resp.status_code == 403:
                        continue
                    roles_resp.raise_for_status()
                    roles = []
                    for role in roles_resp.json():
                        rid = str(role.get("id", ""))
                        if not rid or role.get("name") == "@everyone":
                            continue
                        roles.append({
                            "id": rid,
                            "name": role.get("name", rid),
                            "color": role.get("color", 0),
                            "managed": bool(role.get("managed", False)),
                        })
                    roles.sort(key=lambda r: r["name"].lower())
                    out.append({"guild_id": gid, "guild_name": gname, "roles": roles})
        except Exception as exc:
            logger.warning("Discord [%s] fetch roles failed: %s", agent_id, exc)
        return out

    async def sync_channels_from_platform(
        self,
        agent_id: str,
        *,
        auto_enable: bool = False,
    ) -> dict[str, Any]:
        cfg = self._agent_cfg(agent_id)
        observed = await self.fetch_observed_channels(agent_id)
        updated = reconcile_config(
            cfg, observed, auto_enable_on_platform_access=auto_enable,
        )
        self._agent_configs[agent_id] = updated
        self._ctx.save_config(updated, agent_id=agent_id)
        return updated

    async def apply_channel_desired(
        self,
        agent_id: str,
        channel_id: str,
        *,
        enabled: bool,
        require_mention: bool | None = None,
    ) -> dict[str, Any]:
        cfg = self._agent_cfg(agent_id)
        scoped = apply_desired_channel(
            cfg, channel_id, enabled=enabled, require_mention=require_mention,
        )
        updated = finalize_scoped_config(cfg, scoped)
        perm_warning = ""
        if enabled:
            perm_warning = await self._push_discord_channel_access(agent_id, channel_id, grant=True)
        else:
            perm_warning = await self._push_discord_channel_access(agent_id, channel_id, grant=False)
        self._agent_configs[agent_id] = updated
        self._ctx.save_config(updated, agent_id=agent_id)
        if perm_warning:
            updated = dict(updated)
            updated["_permission_warning"] = perm_warning
        return updated

    async def apply_channels_bulk(
        self,
        agent_id: str,
        selections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Persist many channel desired states in one write (Tools UI save)."""
        from nls.skills.channel_scope import apply_channels_bulk_config

        cfg = self._agent_cfg(agent_id)
        updated = apply_channels_bulk_config(cfg, selections)
        self._agent_configs[agent_id] = updated
        self._ctx.save_config(updated, agent_id=agent_id)
        return updated

    async def _push_discord_channel_access(
        self,
        agent_id: str,
        channel_id: str,
        *,
        grant: bool,
    ) -> str:
        """Push channel permission overwrite for this agent's bot."""
        cfg = self._agent_cfg(agent_id)
        bot_id = self._bot_ids.get(agent_id, cfg.get("bot_id", ""))
        if not bot_id:
            return ""
        ok, msg = await self.grant_channel_member_access(
            agent_id, channel_id, str(bot_id), grant=grant,
        )
        return "" if ok else msg

    async def grant_channel_member_access(
        self,
        agent_id: str,
        channel_id: str,
        target_user_id: str,
        *,
        grant: bool = True,
    ) -> tuple[bool, str]:
        """Grant or revoke channel access for a user/bot id (Manage Channels on caller)."""
        cfg = self._agent_cfg(agent_id)
        token = str(cfg.get("bot_token") or "").strip()
        target_user_id = str(target_user_id or "").strip()
        channel_id = str(channel_id or "").strip()
        if not token:
            return False, "No bot_token configured on this agent"
        if not channel_id or not target_user_id:
            return False, "channel_id and target_user_id are required"
        headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
        overwrite = {
            "type": 1,
            "allow": str(_BOT_PERMS if grant else 0),
            "deny": str(_PERM_VIEW if not grant else 0),
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.put(
                    f"{DISCORD_API}/channels/{channel_id}/permissions/{target_user_id}",
                    headers=headers,
                    json=overwrite,
                )
                if resp.status_code in (200, 204):
                    return True, ""
                logger.warning(
                    "Discord grant access [%s] ch=%s target=%s status=%s",
                    agent_id, channel_id, target_user_id, resp.status_code,
                )
                body = (resp.text or "")[:500]
                from nls.runtime.discord_squad_playbook import grant_access_error_message

                return False, grant_access_error_message(resp.status_code, body)
        except Exception as exc:
            logger.warning("Discord grant access failed: %s", exc)
            return False, str(exc)

    def _squad_peer_bot_ids(self, agent_id: str | None) -> set[str]:
        """Discord bot snowflakes for other agents in the same squad."""
        if not agent_id:
            return set()
        try:
            from server.main import app

            sm = getattr(app.state, "squad_manager", None)
            if sm is None:
                return set()
            squad = sm.get_squad_for_agent(agent_id)
            if squad is None:
                return set()
            peers: set[str] = set()
            for peer_id in squad.all_member_ids:
                if peer_id == agent_id:
                    continue
                peer_cfg = self._agent_configs.get(peer_id) or {}
                bid = str(
                    peer_cfg.get("bot_id")
                    or self._bot_ids.get(peer_id, "")
                ).strip()
                if bid:
                    peers.add(bid)
            return peers
        except Exception:
            return set()

    def _bot_inbound_allowed(
        self,
        message: dict[str, Any],
        agent_id: str | None,
    ) -> bool:
        """Allow squad-mate bot messages (@mention or mention-free scoped channel)."""
        author = message.get("author") or {}
        if not author.get("bot"):
            return True

        receiver = agent_id or ""
        cfg = self._agent_cfg(receiver)
        bot_id = str(self._bot_ids.get(receiver, cfg.get("bot_id", "")))
        sender_id = str(author.get("id", ""))
        if not sender_id or sender_id == bot_id:
            return False

        if sender_id not in self._squad_peer_bot_ids(receiver):
            return False

        mention_ids = {str(m.get("id", "")) for m in message.get("mentions", [])}
        if bot_id and bot_id in mention_ids:
            return True

        cid = str(message.get("channel_id") or "")
        if not cid or message.get("guild_id") is None:
            return False

        scoped = scoped_channels_from_config(cfg)
        entry = (scoped.get("channels") or {}).get(cid)
        if isinstance(entry, dict) and entry.get("effective_enabled"):
            return not bool(entry.get("require_mention", True))
        return False

    def normalize_gateway_message(
        self,
        message: dict[str, Any],
        agent_id: str | None,
    ) -> dict[str, Any] | None:
        content = (message.get("content") or "").strip()
        channel_id = str(message.get("channel_id") or "")
        guild_id = message.get("guild_id")
        author = message.get("author") or {}
        if author.get("bot") and not self._bot_inbound_allowed(message, agent_id):
            return None
        if not channel_id:
            return None
        sender_id = str(author.get("id", ""))
        sender_name = str(author.get("username") or author.get("global_name") or "?")
        is_dm = guild_id is None
        if is_dm:
            session_key = f"discord:dm:{sender_id}"
        else:
            session_key = f"discord:channel:{channel_id}"
        bot_id = self._bot_ids.get(agent_id or "", "")
        mention_ids = {str(m.get("id", "")) for m in message.get("mentions", [])}
        is_mention = bot_id in mention_ids or message.get("mention_everyone")
        return {
            "channel": "discord",
            "session_key": session_key,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "content": content,
            "is_group": not is_dm,
            "group_id": str(guild_id) if guild_id else None,
            "is_mention": is_mention,
            "is_dm": is_dm,
            "metadata": {
                "channel_id": channel_id,
                "message_id": str(message.get("id", "")),
                "guild_id": guild_id,
            },
        }

    def normalize(self, payload: dict[str, Any], agent_id: str | None = None) -> dict[str, Any] | None:
        if payload.get("t") == "MESSAGE_CREATE":
            return self.normalize_gateway_message(payload.get("d") or {}, agent_id)
        if "content" in payload and payload.get("author"):
            return self.normalize_gateway_message(payload, agent_id)
        return None

    def explain_policy_block(
        self,
        message: dict[str, Any],
        agent_id: str | None = None,
    ) -> str | None:
        """Return skip reason when should_respond is False."""
        if self.should_respond(message, agent_id=agent_id):
            return None
        cfg = self._agent_cfg(agent_id)
        cid = str(message.get("channel_id") or "")
        if message.get("guild_id") is None:
            return "DM policy blocked this sender"
        if cid not in effective_channel_ids(cfg):
            return (
                f"#{_channel_display_name(cfg, cid)} not listening — "
                "enable in Tools → Discord → Channel scope"
            )
        return "mention required or sender not allowed"

    def should_respond(self, message: dict[str, Any], agent_id: str | None = None) -> bool:
        from nls.runtime.channels import PolicyEnforcer

        cfg = self._agent_cfg(agent_id)
        enforcer = PolicyEnforcer(cfg)
        author = message.get("author") or {}
        sender_id = str(author.get("id", ""))
        sender_username = str(author.get("username", ""))
        guild_id = message.get("guild_id")
        channel_id = str(message.get("channel_id") or "")

        if guild_id is None:
            return enforcer.check_dm(sender_id, sender_username=sender_username)

        effective = effective_channel_ids(cfg)
        if not effective or channel_id not in effective:
            return False

        bot_id = self._bot_ids.get(agent_id or "", cfg.get("bot_id", ""))
        mention_ids = {str(m.get("id", "")) for m in message.get("mentions", [])}
        is_mention = bool(bot_id and bot_id in mention_ids)
        if not is_mention:
            is_mention = enforcer.check_mention(message.get("content") or "")
        if not is_mention and guild_id:
            mod_roles = {str(r) for r in cfg.get("moderator_role_ids", []) if r}
            if mod_roles:
                member = message.get("member") or {}
                member_roles = {str(r) for r in member.get("roles", [])}
                if member_roles & mod_roles:
                    is_mention = True
        return enforcer.check_group(channel_id, sender_id, is_mention=is_mention)

    def register_known_sender(self, sender_id: str, agent_id: str) -> None:
        sid = str(sender_id)
        senders = self._known_senders.setdefault(agent_id, set())
        if sid not in senders:
            senders.add(sid)
            self._save_known_senders()

    def _outbound_restricted(self, agent_id: str | None) -> bool:
        if not agent_id:
            return False
        cfg = self._agent_configs.get(agent_id, {})
        return bool(cfg.get("enabled"))

    def get_allowed_target_ids(self, agent_id: str | None) -> set[str]:
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
        allowed.update(self._known_senders.get(agent_id, set()))
        allowed.update(effective_channel_ids(cfg))
        try:
            from server.main import app
            am = getattr(app.state, "agent_manager", None)
            if am is not None:
                path = am.agents_dir / agent_id / "contacts.json"
                if path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    items = data if isinstance(data, list) else data.get("contacts", [])
                    for c in items:
                        did = str(c.get("discord_id") or "").strip()
                        if did:
                            allowed.add(did)
        except Exception:
            pass
        return allowed

    def _known_senders_path(self) -> Path:
        return self._ctx._skills_dir / "discord-channel" / "known_senders.json"

    def _load_known_senders(self) -> None:
        path = self._known_senders_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for aid, ids in raw.items():
                self._known_senders[aid] = set(ids)
        except Exception:
            pass

    def _save_known_senders(self) -> None:
        path = self._known_senders_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({aid: sorted(ids) for aid, ids in self._known_senders.items()}, indent=2),
            encoding="utf-8",
        )

    async def _process_inbound(
        self,
        agent_id: str,
        normalized: dict[str, Any],
        raw_message: dict[str, Any],
    ) -> None:
        try:
            from server.main import app
        except ImportError:
            return
        agent_manager = getattr(app.state, "agent_manager", None)
        if agent_manager is None:
            return
        runtime = agent_manager.get_runtime(agent_id)
        if runtime is None:
            return

        self.register_known_sender(normalized["sender_id"], agent_id)
        session_key = normalized["session_key"]
        text = normalized.get("content", "")
        channel_id = normalized["metadata"]["channel_id"]
        sender_name = normalized["sender_name"]

        from nls.skills.surface_send import channel_session_metadata

        session_meta = channel_session_metadata(normalized)
        history = runtime.load_session_history(session_key)
        runtime.save_session_history(
            history + [{"role": "user", "content": text or "[empty]"}],
            session_key=session_key,
            metadata=session_meta,
        )
        broadcast_channel_event(app, agent_id, "discord", normalized, direction="inbound")

        from nls.skills.channel_processing import (
            process_channel_message,
            try_feed_pending_answer,
        )

        if try_feed_pending_answer(agent_id, session_key, text):
            return

        user_input = f"[{sender_name} via Discord]: {text}" if text else f"[{sender_name} via Discord]:"
        response_text = await process_channel_message(
            app, runtime, agent_id, user_input, history,
            channel_adapter=self,
            reply_target=channel_id,
            session_key=session_key,
        )
        clean = strip_signal_tags(response_text) if response_text else ""
        if not clean and response_text:
            clean = response_text.strip()
        if clean:
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": clean})
            runtime.save_session_history(
                history, session_key=session_key,
                metadata=session_meta,
            )
            await self.send(channel_id, clean, agent_id=agent_id)
            broadcast_channel_event(
                app, agent_id, "discord", normalized, clean, direction="response",
            )

    def list_groups(self, agent_id: str) -> list[dict[str, Any]]:
        cfg = self._agent_cfg(agent_id)
        return [
            {"id": c.get("id"), "name": c.get("name", c.get("id"))}
            for c in list_scoped_channels(cfg)
            if c.get("effective_enabled")
        ]

    def get_known_senders(self, agent_id: str) -> dict[str, str]:
        cfg = self._agent_cfg(agent_id)
        known = self._known_senders.get(agent_id, set())
        return {sid: sid for sid in sorted(known)}
