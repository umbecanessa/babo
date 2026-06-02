"""Gmail tools -- search, read, send, reply, labels, attachments, archive.

Write operations (send, reply) support a confirmation gate:
when ``require_confirmation=True``, the first call returns a draft
for the user to review.  The agent presents it and calls again
with ``confirmed=True`` to execute.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
from typing import Any

from nls.tools.agent_tools.base import ToolResult

logger = logging.getLogger(__name__)


def _not_connected() -> ToolResult:
    return ToolResult(
        content="Error: Google account not connected. Use google_workspace_connect first.",
        is_error=True,
    )


class GmailSearchTool:
    """Search the user's Gmail inbox using Gmail query syntax."""

    def __init__(self, adapter: Any, agent_id: str) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "gmail_search"

    @property
    def description(self) -> str:
        return (
            "Search the user's Gmail inbox. Supports Gmail query syntax: "
            "from:, to:, subject:, newer_than:, older_than:, has:attachment, "
            "label:, is:unread, is:starred, etc."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query (e.g. 'from:alice newer_than:7d')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default 10, max 50)",
                },
            },
            "required": ["query"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        query = params.get("query", "")
        max_results = min(params.get("max_results", 10), 50)

        try:
            service = await asyncio.to_thread(flow.build_service, "gmail", "v1")
            results = await asyncio.to_thread(
                lambda: service.users().messages().list(
                    userId="me", q=query, maxResults=max_results,
                ).execute()
            )
            messages = results.get("messages", [])
            if not messages:
                return ToolResult(content=f"No messages found for query: {query}")

            summaries: list[str] = []
            for msg_ref in messages:
                msg = await asyncio.to_thread(
                    lambda mid=msg_ref["id"]: service.users().messages().get(
                        userId="me", id=mid, format="metadata",
                        metadataHeaders=["From", "Subject", "Date"],
                    ).execute()
                )
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                snippet = msg.get("snippet", "")[:120]
                summaries.append(
                    f"ID: {msg['id']}\n"
                    f"  From: {headers.get('From', '?')}\n"
                    f"  Subject: {headers.get('Subject', '(no subject)')}\n"
                    f"  Date: {headers.get('Date', '?')}\n"
                    f"  Snippet: {snippet}"
                )

            return ToolResult(
                content=f"Found {len(messages)} message(s):\n\n" + "\n\n".join(summaries),
                details={"count": len(messages), "message_ids": [m["id"] for m in messages]},
            )
        except Exception as exc:
            return ToolResult(content=f"Gmail search failed: {exc}", is_error=True)


class GmailReadTool:
    """Read a specific email message or thread."""

    def __init__(self, adapter: Any, agent_id: str) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "gmail_read"

    @property
    def description(self) -> str:
        return "Read a specific Gmail message by ID. Returns full headers, body, and attachment list."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "Gmail message ID (from gmail_search results). Provide this OR thread_id.",
                },
                "thread_id": {
                    "type": "string",
                    "description": "Thread ID to read all messages in a conversation. Provide this OR message_id.",
                },
            },
            "required": ["message_id"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        message_id = params.get("message_id", "")
        thread_id = params.get("thread_id", "")

        if not message_id and not thread_id:
            return ToolResult(content="Error: provide message_id or thread_id", is_error=True)

        try:
            service = await asyncio.to_thread(flow.build_service, "gmail", "v1")

            if thread_id:
                thread = await asyncio.to_thread(
                    lambda: service.users().threads().get(
                        userId="me", id=thread_id, format="full",
                    ).execute()
                )
                parts: list[str] = []
                for msg in thread.get("messages", []):
                    parts.append(_format_message(msg))
                return ToolResult(
                    content=f"Thread {thread_id} ({len(thread.get('messages', []))} messages):\n\n"
                    + "\n---\n".join(parts)
                )

            msg = await asyncio.to_thread(
                lambda: service.users().messages().get(
                    userId="me", id=message_id, format="full",
                ).execute()
            )
            return ToolResult(
                content=_format_message(msg),
                details={"thread_id": msg.get("threadId", "")},
            )
        except Exception as exc:
            return ToolResult(content=f"Gmail read failed: {exc}", is_error=True)


class GmailSendTool:
    """Compose and send a new email from the user's Gmail."""

    def __init__(self, adapter: Any, agent_id: str, require_confirmation: bool = True) -> None:
        self._adapter = adapter
        self._agent_id = agent_id
        self._require_confirmation = require_confirmation

    @property
    def name(self) -> str:
        return "gmail_send"

    @property
    def description(self) -> str:
        return (
            "Send a new email from the user's Gmail account. "
            "Supports file attachments from the agent workspace. "
            "If confirmation is required, first call without confirmed=true to "
            "preview the draft, then call again with confirmed=true to send."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Email body (plain text)"},
                "cc": {"type": "string", "description": "CC recipients (comma-separated)"},
                "bcc": {"type": "string", "description": "BCC recipients (comma-separated)"},
                "attachments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of file paths in the agent workspace to attach "
                        "(e.g. ['reports/q1.pdf', 'data.csv'])"
                    ),
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "Set to true to send after reviewing the draft",
                },
            },
            "required": ["to", "subject", "body"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        to = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        cc = params.get("cc", "")
        bcc = params.get("bcc", "")
        attachment_paths = params.get("attachments", []) or []
        confirmed = params.get("confirmed", False)

        if self._require_confirmation and not confirmed:
            draft = f"To: {to}\n"
            if cc:
                draft += f"CC: {cc}\n"
            if bcc:
                draft += f"BCC: {bcc}\n"
            draft += f"Subject: {subject}\n\n{body}"
            if attachment_paths:
                draft += f"\n\nAttachments: {', '.join(attachment_paths)}"
            return ToolResult(
                content=(
                    "**Draft email for review:**\n\n"
                    f"```\n{draft}\n```\n\n"
                    "Present this draft to the user. If they approve, call "
                    "gmail_send again with the same parameters and confirmed=true."
                ),
                details={"draft": True, "needs_confirmation": True},
            )

        if attachment_paths:
            workspace = _get_workspace(self._agent_id)
            for fp in attachment_paths:
                if workspace is None:
                    return ToolResult(content="Cannot resolve workspace for attachments", is_error=True)
                full = (workspace / fp).resolve()
                if not full.is_file() or not str(full).startswith(str(workspace.resolve())):
                    return ToolResult(content=f"Attachment not found in workspace: {fp}", is_error=True)

        try:
            msg = _build_mime_message(
                to=to, subject=subject, body=body, cc=cc, bcc=bcc,
                attachment_paths=attachment_paths,
                agent_id=self._agent_id,
            )

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
            service = await asyncio.to_thread(flow.build_service, "gmail", "v1")
            await asyncio.to_thread(
                lambda: service.users().messages().send(
                    userId="me", body={"raw": raw},
                ).execute()
            )
            self._adapter.audit(
                self._agent_id, "gmail_send",
                to=to, subject=subject, cc=cc or None, bcc=bcc or None,
                attachments=attachment_paths or None,
            )
            att_note = f" with {len(attachment_paths)} attachment(s)" if attachment_paths else ""
            return ToolResult(content=f"Email sent to {to}: {subject}{att_note}")
        except Exception as exc:
            return ToolResult(content=f"Failed to send email: {exc}", is_error=True)


class GmailReplyTool:
    """Reply to an existing email thread from the user's Gmail."""

    def __init__(self, adapter: Any, agent_id: str, require_confirmation: bool = True) -> None:
        self._adapter = adapter
        self._agent_id = agent_id
        self._require_confirmation = require_confirmation

    @property
    def name(self) -> str:
        return "gmail_reply"

    @property
    def description(self) -> str:
        return (
            "Reply to an existing Gmail thread. Preserves threading. "
            "If confirmation is required, first call without confirmed=true "
            "to preview, then call with confirmed=true to send."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "Thread ID to reply to"},
                "message_id": {"type": "string", "description": "Message ID to reply to (for headers)"},
                "body": {"type": "string", "description": "Reply body (plain text)"},
                "attachments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths in agent workspace to attach",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "Set to true to send after reviewing the draft",
                },
            },
            "required": ["thread_id", "body"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        thread_id = params.get("thread_id", "")
        message_id = params.get("message_id", "")
        body = params.get("body", "")
        attachment_paths = params.get("attachments", []) or []
        confirmed = params.get("confirmed", False)

        try:
            service = await asyncio.to_thread(flow.build_service, "gmail", "v1")

            if message_id:
                orig = await asyncio.to_thread(
                    lambda: service.users().messages().get(
                        userId="me", id=message_id, format="metadata",
                        metadataHeaders=["From", "Subject", "Message-ID"],
                    ).execute()
                )
            else:
                thread = await asyncio.to_thread(
                    lambda: service.users().threads().get(
                        userId="me", id=thread_id, format="metadata",
                        metadataHeaders=["From", "Subject", "Message-ID"],
                    ).execute()
                )
                msgs = thread.get("messages", [])
                orig = msgs[-1] if msgs else {}

            orig_headers = {
                h["name"]: h["value"]
                for h in orig.get("payload", {}).get("headers", [])
            }
            reply_to = orig_headers.get("From", "")
            subject = orig_headers.get("Subject", "")
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
            orig_msg_id = orig_headers.get("Message-ID", "")

            if self._require_confirmation and not confirmed:
                att_note = f"\nAttachments: {', '.join(attachment_paths)}" if attachment_paths else ""
                return ToolResult(
                    content=(
                        "**Draft reply for review:**\n\n"
                        f"To: {reply_to}\n"
                        f"Subject: {subject}\n\n"
                        f"{body}{att_note}\n\n"
                        "Present this to the user. If approved, call "
                        "gmail_reply with the same parameters and confirmed=true."
                    ),
                    details={"draft": True, "needs_confirmation": True},
                )

            if attachment_paths:
                workspace = _get_workspace(self._agent_id)
                for fp in attachment_paths:
                    if workspace is None:
                        return ToolResult(content="Cannot resolve workspace for attachments", is_error=True)
                    full = (workspace / fp).resolve()
                    if not full.is_file() or not str(full).startswith(str(workspace.resolve())):
                        return ToolResult(content=f"Attachment not found in workspace: {fp}", is_error=True)

            msg = _build_mime_message(
                to=reply_to, subject=subject, body=body,
                attachment_paths=attachment_paths,
                agent_id=self._agent_id,
            )
            if orig_msg_id:
                msg["In-Reply-To"] = orig_msg_id
                msg["References"] = orig_msg_id

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
            await asyncio.to_thread(
                lambda: service.users().messages().send(
                    userId="me", body={"raw": raw, "threadId": thread_id},
                ).execute()
            )
            self._adapter.audit(
                self._agent_id, "gmail_reply",
                thread_id=thread_id, to=reply_to, subject=subject,
                attachments=attachment_paths or None,
            )
            att_note = f" with {len(attachment_paths)} attachment(s)" if attachment_paths else ""
            return ToolResult(content=f"Reply sent in thread {thread_id} to {reply_to}{att_note}")
        except Exception as exc:
            return ToolResult(content=f"Failed to send reply: {exc}", is_error=True)


class GmailLabelsTool:
    """List, add, or remove Gmail labels."""

    def __init__(self, adapter: Any, agent_id: str) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "gmail_labels"

    @property
    def description(self) -> str:
        return (
            "Manage Gmail labels. Actions: 'list' (all labels), "
            "'add' (add label to message), 'remove' (remove label from message)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add", "remove"],
                    "description": "Action to perform",
                },
                "message_id": {
                    "type": "string",
                    "description": "Message ID (for add/remove)",
                },
                "label": {
                    "type": "string",
                    "description": "Label name or ID (for add/remove)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        action = params.get("action", "list")

        try:
            service = await asyncio.to_thread(flow.build_service, "gmail", "v1")

            if action == "list":
                results = await asyncio.to_thread(
                    lambda: service.users().labels().list(userId="me").execute()
                )
                labels = results.get("labels", [])
                label_list = "\n".join(
                    f"  - {l['name']} (ID: {l['id']}, type: {l.get('type', 'user')})"
                    for l in labels
                )
                return ToolResult(content=f"Gmail labels ({len(labels)}):\n{label_list}")

            message_id = params.get("message_id", "")
            label = params.get("label", "")
            if not message_id or not label:
                return ToolResult(
                    content="Error: message_id and label required for add/remove",
                    is_error=True,
                )

            # Resolve label name to ID if needed
            label_id = await self._resolve_label_id(service, label)
            if not label_id:
                return ToolResult(content=f"Label '{label}' not found", is_error=True)

            if action == "add":
                await asyncio.to_thread(
                    lambda: service.users().messages().modify(
                        userId="me", id=message_id,
                        body={"addLabelIds": [label_id]},
                    ).execute()
                )
                self._adapter.audit(
                    self._agent_id, "gmail_label_add",
                    message_id=message_id, label=label,
                )
                return ToolResult(content=f"Label '{label}' added to message {message_id}")
            elif action == "remove":
                await asyncio.to_thread(
                    lambda: service.users().messages().modify(
                        userId="me", id=message_id,
                        body={"removeLabelIds": [label_id]},
                    ).execute()
                )
                self._adapter.audit(
                    self._agent_id, "gmail_label_remove",
                    message_id=message_id, label=label,
                )
                return ToolResult(content=f"Label '{label}' removed from message {message_id}")

            return ToolResult(content=f"Unknown action: {action}", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"Gmail labels operation failed: {exc}", is_error=True)

    async def _resolve_label_id(self, service: Any, label: str) -> str:
        """Resolve a label name to its ID."""
        results = await asyncio.to_thread(
            lambda: service.users().labels().list(userId="me").execute()
        )
        for lbl in results.get("labels", []):
            if lbl["id"] == label or lbl["name"].lower() == label.lower():
                return lbl["id"]
        return ""


class GmailAttachmentTool:
    """Download a Gmail attachment to the agent's workspace."""

    def __init__(self, adapter: Any, agent_id: str) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "gmail_attachment"

    @property
    def description(self) -> str:
        return (
            "Download an attachment from a Gmail message. "
            "Use gmail_read first to see available attachments and their IDs. "
            "The file is saved to workspace/downloads/."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "Gmail message ID containing the attachment",
                },
                "attachment_id": {
                    "type": "string",
                    "description": (
                        "Attachment ID (from payload part body.attachmentId). "
                        "If omitted, downloads the first attachment."
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": "Override the filename to save as",
                },
            },
            "required": ["message_id"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        message_id = params.get("message_id", "")
        attachment_id = params.get("attachment_id", "")
        override_filename = params.get("filename", "")

        if not message_id:
            return ToolResult(content="Error: message_id is required", is_error=True)

        try:
            service = await asyncio.to_thread(flow.build_service, "gmail", "v1")

            msg = await asyncio.to_thread(
                lambda: service.users().messages().get(
                    userId="me", id=message_id, format="full",
                ).execute()
            )

            target_part = _find_attachment_part(
                msg.get("payload", {}), attachment_id,
            )
            if target_part is None:
                return ToolResult(
                    content="No matching attachment found in this message.",
                    is_error=True,
                )

            part_att_id = target_part.get("body", {}).get("attachmentId", "")
            raw_name = override_filename or target_part.get("filename", "attachment")
            filename = Path(raw_name).name or "attachment"

            att_data = await asyncio.to_thread(
                lambda: service.users().messages().attachments().get(
                    userId="me", messageId=message_id, id=part_att_id,
                ).execute()
            )
            raw_bytes = base64.urlsafe_b64decode(att_data.get("data", ""))

            workspace = _get_workspace(self._agent_id)
            if workspace is None:
                return ToolResult(content="Cannot resolve workspace directory", is_error=True)

            dl_dir = workspace / "downloads"
            dl_dir.mkdir(parents=True, exist_ok=True)
            dest = dl_dir / filename
            dest.write_bytes(raw_bytes)

            return ToolResult(
                content=(
                    f"Attachment saved: downloads/{filename} "
                    f"({len(raw_bytes):,} bytes)"
                ),
                details={
                    "path": f"downloads/{filename}",
                    "size": len(raw_bytes),
                },
            )
        except Exception as exc:
            return ToolResult(content=f"Attachment download failed: {exc}", is_error=True)


class GmailArchiveTool:
    """Archive or trash Gmail messages."""

    def __init__(self, adapter: Any, agent_id: str) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "gmail_archive"

    @property
    def description(self) -> str:
        return (
            "Archive or trash a Gmail message. "
            "Archive removes from Inbox; trash moves to Trash (auto-deleted after 30 days)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "Gmail message ID",
                },
                "action": {
                    "type": "string",
                    "enum": ["archive", "trash"],
                    "description": "Action to perform (default: archive)",
                },
            },
            "required": ["message_id"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        message_id = params.get("message_id", "")
        action = params.get("action", "archive")

        if not message_id:
            return ToolResult(content="Error: message_id is required", is_error=True)

        try:
            service = await asyncio.to_thread(flow.build_service, "gmail", "v1")

            if action == "trash":
                await asyncio.to_thread(
                    lambda: service.users().messages().trash(
                        userId="me", id=message_id,
                    ).execute()
                )
                self._adapter.audit(self._agent_id, "gmail_trash", message_id=message_id)
                return ToolResult(content=f"Message {message_id} moved to Trash.")

            await asyncio.to_thread(
                lambda: service.users().messages().modify(
                    userId="me", id=message_id,
                    body={"removeLabelIds": ["INBOX"]},
                ).execute()
            )
            self._adapter.audit(self._agent_id, "gmail_archive", message_id=message_id)
            return ToolResult(content=f"Message {message_id} archived (removed from Inbox).")
        except Exception as exc:
            return ToolResult(content=f"Gmail {action} failed: {exc}", is_error=True)


# ── Helpers ────────────────────────────────────────────────────


def _get_workspace(agent_id: str) -> Path | None:
    """Resolve the workspace directory for an agent."""
    try:
        from server.main import app
        am = getattr(app.state, "agent_manager", None)
        if am is None:
            return None
        return am.agents_dir / agent_id / "workspace"
    except Exception:
        return None


def _find_attachment_part(
    payload: dict[str, Any], attachment_id: str,
) -> dict[str, Any] | None:
    """Find an attachment part in a message payload, optionally matching by ID."""
    for part in payload.get("parts", []):
        body = part.get("body", {})
        att_id = body.get("attachmentId", "")
        filename = part.get("filename", "")
        if att_id:
            if not attachment_id or att_id == attachment_id:
                return part
        nested = _find_attachment_part(part, attachment_id)
        if nested:
            return nested
    return None


def _build_mime_message(
    *,
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
    attachment_paths: list[str] | None = None,
    agent_id: str = "",
) -> MIMEBase:
    """Build a MIME message, optionally with file attachments from the workspace."""
    resolved_files: list[Path] = []
    skipped: list[str] = []
    if attachment_paths:
        workspace = _get_workspace(agent_id)
        if workspace:
            for fp in attachment_paths:
                full = (workspace / fp).resolve()
                if full.is_file() and str(full).startswith(str(workspace.resolve())):
                    resolved_files.append(full)
                else:
                    skipped.append(fp)
        else:
            skipped.extend(attachment_paths)

    if skipped:
        logger.warning("Skipped unresolvable attachments: %s", skipped)

    if not resolved_files:
        msg = MIMEText(body)
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc
        return msg

    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg.attach(MIMEText(body))

    for fpath in resolved_files:
        mime_type = mimetypes.guess_type(str(fpath))[0] or "application/octet-stream"
        main_type, sub_type = mime_type.split("/", 1)
        with open(fpath, "rb") as f:
            part = MIMEBase(main_type, sub_type)
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition", "attachment",
            filename=fpath.name,
        )
        msg.attach(part)

    return msg


def _format_message(msg: dict[str, Any]) -> str:
    """Format a full Gmail message as readable text."""
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    body = _extract_body(msg.get("payload", {}))
    attachments = _list_attachments(msg.get("payload", {}))

    parts = [
        f"From: {headers.get('From', '?')}",
        f"To: {headers.get('To', '?')}",
        f"Date: {headers.get('Date', '?')}",
        f"Subject: {headers.get('Subject', '(no subject)')}",
        f"Message-ID: {msg.get('id', '')}",
        f"Thread-ID: {msg.get('threadId', '')}",
        "",
        body or "(empty body)",
    ]
    if attachments:
        parts.append(f"\nAttachments: {', '.join(attachments)}")
    return "\n".join(parts)


def _extract_body(payload: dict[str, Any]) -> str:
    """Recursively extract the plain-text body from a message payload."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain" and "body" in payload:
        data = payload["body"].get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result

    # Fallback: try HTML
    if mime == "text/html" and "body" in payload:
        data = payload["body"].get("data", "")
        if data:
            raw = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            import re
            return re.sub(r"<[^>]+>", "", raw).strip()

    return ""


def _list_attachments(payload: dict[str, Any]) -> list[str]:
    """List attachment filenames from a message payload, including attachment IDs."""
    attachments: list[str] = []
    for part in payload.get("parts", []):
        filename = part.get("filename", "")
        if filename:
            body = part.get("body", {})
            size = body.get("size", 0)
            att_id = body.get("attachmentId", "")
            entry = f"{filename} ({size} bytes)"
            if att_id:
                entry += f" [attachmentId: {att_id}]"
            attachments.append(entry)
        attachments.extend(_list_attachments(part))
    return attachments
