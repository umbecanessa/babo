"""Discord Channel -- Bundled NLS skill for Discord integration.

Provides:
  - Real-time Gateway bot connection via discord.py
  - Inbound message processing with mention/command detection
  - Outbound send tool for the agent
  - Group/server support with role/channel permissions
  - DM policy enforcement (open / allowlist / disabled)
  - Conversational onboarding to guide user through setup

Architecture:
  - Uses discord.py asyncio Bot (WebSocket Gateway, not REST polling)
  - Messages normalized and forwarded to AgentRuntime.process_message_agentic_async
  - Agent tools registered for moderation, channel management, and messaging
"""

from nls.skills import ConfigField, SkillMeta, SkillOnboarding

meta = SkillMeta(
    name="discord-channel",
    version="1.0",
    description=(
        "Real-time Discord bot integration with moderation, channel management, "
        "and mention-gated group communication"
    ),
    dependencies=["discord.py"],
    onboarding=SkillOnboarding(
        setup_type="conversational",
        intro_message=(
            "Let's connect your Discord bot! I'll walk you through getting "
            "your bot token and configuring everything -- just a couple steps."
        ),
        setup_prompt=(
            "Guide the user through connecting Discord. Be friendly and concise:\n"
            "1. Tell them their bot must already be created in Discord Developer Portal\n"
            "2. Ask them to paste the bot token (looks like MTA...xyz.AbC...defG)\n"
            "3. Once they paste it, call discord_setup_tool with that token to validate\n"
            "4. After validation succeeds, ask for owner_identity and dm_policy\n"
            "5. Confirm setup is complete and the bot will join servers on next launch.\n"
            "If the token is invalid, ask them to double-check their bot settings."
        ),
        completion_event="channel_connected",
    ),
    config_schema=[
        ConfigField(
            key="bot_token", type="secret", required=True,
            description="Discord bot token from Developer Portal",
            category="connection",
        ),
        ConfigField(
            key="owner_identity", type="string", required=True,
            description="Owner's Discord username (without #) or ID",
            category="identity",
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
            description="Allowed sender usernames/IDs (used when dm_policy=allowlist)",
            category="policy",
        ),
        ConfigField(
            key="guild_id", type="string", required=False,
            description="Discord Guild (server) ID to auto-join (optional)",
            category="connection",
        ),
        ConfigField(
            key="mod_roles", type="list", default=["Administrator"],
            description="Role names that grant admin-level moderation capabilities",
            category="permissions",
        ),
        ConfigField(
            key="mention_pattern", type="string", default="Babo",
            description="Name to detect mentions in groups (e.g. @Babo)",
            category="interaction",
        ),
        ConfigField(
            key="prefix_commands", type="boolean", default=True,
            description="Enable !help, !ban, !kick style prefix commands",
            category="interaction",
        ),
        ConfigField(
            key="auto_moderate", type="boolean", default=True,
            description="Auto-detect spam, hate speech, and abusive content",
            category="moderation",
        ),
    ],
)


def register(app, ctx):
    from .adapter import DiscordAdapter

    global_config = ctx.load_config(defaults={
        "enabled": True,
        "bot_token": "",
        "dm_policy": "disabled",
        "allow_from": [],
        "mod_roles": ["Administrator"],
        "mention_pattern": "Babo",
        "prefix_commands": True,
        "auto_moderate": True,
    })

    adapter = DiscordAdapter(global_config=global_config, ctx=ctx)
    ctx.adapter = adapter

    # Mount local skill webhook router (for NestJS relay)
    from . import webhook
    ctx.include_router(webhook.router, prefix="/skills/discord-channel")

    # Register agent tools
    ctx.register_tool_factory(adapter.create_send_tool)
    ctx.register_tool_factory(adapter.create_setup_tool)
    ctx.on_startup(adapter.startup)
    ctx.on_shutdown(adapter.shutdown)
