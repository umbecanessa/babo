"""Agent Manager -- Agent Lifecycle for the Multi-Agent Platform.

Manages agent creation, loading, state tracking, and eviction.
Bridges the genesis system, ServerRuntime, and AdapterRegistry.

Agent lifecycle on the platform::

    [*] --> creating   User requests new agent
    creating --> alive  Genesis init + adapter load
    alive --> chatting  Message received
    chatting --> alive  Response complete
    alive --> sleeping  ANS triggers sleep → queued on Model B
    sleeping --> alive  Model B done → adapters hot-reloaded
    alive --> offline   Inactive > 30min (adapters still in VRAM)
    offline --> alive   User reconnects
    offline --> evicted Inactive > 24h (adapters unloaded)
    evicted --> alive   User reconnects (full reload ~2-3s)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from nls.models import AgentStatus

logger = logging.getLogger(__name__)


def _kill_agent_processes(agent_dir: Path) -> int:
    """Force-kill every OS process related to *agent_dir*.

    Checks three vectors per process:
    1. Executable path contains the agent dir
    2. Command line arguments contain the agent dir
    3. Current working directory is inside the agent dir
       (catches dev servers whose exe is ``node.exe`` or ``python.exe``
       in a global location but whose cwd is the agent workspace)

    Uses ``psutil`` for reliable cross-platform process inspection.
    Falls back to ``wmic`` + ``/proc`` if psutil is unavailable.

    Returns the number of processes killed.
    """
    killed = 0
    my_pid = os.getpid()
    agent_path_lower = str(agent_dir).lower()

    try:
        import psutil
        for proc in psutil.process_iter(["pid", "exe", "cmdline", "cwd"]):
            try:
                info = proc.info
                pid = info["pid"]
                if pid == my_pid or pid == 0:
                    continue
                exe = (info.get("exe") or "").lower()
                cmdline = " ".join(info.get("cmdline") or []).lower()
                cwd = (info.get("cwd") or "").lower()
                if (agent_path_lower in exe
                        or agent_path_lower in cmdline
                        or agent_path_lower in cwd):
                    proc.kill()
                    killed += 1
                    desc = exe or cwd or cmdline[:120]
                    logger.info(
                        "Killed process %d (%s) for agent dir cleanup",
                        pid, desc,
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied,
                    psutil.ZombieProcess):
                continue
        return killed
    except ImportError:
        pass

    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["wmic", "process", "get",
                 "ProcessId,ExecutablePath,CommandLine",
                 "/FORMAT:CSV"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.strip().split(",", 3)
                if len(parts) < 4:
                    continue
                cmd_line = parts[1].strip().lower()
                exe_path = parts[2].strip().lower()
                pid_str = parts[3].strip()
                if not pid_str.isdigit():
                    continue
                pid = int(pid_str)
                if pid == my_pid:
                    continue
                if agent_path_lower in exe_path or agent_path_lower in cmd_line:
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            capture_output=True, timeout=5,
                        )
                        killed += 1
                        desc = exe_path or cmd_line[:120]
                        logger.info(
                            "Killed process %d (%s) for agent dir cleanup",
                            pid, desc,
                        )
                    except Exception as exc:
                        logger.debug("taskkill pid %d failed: %s", pid, exc)
        except Exception as exc:
            logger.warning("wmic process scan failed: %s", exc)
    else:
        import signal as _sig
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                pid = int(entry)
                if pid == my_pid:
                    continue
                try:
                    exe = os.readlink(f"/proc/{pid}/exe").lower()
                except OSError:
                    exe = ""
                try:
                    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes(
                    ).replace(b"\x00", b" ").decode("utf-8", "replace").lower()
                except OSError:
                    cmdline = ""
                if agent_path in exe or agent_path in cmdline:
                    try:
                        os.kill(pid, _sig.SIGKILL)
                        killed += 1
                        logger.info(
                            "Killed process %d (%s) for agent dir cleanup",
                            pid, exe or cmdline[:120],
                        )
                    except OSError:
                        pass
        except Exception as exc:
            logger.warning("/proc scan failed: %s", exc)

    return killed

# How often to auto-save all agent states (seconds).
# Protects against state loss on ungraceful shutdown (e.g. pkill, OOM).
_AUTOSAVE_INTERVAL = 300  # 5 minutes

# ── Model name patterns that map to the same base architecture ──
# Different quantization providers for the same base model share one
# architecture family (used when resolving tokenizer / model aliases).
import re

_MODEL_ALIASES: dict[str, re.Pattern[str]] = {
    "Qwen3-32B": re.compile(r"Qwen3-32B", re.IGNORECASE),
    "Qwen3-8B": re.compile(r"Qwen3-8B", re.IGNORECASE),
    "Llama-3.1-8B": re.compile(r"Llama-3[._-]1-8B", re.IGNORECASE),
    "Llama-3.1-70B": re.compile(r"Llama-3[._-]1-70B", re.IGNORECASE),
}


def _models_compatible(agent_model: str, server_model: str) -> bool:
    """Check if two model names refer to the same base architecture.

    Exact match is always True.  Otherwise, checks if both models
    match the same architecture pattern (e.g. Qwen3-32B regardless
    of quantization provider like unsloth, nvidia, etc.).
    """
    if agent_model == server_model:
        return True
    for _alias, pattern in _MODEL_ALIASES.items():
        if pattern.search(agent_model) and pattern.search(server_model):
            return True
    return False


class AgentManager:
    """Manages agent instances for the multi-agent server.

    Parameters
    ----------
    agents_dir : Path
        Root directory for agent data.
    genesis_dir : Path
        Root directory for genesis templates.
    sleep_scheduler : SleepScheduler
        For registering sleep callbacks.
    model_manager : DualModelManager
        For model/tokenizer access.
    """

    def __init__(
        self,
        agents_dir: Path,
        genesis_dir: Path,
        sleep_scheduler: Any,
        model_manager: Any,
    ):
        self.agents_dir = agents_dir
        self.genesis_dir = genesis_dir
        self.sleep_scheduler = sleep_scheduler
        self.model_manager = model_manager

        # Active agent runtimes: agent_id -> ServerRuntime
        self._runtimes: dict[str, Any] = {}

        # Agent status: agent_id -> AgentStatus
        self._status: dict[str, AgentStatus] = {}

        # Agent metadata cache: agent_id -> dict
        self._meta: dict[str, dict] = {}

        # Autosave background task handle
        self._autosave_task: asyncio.Task | None = None

        # Consciousness scheduler (set after startup by main.py)
        self.consciousness_scheduler: Any = None

    # ===================================================================
    # Periodic Autosave
    # ===================================================================

    def start_autosave(self) -> None:
        """Start the periodic autosave background task.

        Periodically saves all loaded agent states to disk so that
        state is not lost on ungraceful shutdown (e.g. pkill, OOM,
        power loss).
        """
        if self._autosave_task is not None:
            return  # already running
        self._autosave_task = asyncio.create_task(self._autosave_loop())
        logger.info(
            "Autosave started (every %ds)", _AUTOSAVE_INTERVAL,
        )

    def stop_autosave(self) -> None:
        """Stop the periodic autosave background task."""
        if self._autosave_task is not None:
            self._autosave_task.cancel()
            self._autosave_task = None
            logger.info("Autosave stopped")

    async def _autosave_loop(self) -> None:
        """Background loop: save all agent states periodically."""
        try:
            while True:
                await asyncio.sleep(_AUTOSAVE_INTERVAL)
                saved = 0
                for agent_id, runtime in list(self._runtimes.items()):
                    try:
                        runtime.save_state()
                        saved += 1
                    except Exception as exc:
                        logger.warning(
                            "Autosave failed for agent %s: %s",
                            agent_id, exc,
                        )
                if saved:
                    logger.info(
                        "Autosave: persisted state for %d agent(s)", saved,
                    )
        except asyncio.CancelledError:
            pass  # graceful stop

    # ===================================================================
    # Agent Creation
    # ===================================================================

    async def create_agent(
        self,
        genesis_version: str,
        agent_id: str = "",
        name: str = "",
        sovereignty: str = "local",
        config_overrides: dict | None = None,
        soul_wish: str = "",
    ) -> dict[str, Any]:
        """Create a new agent from a genesis template.

        Wraps ``nls.ledger.genesis.create_agent_from_genesis()`` and
        then loads the agent runtime.

        Returns agent metadata dict.
        """
        from nls.ledger.genesis import create_agent_from_genesis

        self._status[agent_id or "pending"] = AgentStatus.CREATING

        try:
            created_id, chain_state = create_agent_from_genesis(
                genesis_version=genesis_version,
                agent_id=agent_id or None,
                agent_name=name,
                sovereignty_mode=sovereignty,
                use_symlinks=True,
                skip_adapters=True,
                config_overrides=config_overrides,
                soul_wish=soul_wish,
            )
        except Exception as exc:
            logger.error("Agent creation failed: %s", exc)
            raise

        agent_dir = self.agents_dir / created_id

        await self.load_agent(created_id)

        runtime = self._runtimes.get(created_id)
        if runtime and name and not (runtime.agent_name or "").strip():
            runtime._save_agent_name(name.strip())

        logger.info(
            "Agent %s created from genesis %s (name=%s)",
            created_id, genesis_version, name,
        )

        meta = {
            "agent_id": created_id,
            "name": name,
            "genesis_version": genesis_version,
            "sovereignty": sovereignty,
            "status": AgentStatus.ALIVE.value,
            "created_at": time.time(),
        }
        self._meta[created_id] = meta
        return meta

    # ===================================================================
    # Agent Loading (from disk into active serving)
    # ===================================================================

    async def load_agent(self, agent_id: str) -> None:
        """Load an agent for active serving.

        1. Build brain subsystems via factory
        2. Create AgentRuntime
        3. Register with SleepScheduler + ConsciousnessScheduler
        """
        from nls.runtime.agent_runtime import AgentRuntime
        from nls.runtime.factory import build_subsystems

        agent_dir = self.agents_dir / agent_id
        if not agent_dir.exists():
            raise FileNotFoundError(f"Agent not found: {agent_id}")

        from nls.runtime.factory import _load_agent_config
        config = _load_agent_config(agent_dir, "runtime.json")

        subsystems = build_subsystems(
            agent_id, agent_dir, config,
            vllm_client=self.model_manager.vllm_client,
            on_sleep_requested=self.sleep_scheduler.enqueue_sync,
        )

        runtime = AgentRuntime(**subsystems)
        runtime.initialize()

        self._runtimes[agent_id] = runtime
        self._status[agent_id] = AgentStatus.ALIVE

        # Cache metadata from agent_meta.json
        self._load_agent_meta(agent_id, agent_dir)

        # Start Visual Cortex capture loop if enabled.  Each agent gets its
        # own capture loop/buffer; the local VLM subprocess is shared process-
        # wide via SharedVLMRegistry (one SmolVLM worker + bounded request
        # queue for all agents).
        vc = getattr(runtime, "visual_cortex", None)
        if vc is not None and getattr(getattr(vc, "config", None), "enabled", False):
            try:
                await vc.start()
                logger.info("Agent %s: Visual Cortex started", agent_id)
            except Exception as exc:
                logger.warning("Agent %s: Visual Cortex failed to start: %s", agent_id, exc)

        # Register with sleep scheduler
        self.sleep_scheduler.register_runtime(agent_id, runtime)

        # Register with consciousness scheduler (for inner loop)
        if self.consciousness_scheduler is not None:
            self.consciousness_scheduler.register_agent(agent_id)

        logger.info("Agent %s loaded and ready for serving", agent_id)

    def _load_agent_meta(self, agent_id: str, agent_dir: Path) -> None:
        """Load agent metadata from agent_meta.json into cache."""
        import json

        meta_path = agent_dir / "agent_meta.json"
        if not meta_path.exists():
            return
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_data = json.load(f)

            raw_name = meta_data.get("agent_name", "")
            # Ignore auto-generated names that are just the agent ID prefix
            name = raw_name if raw_name and raw_name != agent_id[:8] else ""

            self._meta[agent_id] = {
                "agent_id": agent_id,
                "name": name,
                "genesis_version": meta_data.get("genesis_version", ""),
                "sovereignty": meta_data.get("sovereignty_mode", "local"),
                "created_at": meta_data.get("created_at", ""),
            }
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Failed to load agent_meta.json for %s: %s",
                agent_id, exc,
            )

    def update_agent_name(self, agent_id: str, name: str) -> None:
        """Update the cached agent name after in-conversation naming.

        Called by the chat WebSocket handler when the agent accepts a
        name from the user.  The runtime itself already persisted to
        ``agent_meta.json``; this updates the manager's in-memory cache
        so that ``get_agent_status`` and ``list_agents`` reflect the
        new name immediately.
        """
        if agent_id not in self._meta:
            self._meta[agent_id] = {"agent_id": agent_id}
        self._meta[agent_id]["name"] = name
        logger.info("Agent %s: name cached as '%s'", agent_id, name)

    # ===================================================================
    # Message Processing
    # ===================================================================

    async def process_message(
        self,
        agent_id: str,
        user_input: str,
        history: list[dict] | None = None,
        *,
        memory_test_mode: bool = False,
        no_deltanet: bool = False,
    ) -> dict[str, Any]:
        """Process a user message for an agent.

        Handles adapter switching, runs the cognitive pipeline, and
        returns the structured result.
        """
        # Ensure agent is loaded
        if agent_id not in self._runtimes:
            await self.load_agent(agent_id)

        runtime = self._runtimes[agent_id]

        # Touch agent slot (update LRU timestamp)
        self._status[agent_id] = AgentStatus.CHATTING

        result = await runtime.process_message_async(
            user_input, history,
            memory_test_mode=memory_test_mode,
            no_deltanet=no_deltanet,
        )

        self._status[agent_id] = AgentStatus.ALIVE
        return result._asdict()

    # ===================================================================
    # Agent Eviction / Cleanup
    # ===================================================================

    async def evict_agent(self, agent_id: str) -> None:
        """Evict an agent from active serving.

        Saves state and removes the runtime. The agent can be reloaded on next request.
        """
        runtime = self._runtimes.pop(agent_id, None)
        if runtime is not None:
            runtime.save_state()
            await runtime.shutdown_async()

        self.sleep_scheduler.unregister_runtime(agent_id)

        # Unregister from consciousness scheduler
        if self.consciousness_scheduler is not None:
            self.consciousness_scheduler.unregister_agent(agent_id)

        self._status[agent_id] = AgentStatus.EVICTED

        logger.info("Agent %s evicted", agent_id)

    async def delete_agent(self, agent_id: str) -> None:
        """Fully delete an agent (force shutdown + kill processes + remove disk)."""
        await self.evict_agent(agent_id)

        import shutil
        import stat

        agent_dir = self.agents_dir / agent_id

        # Force-kill any OS processes still running inside the agent workspace
        # (orphaned dev servers, bundlers, watchers spawned by the bash tool).
        n_killed = _kill_agent_processes(agent_dir)
        if n_killed:
            logger.info(
                "Agent %s: killed %d orphaned process(es) before deletion",
                agent_id, n_killed,
            )
            await asyncio.sleep(1.0)

        if agent_dir.exists():
            def _force_remove_readonly(func, path, exc):
                os.chmod(path, stat.S_IWRITE)
                func(path)

            for attempt in range(5):
                try:
                    shutil.rmtree(agent_dir, onexc=_force_remove_readonly)
                    logger.info("Agent %s deleted from disk", agent_id)
                    break
                except PermissionError:
                    # Re-scan and kill processes each retry -- new children
                    # may have been spawned between attempts.
                    _kill_agent_processes(agent_dir)
                    if attempt < 4:
                        delay = min(2.0 * (attempt + 1), 8.0)
                        logger.warning(
                            "Agent %s: rmtree attempt %d/5 failed "
                            "(files still locked), retrying in %.0fs...",
                            agent_id, attempt + 1, delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "Agent %s: rmtree failed after 5 attempts — "
                            "files may still be locked by external processes",
                            agent_id,
                        )
                        raise

        self._status.pop(agent_id, None)
        self._meta.pop(agent_id, None)

    # ===================================================================
    # Status
    # ===================================================================

    def get_agent_status(self, agent_id: str) -> dict[str, Any]:
        """Return status for a single agent."""
        status = self._status.get(agent_id, AgentStatus.OFFLINE)
        runtime = self._runtimes.get(agent_id)

        result: dict[str, Any] = {
            "agent_id": agent_id,
            "status": status.value,
            "in_vram": agent_id in self._runtimes,
        }

        if runtime is not None:
            result.update(runtime.get_status())

        meta = self._meta.get(agent_id, {})
        if meta:
            result["name"] = meta.get("name", "")
            result["genesis_version"] = meta.get("genesis_version", "")

        cs = getattr(self, "consciousness_scheduler", None)
        if cs is not None:
            result["user_paused"] = cs.is_agent_paused(agent_id)
            try:
                cs_agents = cs.get_status().get("agents", {})
                agent_cs = cs_agents.get(agent_id)
                if agent_cs:
                    result["consciousness"] = agent_cs
            except Exception:
                pass

        return result

    def list_agents(self) -> list[dict[str, Any]]:
        """List all known agents with their status."""
        # Scan agents directory for all agents
        agents = []
        if self.agents_dir.exists():
            for agent_dir in sorted(self.agents_dir.iterdir()):
                if agent_dir.is_dir() and (agent_dir / "ledger.yaml").exists():
                    agent_id = agent_dir.name
                    agents.append(self.get_agent_status(agent_id))
        return agents

    def get_runtime(self, agent_id: str) -> Any | None:
        """Get the ServerRuntime for an agent (or None)."""
        return self._runtimes.get(agent_id)

    async def auto_load_all(self, whitelist: list[str] | None = None) -> int:
        """Auto-load all existing agents from disk on server startup.

        Scans the agents directory for agent directories with a valid
        ``ledger.yaml``.  Each discovered agent is loaded into active
        serving (adapters in VRAM, runtime initialized, registered with
        schedulers).

        Agents whose ``base_model`` doesn't match the currently loaded
        model are silently skipped (they remain on disk for when the
        server runs with the matching model size).

        Parameters
        ----------
        whitelist : list[str] | None
            If provided, only load these agent IDs (skip all others).
            Useful for education-only servers.

        Returns the number of agents successfully loaded.
        """
        if not self.agents_dir.exists():
            return 0

        loaded = 0
        skipped_compat = 0

        if whitelist:
            agent_dirs = sorted(
                self.agents_dir / aid for aid in whitelist
                if (self.agents_dir / aid).is_dir()
                and (self.agents_dir / aid / "ledger.yaml").exists()
            )
        else:
            # Skip archived agents, templates, genesis bootstraps, and proof-
            # of-concept agents.  In production, only user-facing agents
            # should be loaded; genesis templates and archived snapshots
            # stay on disk but are not served.
            _SKIP_PREFIXES = ("_archived_", "_template_", "edu-proof-")
            _SKIP_CONTAINS = ("bootstrap",)
            agent_dirs = sorted(
                d for d in self.agents_dir.iterdir()
                if d.is_dir()
                and (d / "ledger.yaml").exists()
                and not any(d.name.startswith(p) for p in _SKIP_PREFIXES)
                and not any(kw in d.name for kw in _SKIP_CONTAINS)
            )

        if not agent_dirs:
            logger.info("No existing agents found to auto-load")
            return 0

        # Detect current model name for compatibility filtering
        current_model = ""
        if self.model_manager and self.model_manager.model_a is not None:
            cfg = getattr(self.model_manager.model_a, "config", None)
            if cfg:
                current_model = getattr(cfg, "_name_or_path", "") or ""
        if not current_model and self.model_manager:
            current_model = getattr(self.model_manager, "hf_model", "") or ""

        logger.info(
            "Auto-loading %d agent(s) from %s (model: %s)...",
            len(agent_dirs), self.agents_dir, current_model or "unknown",
        )

        for agent_dir in agent_dirs:
            agent_id = agent_dir.name

            # ── Model compatibility check ──
            if current_model:
                try:
                    import json
                    agent_model = ""

                    # Try agent_meta.json first (genesis-created agents)
                    meta_path = agent_dir / "agent_meta.json"
                    if meta_path.exists():
                        meta = json.loads(
                            meta_path.read_text(encoding="utf-8"),
                        )
                        agent_model = meta.get("base_model", "")

                    # Fall back to ledger.yaml (pre-genesis agents)
                    if not agent_model:
                        ledger_path = agent_dir / "ledger.yaml"
                        if ledger_path.exists():
                            import yaml
                            with open(ledger_path, "r") as f:
                                ledger = yaml.safe_load(f)
                            if ledger:
                                agent_model = ledger.get(
                                    "base_model", "",
                                )

                    if agent_model and not _models_compatible(
                        agent_model, current_model,
                    ):
                        skipped_compat += 1
                        logger.debug(
                            "Skipping agent %s: model mismatch "
                            "(agent=%s, server=%s)",
                            agent_id, agent_model, current_model,
                        )
                        self._status[agent_id] = AgentStatus.OFFLINE
                        continue
                except Exception:
                    pass  # If we can't read metadata, try loading anyway

            # Skip paused agents: only cache metadata, don't allocate
            # a full runtime.  This saves hundreds of MB per agent.
            # Paused agents can be loaded on-demand when unpaused.
            try:
                import json as _json
                _meta_path = agent_dir / "agent_meta.json"
                if _meta_path.exists():
                    _meta = _json.loads(
                        _meta_path.read_text(encoding="utf-8"),
                    )
                    if _meta.get("user_paused", False):
                        self._meta[agent_id] = _meta
                        self._status[agent_id] = AgentStatus.OFFLINE
                        logger.info(
                            "Skipping paused agent %s (use unpause to load)",
                            agent_id,
                        )
                        continue
            except Exception:
                pass

            try:
                await self.load_agent(agent_id)
                loaded += 1
                logger.info(
                    "Auto-loaded agent %s (%d/%d)",
                    agent_id, loaded, len(agent_dirs),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to auto-load agent %s: %s", agent_id, exc,
                )
                self._status[agent_id] = AgentStatus.OFFLINE

        logger.info(
            "Auto-load complete: %d/%d agents alive (%d skipped, "
            "incompatible model)",
            loaded, len(agent_dirs), skipped_compat,
        )
        return loaded

    def get_loaded_runtimes(self) -> dict[str, Any]:
        """Return all currently loaded agent runtimes.

        Used by the ConsciousnessScheduler to iterate over active agents
        and manage their inner loops.

        Returns
        -------
        dict[str, ServerRuntime]
            Mapping of agent_id -> ServerRuntime for all loaded agents.
        """
        return dict(self._runtimes)

    def get_overview(self) -> dict[str, Any]:
        """Return an overview for the health endpoint."""
        overview: dict[str, Any] = {
            "active_runtimes": len(self._runtimes),
            "agents_in_vram": len([
                s for s in self._status.values()
                if s in (AgentStatus.ALIVE, AgentStatus.CHATTING)
            ]),
            "agents_sleeping": len([
                s for s in self._status.values()
                if s == AgentStatus.SLEEPING
            ]),
        }

        return overview

    def validate_api_key(self, token: str) -> str | None:
        """Validate a user API key (``nlsk_...``).

        Keys are issued and verified by the NestJS control plane when using
        Babo Cloud inference proxy. The local runtime does not store key hashes;
        non-loopback callers should use Nest, not direct runtime HTTP.
        """
        _ = token
        return None
