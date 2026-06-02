"""WhatsApp Channel -- Bundled NLS skill for WhatsApp integration.

Uses a Baileys Node.js bridge for QR-code pairing with a personal
WhatsApp account (OpenFlow mode).  The bridge runs as a sidecar
process managed by the SkillBridge system.

Features:
  - QR code pairing (scan with WhatsApp mobile)
  - Outbound messaging via Baileys bridge
  - Inbound webhook from bridge
  - DM/group policy enforcement
"""

from nls.skills import ConfigField, SkillBridge, SkillMeta, SkillOnboarding, SkillWebhook

meta = SkillMeta(
    name="whatsapp-channel",
    version="2.2",
    description="WhatsApp integration via Baileys QR pairing with personal account support",
    dependencies=["httpx"],
    onboarding=SkillOnboarding(
        setup_type="qr_pair",
        intro_message="Scan this QR code with your WhatsApp to connect your agent.",
        setup_prompt=(
            "After WhatsApp pairing completes (QR code scanned), guide the user:\n"
            "1. Confirm the pairing succeeded and tell them the linked phone number\n"
            "2. Call skill_configure(skill_name='whatsapp-channel') to check what else "
            "needs configuring, then ask the user for the missing fields.\n"
            "3. Confirm everything is set up."
        ),
        completion_event="channel_connected",
    ),
    bridges=[SkillBridge(
        name="baileys",
        runtime="node",
        entry="bridge/index.js",
        port=9223,
        health_check="/health",
    )],
    webhooks=[
        SkillWebhook(
            channel="whatsapp",
            local_path="/skills/whatsapp-channel/webhook/{agent_id}",
        ),
    ],
    config_schema=[
        ConfigField(
            key="linked_phone", type="string",
            description="Phone number paired via QR (set automatically during pairing)",
            category="connection",
        ),
        ConfigField(
            key="owner_identity", type="string", required=True,
            description="Owner's personal WhatsApp phone number",
            category="identity",
        ),
        ConfigField(
            key="dm_policy", type="choice", required=True,
            default="open",
            options=["open", "allowlist", "disabled"],
            description="Who can message the bot in DMs",
            category="policy",
        ),
        ConfigField(
            key="allow_from", type="list", default=[],
            description="Allowed sender phone numbers (used when dm_policy=allowlist)",
            category="policy",
        ),
    ],
)


def register(app, ctx):
    from .adapter import WhatsAppAdapter
    from .webhook import router

    global_config = ctx.load_config(defaults={
        "enabled": True,
        "mode": "baileys",
        "bridge_url": "http://localhost:9223",
        "dm_policy": "open",
        "allow_from": [],
        "group_policy": "open",
        "groups": {
            "*": {"require_mention": True},
        },
        "linked_phone": "",
        "dedicated_number": "",
    })

    adapter = WhatsAppAdapter(global_config=global_config, ctx=ctx)
    ctx.adapter = adapter

    ctx.include_router(router, prefix="/skills/whatsapp-channel")
    ctx.register_tool_factory(adapter.create_send_tool)
    ctx.on_startup(adapter.startup)
    ctx.on_shutdown(adapter.shutdown)
