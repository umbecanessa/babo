"""OAuth2 flow for Google Workspace -- Installed Application pattern.

Handles:
  - Scope computation from per-service config
  - Authorization URL generation
  - Token exchange (auth code -> access + refresh tokens)
  - Encrypted token persistence (Fernet via ``cryptography``)
  - Transparent token refresh via ``google.auth``
  - Token revocation
  - Google API service client construction
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCOPES = {
    "gmail_readonly": "https://www.googleapis.com/auth/gmail.readonly",
    "gmail_send": "https://www.googleapis.com/auth/gmail.send",
    "gmail_modify": "https://www.googleapis.com/auth/gmail.modify",
    "calendar": "https://www.googleapis.com/auth/calendar",
    "calendar_readonly": "https://www.googleapis.com/auth/calendar.readonly",
    "drive_readonly": "https://www.googleapis.com/auth/drive.readonly",
    "drive_file": "https://www.googleapis.com/auth/drive.file",
    "sheets": "https://www.googleapis.com/auth/spreadsheets",
    "sheets_readonly": "https://www.googleapis.com/auth/spreadsheets.readonly",
    "userinfo_email": "https://www.googleapis.com/auth/userinfo.email",
}


def scopes_for_config(config: dict[str, Any]) -> list[str]:
    """Compute the minimal set of OAuth scopes from access-level config."""
    scopes = [SCOPES["userinfo_email"]]

    gmail = config.get("gmail_access", "disabled")
    if gmail == "read_write":
        scopes.extend([SCOPES["gmail_modify"], SCOPES["gmail_send"]])
    elif gmail == "read_only":
        scopes.append(SCOPES["gmail_readonly"])

    cal = config.get("calendar_access", "disabled")
    if cal == "read_write":
        scopes.append(SCOPES["calendar"])
    elif cal == "read_only":
        scopes.append(SCOPES["calendar_readonly"])

    drive = config.get("drive_access", "disabled")
    if drive == "read_write":
        scopes.extend([SCOPES["drive_readonly"], SCOPES["drive_file"]])
    elif drive == "read_only":
        scopes.append(SCOPES["drive_readonly"])

    sheets = config.get("sheets_access", "disabled")
    if sheets == "read_write":
        scopes.append(SCOPES["sheets"])
    elif sheets == "read_only":
        scopes.append(SCOPES["sheets_readonly"])

    return scopes


# ── Token encryption ──────────────────────────────────────────


class TokenStore:
    """Encrypted token persistence for OAuth refresh/access tokens.

    Uses Fernet symmetric encryption with a key derived from a
    per-installation random seed + the agent ID.  Falls back to
    plain JSON if the ``cryptography`` package is unavailable.
    """

    def __init__(self, tokens_dir: Path) -> None:
        self._tokens_dir = tokens_dir
        self._tokens_dir.mkdir(parents=True, exist_ok=True)

    def _token_path(self, agent_id: str) -> Path:
        return self._tokens_dir / f"{agent_id}_tokens.enc"

    def _derive_key(self, agent_id: str) -> bytes:
        seed_path = self._tokens_dir / ".token_seed"
        if seed_path.exists():
            seed = seed_path.read_bytes()
        else:
            seed = secrets.token_bytes(32)
            seed_path.write_bytes(seed)
        raw = hashlib.sha256(agent_id.encode() + seed).digest()
        return base64.urlsafe_b64encode(raw)

    def save(self, agent_id: str, token_data: dict[str, Any]) -> None:
        """Encrypt and persist token data."""
        try:
            from cryptography.fernet import Fernet
            key = self._derive_key(agent_id)
            plaintext = json.dumps(token_data).encode("utf-8")
            self._token_path(agent_id).write_bytes(Fernet(key).encrypt(plaintext))
        except ImportError:
            logger.warning(
                "cryptography not installed -- storing tokens unencrypted. "
                "Run: pip install cryptography"
            )
            self._token_path(agent_id).with_suffix(".json").write_text(
                json.dumps(token_data, indent=2), encoding="utf-8",
            )

    def load(self, agent_id: str) -> dict[str, Any] | None:
        """Load and decrypt stored tokens."""
        enc_path = self._token_path(agent_id)
        json_path = enc_path.with_suffix(".json")

        if enc_path.exists():
            try:
                from cryptography.fernet import Fernet
                key = self._derive_key(agent_id)
                plaintext = Fernet(key).decrypt(enc_path.read_bytes())
                return json.loads(plaintext)
            except ImportError:
                logger.warning("cryptography not installed -- cannot decrypt tokens")
                return None
            except Exception as exc:
                logger.warning("Failed to decrypt tokens for %s: %s", agent_id, exc)
                return None
        elif json_path.exists():
            try:
                return json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def delete(self, agent_id: str) -> bool:
        deleted = False
        for suffix in (".enc", ".json"):
            p = self._token_path(agent_id).with_suffix(suffix)
            if p.exists():
                p.unlink()
                deleted = True
        return deleted


# ── OAuth2 flow ───────────────────────────────────────────────


class OAuth2Flow:
    """Manages the Google OAuth2 Installed Application flow for one agent."""

    TOKEN_URI = "https://oauth2.googleapis.com/token"
    AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
    REVOKE_URI = "https://oauth2.googleapis.com/revoke"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        scopes: list[str],
        token_store: TokenStore,
        agent_id: str,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._token_store = token_store
        self._agent_id = agent_id
        self._credentials: Any | None = None
        self._load_stored_credentials()

    def _load_stored_credentials(self) -> None:
        token_data = self._token_store.load(self._agent_id)
        if not token_data:
            return
        try:
            from google.oauth2.credentials import Credentials

            self._credentials = Credentials(
                token=token_data.get("access_token"),
                refresh_token=token_data.get("refresh_token"),
                token_uri=self.TOKEN_URI,
                client_id=self._client_id,
                client_secret=self._client_secret,
                scopes=self._scopes,
            )
        except Exception as exc:
            logger.warning("Failed to load stored credentials for %s: %s", self._agent_id, exc)

    @property
    def credentials(self) -> Any | None:
        """Return stored credentials without triggering a network refresh.

        Use :meth:`build_service` (which runs in a worker thread) or
        :meth:`async_refresh` to ensure tokens are fresh.
        """
        return self._credentials

    def _refresh_sync(self) -> bool:
        """Synchronously refresh tokens. Must be called from a worker thread."""
        if self._credentials is None:
            return False
        if not self._credentials.expired:
            return True
        if not self._credentials.refresh_token:
            return False
        try:
            from google.auth.transport.requests import Request
            self._credentials.refresh(Request())
            self._persist()
            return True
        except Exception as exc:
            logger.error("Token refresh failed for %s: %s", self._agent_id, exc)
            self._credentials = None
            return False

    async def async_refresh(self) -> bool:
        """Refresh credentials without blocking the event loop."""
        import asyncio
        return await asyncio.to_thread(self._refresh_sync)

    @property
    def is_authenticated(self) -> bool:
        """True if we have credentials (possibly expired but with a refresh token)."""
        if self._credentials is None:
            return False
        if self._credentials.expired:
            return bool(self._credentials.refresh_token)
        return True

    def get_auth_url(self, redirect_uri: str, state: str = "") -> str:
        """Generate the Google OAuth consent URL.

        ``state`` should encode the agent_id and a CSRF nonce so the
        callback can route back to the right agent and reject forged
        redirects.
        """
        from urllib.parse import urlencode
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._scopes),
            "access_type": "offline",
            "prompt": "consent",
        }
        if state:
            params["state"] = state
        return f"{self.AUTH_URI}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange authorization code for tokens."""
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                self.TOKEN_URI,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            resp.raise_for_status()
            token_data = resp.json()

        from google.oauth2.credentials import Credentials

        self._credentials = Credentials(
            token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=self.TOKEN_URI,
            client_id=self._client_id,
            client_secret=self._client_secret,
            scopes=self._scopes,
        )
        self._persist()
        email = await self._fetch_user_email()
        return {"connected": True, "email": email}

    async def _fetch_user_email(self) -> str:
        try:
            import httpx
            creds = self.credentials
            if creds is None:
                return ""
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://www.googleapis.com/oauth2/v2/userinfo",
                    headers={"Authorization": f"Bearer {creds.token}"},
                )
                resp.raise_for_status()
                return resp.json().get("email", "")
        except Exception:
            return ""

    def _persist(self) -> None:
        if self._credentials is None:
            return
        self._token_store.save(self._agent_id, {
            "access_token": self._credentials.token,
            "refresh_token": self._credentials.refresh_token,
            "scopes": list(self._scopes),
        })

    async def revoke(self) -> None:
        """Revoke tokens and clear stored data."""
        if self._credentials and self._credentials.token:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        self.REVOKE_URI,
                        data={"token": self._credentials.token},
                    )
            except Exception:
                pass
        self._credentials = None
        self._token_store.delete(self._agent_id)

    def build_service(self, service_name: str, version: str) -> Any:
        """Build an authenticated Google API service client.

        Transparently refreshes expired tokens.  Always call this from
        a worker thread (``asyncio.to_thread``) to avoid blocking.
        """
        if not self._refresh_sync():
            raise RuntimeError(
                "Google account session expired and could not be refreshed. "
                "Please reconnect: use google_workspace_connect(action='disconnect') "
                "then google_workspace_connect(action='connect')."
            )
        from googleapiclient.discovery import build
        return build(service_name, version, credentials=self._credentials, cache_discovery=False)
