"""Discord squad peer bot inbound — lead @mention tests member bots."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

DiscordAdapter = importlib.import_module(
    "nls.skills.bundled.discord-channel.adapter",
).DiscordAdapter


def _adapter_with_agents(
    configs: dict[str, dict],
    bot_ids: dict[str, str] | None = None,
) -> DiscordAdapter:
    ctx = MagicMock()
    adapter = DiscordAdapter({}, ctx)
    adapter._agent_configs = configs
    adapter._bot_ids = bot_ids or {
        aid: str(cfg.get("bot_id", ""))
        for aid, cfg in configs.items()
        if cfg.get("bot_id")
    }
    return adapter


def test_squad_peer_mention_passes_normalize():
    adapter = _adapter_with_agents({
        "lead": {"bot_id": "111"},
        "mod": {
            "bot_id": "222",
            "scoped_channels": {
                "guilds": {},
                "channels": {
                    "ch1": {
                        "id": "ch1",
                        "effective_enabled": True,
                        "require_mention": True,
                    },
                },
            },
        },
    })
    squad = SimpleNamespace(all_member_ids=["lead", "mod"])

    with patch.object(adapter, "_squad_peer_bot_ids", return_value={"111"}):
        msg = {
            "content": "<@222> ping",
            "channel_id": "ch1",
            "guild_id": "g1",
            "author": {"id": "111", "username": "Babo", "bot": True},
            "mentions": [{"id": "222"}],
        }
        normalized = adapter.normalize_gateway_message(msg, "mod")
        assert normalized is not None
        assert normalized["sender_id"] == "111"
        assert normalized["is_mention"] is True


def test_random_bot_mention_still_dropped():
    adapter = _adapter_with_agents({
        "mod": {"bot_id": "222"},
    })
    with patch.object(adapter, "_squad_peer_bot_ids", return_value=set()):
        msg = {
            "content": "<@222> spam",
            "channel_id": "ch1",
            "guild_id": "g1",
            "author": {"id": "999", "username": "OtherBot", "bot": True},
            "mentions": [{"id": "222"}],
        }
        assert adapter.normalize_gateway_message(msg, "mod") is None


def test_squad_peer_no_mention_allowed_when_channel_open():
    adapter = _adapter_with_agents({
        "lead": {"bot_id": "111"},
        "mod": {
            "bot_id": "222",
            "scoped_channels": {
                "guilds": {},
                "channels": {
                    "team": {
                        "id": "team",
                        "effective_enabled": True,
                        "require_mention": False,
                    },
                },
            },
        },
    })
    with patch.object(adapter, "_squad_peer_bot_ids", return_value={"111"}):
        msg = {
            "content": "status check",
            "channel_id": "team",
            "guild_id": "g1",
            "author": {"id": "111", "username": "Babo", "bot": True},
            "mentions": [],
        }
        normalized = adapter.normalize_gateway_message(msg, "mod")
        assert normalized is not None
        assert adapter._bot_inbound_allowed(msg, "mod") is True


def test_human_author_unchanged():
    adapter = _adapter_with_agents({"mod": {"bot_id": "222"}})
    msg = {
        "content": "<@222> hello",
        "channel_id": "ch1",
        "guild_id": "g1",
        "author": {"id": "user1", "username": "wasnaga", "bot": False},
        "mentions": [{"id": "222"}],
    }
    normalized = adapter.normalize_gateway_message(msg, "mod")
    assert normalized is not None
    assert normalized["sender_name"] == "wasnaga"
