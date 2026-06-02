"""Email Channel -- Bundled NLS skill for email integration via Resend.

Supports two modes:
  - **Conversation**: threaded email replies treated as chat
  - **Content ingestion**: newsletters, forwards, and links
    routed to the ANS study pipeline

Resend credentials come from Babo Cloud (server env) or user BYO settings
on self-hosted NestJS (Settings → Integrations or server env vars).
"""

from nls.skills import ConfigField, SkillMeta, SkillOnboarding, SkillWebhook

_EMAIL_SETUP_PROMPT = (
    "Guide the user through email channel setup. Be concise and practical.\n\n"
    "**If Resend is not configured yet** (activation fails or user is self-hosted):\n"
    "1. Explain they need a Resend account, API key, and verified inbound domain.\n"
    "2. Self-hosted: save credentials in Babo Settings → Integrations, OR set "
    "RESEND_API_KEY + RESEND_INBOUND_DOMAIN on their NestJS server.\n"
    "3. Resend inbound webhook must point to: "
    "{nestjs}/api/channels/email/webhook (their public NestJS URL).\n"
    "4. Babo Desktop must stay online so NestJS can relay inbound mail.\n"
    "5. After credentials are saved, tell them to click Activate Email in Tools "
    "or retry activation.\n\n"
    "**After alias is provisioned:**\n"
    "1. Tell them the agent's new email address.\n"
    "2. Call skill_configure(skill_name='email-channel') for owner_identity and DM policy.\n"
    "3. Confirm setup is complete."
)

meta = SkillMeta(
    name="email-channel",
    version="2.3",
    description="Email integration via Resend with auto-provisioned aliases, newsletter detection, and content ingestion",
    dependencies=["httpx"],
    onboarding=SkillOnboarding(
        setup_type="conversational",
        intro_message=(
            "Let's set up your agent's email inbox. I'll help with Resend "
            "configuration if needed, then activate an address for your agent."
        ),
        setup_prompt=_EMAIL_SETUP_PROMPT,
        completion_event="channel_connected",
    ),
    webhooks=[
        SkillWebhook(
            channel="email",
            local_path="/skills/email-channel/webhook/{agent_id}",
        ),
    ],
    config_schema=[
        ConfigField(
            key="alias", type="string",
            description="Provisioned email alias (set automatically during activation)",
            category="connection",
        ),
        ConfigField(
            key="owner_identity", type="list", required=True,
            description="Owner's email address(es)",
            category="identity",
        ),
        ConfigField(
            key="dm_policy", type="choice", required=True,
            default="open",
            options=["open", "allowlist", "disabled"],
            description="Who can email the agent",
            category="policy",
        ),
        ConfigField(
            key="allow_from", type="list", default=[],
            description="Allowed sender emails (used when dm_policy=allowlist)",
            category="policy",
        ),
    ],
)


def register(app, ctx):
    from .adapter import EmailAdapter
    from .webhook import router

    global_config = ctx.load_config(defaults={
        "enabled": True,
        "alias": "",
        "from_address": "",
        "content_ingestion": True,
        "auto_classify": True,
        "allow_from": [],
        "dm_policy": "open",
    })

    adapter = EmailAdapter(global_config=global_config, ctx=ctx)
    ctx.adapter = adapter

    ctx.include_router(router, prefix="/skills/email-channel")
    ctx.register_tool_factory(adapter.create_send_tool)
    ctx.on_startup(adapter.startup)
    ctx.on_shutdown(adapter.shutdown)
