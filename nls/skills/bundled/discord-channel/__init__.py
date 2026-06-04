"""Discord Channel — bundled NLS skill for Discord integration.

Inbound via NestJS Gateway relay (Babo Cloud / self-hosted NestJS) with
local Gateway fallback when no relay is configured.  Outbound via Discord
REST API.  Channel scope is two-way synced with Discord permissions.
"""

from nls.skills import (
    ConfigField,
    ContactChannelSpec,
    ContactIdentityField,
    SkillMeta,
    SkillOnboarding,
    SkillWebhook,
)
from nls.runtime.interaction_policy import INTERACTION_SETUP_HINT

meta = SkillMeta(
    name="discord-channel",
    version="2.0",
    description=(
        "Discord bot integration with scoped guild channels, mention gating, "
        "DM policy, and two-way channel sync"
    ),
    dependencies=["httpx", "discord.py"],
    onboarding=SkillOnboarding(
        setup_type="conversational",
        intro_message=(
            "Let's connect your Discord bot! Paste your bot token from the "
            "Discord Developer Portal and we'll scope which channels Babo listens in."
        ),
        setup_prompt=(
            "Guide the user through connecting Discord:\n"
            "1. They need a bot token from Discord Developer Portal → Bot → Token\n"
            "2. Ask them to paste the token, then call discord_setup with bot_token\n"
            "3. Call skill_configure(skill_name='discord-channel') for owner_identity and "
            "interaction policy (interaction_mode preset — not raw dm_policy values)\n"
            "4. They can invite the bot to servers/channels in Discord — scope syncs back here\n"
            f"5. {INTERACTION_SETUP_HINT}\n"
            "If invalid token, ask them to reset it in the Developer Portal."
        ),
        completion_event="channel_connected",
    ),
    webhooks=[
        SkillWebhook(
            channel="discord",
            local_path="/skills/discord-channel/webhook/{agent_id}",
        ),
    ],
    contacts=ContactChannelSpec(
        channel_key="discord",
        display_name="Discord",
        identity_fields=[
            ContactIdentityField(
                key="discord_id",
                description="Discord user snowflake ID",
                required_for_outbound=True,
            ),
        ],
        supports_groups=True,
    ),
    config_schema=[
        ConfigField(
            key="bot_token", type="secret", required=True,
            description="Discord bot token from Developer Portal",
            category="connection",
        ),
        ConfigField(
            key="owner_identity", type="string", required=True,
            description="Username without @, or numeric user ID (Developer Mode → Copy User ID)",
            category="identity",
        ),
        ConfigField(
            key="moderator_role_ids", type="list", default=[],
            description=(
                "Server role IDs whose members may use the bot without @mention "
                "(synced from Discord in Tools → Integrations → Discord)"
            ),
            category="policy",
        ),
        ConfigField(
            key="dm_policy", type="choice", required=True,
            default="disabled",
            options=["open", "allowlist", "disabled"],
            description="Who can DM the bot",
            category="policy",
        ),
        ConfigField(
            key="allow_from", type="list", default=[],
            description="Allowed sender IDs/usernames (dm_policy=allowlist)",
            category="policy",
        ),
    ],
)


def register(app, ctx):
    from .adapter import DiscordAdapter
    from .webhook import router

    global_config = ctx.load_config(defaults={
        "enabled": True,
        "bot_token": "",
        "dm_policy": "disabled",
        "allow_from": [],
        "scoped_channels": {"guilds": {}, "channels": {}},
        "moderator_role_ids": [],
        "groups": {"__none__": {"require_mention": True, "allow_from": []}},
        "mention_patterns": [],
    })

    adapter = DiscordAdapter(global_config=global_config, ctx=ctx)
    ctx.adapter = adapter

    ctx.include_router(router, prefix="/skills/discord-channel")
    ctx.register_tool_factory(adapter.create_send_tool)
    ctx.register_tool_factory(adapter.create_setup_tool)
    ctx.on_startup(adapter.startup)
    ctx.on_shutdown(adapter.shutdown)
