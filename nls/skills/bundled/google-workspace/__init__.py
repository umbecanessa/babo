"""Google Workspace -- Bundled NLS skill for Google Workspace integration.

Provides OAuth-based access to the user's Gmail, Calendar, Drive, and
Sheets via Google's "Installed Application" OAuth flow.

On Babo Cloud (``NESTJS_URL`` → api.babo.agency), ships app-owned
credentials so users only sign in.  On self-hosted NestJS backends,
users must supply their own Google Cloud OAuth client.

Complements the email-channel skill:
  - email-channel (Resend) = Agent's own email identity
  - google-workspace (Gmail) = User's email identity
"""

from __future__ import annotations

import os

from nls.skills import ConfigField, SkillMeta, SkillOnboarding, SkillWebhook

_BUILTIN_CLIENT_ID = (
    "128046609738-nrvv462e01so6qtcs6omb2ibdet32q5q.apps.googleusercontent.com"
)
_BUILTIN_CLIENT_SECRET = "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"

_BABO_CLOUD_SETUP_PROMPT = (
    "To connect Google Workspace, call google_workspace_connect(action='connect'). "
    "This opens a connection dialog in the app where the user signs in and "
    "authorizes access directly — you do NOT need to open a browser, give "
    "manual instructions, or create a multi-step plan.\n\n"
    "After calling connect, wait for the user to complete authorization. "
    "Then call google_workspace_connect(action='status') to verify the "
    "connection is active and confirm to the user."
)

_BYO_SETUP_PROMPT = (
    "Guide the user through Google Workspace BYO OAuth setup. Be concise:\n"
    "1. Explain they need a Google Cloud project (not Babo-provided on self-hosted).\n"
    "2. Enable Gmail, Calendar, Drive, and Sheets APIs in Google Cloud Console.\n"
    "3. Create OAuth 2.0 credentials (Desktop app or Web application).\n"
    "4. Add redirect URI: http://localhost:9222/skills/google-workspace/oauth/callback "
    "(use the runtime port from PORT / NLS_SERVE_PORT if different).\n"
    "5. Call google_workspace_connect(action='save_credentials', client_id=..., "
    "client_secret=...) with the values they provide.\n"
    "6. Then call google_workspace_connect(action='connect') to open the sign-in dialog.\n"
    "7. After authorization, call action='status' and confirm connected_email."
)


def babo_cloud_backend_from_env() -> bool:
    """True when the desktop points at Babo Cloud (built-in OAuth allowed)."""
    url = (os.environ.get("NESTJS_URL") or "").strip().lower().rstrip("/")
    if not url:
        return True  # default product URL is Babo Cloud
    return "api.babo.agency" in url


def _onboarding_for_backend() -> SkillOnboarding:
    if babo_cloud_backend_from_env():
        return SkillOnboarding(
            setup_type="ui",
            intro_message=(
                "Let's connect your Google account! A connection dialog will "
                "open for you to sign in and authorize access."
            ),
            setup_prompt=_BABO_CLOUD_SETUP_PROMPT,
            completion_event="channel_connected",
        )
    return SkillOnboarding(
        setup_type="conversational",
        intro_message=(
            "Let's connect Google Workspace with your own Google Cloud OAuth app. "
            "I'll walk you through creating credentials, then we can sign in."
        ),
        setup_prompt=_BYO_SETUP_PROMPT,
        completion_event="channel_connected",
    )


meta = SkillMeta(
    name="google-workspace",
    version="0.2",
    description=(
        "Google Workspace integration -- OAuth-based access to the user's "
        "Gmail, Calendar, Drive, and Sheets"
    ),
    dependencies=[
        "google-auth",
        "google-auth-oauthlib",
        "google-api-python-client",
        "cryptography",
    ],
    onboarding=_onboarding_for_backend(),
    webhooks=[
        SkillWebhook(
            channel="google-workspace",
            local_path="/skills/google-workspace/oauth/callback",
        ),
    ],
    config_schema=[
        ConfigField(
            key="client_id", type="secret", required=False,
            description="Google OAuth Client ID (Babo Cloud built-in; required BYO on self-hosted)",
            category="connection",
        ),
        ConfigField(
            key="client_secret", type="secret", required=False,
            description="Google OAuth Client Secret (Babo Cloud built-in; required BYO on self-hosted)",
            category="connection",
        ),
        ConfigField(
            key="connected_email", type="string",
            description="Connected Google account email (set after OAuth)",
            category="connection",
        ),
        ConfigField(
            key="gmail_access", type="choice", default="read_write",
            options=["read_write", "read_only", "disabled"],
            description="Gmail access level",
            category="gmail",
        ),
        ConfigField(
            key="gmail_poll_interval", type="number", default=120,
            description="Gmail inbox check interval in seconds (0 = disabled)",
            category="gmail",
        ),
        ConfigField(
            key="calendar_access", type="choice", default="read_write",
            options=["read_write", "read_only", "disabled"],
            description="Calendar access level",
            category="calendar",
        ),
        ConfigField(
            key="calendar_poll_interval", type="number", default=300,
            description="Calendar check interval in seconds (0 = disabled)",
            category="calendar",
        ),
        ConfigField(
            key="drive_access", type="choice", default="read_only",
            options=["read_only", "disabled"],
            description="Google Drive access level",
            category="drive",
        ),
        ConfigField(
            key="drive_folders", type="list", default=[],
            description="Allowed Drive folder IDs (empty = all accessible)",
            category="drive",
        ),
        ConfigField(
            key="sheets_access", type="choice", default="read_write",
            options=["read_write", "read_only", "disabled"],
            description="Sheets access level",
            category="sheets",
        ),
        ConfigField(
            key="require_confirmation", type="boolean", default=True,
            description="Require user confirmation before sending emails or modifying data",
            category="policy",
        ),
    ],
)


def register(app, ctx):
    from .adapter import GoogleWorkspaceAdapter
    from .webhook import router

    meta.onboarding = _onboarding_for_backend()
    use_babo_oauth = babo_cloud_backend_from_env()

    defaults: dict = {
        "enabled": True,
        "client_id": _BUILTIN_CLIENT_ID if use_babo_oauth else "",
        "client_secret": _BUILTIN_CLIENT_SECRET if use_babo_oauth else "",
        "connected_email": "",
        "gmail_access": "read_write",
        "gmail_poll_interval": 120,
        "calendar_access": "read_write",
        "calendar_poll_interval": 300,
        "drive_access": "read_only",
        "drive_folders": [],
        "sheets_access": "read_write",
        "require_confirmation": True,
    }

    global_config = ctx.load_config(defaults=defaults)

    if use_babo_oauth:
        if not global_config.get("client_id") or not global_config.get("client_secret"):
            global_config["client_id"] = _BUILTIN_CLIENT_ID
            global_config["client_secret"] = _BUILTIN_CLIENT_SECRET
            ctx.save_config(global_config)
    else:
        # Self-hosted: do not keep Babo-operated OAuth credentials in config.
        if global_config.get("client_id") == _BUILTIN_CLIENT_ID:
            global_config["client_id"] = ""
            global_config["client_secret"] = ""
            ctx.save_config(global_config)

    adapter = GoogleWorkspaceAdapter(global_config=global_config, ctx=ctx)
    ctx.adapter = adapter

    ctx.include_router(router, prefix="/skills/google-workspace")
    ctx.register_tool_factory(adapter.create_connect_tool)
    ctx.on_startup(adapter.startup)
    ctx.on_shutdown(adapter.shutdown)
