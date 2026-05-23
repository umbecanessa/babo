"""Authentication middleware for the NLS server.

Two auth paths:
1. **Backend shared secret** — NestJS backend includes
   ``X-Runtime-Secret: {shared_secret}`` header.
2. **User API keys** — Direct API access via
   ``Authorization: Bearer nlsk_...`` header.

Health and model listing endpoints are public (no auth required).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)
runtime_secret_header = APIKeyHeader(name="X-Runtime-Secret", auto_error=False)


async def verify_auth(
    request: Request,
    authorization: str | None = Security(api_key_header),
    runtime_secret: str | None = Security(runtime_secret_header),
) -> dict[str, Any]:
    """Verify request authentication.

    Returns a dict with auth info:
        {"auth_type": "shared_secret" | "api_key", "agent_id": str | None}

    Raises HTTPException 401 if auth fails.
    """
    settings = request.app.state.settings

    if runtime_secret and settings.shared_secret:
        if runtime_secret == settings.shared_secret:
            return {"auth_type": "shared_secret", "agent_id": None}
        raise HTTPException(status_code=401, detail="Invalid shared secret")

    if authorization:
        token = authorization
        if token.startswith("Bearer "):
            token = token[7:]

        if token.startswith(settings.api_key_prefix):
            agent_id = request.app.state.agent_manager.validate_api_key(token)
            if agent_id:
                return {"auth_type": "api_key", "agent_id": agent_id}
            raise HTTPException(status_code=401, detail="Invalid API key")

    raise HTTPException(status_code=401, detail="Authentication required")
