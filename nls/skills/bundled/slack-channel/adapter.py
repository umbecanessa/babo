"""Slack channel adapter — Events API via NestJS relay, scoped channels."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
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
    reconcile_config,
)
from nls.tools.agent_tools.base import AgentTool, ToolResult

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api"
MAX_MESSAGE_LENGTH = 4000


class SlackSendTool:
    def __init__(self, adapter: SlackAdapter, agent_id: str | None = None) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "slack_send"

    @property
    def description(self) -> str:
        team = self._adapter._team_names.get(self._agent_id or "", "")
        base = (
            "Send a Slack message. Use contacts for slack_id. "
            "Provide channel_id (C…) or user id (U…) for DMs. "
            "Use file_path or file_paths to upload workspace files."
        )
        if team:
            base += f" Workspace: {team}."
        return base

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Slack channel or user ID"},
                "text": {"type": "string", "description": "Message text (mrkdwn supported)"},
                "thread_ts": {"type": "string", "description": "Thread timestamp to reply in"},
                "file_path": {"type": "string", "description": "Workspace file to upload"},
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Multiple workspace files to upload separately",
                },
            },
            "required": ["channel_id"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        channel_id = str(params.get("channel_id") or "").strip()
        text = str(params.get("text") or "")
        thread_ts = str(params.get("thread_ts") or "").strip()
        file_path = str(params.get("file_path") or "").strip()
        file_paths = params.get("file_paths", []) or []
        if not channel_id:
            return ToolResult(content="Error: channel_id required", is_error=True)
        if not text and not file_path and not file_paths:
            return ToolResult(content="Error: text, file_path, or file_paths required", is_error=True)

        allowed = self._adapter.get_allowed_target_ids(self._agent_id)
        if self._adapter._outbound_restricted(self._agent_id):
            if channel_id not in allowed:
                return ToolResult(
                    content=(
                        f"Cannot send to {channel_id} — not allowed. "
                        "Save contact with slack_id, enable the channel in Tools, "
                        "or wait for them to message first."
                    ),
                    is_error=True,
                )
        elif allowed and channel_id not in allowed:
            return ToolResult(
                content=f"Cannot send to {channel_id} — not allowed.",
                is_error=True,
            )
        if file_paths:
            paths = list(file_paths)
            if file_path and file_path not in paths:
                paths.insert(0, file_path)
            for fp in paths:
                result = await self._adapter.upload_file(
                    channel_id, fp, initial_comment="", agent_id=self._agent_id,
                )
                if result.is_error:
                    return result
            if text:
                ok = await self._adapter.send(
                    channel_id, text, agent_id=self._agent_id,
                    thread_ts=thread_ts or None,
                )
                if not ok:
                    return ToolResult(content="Failed to send message after files", is_error=True)
            return ToolResult(content=f"Sent {len(paths)} file(s) to {channel_id}")

        if file_path:
            return await self._adapter.upload_file(
                channel_id, file_path, initial_comment=text, agent_id=self._agent_id,
            )
        ok = await self._adapter.send(
            channel_id, text, agent_id=self._agent_id,
            thread_ts=thread_ts or None,
        )
        if ok:
            return ToolResult(content=f"Message sent to {channel_id}")
        return ToolResult(content="Failed to send", is_error=True)


class SlackSetupTool:
    def __init__(self, adapter: SlackAdapter, agent_id: str | None = None) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "slack_setup"

    @property
    def description(self) -> str:
        return "Validate Slack bot token and signing secret, then connect the workspace."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "bot_token": {"type": "string"},
                "signing_secret": {"type": "string"},
            },
            "required": ["bot_token", "signing_secret"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        token = str(params.get("bot_token") or "").strip()
        secret = str(params.get("signing_secret") or "").strip()
        agent_id = self._agent_id or ""
        if not token or not secret:
            return ToolResult(content="bot_token and signing_secret required", is_error=True)
        if not agent_id:
            return ToolResult(content="No agent context", is_error=True)

        try:
            auth = await self._adapter._api_post(token, "auth.test", {})
        except Exception as exc:
            return ToolResult(content=f"Invalid Slack token: {exc}", is_error=True)

        team_id = str(auth.get("team_id", ""))
        team_name = str(auth.get("team", ""))
        bot_id = str(auth.get("user_id", ""))
        self._adapter.update_config(
            {
                "bot_token": token,
                "signing_secret": secret,
                "enabled": True,
                "team_id": team_id,
                "bot_id": bot_id,
            },
            agent_id=agent_id,
        )
        self._adapter._team_ids[agent_id] = team_id
        self._adapter._team_names[agent_id] = team_name
        self._adapter._bot_ids[agent_id] = bot_id
        self._adapter._connected_agents.add(agent_id)
        register_with_agent(agent_id, self._adapter)

        relay_url = get_relay_base_url(self._adapter._agent_cfg(agent_id))
        relay_note = ""
        if relay_url:
            webhook_url = f"{relay_url}/api/channels/webhook/slack/{agent_id}"
            self._adapter.update_config(
                {"events_request_url": webhook_url},
                agent_id=agent_id,
            )
            await ensure_relay(self._adapter._relay_clients, agent_id, relay_url)
            await self._adapter.register_signing_secret_relay(relay_url, agent_id)
            relay_note = f" Set Slack Event Subscriptions Request URL to: {webhook_url}"

        await self._adapter.sync_channels_from_platform(agent_id)
        return ToolResult(
            content=(
                f"Slack connected to {team_name}.{relay_note} "
                "Call skill_configure(skill_name='slack-channel') for owner, policy, channels."
            ),
        )


class SlackAdapter:
    channel_name: str = "slack"

    def __init__(self, global_config: dict[str, Any], ctx: Any) -> None:
        self._global_config = global_config
        self._ctx = ctx
        self._agent_configs: dict[str, dict[str, Any]] = {}
        self._team_ids: dict[str, str] = {}
        self._team_names: dict[str, str] = {}
        self._bot_ids: dict[str, str] = {}
        self._connected_agents: set[str] = set()
        self._known_senders: dict[str, set[str]] = {}
        self._relay_clients: dict[str, Any] = {}
        self._load_all_agent_configs()
        self._load_known_senders()

    def _load_all_agent_configs(self) -> None:
        for agent_id, cfg in self._ctx.load_all_agent_configs().items():
            self._agent_configs[agent_id] = cfg

    def _agent_cfg(self, agent_id: str | None) -> dict[str, Any]:
        if agent_id:
            from nls.runtime.channel_agent_config import merge_global_and_agent_channel_config

            merged = merge_global_and_agent_channel_config(
                self._global_config,
                self._agent_configs.get(agent_id, {}),
            )
            if not merged.get("groups"):
                merged["groups"] = compile_groups_policy(merged)
            return merged
        return self._global_config

    @property
    def name(self) -> str:
        return "slack"

    def update_config(self, new_config: dict[str, Any], agent_id: str) -> None:
        merged = dict(self._agent_configs.get(agent_id, {}))
        merged.update(new_config)
        merged = reconcile_config(merged) if "scoped_channels" in merged else merged
        if "groups" not in merged or new_config.get("scoped_channels"):
            merged["groups"] = compile_groups_policy(merged)
        self._agent_configs[agent_id] = merged
        self._ctx.save_config(merged, agent_id=agent_id)

    async def _api_post(self, token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(f"{SLACK_API}/{method}", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("error", "slack_api_error"))
        return data

    async def send(
        self,
        target: str,
        message: str,
        **kwargs: Any,
    ) -> bool:
        agent_id = kwargs.pop("agent_id", None)
        thread_ts = kwargs.pop("thread_ts", None)
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token:
            return False
        for chunk in chunk_message(message, MAX_MESSAGE_LENGTH):
            payload: dict[str, Any] = {"channel": target, "text": chunk}
            if thread_ts:
                payload["thread_ts"] = thread_ts
            try:
                await self._api_post(token, "chat.postMessage", payload)
            except Exception as exc:
                logger.error("Slack send failed: %s", exc)
                return False
        return True

    async def upload_file(
        self,
        channel: str,
        file_path: str,
        *,
        initial_comment: str = "",
        agent_id: str | None = None,
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
        file_bytes = resolved.read_bytes()
        try:
            upload = await self._api_post(token, "files.getUploadURLExternal", {
                "filename": resolved.name,
                "length": len(file_bytes),
            })
            upload_url = upload.get("upload_url", "")
            file_id = upload.get("file_id", "")
            if not upload_url or not file_id:
                return ToolResult(content="Slack upload URL missing", is_error=True)
            async with httpx.AsyncClient(timeout=60.0) as client:
                put_resp = await client.post(
                    upload_url,
                    content=file_bytes,
                    headers={"Content-Type": "application/octet-stream"},
                )
                put_resp.raise_for_status()
            complete_payload: dict[str, Any] = {
                "files": [{"id": file_id, "title": resolved.name}],
                "channel_id": channel,
            }
            if initial_comment:
                complete_payload["initial_comment"] = initial_comment
            await self._api_post(token, "files.completeUploadExternal", complete_payload)
            return ToolResult(content=f"Uploaded {resolved.name} to {channel}")
        except Exception as exc:
            return ToolResult(content=f"Upload failed: {exc}", is_error=True)

    async def is_connected(self, agent_id: str | None = None) -> bool:
        if agent_id:
            return agent_id in self._connected_agents
        return bool(self._connected_agents)

    def get_status(self, agent_id: str | None = None) -> dict[str, Any]:
        cfg = self._agent_cfg(agent_id)
        connected = False
        if agent_id:
            connected = agent_id in self._connected_agents
            if not connected:
                try:
                    from nls.runtime.channel_agent_config import agent_channel_is_configured

                    data_root = self._ctx._skills_dir.parent
                    connected = agent_channel_is_configured(data_root, agent_id, "slack")
                except Exception:
                    connected = False
        else:
            connected = bool(self._connected_agents)
        scoped = list_scoped_channels(cfg)
        effective = [c for c in scoped if c.get("effective_enabled")]
        return {
            "channel": "slack",
            "connected": connected,
            "team_name": self._team_names.get(agent_id or "", ""),
            "enabled": cfg.get("enabled", False),
            "events_request_url": cfg.get("events_request_url", ""),
            "scoped_channel_count": len(scoped),
            "active_channel_count": len(effective),
            "channels": scoped,
        }

    def channel_manage_actions(self) -> list[str]:
        return ["sync", "list", "enable"]

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
            return True, format_scoped_channel_status("Slack", status)

        if act == "enable":
            channel_id = str(params.get("channel_id") or "").strip()
            if not channel_id:
                return False, "Error: channel_id required"
            enabled = params.get("enabled")
            if enabled is None:
                enabled = True
            require_mention = params.get("require_mention")
            rm = bool(require_mention) if require_mention is not None else None
            await self.apply_channel_desired(
                agent_id, channel_id, enabled=bool(enabled), require_mention=rm,
            )
            return True, (
                f"Slack channel {channel_id} enabled={enabled} "
                f"(bot joins/leaves via conversations API)."
            )

        supported = ", ".join(self.channel_manage_actions())
        return False, f"Unknown action '{action}'. Supported: {supported}"

    def create_send_tool(self, agent_id: str | None = None) -> SlackSendTool:
        return SlackSendTool(self, agent_id)

    def create_setup_tool(self, agent_id: str | None = None) -> SlackSetupTool:
        return SlackSetupTool(self, agent_id)

    async def startup(self) -> None:
        for agent_id, cfg in list(self._agent_configs.items()):
            if cfg.get("enabled") and cfg.get("bot_token"):
                await self._startup_agent(agent_id)

    async def _startup_agent(self, agent_id: str) -> None:
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token:
            return
        try:
            auth = await self._api_post(token, "auth.test", {})
            self._team_ids[agent_id] = str(auth.get("team_id", ""))
            self._team_names[agent_id] = str(auth.get("team", ""))
            self._bot_ids[agent_id] = str(auth.get("user_id", ""))
            self._connected_agents.add(agent_id)
        except Exception as exc:
            logger.error("Slack [%s] startup failed: %s", agent_id, exc)
            return
        register_with_agent(agent_id, self)
        relay_url = get_relay_base_url(cfg)
        if relay_url:
            await ensure_relay(self._relay_clients, agent_id, relay_url)
            await self.register_signing_secret_relay(relay_url, agent_id)
        await self.sync_channels_from_platform(agent_id)

    async def shutdown(self) -> None:
        for agent_id in list(self._connected_agents):
            await self._unregister_signing_secret_relay(agent_id)
        for relay in self._relay_clients.values():
            await relay.disconnect()
        self._relay_clients.clear()
        self._connected_agents.clear()

    async def register_signing_secret_relay(self, relay_base_url: str, agent_id: str) -> bool:
        cfg = self._agent_cfg(agent_id)
        secret = cfg.get("signing_secret", "")
        if not secret:
            return False
        url = f"{relay_base_url.rstrip('/')}/api/channels/slack/register/{agent_id}"
        headers = {"Content-Type": "application/json"}
        runtime_secret = get_runtime_secret()
        if runtime_secret:
            headers["x-runtime-secret"] = runtime_secret
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url, headers=headers, json={"signing_secret": secret},
                )
                resp.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("Slack [%s] NestJS signing secret register failed: %s", agent_id, exc)
            return False

    async def _unregister_signing_secret_relay(self, agent_id: str) -> None:
        cfg = self._agent_cfg(agent_id)
        relay_url = get_relay_base_url(cfg)
        if not relay_url:
            return
        url = f"{relay_url.rstrip('/')}/api/channels/slack/unregister/{agent_id}"
        headers: dict[str, str] = {}
        runtime_secret = get_runtime_secret()
        if runtime_secret:
            headers["x-runtime-secret"] = runtime_secret
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, headers=headers)
        except Exception as exc:
            logger.warning("Slack [%s] NestJS signing secret unregister failed: %s", agent_id, exc)

    def verify_signature(
        self,
        signing_secret: str,
        timestamp: str,
        body: bytes,
        signature: str,
    ) -> bool:
        if abs(time.time() - int(timestamp or "0")) > 60 * 5:
            return False
        base = f"v0:{timestamp}:{body.decode('utf-8')}"
        digest = hmac.new(
            signing_secret.encode("utf-8"),
            base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        expected = f"v0={digest}"
        return hmac.compare_digest(expected, signature or "")

    async def fetch_observed_channels(self, agent_id: str) -> list[dict[str, Any]]:
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        team_id = self._team_ids.get(agent_id, cfg.get("team_id", ""))
        if not token:
            return []
        observed: list[dict[str, Any]] = []
        cursor = ""
        try:
            while True:
                payload: dict[str, Any] = {
                    "types": "public_channel,private_channel",
                    "exclude_archived": True,
                    "limit": 200,
                }
                if cursor:
                    payload["cursor"] = cursor
                data = await self._api_post(token, "conversations.list", payload)
                for ch in data.get("channels", []):
                    if not ch.get("is_member"):
                        continue
                    observed.append({
                        "id": str(ch.get("id", "")),
                        "name": ch.get("name", ch.get("id", "")),
                        "guild_id": team_id,
                        "guild_name": self._team_names.get(agent_id, team_id),
                        "platform_access": True,
                    })
                cursor = (data.get("response_metadata") or {}).get("next_cursor", "")
                if not cursor:
                    break
        except Exception as exc:
            logger.warning("Slack [%s] list channels failed: %s", agent_id, exc)
        return observed

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
        if enabled:
            await self._join_channel(agent_id, channel_id)
        else:
            await self._leave_channel(agent_id, channel_id)
        self._agent_configs[agent_id] = updated
        self._ctx.save_config(updated, agent_id=agent_id)
        return updated

    async def apply_channels_bulk(
        self,
        agent_id: str,
        selections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from nls.skills.channel_scope import apply_channels_bulk_config

        cfg = self._agent_cfg(agent_id)
        updated = apply_channels_bulk_config(cfg, selections)
        for sel in selections:
            cid = str(sel.get("id") or "")
            if not cid:
                continue
            if sel.get("enabled"):
                await self._join_channel(agent_id, cid)
            else:
                await self._leave_channel(agent_id, cid)
        self._agent_configs[agent_id] = updated
        self._ctx.save_config(updated, agent_id=agent_id)
        return updated

    async def _join_channel(self, agent_id: str, channel_id: str) -> None:
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token:
            return
        try:
            await self._api_post(token, "conversations.join", {"channel": channel_id})
        except Exception as exc:
            logger.warning("Slack join %s failed: %s", channel_id, exc)

    async def _leave_channel(self, agent_id: str, channel_id: str) -> None:
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token:
            return
        try:
            await self._api_post(token, "conversations.leave", {"channel": channel_id})
        except Exception as exc:
            logger.warning("Slack leave %s failed: %s", channel_id, exc)

    def normalize_event(self, event: dict[str, Any], agent_id: str | None) -> dict[str, Any] | None:
        ev_type = event.get("type", "")
        if ev_type not in ("app_mention", "message"):
            return None
        if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
            return None
        if event.get("bot_id"):
            return None
        text = (event.get("text") or "").strip()
        files = event.get("files") or []
        if not text and not files:
            return None
        channel_id = str(event.get("channel") or "")
        user_id = str(event.get("user") or "")
        if not channel_id or not user_id:
            return None
        channel_type = event.get("channel_type", "")
        is_dm = channel_type == "im" or channel_id.startswith("D")
        is_mention = ev_type == "app_mention"
        if is_dm:
            session_key = f"slack:dm:{user_id}"
        else:
            session_key = f"slack:channel:{channel_id}"
        return {
            "channel": "slack",
            "session_key": session_key,
            "sender_id": user_id,
            "sender_name": user_id,
            "content": text,
            "is_group": not is_dm,
            "group_id": channel_id if not is_dm else None,
            "is_mention": is_mention or is_dm,
            "is_dm": is_dm,
            "message_id": str(event.get("ts") or ""),
            "metadata": {
                "channel_id": channel_id,
                "thread_ts": event.get("thread_ts") or event.get("ts", ""),
                "ts": event.get("ts", ""),
            },
            "attachments": [],
        }

    async def download_inbound_attachments(
        self,
        event: dict[str, Any],
        agent_id: str | None,
    ) -> list[dict[str, Any]]:
        """Download Slack message files into workspace/uploads."""
        if not agent_id:
            return []
        cfg = self._agent_cfg(agent_id)
        token = cfg.get("bot_token", "")
        if not token:
            return []

        from nls.skills.channel_attachments import (
            MAX_INBOUND_ATTACHMENTS,
            download_url_to_uploads,
        )

        headers = {"Authorization": f"Bearer {token}"}
        saved: list[dict[str, Any]] = []
        files = list(event.get("files") or [])
        if len(files) > MAX_INBOUND_ATTACHMENTS:
            logger.warning(
                "Slack [%s]: truncating inbound files %d -> %d",
                agent_id, len(files), MAX_INBOUND_ATTACHMENTS,
            )
            files = files[:MAX_INBOUND_ATTACHMENTS]

        for f in files:
            url, meta = await self._resolve_slack_file(token, f)
            if not url:
                continue
            filename = meta.get("name") or f"slack_{meta.get('id', 'file')}"
            mime = meta.get("mimetype") or "application/octet-stream"
            filetype = str(meta.get("filetype") or "")
            is_voice = filetype in ("mp3", "m4a", "wav", "ogg") or mime.startswith("audio/")
            record = await download_url_to_uploads(
                agent_id,
                url,
                filename=filename,
                mime_type=mime,
                headers=headers,
                is_voice=is_voice,
            )
            if record:
                saved.append(record)
        if files and len(saved) < len(files):
            logger.warning(
                "Slack [%s]: saved %d/%d inbound file(s)",
                agent_id, len(saved), len(files),
            )
        return saved

    async def _resolve_slack_file(
        self,
        token: str,
        file_obj: dict[str, Any],
    ) -> tuple[str | None, dict[str, Any]]:
        merged = dict(file_obj)
        url = merged.get("url_private_download") or merged.get("url_private")
        if url:
            return url, merged
        file_id = merged.get("id")
        if not file_id:
            return None, merged
        try:
            data = await self._api_post(token, "files.info", {"file": file_id})
            merged.update(data.get("file") or {})
            url = merged.get("url_private_download") or merged.get("url_private")
            return url, merged
        except Exception:
            logger.warning("Slack files.info failed for %s", file_id, exc_info=True)
            return None, merged

    def normalize(self, payload: dict[str, Any], agent_id: str | None = None) -> dict[str, Any] | None:
        if payload.get("type") == "url_verification":
            return None
        event = payload.get("event") or payload
        return self.normalize_event(event, agent_id)

    def should_respond(self, event: dict[str, Any], agent_id: str | None = None) -> bool:
        from nls.runtime.channels import PolicyEnforcer

        cfg = self._agent_cfg(agent_id)
        enforcer = PolicyEnforcer(cfg)
        user_id = str(event.get("user") or "")
        channel_id = str(event.get("channel") or "")
        channel_type = event.get("channel_type", "")
        is_dm = channel_type == "im" or channel_id.startswith("D")

        if is_dm:
            return enforcer.check_dm(user_id)

        effective = effective_channel_ids(cfg)
        if not effective or channel_id not in effective:
            return False

        is_mention = event.get("type") == "app_mention"
        if not is_mention:
            is_mention = enforcer.check_mention(event.get("text") or "")
        return enforcer.check_group(channel_id, user_id, is_mention=is_mention)

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
        for key in ("owner_identity",):
            val = cfg.get(key, "")
            if val:
                allowed.add(str(val))
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
                        sid = str(c.get("slack_id") or "").strip()
                        if sid:
                            allowed.add(sid)
        except Exception:
            pass
        return allowed

    def _known_senders_path(self) -> Path:
        return self._ctx._skills_dir / "slack-channel" / "known_senders.json"

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

    def list_groups(self, agent_id: str) -> list[dict[str, Any]]:
        cfg = self._agent_cfg(agent_id)
        return [
            {"id": c.get("id"), "name": c.get("name")}
            for c in list_scoped_channels(cfg)
            if c.get("effective_enabled")
        ]

    def get_known_senders(self, agent_id: str) -> dict[str, str]:
        return {sid: sid for sid in sorted(self._known_senders.get(agent_id, set()))}

    async def handle_member_event(self, agent_id: str, event: dict[str, Any]) -> None:
        if event.get("type") in ("member_joined_channel", "member_left_channel"):
            await self.sync_channels_from_platform(agent_id, auto_enable=True)
