"""Per-agent PTY shell pool — one persistent terminal per agent workspace."""

from __future__ import annotations

import logging
from pathlib import Path

import asyncio

from server.services.pty_session import PtySession
from server.services.pty_workspace import normalize_pty_workspace

logger = logging.getLogger(__name__)


def _agents_dir() -> Path | None:
    try:
        from server.config import get_settings

        return get_settings().agents_dir
    except Exception:
        return None

_pool: AgentPtyPool | None = None


class AgentPtyPool:
    def __init__(self) -> None:
        self._sessions: dict[str, PtySession] = {}
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

    async def close_session(self, agent_id: str, workspace: str) -> None:
        key = self.session_key(agent_id, workspace)
        session = self._sessions.pop(key, None)
        if session:
            try:
                await session.close()
            except Exception as exc:
                logger.debug("PTY close %s: %s", key, exc)

    async def close_agent(self, agent_id: str) -> None:
        prefix = f"{(agent_id or 'default').strip()}:"
        keys = [k for k in self._sessions if k.startswith(prefix)]
        for key in keys:
            session = self._sessions.pop(key, None)
            if session:
                try:
                    await session.close()
                except Exception as exc:
                    logger.debug("PTY close %s: %s", key, exc)

    async def close_all(self) -> None:
        keys = list(self._sessions.keys())
        for key in keys:
            session = self._sessions.pop(key, None)
            if session:
                try:
                    await session.close()
                except Exception as exc:
                    logger.debug("PTY close %s: %s", key, exc)

    def get_existing(self, agent_id: str, workspace: str) -> PtySession | None:
        return self._sessions.get(self.session_key(agent_id, workspace))


def get_agent_pty_pool() -> AgentPtyPool:
    global _pool
    if _pool is None:
        _pool = AgentPtyPool()
    return _pool


def reset_agent_pty_pool() -> None:
    """Test helper."""
    global _pool
    _pool = None
