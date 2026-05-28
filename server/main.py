"""Babo Agent Runtime — FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.config import ServerSettings, get_settings
from server.middleware.auth import verify_auth
from server.routes import agents, chat, completions, filesystem, health
from server.services.agent_manager import AgentManager
from server.services.consciousness_scheduler import ConsciousnessScheduler
from server.services.dual_model_manager import DualModelManager
from server.services.sleep_scheduler import SleepScheduler

if sys.platform == "win32":
    import io

    _log_stream = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace",
    )
else:
    _log_stream = sys.stdout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=_log_stream,
)
logger = logging.getLogger("babo.server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.loop = asyncio.get_running_loop()
    app.state.start_time = time.time()

    settings: ServerSettings = app.state.settings
    t0 = time.perf_counter()

    from server.services.genesis_seed import ensure_bundled_genesis, write_standard_v1_template

    write_standard_v1_template()
    ensure_bundled_genesis(settings.genesis_dir)

    logger.info("=" * 60)
    logger.info("Babo Agent Runtime starting...")
    logger.info("Product mode: %s", settings.product_mode)
    logger.info("Model: %s", settings.hf_model)
    logger.info("Inference: OpenAI-compatible @ %s", settings.vllm_base_url)
    logger.info("Data dir: %s", settings.data_dir)
    logger.info("Sleep enabled: %s (consolidation)", settings.sleep_enabled)
    logger.info("Default genesis: %s", settings.default_genesis)
    logger.info("=" * 60)

    settings.agents_dir.mkdir(parents=True, exist_ok=True)
    settings.genesis_dir.mkdir(parents=True, exist_ok=True)

    model_manager = DualModelManager(
        hf_model=settings.hf_model,
        vllm_base_url=settings.vllm_base_url,
        genesis_dir=settings.genesis_dir,
        agents_dir=settings.agents_dir,
        default_genesis=settings.default_genesis,
        inference_api_key=settings.inference_api_key,
        product_mode=settings.product_mode,
    )
    model_manager.load_models()
    app.state.model_manager = model_manager

    vllm_client = model_manager.vllm_client
    if vllm_client is not None:
        if getattr(model_manager, "_remote_inference", False):
            logger.info(
                "Remote inference at %s — not blocking local startup on upstream /health",
                settings.vllm_base_url,
            )
        else:
            vllm_ready = await vllm_client.wait_until_ready(timeout=300.0)
            if not vllm_ready:
                logger.warning(
                    "Inference backend not ready at %s — requests will fail until it starts.",
                    settings.vllm_base_url,
                )

    sleep_scheduler = SleepScheduler(
        model_manager=model_manager,
        agents_dir=settings.agents_dir,
        product_mode=settings.product_mode,
    )
    if settings.sleep_enabled:
        sleep_scheduler.start()
    app.state.sleep_scheduler = sleep_scheduler

    agent_manager = AgentManager(
        agents_dir=settings.agents_dir,
        genesis_dir=settings.genesis_dir,
        sleep_scheduler=sleep_scheduler,
        model_manager=model_manager,
    )
    app.state.agent_manager = agent_manager

    from server.services.skill_loader import SkillLoader

    bundled_skills = Path(__file__).resolve().parent.parent / "nls" / "skills" / "bundled"
    skill_loader = SkillLoader(
        settings.data_dir / "skills",
        app,
        bundled_dir=bundled_skills,
    )
    await skill_loader.load_all()
    await skill_loader.run_startup_hooks()
    app.state.skill_loader = skill_loader

    from nls.tools.agent_tools.scheduler import SchedulerManager

    scheduler_manager = SchedulerManager(str(settings.data_dir))
    scheduler_manager.start()
    app.state.scheduler_manager = scheduler_manager

    _whitelist = (
        [s.strip() for s in settings.agent_whitelist.split(",") if s.strip()]
        if settings.agent_whitelist else None
    )
    n_loaded = await agent_manager.auto_load_all(whitelist=_whitelist)
    if n_loaded:
        logger.info("Restored %d agent(s) from previous session", n_loaded)
        _todo_sk = skill_loader.skills.get("todo-list")
        if _todo_sk and _todo_sk.context:
            _todo_mgr = getattr(_todo_sk.context, "adapter", None)
            if _todo_mgr and hasattr(_todo_mgr, "_resync_all_idle_intentions"):
                _todo_mgr._resync_all_idle_intentions()

    agent_manager.start_autosave()

    from server.services.connection_manager import ConnectionManager

    connection_manager = ConnectionManager()
    app.state.connection_manager = connection_manager
    sleep_scheduler.connection_manager = connection_manager

    nestjs_url = os.environ.get("NESTJS_URL", "")
    relay_secret = (
        os.environ.get("RUNTIME_SHARED_SECRET")
        or os.environ.get("RUNTIME_SHARED_SECRET", "")
    )
    if nestjs_url:
        from nls.runtime.channels import ChannelRelayClient

        for agent_id, runtime in agent_manager.get_loaded_runtimes().items():
            try:
                relay = ChannelRelayClient(
                    nestjs_url,
                    agent_id,
                    relay_secret,
                    agent_name=getattr(runtime, "_agent_name", "") or "",
                    genesis_version=getattr(runtime, "_genesis_version", "") or "",
                )
                await relay.connect()
                connection_manager.register_relay(agent_id, relay)
            except Exception as exc:
                logger.debug("Dashboard relay skipped for %s: %s", agent_id, exc)

    consciousness_enabled = os.environ.get(
        "NLS_CONSCIOUSNESS_ENABLED", "true",
    ).lower() not in ("false", "0", "no")
    consciousness_scheduler = ConsciousnessScheduler(
        agent_manager=agent_manager,
        connection_manager=connection_manager,
        model_a=vllm_client,
        model_a_tokenizer=model_manager.tokenizer,
        vllm_client=vllm_client,
        sleep_scheduler=sleep_scheduler,
    )
    if vllm_client is not None and consciousness_enabled:
        consciousness_scheduler.start()
        for agent_id in agent_manager.get_loaded_runtimes():
            consciousness_scheduler.register_agent(agent_id)
        logger.info(
            "ConsciousnessScheduler: %d agent(s) registered",
            len(agent_manager.get_loaded_runtimes()),
        )
    elif not consciousness_enabled:
        logger.info("ConsciousnessScheduler DISABLED")
    app.state.consciousness_scheduler = consciousness_scheduler
    agent_manager.consciousness_scheduler = consciousness_scheduler

    def _make_scheduler_agent_message_handler(cs, am):
        import re as _re

        async def _handler(message: str) -> None:
            m = _re.match(r'\[AGENT_MSG\|agent_id=([^\|]+)\|[^\]]*\]', message)
            target_ids = [m.group(1)] if m else list(am.get_loaded_runtimes().keys())
            for agent_id in target_ids:
                entry = cs._agents.get(agent_id) if cs is not None else None
                il = getattr(entry, "inner_loop", None) if entry else None
                if il is not None:
                    il.enqueue_autonomous_dispatch(message, source="scheduler")
        return _handler

    scheduler_manager.set_agent_message_handler(
        _make_scheduler_agent_message_handler(consciousness_scheduler, agent_manager),
    )
    sleep_scheduler.on_sleep_done = consciousness_scheduler.on_sleep_complete
    app.state.daydream_scheduler = None

    logger.info("=" * 60)
    logger.info("Server ready in %.1fs", time.perf_counter() - t0)
    logger.info("Endpoints: http://%s:%d/health", settings.host, settings.port)
    logger.info("API docs:  http://%s:%d/docs", settings.host, settings.port)
    logger.info("=" * 60)

    yield

    logger.info("Shutting down Babo server...")
    await scheduler_manager.stop()
    await skill_loader.run_shutdown_hooks()
    agent_manager.stop_autosave()
    consciousness_scheduler.stop()
    sleep_scheduler.stop()

    for agent_id, runtime in agent_manager._runtimes.items():
        try:
            runtime.shutdown()
            logger.info("Shut down agent %s", agent_id)
        except Exception as exc:
            logger.warning("Failed to shut down agent %s: %s", agent_id, exc)

    await model_manager.async_unload()
    logger.info("Babo server shutdown complete")

    from nls.tools.agent_tools.request_restart import (
        RESTART_EXIT_CODE,
        is_restart_requested,
    )

    if is_restart_requested():
        logger.info("Restart requested — exiting with code %d", RESTART_EXIT_CODE)
        os._exit(RESTART_EXIT_CODE)


def create_app(settings: ServerSettings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title="Babo Agent Runtime",
        description=(
            "Open-source agent runtime with biologically-inspired cognition, "
            "per-agent memory, and BYO OpenAI-compatible inference."
        ),
        version="0.4.0",
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(agents.router, dependencies=[Depends(verify_auth)])
    app.include_router(completions.router, dependencies=[Depends(verify_auth)])
    app.include_router(chat.router)

    app.include_router(filesystem.router, dependencies=[Depends(verify_auth)])

    from server.routes import terminal_ws

    app.include_router(terminal_ws.router)

    from server.routes import files as files_routes
    from server.routes import transcribe
    from server.routes import admin as admin_routes
    from server.routes import skills as skills_routes
    from server.routes import webhooks
    from server.routes import channels
    from server.routes import teams as teams_routes

    app.include_router(files_routes.router, dependencies=[Depends(verify_auth)])
    app.include_router(transcribe.router, dependencies=[Depends(verify_auth)])
    app.include_router(admin_routes.router, dependencies=[Depends(verify_auth)])
    app.include_router(skills_routes.router, dependencies=[Depends(verify_auth)])
    app.include_router(skills_routes.agent_skills_router, dependencies=[Depends(verify_auth)])
    app.include_router(skills_routes.crystallization_router, dependencies=[Depends(verify_auth)])
    app.include_router(skills_routes.clawhub_router, dependencies=[Depends(verify_auth)])
    app.include_router(webhooks.router, prefix="/webhooks")
    app.include_router(channels.router)
    app.include_router(teams_routes.router, dependencies=[Depends(verify_auth)])

    return app


app = create_app()
