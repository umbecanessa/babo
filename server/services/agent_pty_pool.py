"""Per-agent PTY shell pool — persistent terminal per agent workspace.

When a long-running daemon/interactive process owns the command shell, we
**park** that session (kept alive for the UI mirror + the process) and spawn a
fresh command shell so the next ``bash()`` does not write into the busy PTY.
"""

from __future__ import annotations

import logging
from pathlib import Path

import asyncio

from server.services.pty_session import PtySession
from server.services.pty_workspace import normalize_pty_workspace

logger = logging.getLogger(__name__)

_PARK_NOTICE = (
    "\r\n[NLS] Long-running process owns this shell — "
    "opening a new agent shell for follow-up commands.\r\n"
)


def _agents_dir() -> Path | None:
    try:
        from server.config import get_settings

        return get_settings().agents_dir
    except Exception:
        return None


_pool: AgentPtyPool | None = None


class AgentPtyPool:
    def __init__(self) -> None:
        # Active shell used by bash() / project_install.
        self._sessions: dict[str, PtySession] = {}
        # Shell the UI mirrors — often a parked daemon session.
        self._mirror: dict[str, PtySession] = {}
        # Older parked shells (previous daemons), kept alive until agent close.
        self._background: dict[str, list[PtySession]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    @staticmethod
    def session_key(agent_id: str, workspace: str) -> str:
        aid = (agent_id or "default").strip()
        ws = normalize_pty_workspace(aid, workspace, agents_dir=_agents_dir())
        return f"{aid}:{ws}"

    async def get_session(
        self,
        *,
        agent_id: str,
        workspace: str,
        env: dict[str, str],
        cwd: str | None = None,
    ) -> PtySession:
        norm_workspace = normalize_pty_workspace(
            agent_id, workspace, agents_dir=_agents_dir(),
        )
        key = self.session_key(agent_id, norm_workspace)
        async with self._lock_for(key):
            session = self._sessions.get(key)
            if session is None:
                session = PtySession(
                    cwd=cwd or norm_workspace,
                    env=env,
                    session_key=key,
                )
                self._sessions[key] = session
                # First shell is both command + mirror until something is parked.
                self._mirror.setdefault(key, session)
                await session.start()
            else:
                await session.sync_env(env)
                if cwd:
                    try:
                        if Path(cwd).resolve() != Path(session.cwd).resolve():
                            await session.set_cwd(cwd)
                    except OSError:
                        if cwd != session.cwd:
                            await session.set_cwd(cwd)
            return session

    async def park_long_running_and_spawn_fresh(
        self,
        *,
        agent_id: str,
        workspace: str,
        env: dict[str, str],
        cwd: str | None = None,
    ) -> PtySession:
        """Keep the busy shell alive and return a new command shell.

        The parked shell remains the UI mirror target so daemon logs keep
        streaming. Subsequent ``get_session`` / ``bash()`` use the fresh shell.
        """
        norm_workspace = normalize_pty_workspace(
            agent_id, workspace, agents_dir=_agents_dir(),
        )
        key = self.session_key(agent_id, norm_workspace)
        async with self._lock_for(key):
            current = self._sessions.pop(key, None)
            if current is not None:
                try:
                    await current.write(_PARK_NOTICE)
                except Exception:
                    pass
                prev_mirror = self._mirror.get(key)
                if prev_mirror is not None and prev_mirror is not current:
                    self._background.setdefault(key, []).append(prev_mirror)
                self._mirror[key] = current
                logger.info(
                    "PTY parked long-running shell key=%s pid=%s",
                    key,
                    current.shell_pid,
                )

            fresh = PtySession(
                cwd=cwd or (current.cwd if current else norm_workspace),
                env=env,
                session_key=key,
            )
            self._sessions[key] = fresh
            if key not in self._mirror:
                self._mirror[key] = fresh
            await fresh.start()
            if cwd:
                try:
                    if Path(cwd).resolve() != Path(fresh.cwd).resolve():
                        await fresh.set_cwd(cwd)
                except OSError:
                    if cwd != fresh.cwd:
                        await fresh.set_cwd(cwd)
            await fresh.sync_env(env)
            logger.info(
                "PTY spawned fresh command shell key=%s pid=%s",
                key,
                fresh.shell_pid,
            )
            return fresh

    async def _close_session_obj(self, key: str, session: PtySession | None) -> None:
        if session is None:
            return
        try:
            await session.close()
        except Exception as exc:
            logger.debug("PTY close %s: %s", key, exc)

    async def close_session(self, agent_id: str, workspace: str) -> None:
        key = self.session_key(agent_id, workspace)
        async with self._lock_for(key):
            session = self._sessions.pop(key, None)
            mirror = self._mirror.pop(key, None)
            background = self._background.pop(key, [])
        await self._close_session_obj(key, session)
        if mirror is not None and mirror is not session:
            await self._close_session_obj(key, mirror)
        for parked in background:
            if parked is not session and parked is not mirror:
                await self._close_session_obj(key, parked)

    async def close_agent(self, agent_id: str) -> None:
        prefix = f"{(agent_id or 'default').strip()}:"
        keys = {
            k
            for k in list(self._sessions) + list(self._mirror) + list(self._background)
            if k.startswith(prefix)
        }
        for key in keys:
            aid, _, rest = key.partition(":")
            await self.close_session(aid, rest)

    async def close_all(self) -> None:
        keys = {
            k
            for k in list(self._sessions) + list(self._mirror) + list(self._background)
        }
        for key in keys:
            aid, _, rest = key.partition(":")
            await self.close_session(aid, rest)

    def get_existing(self, agent_id: str, workspace: str) -> PtySession | None:
        """Session the UI should mirror (parked daemon if any, else command shell)."""
        key = self.session_key(agent_id, workspace)
        return self._mirror.get(key) or self._sessions.get(key)


def get_agent_pty_pool() -> AgentPtyPool:
    global _pool
    if _pool is None:
        _pool = AgentPtyPool()
    return _pool


def reset_agent_pty_pool() -> None:
    """Test helper."""
    global _pool
    _pool = None
