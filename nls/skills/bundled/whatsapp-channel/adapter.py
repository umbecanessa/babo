"""WhatsApp channel adapter -- send/receive via Baileys Node.js bridge."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from nls.tools.agent_tools.base import AgentTool, ToolResult
from nls.agentic.outbound_notify import FINAL_SUMMARY_SCHEMA_PROPERTY

logger = logging.getLogger(__name__)


class WhatsAppSendTool:
    """Agent tool for sending WhatsApp messages via the Baileys bridge.

    Each agent gets its own tool instance with ``agent_id`` so the
    adapter resolves the correct per-agent bridge URL.
    """

    def __init__(self, _adapter: WhatsAppAdapter, agent_id: str | None = None) -> None:
        self._adapter = _adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "whatsapp_send"

    @property
    def description(self) -> str:
        return (
            "Send a WhatsApp message (text, file, or both). "
            "Use the contacts tool to look up phone numbers first. "
            "Provide phone (E.164 format) or group_id. "
            "To share a document/report/image, use file_path with the "
            "workspace path — do NOT paste URLs or file contents into text."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": (
                        "Recipient phone number in E.164 format. "
                        "Required for DMs, omit when using group_id."
                    ),
                },
                "group_id": {
                    "type": "string",
                    "description": (
                        "WhatsApp group ID (JID) for sending to a group "
                        "(e.g. '120363012345678901@g.us'). "
                        "Use this instead of phone for group messages."
                    ),
                },
                "text": {
                    "type": "string",
                    "description": (
                        "Message text to send. When attaching a file, "
                        "this becomes the caption."
                    ),
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "Workspace file path to attach as a document "
                        "(e.g. 'PROJECT_REPORT.md', 'data/results.csv'). "
                        "The file is sent as a WhatsApp document attachment. "
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
                "final_summary": FINAL_SUMMARY_SCHEMA_PROPERTY,
            },
            "required": [],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        phone = params.get("phone") or params.get("to") or params.get("number", "")
        group_id = params.get("group_id", "")
        text = params.get("text") or params.get("message") or params.get("body", "")
        file_path = params.get("file_path", "")
        file_paths = params.get("file_paths", []) or []

        if not phone and not group_id:
            return ToolResult(content="Error: phone or group_id is required", is_error=True)
        if not text and not file_path and not file_paths:
            return ToolResult(
                content="Error: 'text' parameter is required (you may have used 'message' — the correct parameter name is 'text')",
                is_error=True,
            )

        if group_id:
            target = group_id if "@" in group_id else f"{group_id}@g.us"
        else:
            # Normalise phone: strip formatting, then validate length.
            # The model occasionally hallucinates repeated digit sequences
            # (e.g. +3936630663032357 instead of +393663032357 — KL #403).
            # E.164 numbers are 7-15 digits; anything longer is a hallucination.
            _digits_only = phone.lstrip("+").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
            if "@" in _digits_only:
                _digits_only = _digits_only.split("@")[0]
            if len(_digits_only) > 15:
                return ToolResult(
                    content=(
                        f"Phone number '{phone}' has {len(_digits_only)} digits — "
                        f"E.164 numbers are max 15 digits. This looks like a "
                        f"hallucinated/duplicated number. Check your contacts and "
                        f"retry with the correct number."
                    ),
                    is_error=True,
                )
            if len(_digits_only) < 7:
                return ToolResult(
                    content=f"Phone number '{phone}' is too short to be valid.",
                    is_error=True,
                )
            target = phone
            allowed = self._adapter.get_allowed_phones(self._agent_id)
            normalized = _digits_only
            if allowed and normalized not in allowed:
                known_list = ", ".join(sorted(allowed)[:3])
                logger.warning(
                    "WhatsApp send BLOCKED: agent=%s tried to message %s "
                    "(allowed: %s)",
                    self._agent_id, phone, allowed,
                )
                return ToolResult(
                    content=(
                        f"Cannot send to {phone} — that number has not "
                        f"messaged you yet. To initiate contact, first save "
                        f"the person with contacts(action='add', name='...', "
                        f"phone='{phone}'), then retry whatsapp_send. "
                        f"Known contacts: {known_list}"
                    ),
                    is_error=True,
                )

        if file_paths:
            for fp in file_paths:
                await self._adapter.send_file(
                    target, fp, caption="", agent_id=self._agent_id,
                )
            if text:
                await self._adapter.send(target, text, agent_id=self._agent_id)
            return ToolResult(content=f"Sent {len(file_paths)} file(s) to {target}")

        if file_path:
            return await self._adapter.send_file(
                target, file_path,
                caption=text,
                agent_id=self._agent_id,
            )

        ok = await self._adapter.send(target, text, agent_id=self._agent_id)
        dest = group_id or phone
        if ok:
            return ToolResult(content=f"WhatsApp message sent to {dest}")
        return ToolResult(content="Failed to send WhatsApp message", is_error=True)


class WhatsAppAdapter:
    """WhatsApp adapter that communicates with the Baileys Node.js bridge.

    Supports multi-agent operation: each agent gets its own config
    stored at ``data/skills/whatsapp-channel/agents/{agent_id}.json``.
    """

    channel_name: str = "whatsapp"

    def __init__(self, global_config: dict[str, Any], ctx: Any) -> None:
        self._global_config = global_config
        self._ctx = ctx
        self._agent_configs: dict[str, dict[str, Any]] = {}
        self._connected_agents: set[str] = set()
        self._phone_agent_map: dict[str, str] = {}
        self._known_senders: dict[str, dict[str, str]] = {}
        self._load_all_agent_configs()
        self._load_phone_map()
        self._load_known_senders()

    def _load_all_agent_configs(self) -> None:
        for agent_id, cfg in self._ctx.load_all_agent_configs().items():
            self._agent_configs[agent_id] = cfg

    # ── Phone ↔ Agent mapping ─────────────────────────────────

    def _phone_map_path(self) -> Path:
        return self._ctx._skills_dir / "whatsapp-channel" / "phone_agent_map.json"

    def _load_phone_map(self) -> None:
        """Load persisted phone→agent mapping, plus rebuild from agent configs."""
        path = self._phone_map_path()
        if path.exists():
            try:
                self._phone_agent_map = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self._phone_agent_map = {}
        for agent_id, cfg in self._agent_configs.items():
            phone = cfg.get("linked_phone", "")
            if phone and cfg.get("enabled"):
                self._phone_agent_map[phone] = agent_id

    def _save_phone_map(self) -> None:
        path = self._phone_map_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._phone_agent_map, indent=2), encoding="utf-8")

    def register_phone(self, phone: str, agent_id: str) -> None:
        """Record that *phone* belongs to *agent_id* and persist."""
        if not phone:
            return
        self._phone_agent_map[phone] = agent_id
        self._save_phone_map()
        logger.info("Phone mapping registered: %s → %s", phone, agent_id)

    # ── Known senders persistence ────────────────────────────

    def _known_senders_path(self) -> Path:
        return self._ctx._skills_dir / "whatsapp-channel" / "known_senders.json"

    def _load_known_senders(self) -> None:
        path = self._known_senders_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for agent_id, phones in raw.items():
                if isinstance(phones, dict):
                    self._known_senders[agent_id] = dict(phones)
                elif isinstance(phones, list):
                    # Migrate legacy format: list of phones -> dict with empty names
                    self._known_senders[agent_id] = {p: "" for p in phones}
                else:
                    self._known_senders[agent_id] = {}
        except Exception:
            pass

    def _save_known_senders(self) -> None:
        path = self._known_senders_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._known_senders, indent=2), encoding="utf-8",
        )

    def register_known_sender(
        self, phone: str, agent_id: str, name: str = "",
    ) -> None:
        """Track a phone that has sent a message to this agent.

        This allows the agent to reply to anyone who has contacted it,
        regardless of policy mode.  Persisted across restarts.
        """
        norm = phone.lstrip("+").replace("-", "").replace(" ", "")
        if "@" in norm:
            norm = norm.split("@")[0]
        senders = self._known_senders.setdefault(agent_id, {})
        existing_name = senders.get(norm, None)
        if existing_name is None or (name and not existing_name):
            senders[norm] = name
            self._save_known_senders()

    def get_allowed_phones(self, agent_id: str | None) -> set[str]:
        """Return the set of normalized phone numbers this agent may message.

        Includes:
        - Owner's linked_phone
        - Phones in the allow_from config list
        - Any phone that has sent a message this session (known senders)
        - Phones in the phone_agent_map for this agent
        - Phones explicitly saved in the agent's personal contacts store
          (owner-authorised first-contact: if the owner told the agent to
          save someone's number, the agent is allowed to initiate contact)

        Returns empty set only if no data at all (backwards compat: allow all).
        """
        allowed: set[str] = set()
        if not agent_id:
            return allowed

        cfg = self._agent_configs.get(agent_id, {})

        linked = cfg.get("linked_phone", "")
        if linked:
            allowed.add(linked.lstrip("+").replace("-", "").replace(" ", ""))

        owner = cfg.get("owner_identity", "")
        if owner:
            allowed.add(owner.lstrip("+").replace("-", "").replace(" ", ""))

        for entry in cfg.get("allow_from", []):
            if entry:
                allowed.add(
                    str(entry).lstrip("+").replace("-", "").replace(" ", ""),
                )

        for phone, aid in self._phone_agent_map.items():
            if aid == agent_id:
                allowed.add(phone.lstrip("+").replace("-", "").replace(" ", ""))

        known = self._known_senders.get(agent_id, {})
        allowed.update(known.keys())

        # Personal contacts store (owner-authorised first-contact).
        # Any number saved via contacts(action='add') is considered
        # explicitly approved by the owner for outbound messaging.
        try:
            from server.main import app as _app  # noqa: PLC0415
            _am = getattr(_app.state, "agent_manager", None)
            if _am is not None:
                _contacts_path = _am.agents_dir / agent_id / "contacts.json"
                if _contacts_path.exists():
                    _cdata = json.loads(_contacts_path.read_text(encoding="utf-8"))
                    for _c in _cdata.get("contacts", []):
                        for _ph in _c.get("phones", []):
                            _norm = str(_ph).lstrip("+").replace("-", "").replace(" ", "")
                            if _norm:
                                allowed.add(_norm)
        except Exception:
            pass

        return allowed

    def get_known_senders(self, agent_id: str) -> dict[str, str]:
        """Return {phone: name} for all known senders of this agent."""
        return dict(self._known_senders.get(agent_id, {}))

    def resolve_agent_for_bridge(self) -> str | None:
        """Return the agent_id that owns the current bridge connection.

        Looks at enabled agents with a linked_phone, falling back to the
        phone_agent_map.  Returns None if no mapping exists.
        """
        for agent_id, cfg in self._agent_configs.items():
            if cfg.get("enabled") and cfg.get("linked_phone"):
                return agent_id
        if self._phone_agent_map:
            return next(iter(self._phone_agent_map.values()), None)
        return None

    def _agent_cfg(self, agent_id: str | None) -> dict[str, Any]:
        if agent_id:
            merged = dict(self._global_config)
            merged.update(self._agent_configs.get(agent_id, {}))
            return merged
        return self._global_config

    def _bridge_url(self, agent_id: str | None = None) -> str:
        return self._agent_cfg(agent_id).get("bridge_url", "http://localhost:9223")

    @property
    def name(self) -> str:
        return "whatsapp"

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
                logger.warning("WhatsApp: path traversal blocked: %s", file_path)
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
        """Send a file from the agent's workspace via the Baileys bridge."""
        if not agent_id:
            return ToolResult(content="No agent_id for file send", is_error=True)

        resolved = self._resolve_workspace_file(agent_id, file_path)
        if resolved is None:
            return ToolResult(
                content=f"File not found in workspace: {file_path}",
                is_error=True,
            )

        phone = target.lstrip("+").replace("-", "").replace(" ", "")
        jid = phone if "@" in phone else f"{phone}@s.whatsapp.net"
        mime = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        data_b64 = base64.b64encode(resolved.read_bytes()).decode()

        try:
            bridge = self._bridge_url(agent_id)
            media_url = f"{bridge}/send-media/{agent_id}" if agent_id else f"{bridge}/send-media"
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    media_url,
                    json={
                        "jid": jid,
                        "file": data_b64,
                        "filename": resolved.name,
                        "mime_type": mime,
                        "caption": caption or resolved.name,
                    },
                )
                resp.raise_for_status()
                return ToolResult(
                    content=f"File '{resolved.name}' sent to {target} via WhatsApp",
                )
        except Exception as exc:
            logger.error("WhatsApp send_file failed: %s", exc)
            return ToolResult(content=f"Failed to send file: {exc}", is_error=True)

    # -- ChannelAdapter protocol -------------------------------------------

    async def send(
        self,
        target: str,
        message: str,
        **kwargs: Any,
    ) -> bool:
        agent_id = kwargs.pop("agent_id", None)
        phone = target.lstrip("+").replace("-", "").replace(" ", "")
        if "@" in phone:
            jid = phone
        else:
            jid = f"{phone}@s.whatsapp.net"

        try:
            bridge = self._bridge_url(agent_id)
            send_url = f"{bridge}/send/{agent_id}" if agent_id else f"{bridge}/send"
            logger.info("WhatsApp send: jid=%s, bridge=%s, len=%d", jid, bridge, len(message))
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    send_url,
                    json={"jid": jid, "text": message},
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info("WhatsApp send OK: jid=%s, bridge_response=%s", jid, data)
                return True
        except Exception as exc:
            logger.error("WhatsApp send failed: jid=%s, error=%s", jid, exc)
            return False

    async def is_connected(self, agent_id: str | None = None) -> bool:
        if agent_id:
            return agent_id in self._connected_agents
        return bool(self._connected_agents)

    def get_config(self, agent_id: str | None = None) -> dict[str, Any]:
        return dict(self._agent_cfg(agent_id))

    def get_status(self, agent_id: str | None = None) -> dict[str, Any]:
        cfg = self._agent_cfg(agent_id)
        connected = agent_id in self._connected_agents if agent_id else bool(self._connected_agents)
        return {
            "channel": "whatsapp",
            "connected": connected,
            "enabled": cfg.get("enabled", False),
            "mode": "baileys",
            "linked_phone": cfg.get("linked_phone", ""),
        }

    # -- per-agent config --------------------------------------------------

    def update_config(self, new_config: dict[str, Any], agent_id: str) -> None:
        self._agent_configs.setdefault(agent_id, {}).update(new_config)
        self._ctx.save_config(self._agent_configs[agent_id], agent_id=agent_id)

    # -- lifecycle ---------------------------------------------------------

    async def startup(self) -> None:
        """Start all previously-activated agents."""
        for agent_id, cfg in list(self._agent_configs.items()):
            if cfg.get("enabled"):
                await self._startup_agent(agent_id)

        if not self._connected_agents:
            logger.info("WhatsApp channel: no agents with WhatsApp enabled")

    async def startup_agent(self, agent_id: str) -> None:
        await self._startup_agent(agent_id)

    async def _startup_agent(self, agent_id: str) -> None:
        cfg = self._agent_cfg(agent_id)
        if not cfg.get("enabled", False):
            logger.info("WhatsApp channel [%s]: disabled", agent_id)
            return

        bridge = self._bridge_url(agent_id)
        max_retries = 6
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f"{bridge}/configure",
                        json={"agent_id": agent_id},
                    )

                    resp = await client.get(f"{bridge}/status/{agent_id}")
                    resp.raise_for_status()
                    data = resp.json()
                    if data.get("connected"):
                        self._connected_agents.add(agent_id)
                        linked = data.get("phone", "")
                        if linked:
                            self._agent_configs.setdefault(agent_id, {})["linked_phone"] = linked
                            self.register_phone(linked, agent_id)
                            self.update_config({"enabled": True, "linked_phone": linked}, agent_id=agent_id)
                        logger.info("WhatsApp channel [%s]: connected via Baileys (phone=%s)", agent_id, linked)
                        self._register_with_agent(agent_id)
                    else:
                        logger.info("WhatsApp channel [%s]: bridge running but not paired", agent_id)
                    return
            except Exception as exc:
                if attempt < max_retries - 1:
                    wait = 2 * (attempt + 1)
                    logger.info("WhatsApp channel [%s]: bridge not ready, retrying in %ds (%s)", agent_id, wait, exc)
                    await asyncio.sleep(wait)
                else:
                    logger.info("WhatsApp channel [%s]: bridge not reachable after %d attempts (%s)", agent_id, max_retries, exc)

    async def shutdown(self) -> None:
        self._connected_agents.clear()

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
                    cr.register("whatsapp", self)
        except Exception:
            pass

    # -- tool factory ------------------------------------------------------

    def create_send_tool(self, agent_id: str | None = None) -> WhatsAppSendTool:
        return WhatsAppSendTool(_adapter=self, agent_id=agent_id)

    # -- policy enforcement ------------------------------------------------

    def should_respond(self, phone: str, is_group: bool = False, agent_id: str | None = None) -> bool:
        from nls.runtime.channels import PolicyEnforcer

        cfg = self._agent_cfg(agent_id)
        enforcer = PolicyEnforcer(cfg)
        if is_group:
            return enforcer.check_group(group_id="*", sender_id=phone)
        return enforcer.check_dm(phone)

    # -- message normalization ---------------------------------------------

    def normalize(
        self, msg: dict[str, Any], agent_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Normalize an inbound message from the Baileys bridge.

        Handles text and/or media payloads.  When media is present the
        file is saved to ``workspace/uploads/`` and an ``attachments``
        list is populated so the channel processing pipeline can augment
        the user input.
        """
        phone_jid = msg.get("from", "")
        text = msg.get("text", "")
        media = msg.get("media")

        if not phone_jid or (not text and not media):
            return None

        phone = phone_jid.split("@")[0]
        profile_name = msg.get("name", phone)
        is_group = msg.get("isGroup", False)
        group_id = msg.get("groupId")

        if is_group and group_id:
            session_key = f"whatsapp:group:{group_id}"
        else:
            session_key = f"whatsapp:dm:{phone}"

        attachments: list[dict[str, Any]] = []
        if media and agent_id:
            att = self._save_media(agent_id, media)
            if att:
                attachments.append(att)

        return {
            "channel": "whatsapp",
            "session_key": session_key,
            "sender_id": phone,
            "sender_jid": phone_jid,
            "sender_name": profile_name,
            "content": text or "",
            "is_group": is_group,
            "group_id": group_id,
            "is_mention": True,
            "is_forwarded": msg.get("isForwarded", False),
            "is_reply_to_bot": False,
            "message_id": msg.get("messageId", ""),
            "attachments": attachments,
            "metadata": {
                "phone": phone,
                "timestamp": msg.get("timestamp", ""),
                "raw": msg,
            },
        }

    def _save_media(
        self, agent_id: str, media: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Decode base64 media and save to agent workspace/uploads/."""
        try:
            from server.main import app
            am = getattr(app.state, "agent_manager", None)
            if am is None:
                return None

            data_b64 = media.get("data", "")
            if not data_b64:
                return None

            raw = base64.b64decode(data_b64)
            import time as _time
            filename = media.get("filename", f"media_{int(_time.time())}")
            mime = media.get("mime_type", "application/octet-stream")
            is_voice = media.get("is_voice", False)

            uploads = am.agents_dir / agent_id / "workspace" / "uploads"
            uploads.mkdir(parents=True, exist_ok=True)
            dest = uploads / filename
            dest.write_bytes(raw)

            rel_path = f"uploads/{filename}"
            return {
                "name": filename,
                "path": rel_path,
                "mime_type": mime,
                "size": len(raw),
                "is_voice": is_voice,
            }
        except Exception:
            logger.warning("WhatsApp: failed to save media", exc_info=True)
            return None
