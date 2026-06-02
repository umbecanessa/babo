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
