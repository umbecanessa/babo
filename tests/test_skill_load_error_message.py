"""skill_install load error enrichment."""

from __future__ import annotations

from pathlib import Path

from nls.skills_setup_policy import format_skill_load_error_message


def test_router_import_error_hint():
    msg = format_skill_load_error_message(
        "discord-channel",
        "cannot import name 'router' from '_nls_skill_discord-channel.webhook'",
        dest=Path("/data/skills/discord-channel"),
    )
    assert "module-level" in msg
    assert "router" in msg
    assert "task_complete" in msg.lower()


def test_infer_pre_shipped_includes_discord_and_slack():
    from nls.skills_setup_policy import infer_pre_shipped_channel_skill

    assert infer_pre_shipped_channel_skill("discord bot token") == "discord-channel"
    assert infer_pre_shipped_channel_skill("slack workspace app") == "slack-channel"
    assert infer_pre_shipped_channel_skill("telegram bot setup") == "telegram-channel"
