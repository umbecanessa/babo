"""Google Workspace -- Bundled NLS skill for Google Workspace integration.

Provides OAuth-based access to the user's Gmail, Calendar, Drive, and
Sheets via Google's "Installed Application" OAuth flow.  Ships with
app-owned credentials so users only need to sign in and authorize —
no Google Cloud project setup required.

Complements the email-channel skill:
  - email-channel (Resend) = Agent's own email identity
  - google-workspace (Gmail) = User's email identity
"""

from nls.skills import ConfigField, SkillMeta, SkillOnboarding, SkillWebhook

# ---------------------------------------------------------------------------
# Setup prompt — UI-driven OAuth
# ---------------------------------------------------------------------------
# The OAuth flow is handled entirely by the frontend modal.  The agent
# only needs to call google_workspace_connect(action='connect') which
# opens the connection dialog in the UI.  No browser tool or step-by-step
# instructions needed.
# ---------------------------------------------------------------------------

_SETUP_PROMPT = (
    "To connect Google Workspace, call google_workspace_connect(action='connect'). "
    "This opens a connection dialog in the app where the user signs in and "
    "authorizes access directly — you do NOT need to open a browser, give "
    "manual instructions, or create a multi-step plan.\n\n"
    "After calling connect, wait for the user to complete authorization. "
    "Then call google_workspace_connect(action='status') to verify the "
    "connection is active and confirm to the user."
)


meta = SkillMeta(
    name="google-workspace",
    version="0.1",
    description=(
        "Google Workspace integration -- OAuth-based access to the user's "
        "Gmail, Calendar, Drive, and Sheets with built-in credentials"
    ),
    dependencies=["google-auth", "google-auth-oauthlib", "google-api-python-client", "cryptography"],
    onboarding=SkillOnboarding(
        setup_type="ui",
        intro_message=(
            "Let's connect your Google account! A connection dialog will "
            "open for you to sign in and authorize access."
        ),
        setup_prompt=_SETUP_PROMPT,
        completion_event="channel_connected",
    ),
    webhooks=[
        SkillWebhook(
            channel="google-workspace",
            local_path="/skills/google-workspace/oauth/callback",
        ),
    ],
    config_schema=[
        ConfigField(
            key="client_id", type="secret", required=False,
            description="Google OAuth Client ID (built-in; override for custom credentials)",
            category="connection",
        ),
        ConfigField(
            key="client_secret", type="secret", required=False,
            description="Google OAuth Client Secret (built-in; override for custom credentials)",
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


_BUILTIN_CLIENT_ID = "128046609738-nrvv462e01so6qtcs6omb2ibdet32q5q.apps.googleusercontent.com"
_BUILTIN_CLIENT_SECRET = "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"


def register(app, ctx):
    from .adapter import GoogleWorkspaceAdapter
    from .webhook import router

    global_config = ctx.load_config(defaults={
        "enabled": True,
        "client_id": _BUILTIN_CLIENT_ID,
        "client_secret": _BUILTIN_CLIENT_SECRET,
        "connected_email": "",
        "gmail_access": "read_write",
        "gmail_poll_interval": 120,
        "calendar_access": "read_write",
        "calendar_poll_interval": 300,
        "drive_access": "read_only",
        "drive_folders": [],
        "sheets_access": "read_write",
        "require_confirmation": True,
    })

    # Migration: existing config.json may have empty credentials from
    # before built-in defaults were added.  Backfill if empty.
    if not global_config.get("client_id") or not global_config.get("client_secret"):
        global_config["client_id"] = _BUILTIN_CLIENT_ID
        global_config["client_secret"] = _BUILTIN_CLIENT_SECRET
        ctx.save_config(global_config)

    adapter = GoogleWorkspaceAdapter(global_config=global_config, ctx=ctx)
    ctx.adapter = adapter

    ctx.include_router(router, prefix="/skills/google-workspace")
    ctx.register_tool_factory(adapter.create_connect_tool)
    ctx.on_startup(adapter.startup)
    ctx.on_shutdown(adapter.shutdown)
