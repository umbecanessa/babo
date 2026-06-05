"""Structured Discord multi-face squad playbook — readiness, grants, OAuth, next steps."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

# View channels, send messages, read history, add reactions, embed links, attach files,
# use external emoji, connect/speak (voice optional), manage channels for grant path.
DEFAULT_BOT_INVITE_PERMISSIONS = 36700544

DISCORD_API = "https://discord.com/api/v10"


def oauth_invite_url(client_id: str, *, permissions: int = DEFAULT_BOT_INVITE_PERMISSIONS) -> str:
    """OAuth2 URL to add a bot application to a guild (human step)."""
    cid = str(client_id or "").strip()
    if not cid:
        return ""
    qs = urlencode({
        "client_id": cid,
        "permissions": str(permissions),
        "scope": "bot applications.commands",
    })
    return f"https://discord.com/api/oauth2/authorize?{qs}"


def grant_access_error_message(status_code: int, body: str) -> str:
    """Map Discord channel-permission API errors to operator-facing text."""
    body_l = (body or "").lower()
    if status_code == 404 and "unknown overwrite" in body_l:
        return (
            "Bot is not a member of this guild yet — Discord cannot set channel "
            "overwrites until the bot joins the server. Use oauth_invite_url from "
            "invite_squad_bots results (owner opens in browser), then re-run "
            "squad(action='invite_squad_bots', channel_id=...)."
        )
    if status_code == 403:
        return (
            "403 — lead bot lacks Manage Channels / Manage Permissions on this "
            "channel. Re-invite the lead bot with Manage Channels or fix channel "
            "permissions in Discord, then retry grant_bot_access / invite_squad_bots."
        )
    if status_code == 404:
        return (
            "404 — channel or target not found. Verify channel_id and that member "
            "bots are in the same guild as the lead."
        )
    snippet = (body or "")[:200]
    return f"HTTP {status_code}: {snippet}"


async def fetch_channel_guild_id(lead_token: str, channel_id: str) -> str | None:
    """Resolve guild id for a channel using the lead bot token."""
    import httpx

    token = (lead_token or "").strip()
    cid = str(channel_id or "").strip()
    if not token or not cid:
        return None
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                f"{DISCORD_API}/channels/{cid}",
                headers={"Authorization": f"Bot {token}"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            gid = data.get("guild_id")
            return str(gid) if gid else None
    except Exception:
        return None


async def probe_bot_in_guild(token: str, guild_id: str) -> bool | None:
    """True when GET /guilds/{id} succeeds for this bot token."""
    import httpx

    token = (token or "").strip()
    guild_id = str(guild_id or "").strip()
    if not token or not guild_id or "masked" in token:
        return None
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                f"{DISCORD_API}/guilds/{guild_id}",
                headers={"Authorization": f"Bot {token}"},
            )
            if resp.status_code == 200:
                return True
            if resp.status_code in (403, 404):
                return False
            return None
    except Exception:
        return None


def _face_blocked(face: Any) -> bool:
    return getattr(face, "issue", "") != "OK — listening"


def build_playbook(
    faces: list[Any],
    channel_id: str,
    *,
    guild_id: str | None = None,
    in_guild_by_agent: dict[str, bool | None] | None = None,
) -> dict[str, Any]:
    """Deterministic next steps from readiness face rows (no LLM)."""
    in_guild_by_agent = in_guild_by_agent or {}
    oauth_invites: list[dict[str, str]] = []
    next_steps: list[dict[str, Any]] = []
    seen_oauth: set[str] = set()

    for face in faces:
        if getattr(face, "role", "") == "lead":
            continue
        if not _face_blocked(face):
            continue
        aid = getattr(face, "agent_id", "")
        bid = getattr(face, "bot_id", "")
        in_guild = in_guild_by_agent.get(aid)

        if in_guild is False and bid and bid not in seen_oauth:
            url = oauth_invite_url(bid)
            if url:
                oauth_invites.append({
                    "agent_id": aid,
                    "name": getattr(face, "name", ""),
                    "bot_id": bid,
                    "oauth_invite_url": url,
                })
                seen_oauth.add(bid)
                next_steps.append({
                    "order": len(next_steps) + 1,
                    "kind": "owner_oauth_invite",
                    "agent_id": aid,
                    "bot_id": bid,
                    "oauth_invite_url": url,
                    "reason": "Member bot is not in the guild — owner must open OAuth URL.",
                })

        if getattr(face, "configured", False) is False:
            next_steps.append({
                "order": len(next_steps) + 1,
                "kind": "configure_member",
                "tool": "squad",
                "action": "configure_member",
                "params": {
                    "target_agent_id": aid,
                    "skill": "discord-channel",
                    "owner_confirmed": True,
                },
                "reason": "Discord skill not configured on member agent.",
            })
            continue

        if in_guild is not False and getattr(face, "api_can_view", None) is False:
            next_steps.append({
                "order": len(next_steps) + 1,
                "kind": "grant_channel_access",
                "tool": "squad",
                "action": "invite_squad_bots",
                "params": {"channel_id": channel_id},
                "reason": f"Grant channel access for @{getattr(face, 'bot_username', '') or bid}.",
            })

        if getattr(face, "api_can_view", None) is True and not getattr(face, "scoped", False):
            next_steps.append({
                "order": len(next_steps) + 1,
                "kind": "sync_scope",
                "tool": "squad",
                "action": "sync_member_channels",
                "params": {
                    "target_agent_id": aid,
                    "channel": "discord",
                    "mirror_lead_channel_scope": True,
                },
                "reason": "Bot can view channel but Babo scope is empty — sync member channels.",
            })

        if getattr(face, "scoped", False) and not getattr(face, "listening", False):
            next_steps.append({
                "order": len(next_steps) + 1,
                "kind": "enable_channel",
                "tool": "channel_manage",
                "action": "enable",
                "params": {
                    "channel": "discord",
                    "channel_id": channel_id,
                    "enabled": True,
                },
                "reason": "Channel in scope but not listening — enable on member (via lead configure) or sync.",
            })

    all_ready = not any(_face_blocked(f) for f in faces)
    if all_ready:
        next_steps.append({
            "order": 1,
            "kind": "test_mentions",
            "tool": "discord_send",
            "action": "send",
            "params": {"channel_id": channel_id},
            "reason": (
                "All squad bots ready — lead sends with @mentions of member bot_id "
                "snowflakes from squad(action='inspect')."
            ),
        })

    return {
        "channel_id": channel_id,
        "guild_id": guild_id or "",
        "all_ready": all_ready,
        "oauth_invites": oauth_invites,
        "next_steps": next_steps,
    }


def playbook_summary(playbook: dict[str, Any]) -> str:
    """Human-readable playbook appendix for tool JSON."""
    lines: list[str] = []
    if playbook.get("all_ready"):
        lines.append("All squad bots are ready on this channel.")
        lines.append("Next: discord_send with @mentions of member bot_id values.")
        return "\n".join(lines)

    invites = playbook.get("oauth_invites") or []
    if invites:
        lines.append("Guild invites required (owner opens in browser):")
        for inv in invites:
            lines.append(
                f"  • {inv.get('name', '')} ({inv.get('bot_id', '')}): "
                f"{inv.get('oauth_invite_url', '')}",
            )

    steps = playbook.get("next_steps") or []
    if steps:
        lines.append("Execute next_steps in order (use squad / channel_manage — not bash/curl):")
        for step in steps:
            if step.get("kind") == "owner_oauth_invite":
                continue
            tool = step.get("tool", "")
            action = step.get("action", "")
            params = step.get("params") or {}
            lines.append(f"  {step.get('order', '?')}. {tool}(action='{action}', ...) — {step.get('reason', '')}")
            if params:
                keys = ", ".join(f"{k}={params[k]!r}" for k in sorted(params.keys())[:4])
                lines.append(f"     params: {keys}")
    return "\n".join(lines) if lines else "No automated steps — check report above."
