"""Google Workspace adapter -- OAuth lifecycle, tool injection, and polling.

Follows the EmailAdapter pattern: per-agent config, startup/shutdown,
tool injection into agent runtimes, and SkillPoller for periodic
inbox/calendar checks.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from typing import Any

from nls.tools.agent_tools.base import ToolResult

from .oauth import OAuth2Flow, TokenStore, scopes_for_config

logger = logging.getLogger(__name__)

_GW_TOOL_NAMES = frozenset({
    "gmail_search", "gmail_read", "gmail_send", "gmail_reply",
    "gmail_labels", "gmail_attachment", "gmail_archive",
    "calendar_list", "calendar_create",
    "calendar_update", "drive_search", "drive_list", "drive_read",
    "drive_upload", "sheets_info", "sheets_read", "sheets_write",
})


# ── Connection management tool ────────────────────────────────


class GoogleWorkspaceConnectTool:
    """Agent tool for managing the Google Workspace connection.

    Actions: status, save_credentials, connect, disconnect.
    """

    def __init__(self, adapter: GoogleWorkspaceAdapter, agent_id: str) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "google_workspace_connect"

    @property
    def description(self) -> str:
        return (
            "Manage the Google Workspace connection. Actions: "
            "'status' (check connection), "
            "'save_credentials' (store client_id + client_secret), "
            "'connect' (opens a connection dialog in the app for the user to authorize -- "
            "do NOT open a browser yourself), "
            "'disconnect' (revoke access)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "save_credentials", "connect", "disconnect"],
                    "description": "The action to perform.",
                },
                "client_id": {
                    "type": "string",
                    "description": "Google OAuth Client ID (for save_credentials).",
                },
                "client_secret": {
                    "type": "string",
                    "description": "Google OAuth Client Secret (for save_credentials).",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        action = params.get("action", "")
        if action == "status":
            return await self._status()
        if action == "save_credentials":
            return await self._save_credentials(params)
        if action == "connect":
            return await self._connect()
        if action == "disconnect":
            return await self._disconnect()
        return ToolResult(
            content=f"Unknown action: '{action}'. Use status/save_credentials/connect/disconnect.",
            is_error=True,
        )

    async def _status(self) -> ToolResult:
        cfg = self._adapter._agent_cfg(self._agent_id)
        flow = self._adapter.get_oauth_flow(self._agent_id)
        connected = flow is not None and flow.is_authenticated
        email = cfg.get("connected_email", "")
        needs_reauth = cfg.get("needs_reauth", False)
        lines = [
            f"Google Workspace: {'Connected' if connected else 'Not connected'}"
            + (f" as {email}" if email else ""),
            f"Gmail: {cfg.get('gmail_access', 'disabled')}",
            f"Calendar: {cfg.get('calendar_access', 'disabled')}",
            f"Drive: {cfg.get('drive_access', 'disabled')}",
            f"Sheets: {cfg.get('sheets_access', 'disabled')}",
        ]
        if needs_reauth:
            lines.append(
                "\n⚠ Access levels changed since last authorization. "
                "Please disconnect and reconnect to apply new permissions."
            )
        return ToolResult(
            content="\n".join(lines),
            details={"connected": connected, "email": email, "needs_reauth": needs_reauth},
        )

    async def _save_credentials(self, params: dict[str, Any]) -> ToolResult:
        client_id = params.get("client_id", "").strip()
        client_secret = params.get("client_secret", "").strip()
        if not client_id or not client_secret:
            return ToolResult(
                content="Error: both client_id and client_secret are required.",
                is_error=True,
            )

        # Basic format validation — Google OAuth client IDs end with
        # .apps.googleusercontent.com and secrets are short alphanum tokens.
        if not client_id.endswith(".apps.googleusercontent.com"):
            return ToolResult(
                content=(
                    f"Error: client_id looks invalid — expected a string "
                    f"ending with '.apps.googleusercontent.com' but got: "
                    f"'{client_id[:60]}...'. "
                    f"Ask the user to copy the exact Client ID from the "
                    f"Google Cloud Console credentials page."
                ),
                is_error=True,
            )
        if len(client_secret) < 10 or " " in client_secret:
            return ToolResult(
                content=(
                    f"Error: client_secret looks invalid — it should be "
                    f"an alphanumeric token (e.g. 'GOCSPX-...') with no "
                    f"spaces, but got: '{client_secret[:40]}...'. "
                    f"Ask the user to copy the exact Client Secret from "
                    f"the Google Cloud Console credentials page."
                ),
                is_error=True,
            )

        self._adapter.update_config(
            {"client_id": client_id, "client_secret": client_secret},
            agent_id=self._agent_id,
        )
        # Clear cached flow so it picks up new credentials
        self._adapter._oauth_flows.pop(self._agent_id, None)
        return ToolResult(
            content="Credentials saved. Now call google_workspace_connect(action='connect') to authorize."
        )

    async def _connect(self) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if flow is None:
            return ToolResult(
                content=(
                    "Error: Google OAuth credentials are not configured. "
                    "Built-in credentials should be available by default. "
                    "If they were cleared, use save_credentials to provide "
                    "custom client_id and client_secret, or contact the "
                    "app administrator."
                ),
                is_error=True,
            )
        if flow.is_authenticated:
            return ToolResult(content="Already connected. Use disconnect first to reconnect.")

        # Emit a connection_request event to the frontend so it opens
        # the OAuth modal directly — no agent browser interaction needed.
        self._adapter._broadcast_notification(self._agent_id, {
            "type": "connection_request",
            "skill": "google-workspace",
        })

        # Also prepare the OAuth flow server-side so the REST endpoint
        # is ready when the frontend calls POST /connect/{agent_id}.
        redirect_uri = self._adapter.get_redirect_uri()
        state = self._adapter.create_oauth_state(self._agent_id)
        auth_url = flow.get_auth_url(redirect_uri, state=state)
        self._adapter._pending_oauth[self._agent_id] = flow

        return ToolResult(
            content=(
                "A connection dialog has been opened for the user. "
                "They will sign in to Google and authorize access in their browser. "
                "Do NOT open a browser or give manual instructions — the dialog "
                "handles everything.\n\n"
                "Next: call ask_user() to let the user know the dialog is open "
                "and wait for them to finish. Then call "
                "google_workspace_connect(action='status') to verify."
            ),
            details={"auth_url": auth_url, "ui_driven": True},
        )

    async def _disconnect(self) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if flow:
            await flow.revoke()
        self._adapter._oauth_flows.pop(self._agent_id, None)
        self._adapter.update_config({"connected_email": ""}, agent_id=self._agent_id)
        self._adapter._connected_agents.discard(self._agent_id)
        self._adapter._strip_tools(self._agent_id)
        return ToolResult(content="Google Workspace disconnected. Tokens revoked.")


# ── Adapter ───────────────────────────────────────────────────


class GoogleWorkspaceAdapter:
    """Google Workspace adapter -- manages OAuth, tools, and polling per agent."""

    def __init__(self, global_config: dict[str, Any], ctx: Any) -> None:
        self._global_config = global_config
        self._ctx = ctx
        self._agent_configs: dict[str, dict[str, Any]] = {}
        self._connected_agents: set[str] = set()
        self._oauth_flows: dict[str, OAuth2Flow] = {}
        self._pending_oauth: dict[str, OAuth2Flow] = {}
        self._token_store = TokenStore(ctx.data_dir / "tokens")
        self._oauth_states: dict[str, str] = {}  # state_token -> agent_id
        self._oauth_state_times: dict[str, float] = {}  # state_token -> creation time
        self._polling_registered = False
        self._seen_gmail_ids: dict[str, set[str]] = {}  # agent_id -> set of message ids
        self._seen_calendar_ids: dict[str, set[str]] = {}
        self._load_all_agent_configs()

    def _load_all_agent_configs(self) -> None:
        for agent_id, cfg in self._ctx.load_all_agent_configs().items():
            self._agent_configs[agent_id] = cfg

    def _agent_cfg(self, agent_id: str) -> dict[str, Any]:
        merged = dict(self._global_config)
        merged.update(self._agent_configs.get(agent_id, {}))
        return merged

    # ── OAuth flow management ─────────────────────────────────

    def get_oauth_flow(self, agent_id: str) -> OAuth2Flow | None:
        if agent_id in self._oauth_flows:
            return self._oauth_flows[agent_id]
        cfg = self._agent_cfg(agent_id)
        client_id = cfg.get("client_id", "")
        client_secret = cfg.get("client_secret", "")
        if not client_id or not client_secret:
            return None
        scopes = scopes_for_config(cfg)
        flow = OAuth2Flow(
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
            token_store=self._token_store,
            agent_id=agent_id,
        )
        self._oauth_flows[agent_id] = flow
        return flow

    def get_redirect_uri(self) -> str:
        """Build the OAuth redirect URI pointing to our FastAPI callback route."""
        import os
        port = os.environ.get("PORT", os.environ.get("NLS_SERVE_PORT", "9222"))
        return f"http://localhost:{port}/skills/google-workspace/oauth/callback"

    # ── OAuth state / CSRF ──────────────────────────────────

    def create_oauth_state(self, agent_id: str) -> str:
        """Generate a state token encoding the agent_id for CSRF protection."""
        import time
        self._prune_stale_oauth_states()
        nonce = secrets.token_urlsafe(16)
        token = f"{agent_id}:{nonce}"
        digest = hashlib.sha256(token.encode()).hexdigest()[:12]
        state = f"{agent_id}.{digest}"
        self._oauth_states[state] = agent_id
        self._oauth_state_times[state] = time.monotonic()
        return state

    def resolve_oauth_state(self, state: str) -> str | None:
        """Resolve a state token to an agent_id, consuming it."""
        self._oauth_state_times.pop(state, None)
        return self._oauth_states.pop(state, None)

    def _prune_stale_oauth_states(self) -> None:
        """Remove state tokens older than 10 minutes."""
        import time
        cutoff = time.monotonic() - 600
        stale = [s for s, t in self._oauth_state_times.items() if t < cutoff]
        for s in stale:
            self._oauth_states.pop(s, None)
            self._oauth_state_times.pop(s, None)

    # ── Config management ─────────────────────────────────────

    def update_config(self, new_config: dict[str, Any], agent_id: str) -> None:
        old_cfg = dict(self._agent_configs.get(agent_id, {}))
        self._agent_configs.setdefault(agent_id, {}).update(new_config)
        self._ctx.save_config(self._agent_configs[agent_id], agent_id=agent_id)

        _ACCESS_KEYS = ("gmail_access", "calendar_access", "drive_access", "sheets_access")
        access_changed = any(old_cfg.get(k) != self._agent_configs[agent_id].get(k) for k in _ACCESS_KEYS)
        if access_changed and agent_id in self._connected_agents:
            old_scopes = set(scopes_for_config(old_cfg))
            new_scopes = set(scopes_for_config(self._agent_configs[agent_id]))
            if new_scopes - old_scopes:
                self._agent_configs[agent_id]["needs_reauth"] = True
                self._ctx.save_config(self._agent_configs[agent_id], agent_id=agent_id)
                logger.info(
                    "Google Workspace [%s]: access levels changed, re-auth needed "
                    "(new scopes: %s)", agent_id, new_scopes - old_scopes,
                )

    def get_status(self, agent_id: str) -> dict[str, Any]:
        cfg = self._agent_cfg(agent_id)
        flow = self.get_oauth_flow(agent_id)
        connected = flow is not None and flow.is_authenticated
        return {
            "channel": "google-workspace",
            "connected": connected,
            "enabled": cfg.get("enabled", False),
            "email": cfg.get("connected_email", ""),
            "gmail_access": cfg.get("gmail_access", "disabled"),
            "calendar_access": cfg.get("calendar_access", "disabled"),
            "drive_access": cfg.get("drive_access", "disabled"),
            "sheets_access": cfg.get("sheets_access", "disabled"),
        }

    # ── Tool factory (connection tool only) ───────────────────

    def create_connect_tool(self, agent_id: str) -> GoogleWorkspaceConnectTool:
        return GoogleWorkspaceConnectTool(adapter=self, agent_id=agent_id)

    # ── Service tool injection (email adapter pattern) ────────

    def _inject_tools(self, agent_id: str) -> None:
        """Inject Google Workspace service tools into the agent's runtime.

        Works with AgentRuntime directly.  After replacing tools in the
        _agent_tools list, calls refresh_tools() so the LLM sees the
        updated tool directory in the system prompt.
        """
        try:
            from server.main import app
            agent_manager = getattr(app.state, "agent_manager", None)
            if agent_manager is None:
                return
            runtime = agent_manager.get_runtime(agent_id)
            if runtime is None:
                return

            new_tools = self._build_service_tools(agent_id)

            if hasattr(runtime, "_agent_tools") and runtime._agent_tools is not None:
                self._replace_tools_on(runtime, new_tools)
                logger.info(
                    "Google Workspace [%s]: injected %d tools into runtime",
                    agent_id, len(new_tools),
                )
        except Exception as exc:
            logger.warning(
                "Google Workspace [%s]: failed to inject tools: %s",
                agent_id, exc,
            )

    @staticmethod
    def _replace_tools_on(target: Any, new_tools: list[Any]) -> None:
        """Replace GW tools on a runtime and rebuild schemas + prompt cache."""
        target._agent_tools = [
            t for t in target._agent_tools
            if getattr(t, "name", "") not in _GW_TOOL_NAMES
        ] + list(new_tools)
        if hasattr(target, "refresh_tools"):
            target.refresh_tools()
        else:
            try:
                from nls.tools.agent_tools.base import tools_to_openai_schema
                target._openai_tools = tools_to_openai_schema(target._agent_tools)
            except Exception:
                pass

    def _strip_tools(self, agent_id: str) -> None:
        """Remove all Google Workspace service tools from the agent."""
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
            runtime._agent_tools = [
                t for t in runtime._agent_tools
                if getattr(t, "name", "") not in _GW_TOOL_NAMES
            ]
            if hasattr(runtime, "refresh_tools"):
                runtime.refresh_tools()
            else:
                try:
                    from nls.tools.agent_tools.base import tools_to_openai_schema
                    runtime._openai_tools = tools_to_openai_schema(runtime._agent_tools)
                except Exception:
                    pass
        except Exception:
            pass

    def _build_service_tools(self, agent_id: str) -> list[Any]:
        """Build the set of service tools based on agent config."""
        cfg = self._agent_cfg(agent_id)
        tools: list[Any] = []
        require_confirm = cfg.get("require_confirmation", True)

        gmail = cfg.get("gmail_access", "disabled")
        if gmail != "disabled":
            from .tools.gmail import (
                GmailSearchTool, GmailReadTool, GmailLabelsTool,
                GmailSendTool, GmailReplyTool, GmailAttachmentTool,
                GmailArchiveTool,
            )
            tools.extend([
                GmailSearchTool(self, agent_id),
                GmailReadTool(self, agent_id),
                GmailLabelsTool(self, agent_id),
                GmailAttachmentTool(self, agent_id),
            ])
            if gmail == "read_write":
                tools.extend([
                    GmailSendTool(self, agent_id, require_confirmation=require_confirm),
                    GmailReplyTool(self, agent_id, require_confirmation=require_confirm),
                    GmailArchiveTool(self, agent_id),
                ])

        cal = cfg.get("calendar_access", "disabled")
        if cal != "disabled":
            from .tools.calendar import (
                CalendarListTool, CalendarCreateTool, CalendarUpdateTool,
            )
            tools.append(CalendarListTool(self, agent_id))
            if cal == "read_write":
                tools.extend([
                    CalendarCreateTool(self, agent_id, require_confirmation=require_confirm),
                    CalendarUpdateTool(self, agent_id, require_confirmation=require_confirm),
                ])

        drive = cfg.get("drive_access", "disabled")
        if drive != "disabled":
            from .tools.drive import DriveSearchTool, DriveListTool, DriveReadTool, DriveUploadTool
            folder_allowlist = cfg.get("drive_folders", [])
            tools.extend([
                DriveSearchTool(self, agent_id, folder_allowlist=folder_allowlist),
                DriveListTool(self, agent_id, folder_allowlist=folder_allowlist),
                DriveReadTool(self, agent_id, folder_allowlist=folder_allowlist),
            ])
            if drive == "read_write":
                tools.append(DriveUploadTool(self, agent_id))

        sheets = cfg.get("sheets_access", "disabled")
        if sheets != "disabled":
            from .tools.sheets import SheetsInfoTool, SheetsReadTool, SheetsWriteTool
            tools.extend([
                SheetsInfoTool(self, agent_id),
                SheetsReadTool(self, agent_id),
            ])
            if sheets == "read_write":
                tools.append(
                    SheetsWriteTool(self, agent_id, require_confirmation=require_confirm)
                )

        return tools

    # ── OAuth callback completion ─────────────────────────────

    async def complete_oauth(self, agent_id: str, code: str) -> dict[str, Any]:
        """Called by the webhook route after Google redirects with the auth code."""
        flow = self._pending_oauth.pop(agent_id, None) or self.get_oauth_flow(agent_id)
        if flow is None:
            raise RuntimeError("No OAuth flow in progress for this agent.")

        redirect_uri = self.get_redirect_uri()
        result = await flow.exchange_code(code, redirect_uri)
        email = result.get("email", "")

        self.update_config({"connected_email": email, "needs_reauth": False}, agent_id=agent_id)
        self._oauth_flows[agent_id] = flow
        self._connected_agents.add(agent_id)
        self._inject_tools(agent_id)

        if not self._polling_registered:
            self._register_pollers()
            self._polling_registered = True

        logger.info("Google Workspace [%s]: connected as %s", agent_id, email)
        return {"connected": True, "email": email}

    # ── Polling ───────────────────────────────────────────────

    def _register_pollers(self) -> None:
        """Register Gmail and Calendar pollers via SkillPoller.

        Uses the smallest configured interval across all connected agents
        so no agent misses its window.
        """
        from nls.skills import SkillPoller

        gmail_interval = 120
        calendar_interval = 300
        for agent_id in self._connected_agents:
            cfg = self._agent_cfg(agent_id)
            gi = cfg.get("gmail_poll_interval", 120)
            ci = cfg.get("calendar_poll_interval", 300)
            if 0 < gi < gmail_interval:
                gmail_interval = gi
            if 0 < ci < calendar_interval:
                calendar_interval = ci

        adapter_ref = self

        async def _gmail_poll() -> None:
            await adapter_ref._poll_gmail_all()

        async def _calendar_poll() -> None:
            await adapter_ref._poll_calendar_all()

        self._ctx.register_poller(SkillPoller(
            name="gmail-poll",
            interval_seconds=gmail_interval,
            callback=_gmail_poll,
        ))
        self._ctx.register_poller(SkillPoller(
            name="calendar-poll",
            interval_seconds=calendar_interval,
            callback=_calendar_poll,
        ))
        logger.info(
            "Google Workspace: pollers registered (gmail=%ds, calendar=%ds)",
            gmail_interval, calendar_interval,
        )

    async def _poll_gmail_all(self) -> None:
        for agent_id in list(self._connected_agents):
            cfg = self._agent_cfg(agent_id)
            interval = cfg.get("gmail_poll_interval", 120)
            if interval <= 0 or cfg.get("gmail_access") == "disabled":
                continue
            await self._poll_gmail(agent_id)

    async def _poll_gmail(self, agent_id: str) -> None:
        """Check for new Gmail messages and notify the agent."""
        flow = self.get_oauth_flow(agent_id)
        if flow is None or not flow.is_authenticated:
            return
        try:
            service = await asyncio.to_thread(flow.build_service, "gmail", "v1")
            results = await asyncio.to_thread(
                lambda: service.users().messages().list(
                    userId="me", q="is:unread newer_than:1d", maxResults=5,
                ).execute()
            )
            messages = results.get("messages", [])
            if not messages:
                return

            seen = self._seen_gmail_ids.setdefault(agent_id, set())
            new_ids = [m["id"] for m in messages if m["id"] not in seen]
            if not new_ids:
                return

            summaries: list[str] = []
            for mid in new_ids[:5]:
                msg = await asyncio.to_thread(
                    lambda _mid=mid: service.users().messages().get(
                        userId="me", id=_mid, format="metadata",
                        metadataHeaders=["From", "Subject"],
                    ).execute()
                )
                hdrs = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                summaries.append(f"- From: {hdrs.get('From', '?')} | Subject: {hdrs.get('Subject', '(no subject)')}")
                seen.add(mid)

            if len(seen) > 200:
                self._seen_gmail_ids[agent_id] = set(list(seen)[-100:])

            self._broadcast_notification(agent_id, {
                "type": "gmail_notification",
                "channel": "google-workspace",
                "count": len(new_ids),
                "messages": summaries,
            })
            logger.info("Google Workspace [%s]: %d new Gmail message(s)", agent_id, len(new_ids))
        except Exception as exc:
            logger.debug("Gmail poll [%s] failed: %s", agent_id, exc)

    async def _poll_calendar_all(self) -> None:
        for agent_id in list(self._connected_agents):
            cfg = self._agent_cfg(agent_id)
            interval = cfg.get("calendar_poll_interval", 300)
            if interval <= 0 or cfg.get("calendar_access") == "disabled":
                continue
            await self._poll_calendar(agent_id)

    async def _poll_calendar(self, agent_id: str) -> None:
        """Check for upcoming Calendar events and notify the agent."""
        flow = self.get_oauth_flow(agent_id)
        if flow is None or not flow.is_authenticated:
            return
        try:
            from datetime import datetime, timedelta, timezone
            service = await asyncio.to_thread(flow.build_service, "calendar", "v3")
            now = datetime.now(timezone.utc)
            now_str = now.isoformat()
            soon_str = (now + timedelta(minutes=30)).isoformat()
            results = await asyncio.to_thread(
                lambda: service.events().list(
                    calendarId="primary", timeMin=now_str, timeMax=soon_str,
                    singleEvents=True, orderBy="startTime", maxResults=5,
                ).execute()
            )
            events = results.get("items", [])
            if not events:
                return

            seen = self._seen_calendar_ids.setdefault(agent_id, set())
            new_events = [e for e in events if e.get("id", "") not in seen]
            if not new_events:
                return

            summaries: list[str] = []
            for ev in new_events:
                start = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", "?"))
                summaries.append(f"- {ev.get('summary', '(no title)')} at {start}")
                seen.add(ev.get("id", ""))

            if len(seen) > 200:
                self._seen_calendar_ids[agent_id] = set(list(seen)[-100:])

            self._broadcast_notification(agent_id, {
                "type": "calendar_notification",
                "channel": "google-workspace",
                "count": len(new_events),
                "events": summaries,
            })
            logger.info("Google Workspace [%s]: %d upcoming event(s) in next 30min", agent_id, len(new_events))
        except Exception as exc:
            logger.debug("Calendar poll [%s] failed: %s", agent_id, exc)

    # ── Notification broadcast ─────────────────────────────────

    def _broadcast_notification(self, agent_id: str, payload: dict[str, Any]) -> None:
        """Send a notification to the agent's connected frontend clients."""
        try:
            from server.main import app
            cm = getattr(app.state, "connection_manager", None)
            if cm is None:
                return
            loop = asyncio.get_running_loop()
            loop.create_task(cm.broadcast(agent_id, payload))
        except Exception:
            pass

    # ── Audit trail ────────────────────────────────────────────

    def audit(self, agent_id: str, action: str, **details: Any) -> None:
        """Log a write action for audit purposes (JSONL append)."""
        import json as _json
        from datetime import datetime, timezone

        audit_dir = self._ctx.data_dir / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id,
            "action": action,
            **details,
        }
        try:
            with open(audit_dir / "google_workspace.jsonl", "a", encoding="utf-8") as f:
                f.write(_json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            logger.debug("Audit write failed for %s/%s", agent_id, action)

    # ── Lifecycle ─────────────────────────────────────────────

    async def startup(self) -> None:
        """Start all previously-connected agents."""
        for agent_id, cfg in list(self._agent_configs.items()):
            if not cfg.get("enabled", True):
                continue
            flow = self.get_oauth_flow(agent_id)
            if flow and flow.is_authenticated:
                self._connected_agents.add(agent_id)
                self._inject_tools(agent_id)
                logger.info(
                    "Google Workspace [%s]: restored connection (%s)",
                    agent_id, cfg.get("connected_email", ""),
                )

        if self._connected_agents and not self._polling_registered:
            self._register_pollers()
            self._polling_registered = True

        if not self._connected_agents:
            logger.info("Google Workspace: no agents with active connections")

    async def shutdown(self) -> None:
        self._connected_agents.clear()
        self._oauth_flows.clear()
        self._pending_oauth.clear()
