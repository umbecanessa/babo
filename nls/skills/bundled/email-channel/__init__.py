"""Email Channel -- Bundled NLS skill for email integration via Resend.

Supports two modes:
  - **Conversation**: threaded email replies treated as chat
  - **Content ingestion**: newsletters, forwards, and links
    routed to the ANS study pipeline

Outbound and inbound via Resend API.  Server-level env vars
``RESEND_API_KEY`` and ``RESEND_INBOUND_DOMAIN`` must be set.
"""

from nls.skills import ConfigField, SkillMeta, SkillOnboarding, SkillWebhook

meta = SkillMeta(
    name="email-channel",
    version="2.2",
    description="Email integration via Resend with auto-provisioned aliases, newsletter detection, and content ingestion",
    dependencies=["httpx"],
    onboarding=SkillOnboarding(
        setup_type="auto",
        intro_message="Your agent now has a personal email address! Forward newsletters here or share it with contacts.",
        setup_prompt=(
            "After the email alias is provisioned, guide the user:\n"
            "1. Tell them their agent's new email address\n"
            "2. Call skill_configure(skill_name='email-channel') to check what else "
            "needs configuring, then ask the user for the missing fields.\n"
            "3. Confirm everything is set up."
        ),
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
