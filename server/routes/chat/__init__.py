"""Chat route package — WebSocket + REST endpoints.

Modules:
    ws_handler  — main ``websocket_chat`` WebSocket handler
    endpoints   — REST endpoints (list_sessions, chat_relay, etc.)
    helpers     — pure helper functions & constants
    history     — conversation history persistence
    commands    — slash-command handler
    agentic     — agentic loop runner with concurrent WS receive
"""

from fastapi import APIRouter

from .endpoints import router as _rest_router
from .ws_handler import websocket_chat

router = APIRouter(tags=["chat"])

# Mount REST endpoints
router.include_router(_rest_router)

# Mount WebSocket endpoint
router.websocket("/ws/chat/{agent_id}")(websocket_chat)

__all__ = ["router"]
