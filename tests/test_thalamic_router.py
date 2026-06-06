"""Thalamic router engagement depth for agent events."""

from nls.engine.events import AgentEvent, EngagementDepth, EventType
from nls.engine.thalamic_router import ThalamicRouter


def test_ws_user_message_is_drop_not_second_deep_loop():
    router = ThalamicRouter()
    event = AgentEvent(
        type=EventType.USER_MESSAGE,
        source="ws",
        payload={"user_input": "Your name is Babo"},
    )
    assert (
        router.route(event, deep_slot_busy=False)
        == EngagementDepth.DROP
    )
    assert (
        router.route(event, deep_slot_busy=True)
        == EngagementDepth.DROP
    )


def test_direct_channel_message_uses_focus_when_deep_busy():
    router = ThalamicRouter()
    event = AgentEvent(
        type=EventType.CHANNEL_MESSAGE,
        source="telegram",
        payload={
            "user_input": "[User via Telegram]: @bot do you read me?",
            "user_direct": True,
        },
    )
    assert (
        router.route(event, deep_slot_busy=True)
        == EngagementDepth.FOCUS
    )


def test_ambient_channel_message_can_defer_when_deep_busy():
    router = ThalamicRouter()
    event = AgentEvent(
        type=EventType.CHANNEL_MESSAGE,
        source="telegram",
        payload={
            "user_input": "[User via Telegram]: please refactor the auth module",
            "user_direct": False,
        },
    )
    assert (
        router.route(event, deep_slot_busy=True)
        == EngagementDepth.DEFER
    )
