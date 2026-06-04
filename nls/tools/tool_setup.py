"""Tool Setup — extracted from ServerRuntime.setup_agent_tools_v2 (M-014).

Provides ``setup_tools()`` to initialise the v2 tool system (coding tools,
skill tools, plan, browser, MCP proxies) for any runtime.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def setup_tools(
    agent_id: str,
    agent_dir: Path,
    runtime: Any,
    config: dict[str, Any],
    *,
    skill_loader: Any | None = None,
    enabled_skills: list[str] | None = None,
    ans: Any | None = None,
    calibrator: Any | None = None,
    theory_of_mind: Any | None = None,
    narrative_self: Any | None = None,
    working_memory: Any | None = None,
    dual_wm: Any | None = None,
    channel_registry: Any | None = None,
    on_bash_output: Any | None = None,
) -> tuple[list[Any], list[dict], Any | None, Any | None]:
    """Create the full v2 tool set.

    Returns ``(tools, openai_schemas, scheduler_manager, team_manager)``.
    """
    from .agent_tools import create_coding_tools, tools_to_openai_schema

    work_dir = str(agent_dir / "workspace")
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    agency_cfg = config.get("agency", {})
    agentic_cfg = agency_cfg.get("agentic_loop", {})
    browser_headless = agency_cfg.get("browser_headless", False)

    runtime_url = agency_cfg.get("runtime_url", "")
    if not runtime_url:
        runtime_url = os.environ.get("NLS_RUNTIME_PUBLIC_URL", "").strip()
    if not runtime_url:
        host = os.environ.get("NLS_HOST", "127.0.0.1")
        port = os.environ.get("NLS_PORT", "9222")
        runtime_url = f"http://{host}:{port}"

    gpu_worker_secret = os.environ.get("NLS_GPU_WORKER_SECRET", "")

    browser_profile_dir = str(agent_dir / "browser_profile")
    browser_cdp_url = os.environ.get("NLS_BROWSER_CDP_URL", "")

    _bash_blocked = [
        "nls.cli", "python -m nls",
        "server.main:app", "server.main:application",
        "rm -rf /", "rm -rf ~", "mkfs.", ":(){:|:&};:", "kill -9 1",
        "git reset --hard",
        "git clean -fdx",
    ]

    tools, scheduler_manager = create_coding_tools(
        work_dir,
        bash_timeout=agentic_cfg.get("tool_timeout_seconds", 120),
        blocked_commands=_bash_blocked,
        on_bash_output=on_bash_output,
        browser_headless=browser_headless,
        browser_profile_dir=browser_profile_dir,
        browser_cdp_url=browser_cdp_url,
        runtime_url=runtime_url,
        data_dir=str(agent_dir.parent.parent),
        agent_id=agent_id,
        gpu_worker_secret=gpu_worker_secret,
    )

    # File-change ledger — inject into write/edit tools so every successful
    # file mutation is recorded with a unified diff and author attribution.
    _file_ledger = None
    _read_index = None
    _guardrails_registry = None
    try:
        from .agent_tools.guardrails_registry import AgentGuardrailsRegistry
        _guardrails_registry = AgentGuardrailsRegistry(agent_dir)
        runtime._guardrails_registry = _guardrails_registry
    except Exception as _gre:
        logger.warning("Agent %s: guardrails registry init failed: %s", agent_id, _gre)
    try:
        from .agent_tools.file_ledger import FileLedger, FileHistoryTool
        _agentic_cfg = config.get("agentic_loop", {}) or {}
        _enable_read_index = _agentic_cfg.get("enable_read_index", True)
        _file_ledger = FileLedger(agent_dir / "file_ledger.jsonl")
        if _enable_read_index:
            from .agent_tools.read_index import AgentReadIndex
            _read_index = AgentReadIndex(agent_dir)
            _file_ledger.set_read_index(_read_index)
        _orchestrator_meta = {"role": "orchestrator", "loop_id": agent_id}
        _file_cache = next(
            (
                getattr(t, "_file_state_cache", None)
                for t in tools
                if getattr(t, "name", "") == "read"
            ),
            None,
        )
        _shared_cwd_ref = next(
            (getattr(t, "_shared_cwd", None) for t in tools
             if getattr(t, "_shared_cwd", None) is not None),
            None,
        )
        for _t in tools:
            _tname = getattr(_t, "name", None)
            if _tname in ("write", "edit"):
                _t._ledger = _file_ledger
                _t._ledger_meta = _orchestrator_meta
            if _tname == "read" and _read_index is not None:
                _t._read_index = _read_index
                _t._reader_label = "orchestrator"
                _t._loop_id = agent_id
        tools.append(FileHistoryTool(
            _file_ledger,
            file_state_cache=_file_cache,
            cwd=work_dir,
            shared_cwd=_shared_cwd_ref,
        ))
        logger.info("Agent %s: file ledger initialized at %s", agent_id, _file_ledger._path)
    except Exception as _le:
        logger.warning("Agent %s: file ledger init failed: %s", agent_id, _le)

    # Plan tool
    try:
        from .agent_tools.plan import create_plan_tool

        _NO_THINK_BODY = {"chat_template_kwargs": {"enable_thinking": False}}

        async def _resolve_inference() -> tuple[Any, str | None]:
            if hasattr(runtime, "inference_pipeline"):
                return runtime.inference_pipeline()
            client = getattr(runtime, "vllm_client", None)
            return client, None

        async def _plan_verify(prompt: str) -> str:
            client, adapter = await _resolve_inference()
            if client is None:
                return "ALL_CRITERIA_MET"
            from nls.runtime.inference_compat import prepare_micro_inference

            _micro_msgs, _micro_body = prepare_micro_inference(
                [{"role": "user", "content": prompt}],
                vllm_client=client,
                adapter_name=adapter,
            )
            result = await client.generate(
                adapter_name=adapter,
                messages=_micro_msgs,
                max_tokens=512, temperature=0.1,
                extra_body=_micro_body,
            )
            return getattr(result, "text", str(result)).strip()

        async def _dep_inference(prompt: str) -> str:
            """Dedicated inference for dependency graph fixing — higher token budget."""
            client, adapter = await _resolve_inference()
            if client is None:
                raise RuntimeError("No inference client for dependency inference")
            from nls.runtime.inference_compat import prepare_micro_inference

            _micro_msgs, _micro_body = prepare_micro_inference(
                [{"role": "user", "content": prompt}],
                vllm_client=client,
                adapter_name=adapter,
            )
            result = await client.generate(
                adapter_name=adapter,
                messages=_micro_msgs,
                max_tokens=1024, temperature=0.0,
                extra_body=_micro_body,
            )
            return getattr(result, "text", str(result)).strip()

        plan_tool = create_plan_tool(
            work_dir,
            inference_fn=_plan_verify,
            dep_inference_fn=_dep_inference,
        )
        tools.append(plan_tool)

        # Wire CWD-switch so the orchestrator's tools move into the
        # project directory when a plan sets project_dir.
        _shared_cwd_ref = next(
            (getattr(t, "_shared_cwd", None) for t in tools
             if getattr(t, "_shared_cwd", None) is not None),
            None,
        )
        _bash_ref = next(
            (t for t in tools if getattr(t, "name", "") == "bash"), None,
        )
        if _shared_cwd_ref is not None:
            def _switch_cwd(pd_abs: str, _scwd=_shared_cwd_ref, _bt=_bash_ref) -> None:
                from pathlib import Path as _P
                _P(pd_abs).mkdir(parents=True, exist_ok=True)
                _scwd.path = pd_abs
                if _bt is not None and hasattr(_bt, "_cwd"):
                    _bt._cwd = pd_abs
                logger.info("Orchestrator CWD switched to %s", pd_abs)
            plan_tool.set_cwd_switch_fn(_switch_cwd)
            # Reset CWD to workspace root when a plan completes, so that
            # subsequent writes (research notes, new projects) are not placed
            # inside the completed project's folder (KL #403).
            plan_tool.set_cwd_reset_fn(_switch_cwd)

        def _get_plan_project_dir() -> str:
            try:
                store = plan_tool.get_store()
                active = store.find_active()
                if active and active.project_dir:
                    return active.project_dir
                return store.find_any_project_dir()
            except Exception:
                return ""

        def _plan_blocks_server_install() -> bool:
            try:
                store = plan_tool.get_store()
                active = store.find_active()
                if not active:
                    return False
                return bool(getattr(active, "tech_stack", None))
            except Exception:
                return False

        for _t in tools:
            if hasattr(_t, "set_plan_project_dir_fn"):
                _t.set_plan_project_dir_fn(_get_plan_project_dir)
            if hasattr(_t, "set_plan_blocks_server_install_fn"):
                _t.set_plan_blocks_server_install_fn(_plan_blocks_server_install)

        _ring_wm = dual_wm if dual_wm is not None else working_memory
        if plan_tool is not None and _ring_wm is not None:
            if hasattr(_ring_wm, "set_plan_requirements"):
                def _sync_plan_context(
                    requirements: str, tech_block: str, stack: dict,
                ) -> None:
                    try:
                        if requirements:
                            _ring_wm.set_plan_requirements(requirements)
                        elif hasattr(_ring_wm, "set_plan_requirements"):
                            _ring_wm.set_plan_requirements("")
                        if tech_block and stack:
                            _ring_wm.set_plan_tech_stack(tech_block)
                        elif hasattr(_ring_wm, "clear_plan_tech_stack"):
                            _ring_wm.clear_plan_tech_stack()
                    except Exception:
                        pass

                def _clear_plan_context() -> None:
                    try:
                        if hasattr(_ring_wm, "clear_plan_context"):
                            _ring_wm.clear_plan_context()
                    except Exception:
                        pass

                plan_tool.set_context_sync_fn(_sync_plan_context)
                plan_tool.set_context_clear_fn(_clear_plan_context)
                try:
                    _active = plan_tool.get_store().find_active()
                    if _active:
                        plan_tool.sync_context_from_plan(_active)
                except Exception:
                    pass

        if plan_tool is not None and ans is not None:
            try:
                ps = plan_tool.get_store()
                ans._plan_fact_validator = lambda _s=ps: _s.find_active()
            except Exception:
                pass
    except Exception as exc:
        logger.warning("Agent %s: PlanTool init failed: %s", agent_id, exc)

    # Shared scheduler manager from app state
    try:
        from server.main import app as _app
        shared_mgr = getattr(_app.state, "scheduler_manager", None)
        if shared_mgr is not None:
            scheduler_manager = shared_mgr
            for t in tools:
                if hasattr(t, "_manager"):
                    t._manager = shared_mgr
                if getattr(t, "name", "") == "scheduler" and hasattr(t, "_agent_id"):
                    t._agent_id = agent_id
    except Exception:
        pass

    if scheduler_manager and not scheduler_manager._task:
        scheduler_manager.start()

    # Skill tools
    if skill_loader is not None:
        _inject_skill_tools(
            tools, agent_id, agent_dir, skill_loader,
            enabled_skills or [],
            calibrator=calibrator, ans=ans,
            theory_of_mind=theory_of_mind,
            narrative_self=narrative_self,
            working_memory=working_memory,
        )

    # task_complete tool — explicit "I'm done" signal from the agent
    try:
        from .agent_tools.task_complete import create_task_complete_tool
        tools.append(create_task_complete_tool())
    except Exception as exc:
        logger.warning("Agent %s: task_complete tool init failed: %s", agent_id, exc)

    # Contacts tool — on-demand address book for all channels
    try:
        from .agent_tools.contacts import create_contacts_tool
        tools.append(create_contacts_tool(agent_id))
    except Exception as exc:
        logger.warning("Agent %s: contacts tool init failed: %s", agent_id, exc)

    # Email ledger — history of sent and received emails
    try:
        from .agent_tools.email_ledger import EmailLedger, EmailHistoryTool
        _email_ledger = EmailLedger(agent_dir)
        tools.append(EmailHistoryTool(agent_id, _email_ledger))
        logger.info("Agent %s: email ledger initialized at %s", agent_id, _email_ledger._path)
    except Exception as exc:
        logger.warning("Agent %s: email ledger init failed: %s", agent_id, exc)

    # Chat transcript search — full user-visible conversation log
    try:
        from .agent_tools.chat_history import create_chat_history_tool
        tools.append(create_chat_history_tool(agent_dir))
    except Exception as exc:
        logger.warning("Agent %s: chat_history tool init failed: %s", agent_id, exc)

    # MCP proxy wiring
    for t in tools:
        if getattr(t, "name", "") == "mcp_manage" and hasattr(t, "_tools_ref"):
            t._tools_ref = tools
            mgr = getattr(t, "_manager", None)
            if mgr:
                existing_names = {p.name for p in tools}
                for p in mgr.get_all_proxies():
                    if p.name not in existing_names:
                        tools.append(p)

    # Team tool — bridges plans, delegates, and kanban.
    # The TeamManager is returned so the runtime can wire delegate_manager
    # and connection_manager after they become available.
    _team_manager = None
    try:
        from nls.agentic.team_manager import TeamManager
        from nls.agentic.plan_store import PlanStore
        from .agent_tools.team import create_team_tool

        _workspace = str(agent_dir / "workspace")
        _plan_store = PlanStore(_workspace)

        _todo_tool_ref = next(
            (t for t in tools if getattr(t, "name", "") == "todo"), None,
        )
        _todo_store = getattr(_todo_tool_ref, "_store", None) if _todo_tool_ref else None

        _team_manager = TeamManager(
            agent_dir=agent_dir,
            plan_store=_plan_store,
            todo_store=_todo_store,
            agent_id=agent_id,
        )
        tools.append(create_team_tool(_team_manager))
        logger.info("Agent %s: team tool initialized", agent_id)
    except Exception as exc:
        logger.warning("Agent %s: team tool init failed: %s", agent_id, exc)

    # delegate_ring tool — orchestrator ring manipulation for sub-agents.
    # DelegateManager is wired later by the runtime; the tool gracefully
    # returns an error if called before the manager is available.
    _delegate_ring_tool = None
    try:
        from .agent_tools.delegate_ring import DelegateRingTool
        _delegate_ring_tool = DelegateRingTool(delegate_manager=None)
        tools.append(_delegate_ring_tool)
        logger.info("Agent %s: delegate_ring tool initialized", agent_id)
    except Exception as exc:
        logger.warning("Agent %s: delegate_ring tool init failed: %s", agent_id, exc)

    # Wire plan → team_manager so plan(action='delete') can cancel teams.
    _plan_tool = next((t for t in tools if getattr(t, "name", "") == "plan"), None)
    if _plan_tool is not None and _team_manager is not None:
        _plan_tool._team_manager = _team_manager

    if _team_manager is not None and _file_ledger is not None:
        _team_manager.set_file_ledger(_file_ledger)

    try:
        from server.main import app as _app

        _sm = getattr(_app.state, "squad_manager", None)
        if _sm is not None and _sm.get_squad_for_agent(agent_id) is not None:
            from .agent_tools.squad import (
                SquadEscalateTool,
                SquadMessageTool,
                SquadReportDoneTool,
                SquadTool,
            )

            tools.extend([
                SquadTool(_sm, agent_id),
                SquadEscalateTool(_sm, agent_id),
                SquadMessageTool(_sm, agent_id),
                SquadReportDoneTool(_sm, agent_id),
            ])
            logger.info("Agent %s: squad tools initialized", agent_id)
    except Exception as exc:
        logger.debug("Agent %s: squad tools init skipped: %s", agent_id, exc)

    # Wire plan → todo lifecycle auto-sync.
    _todo_tool = next((t for t in tools if getattr(t, "name", "") == "todo"), None)
    if _plan_tool is not None and _todo_tool is not None and hasattr(_plan_tool, "set_todo_complete_fn"):

        def _resolve_todo_id(todo_id: str) -> str:
            """Resolve a possibly-hallucinated todo ID via prefix match."""
            if _todo_tool._store.get(todo_id) is not None:
                return todo_id
            for item in _todo_tool._store.list_items():
                if todo_id.startswith(item.id) or item.id.startswith(todo_id):
                    logger.info(
                        "Plan→todo ID fuzzy resolve: '%s' → '%s'",
                        todo_id, item.id,
                    )
                    return item.id
            return todo_id

        async def _auto_todo_complete(todo_id: str) -> None:
            await _todo_tool._complete({"id": _resolve_todo_id(todo_id)})
        _plan_tool.set_todo_complete_fn(_auto_todo_complete)

        if hasattr(_plan_tool, "set_todo_start_fn"):
            async def _auto_todo_start(todo_id: str, plan_id: str) -> None:
                await _todo_tool._update({
                    "id": _resolve_todo_id(todo_id),
                    "status": "in_progress",
                    "plan_id": plan_id,
                })
            _plan_tool.set_todo_start_fn(_auto_todo_start)

    # WM (Cryptex) navigation tool
    if working_memory is not None:
        try:
            from .agent_tools.wm_tool import WMTool
            _wm_tool = WMTool(working_memory)
            tools.append(_wm_tool)
            logger.info("Agent %s: WM tool initialized", agent_id)
        except Exception as exc:
            logger.warning("Agent %s: WM tool init failed: %s", agent_id, exc)

    # Populate Tools+MCP ring (Ring 11) in the cryptex
    _populate_tools_ring(working_memory, tools)

    openai_schemas = tools_to_openai_schema(tools)

    logger.info(
        "Agent %s: tools initialized (%d): %s",
        agent_id, len(tools),
        ", ".join(t.name for t in tools),
    )

    return tools, openai_schemas, scheduler_manager, _team_manager


def _inject_skill_tools(
    tools: list,
    agent_id: str,
    agent_dir: Path,
    skill_loader: Any,
    enabled: list[str],
    *,
    calibrator: Any | None = None,
    ans: Any | None = None,
    theory_of_mind: Any | None = None,
    narrative_self: Any | None = None,
    working_memory: Any | None = None,
) -> None:
    """Append skill-provided tools (factories, CLI wrappers, extras)."""
    try:
        skill_tools = skill_loader.tools_for(enabled)
        factories = skill_loader.tool_factories_for(enabled)
        for fac in factories:
            try:
                skill_tools.append(fac(agent_id))
            except Exception as exc:
                logger.warning("Agent %s: tool factory %r failed: %s", agent_id, fac, exc)

        valid = [
            t for t in skill_tools
            if all(hasattr(t, a) for a in ("name", "description", "parameters", "execute"))
        ]

        _has_schemas = any(
            sk.meta and sk.meta.config_schema
            for sk in skill_loader.skills.values()
            if sk.name in enabled or enabled == ["*"]
        )
        if _has_schemas:
            try:
                from .agent_tools.skill_configure import create_skill_configure_tool
                valid.append(create_skill_configure_tool(agent_id))
            except Exception:
                pass

        try:
            from .agent_tools.clawhub import create_clawhub_tool
            valid.append(create_clawhub_tool(agent_id))
        except Exception:
            pass

        try:
            from .agent_tools.crystallize import create_crystallize_tool
            valid.append(create_crystallize_tool(
                skill_loader=skill_loader,
                calibrator=calibrator, ans=ans,
                data_dir=str(agent_dir.parent.parent),
                theory_of_mind=theory_of_mind,
                narrative_self=narrative_self,
                working_memory=working_memory,
            ))
        except Exception:
            pass

        # CLI wrappers
        try:
            workspace = agent_dir / "workspace"
            cli_wrappers = skill_loader.cli_wrappers_for(
                enabled, calibrator=calibrator,
                cwd=str(workspace) if workspace.is_dir() else str(agent_dir),
            )
            valid.extend(cli_wrappers)
        except Exception:
            pass

        for st in valid:
            if not hasattr(st, "_skill_name"):
                st._skill_name = None

        if valid:
            tools.extend(valid)
            logger.info(
                "Agent %s: %d skill tool(s) injected (enabled=%s)",
                agent_id, len(valid), enabled,
            )
    except Exception as exc:
        logger.warning("Agent %s: skill tool injection failed: %s", agent_id, exc)


_TOOL_GROUP_MAP: dict[str, str] = {
    "read": "coding", "write": "coding", "edit": "coding",
    "glob": "coding", "grep": "coding", "bash": "coding",
    "list_dir": "coding",
    "plan": "planning", "todo": "planning", "delegate": "planning",
    "team": "planning", "scheduler": "planning", "task_complete": "planning",
    "email": "communication", "whatsapp": "communication",
    "telegram": "communication", "contacts": "communication",
    "email_history": "communication",
    "chat_history": "memory",
    "web_search": "research", "web_fetch": "research",
    "browser": "research", "browser_navigate": "research",
    "wm": "memory",
    "discover_tools": "meta",
}


def _populate_tools_ring(
    working_memory: Any | None, tools: list[Any],
) -> None:
    """Populate the cryptex Tools+MCP ring (Ring 11) from initialized tools."""
    if working_memory is None:
        return
    try:
        from nls.brain.cryptex import CryptexMemory, RING_TOOLS_MCP
    except ImportError:
        return
    if not isinstance(working_memory, CryptexMemory):
        return

    ring = working_memory.get_ring(RING_TOOLS_MCP)
    if ring is None:
        return

    from nls.brain.working_memory import WMSlot

    groups: dict[str, list[str]] = {}
    for tool in tools:
        name = getattr(tool, "name", "")
        if not name:
            continue
        group = _TOOL_GROUP_MAP.get(name, "other")
        if name.startswith("mcp_"):
            group = f"mcp:{name}"
        groups.setdefault(group, []).append(name)

    for group, tool_names in groups.items():
        desc = f"{group}: {', '.join(tool_names[:8])}"
        if len(tool_names) > 8:
            desc += f" (+{len(tool_names) - 8} more)"
        ring.upsert_slot(
            domain=f"tool_group.{group}",
            content=desc,
            slot_type="fact",
            salience=0.5,
            source="tool_setup",
            position=group,
        )
