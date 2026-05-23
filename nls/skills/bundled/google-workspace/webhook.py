"""Google Workspace webhook routes -- OAuth flow and status endpoints.

Routes:
  POST /connect/{agent_id}   -- Initiate OAuth flow (returns auth URL)
  GET  /status/{agent_id}    -- Connection status
  GET  /oauth/callback       -- Google OAuth redirect handler
  POST /disconnect/{agent_id} -- Revoke access and disconnect
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_adapter(request: Request) -> Any:
    """Retrieve the GoogleWorkspaceAdapter from the app state (set by skill loader)."""
    from server.services.skill_loader import SkillLoader
    loader: SkillLoader | None = getattr(request.app.state, "skill_loader", None)
    if loader is None:
        raise HTTPException(status_code=503, detail="Skill loader not available")
    skill = loader.skills.get("google-workspace")
    if skill is None or skill.context is None or skill.context.adapter is None:
        raise HTTPException(status_code=503, detail="Google Workspace skill not loaded")
    return skill.context.adapter


@router.post("/connect/{agent_id}")
async def connect(agent_id: str, request: Request) -> dict[str, Any]:
    """Initiate the Google OAuth flow. Returns the authorization URL."""
    adapter = _get_adapter(request)
    flow = adapter.get_oauth_flow(agent_id)
    if flow is None:
        raise HTTPException(
            status_code=400,
            detail="client_id and client_secret not configured for this agent",
        )
    if flow.is_authenticated:
        cfg = adapter._agent_cfg(agent_id)
        return {
            "connected": True,
            "email": cfg.get("connected_email", ""),
            "message": "Already connected",
        }

    redirect_uri = adapter.get_redirect_uri()
    state = adapter.create_oauth_state(agent_id)
    auth_url = flow.get_auth_url(redirect_uri, state=state)
    adapter._pending_oauth[agent_id] = flow
    return {"auth_url": auth_url, "message": "Open this URL to authorize"}


@router.get("/status/{agent_id}")
async def status(agent_id: str, request: Request) -> dict[str, Any]:
    adapter = _get_adapter(request)
    return adapter.get_status(agent_id)


@router.get("/oauth/callback")
async def oauth_callback(request: Request) -> HTMLResponse:
    """Handle the Google OAuth redirect with the authorization code."""
    code = request.query_params.get("code", "")
    error = request.query_params.get("error", "")

    if error:
        logger.warning("Google OAuth error: %s", error)
        return HTMLResponse(
            content=_html_result(
                "Authorization Failed",
                f"Google returned an error: {error}. Please try again.",
                success=False,
            ),
            status_code=400,
        )

    if not code:
        return HTMLResponse(
            content=_html_result(
                "Missing Code",
                "No authorization code received. Please try again.",
                success=False,
            ),
            status_code=400,
        )

    adapter = _get_adapter(request)
    state = request.query_params.get("state", "")

    # Resolve agent_id from state token (CSRF-safe)
    agent_id = adapter.resolve_oauth_state(state) if state else None

    if agent_id is None:
        # Fallback for flows started before state was implemented
        for aid in list(adapter._pending_oauth.keys()):
            agent_id = aid
            break

    if agent_id is None:
        return HTMLResponse(
            content=_html_result(
                "Invalid Session",
                "Could not determine which agent initiated the OAuth flow. "
                "The state token may have expired. Please try connecting again.",
                success=False,
            ),
            status_code=400,
        )

    try:
        result = await adapter.complete_oauth(agent_id, code)
        email = result.get("email", "")
        return HTMLResponse(
            content=_html_result(
                "Connected!",
                f"Successfully connected as {email}. You can close this window.",
                success=True,
            ),
        )
    except Exception as exc:
        logger.error("OAuth callback failed for %s: %s", agent_id, exc, exc_info=True)
        return HTMLResponse(
            content=_html_result(
                "Connection Failed",
                f"Error: {exc}. Please try again.",
                success=False,
            ),
            status_code=500,
        )


@router.post("/disconnect/{agent_id}")
async def disconnect(agent_id: str, request: Request) -> dict[str, Any]:
    adapter = _get_adapter(request)
    flow = adapter.get_oauth_flow(agent_id)
    if flow:
        await flow.revoke()
    adapter._oauth_flows.pop(agent_id, None)
    adapter.update_config({"connected_email": ""}, agent_id=agent_id)
    adapter._connected_agents.discard(agent_id)
    adapter._strip_tools(agent_id)
    return {"disconnected": True}


def _html_result(title: str, message: str, success: bool = True) -> str:
    color = "#4caf50" if success else "#f44336"
    icon = "&#10004;" if success else "&#10006;"
    return f"""<!DOCTYPE html>
<html><head><title>{title}</title>
<style>
body {{ font-family: -apple-system, sans-serif; display: flex;
       justify-content: center; align-items: center; min-height: 100vh;
       margin: 0; background: #1a1a2e; color: #eee; }}
.card {{ background: #16213e; border-radius: 12px; padding: 2rem 3rem;
         text-align: center; box-shadow: 0 4px 24px rgba(0,0,0,0.3); }}
.icon {{ font-size: 3rem; color: {color}; }}
h1 {{ margin: 0.5rem 0; }}
p {{ color: #aaa; }}
</style></head>
<body><div class="card">
  <div class="icon">{icon}</div>
  <h1>{title}</h1>
  <p>{message}</p>
</div></body></html>"""
