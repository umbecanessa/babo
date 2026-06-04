"""Telegram Channel -- Bundled NLS skill for Telegram integration.

Provides:
  - Inbound webhook (via NestJS relay) or long-polling fallback
  - Outbound send tool for the agent
  - Group chat support with mention detection
  - DM / group policy enforcement
  - Conversational onboarding via @BotFather
"""

from nls.skills import ConfigField, SkillMeta, SkillOnboarding, SkillWebhook
from nls.runtime.interaction_policy import INTERACTION_SETUP_HINT

meta = SkillMeta(
    name="telegram-channel",
    version="2.2",
    description="Telegram bot integration with group support, mention gating, and policy enforcement",
    dependencies=["httpx"],
    onboarding=SkillOnboarding(
        setup_type="conversational",
        intro_message=(
            "Let's connect your Telegram bot! I'll walk you through it "
            "step by step -- it only takes a minute."
        ),
        setup_prompt=(
            "Guide the user through connecting Telegram. Be friendly and concise:\n"
            "1. Tell them to open Telegram and search for @BotFather\n"
            "2. Tell them to send /newbot and follow the prompts to name their bot\n"
            "3. Ask them to paste the bot token they receive (it looks like 123456:ABC-DEF...)\n"
            "4. Once they paste it, call the telegram_setup tool with that token to validate and save it\n"
            "5. After setup succeeds, call skill_configure(skill_name='telegram-channel') "
            "for owner_identity and interaction policy\n"
            f"6. {INTERACTION_SETUP_HINT}\n"
            "If the token is invalid, ask them to try again."
        ),
        completion_event="channel_connected",
    ),
    webhooks=[
        SkillWebhook(
            channel="telegram",
            local_path="/skills/telegram-channel/webhook/{agent_id}",
        ),
    ],
    config_schema=[
        ConfigField(
            key="bot_token", type="secret", required=True,
            description="Bot token from @BotFather",
            category="connection",
        ),
        ConfigField(
            key="owner_identity", type="string", required=True,
            description="Owner's Telegram username (without @)",
            category="identity",
        ),
        ConfigField(
            key="dm_policy", type="choice", required=True,
            default="open",
            options=["open", "allowlist", "disabled"],
            description="Who can DM the bot",
            category="policy",
        ),
        ConfigField(
            key="allow_from", type="list", default=[],
            description="Allowed sender IDs (used when dm_policy=allowlist)",
            category="policy",
        ),
    ],
)


def register(app, ctx):
    from .adapter import TelegramAdapter
    from .webhook import router

    global_config = ctx.load_config(defaults={
        "enabled": True,
        "bot_token": "",
        "dm_policy": "open",
        "allow_from": [],
        "groups": {
            "*": {"require_mention": True},
        },
        "mention_patterns": [],
        "history_limit": 50,
    })

    adapter = TelegramAdapter(global_config=global_config, ctx=ctx)
    ctx.adapter = adapter

    ctx.include_router(router, prefix="/skills/telegram-channel")
    ctx.register_tool_factory(adapter.create_send_tool)
    ctx.register_tool_factory(adapter.create_setup_tool)
    ctx.on_startup(adapter.startup)
    ctx.on_shutdown(adapter.shutdown)
