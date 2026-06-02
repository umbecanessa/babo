"""NLS Permission System -- Apple-style permission management.

Controls what tools can do. Every tool execution goes through the permission
manager before accessing protected resources.

Permission categories:
    filesystem.read     -- Read files (scoped to directories)
    filesystem.write    -- Write/create files (scoped to directories)
    shell.execute       -- Run shell commands
    clipboard.read      -- Read system clipboard
    clipboard.write     -- Write to system clipboard
    notification        -- Show OS notifications
    network.outbound    -- Make outbound network requests (scoped to domains)
    camera              -- Access camera
    microphone          -- Access microphone
    keychain            -- Access stored credentials
    screenshot          -- Capture screen

Permission profiles (presets):
    research    -- Web + read-only files
    developer   -- Web + files + shell + code + git
    private     -- No network, local only
    custom      -- User picks what to enable

In the Electron desktop app, permission prompts appear as native dialogs
via the IPC bridge. In server/headless mode, permissions are configured
via a JSON file or environment variables.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Permission grant record
# ---------------------------------------------------------------------------


@dataclass
class PermissionGrant:
    """A single permission decision."""
    permission: str
    granted: bool
    scope: str = ""           # e.g., directory path or domain
    granted_at: float = 0.0   # timestamp
    expires_at: float = 0.0   # 0 = never expires
    reason: str = ""

    @property
    def expired(self) -> bool:
        if self.expires_at == 0:
            return False
        return time.time() > self.expires_at


# ---------------------------------------------------------------------------
# Permission profiles
# ---------------------------------------------------------------------------

PROFILES: dict[str, dict[str, bool]] = {
    "research": {
        "filesystem.read": True,
        "filesystem.write": False,
        "shell.execute": False,
        "clipboard.read": True,
        "clipboard.write": True,
        "notification": True,
        "network.outbound": True,
        "screenshot": False,
        "keychain": False,
    },
    "developer": {
        "filesystem.read": True,
        "filesystem.write": True,
        "shell.execute": True,
        "clipboard.read": True,
        "clipboard.write": True,
        "notification": True,
        "network.outbound": True,
        "screenshot": True,
        "keychain": True,
    },
    "private": {
        "filesystem.read": True,
        "filesystem.write": True,
        "shell.execute": False,
        "clipboard.read": True,
        "clipboard.write": True,
        "notification": True,
        "network.outbound": False,
        "screenshot": False,
        "keychain": False,
    },
}


# ---------------------------------------------------------------------------
# Permission denied error
# ---------------------------------------------------------------------------


class PermissionDeniedError(Exception):
    """Raised when a tool operation is denied by the permission system."""

    def __init__(self, permission: str, scope: str = "") -> None:
        self.permission = permission
        self.scope = scope
        scope_str = f" ({scope})" if scope else ""
        super().__init__(f"Permission denied: {permission}{scope_str}")


# ---------------------------------------------------------------------------
# Permission Manager
# ---------------------------------------------------------------------------


class PermissionManager:
    """Manages runtime permissions for NLS tools.

    Supports two modes:
        - Interactive (desktop): prompts the user via a callback
        - Headless (server): uses a pre-configured allow/deny list

    Usage::

        pm = PermissionManager()
        pm.apply_profile("developer")

        # Check permission (raises PermissionDeniedError if denied)
        pm.require("filesystem.read", scope="/home/user/project")

        # Or check without raising
        if pm.check("shell.execute"):
            run_command(...)
    """

    def __init__(
        self,
        prompt_fn: Callable[[str, str], bool] | None = None,
        auto_grant: bool = False,
    ) -> None:
        """
        Args:
            prompt_fn: Optional callback(permission, reason) -> bool for
                      interactive permission prompts. If None, uses
                      auto_grant behavior.
            auto_grant: If True and no prompt_fn, automatically grant all
                       permissions (useful for testing/development).
        """
        self._grants: dict[str, PermissionGrant] = {}
        self._prompt_fn = prompt_fn
        self._auto_grant = auto_grant

    def require(self, permission: str, scope: str = "", reason: str = "") -> None:
        """Check permission and raise PermissionDeniedError if denied.

        This is the primary method tools call before executing.
        """
        if not self.check(permission, scope, reason):
            raise PermissionDeniedError(permission, scope)

    def check(self, permission: str, scope: str = "", reason: str = "") -> bool:
        """Check if a permission is granted. Returns bool.

        Resolution order:
            1. Check scoped grant (permission:scope)
            2. Check base grant (permission)
            3. Prompt user if interactive
            4. Use auto_grant default
        """
        # Check scoped grant
        if scope:
            scoped_key = f"{permission}:{scope}"
            grant = self._grants.get(scoped_key)
            if grant and not grant.expired:
                return grant.granted

        # Check base grant
        grant = self._grants.get(permission)
        if grant and not grant.expired:
            return grant.granted

        # Prompt user if we have a callback
        if self._prompt_fn is not None:
            scope_text = f" ({scope})" if scope else ""
            reason_text = reason or f"A tool needs to {permission}{scope_text}"
            try:
                allowed = self._prompt_fn(permission, reason_text)
            except Exception:
                allowed = False

            self._store_grant(permission, allowed, scope, reason)
            return allowed

        # Auto-grant for testing/headless
        if self._auto_grant:
            self._store_grant(permission, True, scope, reason)
            return True

        # Default deny
        logger.warning(
            "Permission '%s' denied (no grant, no prompt, auto_grant=False)",
            permission,
        )
        return False

    def grant(self, permission: str, scope: str = "", duration_seconds: float = 0) -> None:
        """Explicitly grant a permission."""
        self._store_grant(permission, True, scope, "", duration_seconds)

    def deny(self, permission: str, scope: str = "") -> None:
        """Explicitly deny a permission."""
        self._store_grant(permission, False, scope)

    def revoke(self, permission: str, scope: str = "") -> None:
        """Remove a permission grant (will be re-prompted next time)."""
        key = f"{permission}:{scope}" if scope else permission
        self._grants.pop(key, None)

    def apply_profile(self, profile_name: str) -> None:
        """Apply a permission profile preset."""
        profile = PROFILES.get(profile_name)
        if not profile:
            raise ValueError(f"Unknown profile: {profile_name}. Available: {list(PROFILES.keys())}")

        for permission, granted in profile.items():
            self._store_grant(permission, granted)

        logger.info("Applied permission profile: %s", profile_name)

    def get_all(self) -> dict[str, bool]:
        """Get all current permission states."""
        return {
            key: grant.granted
            for key, grant in self._grants.items()
            if not grant.expired
        }

    def get_profiles(self) -> dict[str, dict[str, bool]]:
        """Get available permission profiles."""
        return PROFILES.copy()

    def reset(self) -> None:
        """Clear all permission grants."""
        self._grants.clear()

    # ── Persistence ──────────────────────────────────────────────────

    def save(self, path: Path) -> None:
        """Save permissions to disk."""
        data = {
            "grants": [
                {
                    "permission": g.permission,
                    "granted": g.granted,
                    "scope": g.scope,
                    "granted_at": g.granted_at,
                    "expires_at": g.expires_at,
                    "reason": g.reason,
                }
                for g in self._grants.values()
                if not g.expired
            ]
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self, path: Path) -> None:
        """Load permissions from disk."""
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for g in data.get("grants", []):
                grant = PermissionGrant(
                    permission=g["permission"],
                    granted=g["granted"],
                    scope=g.get("scope", ""),
                    granted_at=g.get("granted_at", 0.0),
                    expires_at=g.get("expires_at", 0.0),
                    reason=g.get("reason", ""),
                )
                if not grant.expired:
                    key = f"{grant.permission}:{grant.scope}" if grant.scope else grant.permission
                    self._grants[key] = grant
        except Exception as e:
            logger.warning("Failed to load permissions: %s", e)

    # ── Internal ─────────────────────────────────────────────────────

    def _store_grant(
        self,
        permission: str,
        granted: bool,
        scope: str = "",
        reason: str = "",
        duration_seconds: float = 0,
    ) -> None:
        key = f"{permission}:{scope}" if scope else permission
        self._grants[key] = PermissionGrant(
            permission=permission,
            granted=granted,
            scope=scope,
            granted_at=time.time(),
            expires_at=time.time() + duration_seconds if duration_seconds > 0 else 0,
            reason=reason,
        )
