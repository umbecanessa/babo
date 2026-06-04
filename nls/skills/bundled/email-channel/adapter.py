"""Email channel adapter -- send/receive via NestJS backend (Resend proxy).

Sending goes through the NestJS API at NESTJS_API_URL so the Resend API key
never leaves the server.  The Python runtime authenticates with the shared
Runtime shared secret.

Inbound polling uses the SDK's ``SkillPoller`` primitive so the scheduler
manages the lifecycle instead of a hand-rolled asyncio task.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

import httpx

from nls.tools.agent_tools.base import AgentTool, ToolResult
from nls.agentic.outbound_notify import FINAL_SUMMARY_SCHEMA_PROPERTY

logger = logging.getLogger(__name__)

_NEWSLETTER_HEADERS = {"list-unsubscribe", "list-id", "x-mailer", "x-campaign"}

POLL_INTERVAL_SECONDS = 30


def _nestjs_url() -> str:
    base = (
        os.environ.get("NESTJS_API_URL")
        or os.environ.get("NESTJS_URL")
        or os.environ.get("API_URL")
        or "http://localhost:3000"
    )
    return f"{base.rstrip('/')}/api" if not base.endswith("/api") else base


def _runtime_secret() -> str:
    return os.environ.get("RUNTIME_SHARED_SECRET", "") or os.environ.get("NLS_SHARED_SECRET", "")


class EmailSendTool:
    """Agent tool for sending emails (proxied through NestJS -> Resend).

    Each agent gets its own tool instance with an ``agent_id`` so that
    ``send()`` resolves the correct per-agent config (alias / from_address).
    """

    def __init__(self, _adapter: EmailAdapter, agent_id: str | None = None) -> None:
        self._adapter = _adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "email_send"

    @property
    def description(self) -> str:
        cfg = self._adapter._agent_configs.get(self._agent_id, {}) if self._agent_id else {}
        from_addr = cfg.get("from_address", "") or cfg.get("alias", "")
        base = (
            "Compose and SEND an outgoing email to any recipient. "
            "Use contacts(action='owner') to find the user's personal email address. "
            "Do NOT use this tool to read or check emails — use gmail_search for that. "
            "Does not require prior email history. "
            "Provide 'to', 'subject', and 'body'. "
            "To CC multiple people use comma-separated addresses in 'cc'. "
            "To attach a file, use file_path with the workspace path."
        )
        if from_addr:
            base += f" This agent sends from {from_addr}."
        return base

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line",
                },
                "body": {
                    "type": "string",
                    "description": "Email body (plain text)",
                },
                "cc": {
                    "type": "string",
                    "description": "CC recipients (comma-separated email addresses)",
                },
                "bcc": {
                    "type": "string",
                    "description": "BCC recipients (comma-separated email addresses)",
                },
                "in_reply_to": {
                    "type": "string",
                    "description": "Message-ID to reply to (for threading)",
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "Workspace file path to attach to the email "
                        "(e.g. 'PROJECT_REPORT.md', 'data/results.csv'). "
                        "Do NOT paste file contents into the body — "
                        "use this parameter instead."
                    ),
                },
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Multiple workspace file paths to attach "
                        "(e.g. ['report.pdf', 'data.csv'])"
                    ),
                },
                "final_summary": FINAL_SUMMARY_SCHEMA_PROPERTY,
            },
            "required": ["to", "subject", "body"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        to = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        cc = params.get("cc", "")
        bcc = params.get("bcc", "")
        in_reply_to = params.get("in_reply_to", "")
        file_path = params.get("file_path", "")
        file_paths = params.get("file_paths", []) or []

        if not to or not body:
            return ToolResult(
                content="Error: 'to' and 'body' are required",
                is_error=True,
            )

        allowed = self._adapter.get_allowed_recipients(self._agent_id)
        if allowed and to.lower() not in allowed:
            known_list = ", ".join(sorted(allowed)[:3])
            logger.warning(
                "Email send BLOCKED: agent=%s tried to email %s "
                "(allowed: %s)", self._agent_id, to, allowed,
            )
            return ToolResult(
                content=(
                    f"Cannot send to {to} — that address has not emailed "
                    f"you. You can only reply to people who have contacted "
                    f"you. Known contacts: {known_list}"
                ),
                is_error=True,
            )

        all_paths = ([file_path] if file_path else []) + file_paths
        attachments_payload: list[dict[str, str]] | None = None
        if all_paths and self._agent_id:
            attachments_payload = []
            for fp in all_paths:
                resolved = self._adapter._resolve_workspace_file(self._agent_id, fp)
                if resolved is None:
                    return ToolResult(
                        content=f"File not found in workspace: {fp}",
                        is_error=True,
                    )
                mime = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
                attachments_payload.append({
                    "filename": resolved.name,
                    "content": base64.b64encode(resolved.read_bytes()).decode(),
                    "content_type": mime,
                })

        ok = await self._adapter.send(
            to, body, subject=subject, in_reply_to=in_reply_to,
            agent_id=self._agent_id,
            attachments=attachments_payload,
            cc=cc, bcc=bcc,
        )
        if ok:
            att_count = len(all_paths)
            extra = f" (with {att_count} attachment(s))" if att_count else ""
            # Record to the email ledger
            try:
                from nls.tools.agent_tools.email_ledger import get_email_ledger
                if self._agent_id:
                    _ledger = get_email_ledger(self._agent_id)
                    if _ledger:
                        cfg = self._adapter._agent_cfg(self._agent_id)
                        from_addr = cfg.get("from_address", "") or cfg.get("alias", "")
                        _ledger.record_sent(
                            from_addr=from_addr,
                            to=to, subject=subject, body=body,
                            cc=cc, bcc=bcc, in_reply_to=in_reply_to,
                            status="ok",
                        )
            except Exception as _le:
                logger.debug("email_ledger record_sent failed: %s", _le)
            return ToolResult(content=f"Email sent to {to}{extra}")
        # Record failure too
        try:
            from nls.tools.agent_tools.email_ledger import get_email_ledger
            if self._agent_id:
                _ledger = get_email_ledger(self._agent_id)
                if _ledger:
                    cfg = self._adapter._agent_cfg(self._agent_id)
                    from_addr = cfg.get("from_address", "") or cfg.get("alias", "")
                    _ledger.record_sent(
                        from_addr=from_addr,
                        to=to, subject=subject, body=body,
                        cc=cc, bcc=bcc,
                        status="failed",
                    )
        except Exception as _le:
            logger.debug("email_ledger record_sent(failed) failed: %s", _le)
        return ToolResult(content="Failed to send email", is_error=True)


class EmailAdapter:
    """Email adapter that proxies send operations through NestJS.

    Supports multi-agent operation: each agent gets its own config
    (alias, from_address, enabled) stored at
    ``data/skills/email-channel/agents/{agent_id}.json``.
    """

    channel_name: str = "email"

    def __init__(self, global_config: dict[str, Any], ctx: Any) -> None:
        self._global_config = global_config
        self._ctx = ctx
        self._agent_configs: dict[str, dict[str, Any]] = {}
        self._connected_agents: set[str] = set()
        self._known_senders: dict[str, set[str]] = {}
        self._drain_registered = False
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
        return "email"

    # -- ChannelAdapter protocol -------------------------------------------

    async def send(
        self,
        target: str,
        message: str,
        **kwargs: Any,
    ) -> bool:
        agent_id = kwargs.pop("agent_id", None)
        attachments = kwargs.pop("attachments", None)
        cfg = self._agent_cfg(agent_id)
        from_addr = cfg.get("from_address", "") or cfg.get("alias", "")
        if not from_addr:
            logger.warning("Email: no from_address/alias configured (agent=%s)", agent_id)
            return False

        subject = kwargs.get("subject", "Message from your agent")
        in_reply_to = kwargs.get("in_reply_to", "")
        references = kwargs.get("references", "")
        cc = kwargs.get("cc", "")
        bcc = kwargs.get("bcc", "")

        payload: dict[str, Any] = {
            "from": from_addr,
            "to": target,
            "subject": subject,
            "text": message,
        }
        # Transactional email APIs expect arrays of addresses, not a
        # comma-separated string.  Split and strip each entry.
        if cc:
            cc_list = [a.strip() for a in cc.split(",") if a.strip()]
            payload["cc"] = cc_list if len(cc_list) > 1 else cc_list[0] if cc_list else cc
        if bcc:
            bcc_list = [a.strip() for a in bcc.split(",") if a.strip()]
            payload["bcc"] = bcc_list if len(bcc_list) > 1 else bcc_list[0] if bcc_list else bcc
        if in_reply_to:
            payload["in_reply_to"] = in_reply_to
            if references:
                payload["references"] = f"{references} {in_reply_to}".strip()
            else:
                payload["references"] = in_reply_to

        alias = cfg.get("alias", "")
        if alias:
            payload["reply_to"] = alias

        if attachments:
            payload["attachments"] = attachments

        try:
            url = f"{_nestjs_url()}/channels/email/send"
            headers: dict[str, str] = {"Content-Type": "application/json"}
            secret = _runtime_secret()
            if secret:
                headers["X-Runtime-Secret"] = secret

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.error("Email send via NestJS failed: %s", exc)
            return False

    async def is_connected(self, agent_id: str | None = None) -> bool:
        if agent_id:
            return agent_id in self._connected_agents
        return bool(self._connected_agents)

    def get_config(self, agent_id: str | None = None) -> dict[str, Any]:
        return dict(self._agent_cfg(agent_id))

    def get_status(self, agent_id: str | None = None) -> dict[str, Any]:
        cfg = self._agent_cfg(agent_id)
        return {
            "channel": "email",
            "connected": agent_id in self._connected_agents if agent_id else bool(self._connected_agents),
            "enabled": cfg.get("enabled", False),
            "alias": cfg.get("alias", ""),
            "from_address": cfg.get("from_address", "") or cfg.get("alias", ""),
        }

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
                logger.warning("Email: path traversal blocked: %s", file_path)
                return None
            if not resolved.is_file():
                return None
            return resolved
        except Exception:
            return None

    # -- known-sender tracking (outbound guard) ----------------------------

    def _known_senders_path(self) -> Path:
        return self._ctx._skills_dir / "email-channel" / "known_senders.json"

    def _load_known_senders(self) -> None:
        path = self._known_senders_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for agent_id, addrs in raw.items():
                self._known_senders[agent_id] = set(addrs)
        except Exception:
            pass

    def _save_known_senders(self) -> None:
        path = self._known_senders_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {
            aid: sorted(addrs) for aid, addrs in self._known_senders.items()
        }
        path.write_text(
            json.dumps(serializable, indent=2), encoding="utf-8",
        )

    def register_known_sender(self, email: str, agent_id: str) -> None:
        """Track an email that has sent a message to this agent.

        Persisted across restarts.
        """
        addr = email.lower()
        senders = self._known_senders.setdefault(agent_id, set())
        if addr not in senders:
            senders.add(addr)
            self._save_known_senders()

    def get_allowed_recipients(self, agent_id: str | None) -> set[str]:
        """Return email addresses this agent is allowed to message.

        Includes owner_identity, allow_from entries, and any sender that
        has contacted the agent this session.
        """
        allowed: set[str] = set()
        if not agent_id:
            return allowed

        cfg = self._agent_configs.get(agent_id, {})

        owner = cfg.get("owner_identity", [])
        if isinstance(owner, str):
            owner = [owner]
        for addr in owner:
            if addr:
                allowed.add(addr.lower())

        for entry in cfg.get("allow_from", []):
            if entry:
                allowed.add(str(entry).lower())

        known = self._known_senders.get(agent_id, set())
        allowed.update(known)
        return allowed

    # -- policy enforcement ------------------------------------------------

    def should_respond(
        self,
        sender_email: str,
        agent_id: str | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> bool:
        from nls.runtime.interaction_policy import check_email_inbound_policy

        cfg = self._agent_cfg(agent_id)
        hdrs = headers or {}
        agent_addrs: set[str] = set()
        for key in ("alias", "from_address", "connected_email"):
            val = str(cfg.get(key, "")).strip()
            if val:
                agent_addrs.add(val.lower())
        return check_email_inbound_policy(cfg, sender_email, hdrs, agent_addrs)

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
            logger.info("Email channel: no agents with email enabled")

    async def startup_agent(self, agent_id: str) -> None:
        """Start (or restart) email for a single agent."""
        await self._startup_agent(agent_id)

    async def _startup_agent(self, agent_id: str) -> None:
        cfg = self._agent_cfg(agent_id)
        if not cfg.get("enabled", False):
            logger.info("Email channel [%s]: disabled", agent_id)
            return

        alias = cfg.get("alias", "")
        if alias:
            self._connected_agents.add(agent_id)
            logger.info("Email channel [%s]: connected (alias=%s)", agent_id, alias)
            self._register_with_agent(agent_id)
            self._inject_send_tool(agent_id)
            if not self._drain_registered:
                self._register_drain_poller()
                self._drain_registered = True
        else:
            logger.info("Email channel [%s]: not fully configured", agent_id)

    async def shutdown(self) -> None:
        self._connected_agents.clear()

    # -- poller via SDK primitive -------------------------------------------

    def _register_drain_poller(self) -> None:
        """Register a single drain poller that serves all connected agents."""
        from nls.skills import SkillPoller

        adapter_ref = self

        async def _drain_callback() -> None:
            await adapter_ref._drain_all_agents()

        self._ctx.register_poller(SkillPoller(
            name="email-drain",
            interval_seconds=POLL_INTERVAL_SECONDS,
            callback=_drain_callback,
        ))
        logger.info("Email channel: registered drain poller (every %ds)", POLL_INTERVAL_SECONDS)

    async def _drain_all_agents(self) -> None:
        for agent_id in list(self._connected_agents):
            await self._drain_pending(agent_id)

    async def _drain_pending(self, agent_id: str) -> None:
        """Fetch and process pending messages from NestJS for one agent."""
        url = f"{_nestjs_url()}/channels/pending/drain"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        secret = _runtime_secret()
        if secret:
            headers["X-Runtime-Secret"] = secret

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url, headers=headers,
                    json={"agent_id": agent_id},
                )
                if resp.status_code not in (200, 201):
                    return
                data = resp.json()
        except Exception:
            return

        messages = data.get("messages", [])
        if not messages:
            return

        logger.info("Email poller [%s]: draining %d pending message(s)", agent_id, len(messages))

        for msg in messages:
            payload = msg.get("payload", {})
            channel = msg.get("channel", "email")
            if channel != "email":
                continue

            try:
                from .webhook import process_inbound_email
                from server.main import app
                await process_inbound_email(app, agent_id, payload)
            except Exception as exc:
                logger.error("Email poller [%s]: failed to process message %s: %s", agent_id, msg.get("id"), exc)

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
                    cr.register("email", self)
        except Exception:
            pass

    def _inject_send_tool(self, agent_id: str) -> None:
        """Inject a per-agent EmailSendTool into the runtime."""
        try:
            from server.main import app
            agent_manager = getattr(app.state, "agent_manager", None)
            if agent_manager is None:
                return
            runtime = agent_manager.get_runtime(agent_id)
            if runtime is None or not hasattr(runtime, "_agent_tools"):
                return
            if runtime._agent_tools is None:
                return

            tool = EmailSendTool(self, agent_id=agent_id)
            runtime._agent_tools = [
                t for t in runtime._agent_tools
                if getattr(t, "name", "") != "email_send"
            ] + [tool]
            if hasattr(runtime, "refresh_tools"):
                runtime.refresh_tools()
            else:
                try:
                    from nls.tools.agent_tools.base import tools_to_openai_schema
                    runtime._openai_tools = tools_to_openai_schema(runtime._agent_tools)
                except Exception:
                    pass

            logger.info("Email channel [%s]: injected email_send tool", agent_id)
        except Exception as exc:
            logger.warning("Email channel [%s]: failed to inject tool: %s", agent_id, exc)

    # -- tool factory ------------------------------------------------------

    def create_send_tool(self, agent_id: str | None = None) -> EmailSendTool:
        return EmailSendTool(_adapter=self, agent_id=agent_id)

    # -- newsletter / content detection ------------------------------------

    @staticmethod
    def is_newsletter(headers: dict[str, str], body: str) -> bool:
        lower_headers = {k.lower(): v for k, v in headers.items()}

        if _NEWSLETTER_HEADERS & set(lower_headers.keys()):
            return True

        if lower_headers.get("x-forwarded-for") or lower_headers.get("x-forwarded-to"):
            return True

        subject = lower_headers.get("subject", "")
        if subject.lower().startswith("fwd:") or subject.lower().startswith("fw:"):
            return True

        if len(body) > 2000 and not re.search(r"\?$", body.strip()[-50:]):
            return True

        return False

    # -- message normalization ---------------------------------------------

    def normalize_inbound(
        self,
        sender: str,
        subject: str,
        body: str,
        headers: dict[str, str] | None = None,
        message_id: str = "",
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        headers = headers or {}

        in_reply_to = headers.get("In-Reply-To", headers.get("in-reply-to", ""))
        references = headers.get("References", headers.get("references", ""))

        # Thread key uses the ROOT message-id from References (oldest first).
        # This keeps every reply in the same session regardless of depth.
        thread_root = ""
        if references:
            refs = [r.strip() for r in references.replace(">", "> ").split()
                    if r.strip().startswith("<")]
            if refs:
                thread_root = refs[0].strip("<>").split("@")[0][:32]
        if not thread_root and in_reply_to:
            thread_root = in_reply_to.strip("<>").split("@")[0][:32]
        if not thread_root:
            thread_root = (message_id or subject).strip("<>").split("@")[0][:32]

        session_key = f"email:thread:{thread_root}"
        is_content = self.is_newsletter(headers, body)

        from nls.runtime.interaction_policy import is_shared_email_inbound

        cfg = self._agent_cfg(agent_id)
        agent_addrs: set[str] = set()
        for key in ("alias", "from_address", "connected_email"):
            val = str(cfg.get(key, "")).strip()
            if val:
                agent_addrs.add(val.lower())
        is_group = is_shared_email_inbound(headers, agent_addrs)

        return {
            "channel": "email",
            "session_key": session_key,
            "sender_id": sender,
            "sender_name": sender.split("@")[0] if "@" in sender else sender,
            "content": body,
            "is_group": is_group,
            "group_id": thread_root if is_group else None,
            "is_mention": True,
            "is_forwarded": subject.lower().startswith(("fwd:", "fw:")),
            "is_reply_to_bot": bool(in_reply_to),
            "message_type": "content" if is_content else "chat",
            "message_id": message_id,
            "attachments": [],
            "metadata": {
                "subject": subject,
                "email_headers": headers,
                "in_reply_to": in_reply_to,
                "references": references,
            },
        }

