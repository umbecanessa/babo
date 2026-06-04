"""Slack Channel — bundled NLS skill for Slack workspace integration."""

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
    name="slack-channel",
    version="1.0",
    description=(
        "Slack bot integration with scoped channels, app_mention gating, "
        "DM policy, and two-way channel sync via Events API"
    ),
    dependencies=["httpx"],
    onboarding=SkillOnboarding(
        setup_type="conversational",
        intro_message=(
            "Let's connect Slack! You'll need a bot token and signing secret "
            "from your Slack app, plus Event Subscriptions pointed at Babo Cloud."
        ),
        setup_prompt=(
            "Guide the user through Slack setup:\n"
            "1. Create a Slack app at api.slack.com/apps\n"
            "2. Add bot scopes: app_mentions:read, chat:write, channels:history, im:history\n"
            "3. Enable Event Subscriptions — Request URL is shown in Tools after token setup\n"
            "4. Ask for bot token (xoxb-…) and signing secret, then call slack_setup\n"
            "5. Call skill_configure(skill_name='slack-channel') for owner and interaction policy\n"
            f"6. {INTERACTION_SETUP_HINT}\n"
            "Users can also /invite @App in Slack — channel scope syncs back here."
        ),
        completion_event="channel_connected",
    ),
    webhooks=[
        SkillWebhook(
            channel="slack",
            local_path="/skills/slack-channel/webhook/{agent_id}",
        ),
    ],
    contacts=ContactChannelSpec(
        channel_key="slack",
        display_name="Slack",
        identity_fields=[
            ContactIdentityField(
                key="slack_id",
                description="Slack user ID (U…)",
                required_for_outbound=True,
            ),
        ],
        supports_groups=True,
    ),
    config_schema=[
        ConfigField(
            key="bot_token", type="secret", required=True,
            description="Slack bot token (xoxb-…)",
            category="connection",
        ),
        ConfigField(
            key="signing_secret", type="secret", required=True,
            description="Slack app signing secret (Events API verification)",
            category="connection",
        ),
        ConfigField(
            key="owner_identity", type="string", required=True,
            description="Owner Slack user ID or @handle",
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
            description="Allowed sender IDs (dm_policy=allowlist)",
            category="policy",
        ),
    ],
)


def register(app, ctx):
    from .adapter import SlackAdapter
    from .webhook import router

    global_config = ctx.load_config(defaults={
        "enabled": True,
        "bot_token": "",
        "signing_secret": "",
        "dm_policy": "disabled",
        "allow_from": [],
        "scoped_channels": {"guilds": {}, "channels": {}},
        "groups": {"__none__": {"require_mention": True, "allow_from": []}},
        "mention_patterns": [],
    })

    adapter = SlackAdapter(global_config=global_config, ctx=ctx)
    ctx.adapter = adapter

    ctx.include_router(router, prefix="/skills/slack-channel")
    ctx.register_tool_factory(adapter.create_send_tool)
    ctx.register_tool_factory(adapter.create_setup_tool)
    ctx.on_startup(adapter.startup)
    ctx.on_shutdown(adapter.shutdown)
