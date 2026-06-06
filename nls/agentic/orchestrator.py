"""v4 sub-agent orchestrator — delegation waves, spawning, parallel execution.

Manages the delegation lifecycle:
1. Parse delegation requests from the executor (virtual `delegate` tool).
2. Build dependency waves from plan steps.
3. Spawn sub-agents via recursive `run_loop()` with scoped config.
4. Collect results and propagate back to the parent loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from nls.tools.agent_tools.base import AgentTool

from .bridge import LoopHooks
from .events import AgentEvent, EventType, emit
from .plan_store import PlanStore, get_delegation_waves
from .types import LoopConfig, LoopResult

logger = logging.getLogger(__name__)

MAX_SUB_AGENT_ITERATIONS = 12
MAX_CONCURRENT_DELEGATIONS = 3


@dataclass
class DelegationRequest:
    """A request to delegate a task to a sub-agent."""

    task: str = ""
    step_id: str = ""
    plan_id: str = ""
    sub_plan_id: str = ""
    sub_plan_context: str = ""
    max_steps: int = MAX_SUB_AGENT_ITERATIONS
    parent_anchor_summary: str = ""


@dataclass
class DelegationResult:
    """Result from a completed sub-agent delegation."""

    step_id: str = ""
    success: bool = False
    response: str = ""
    iterations: int = 0
    tools_used: list[str] = field(default_factory=list)
    error: str = ""


def _build_scoped_config(parent_config: LoopConfig) -> LoopConfig:
    """Create a scoped config for sub-agents with lower limits."""
    return LoopConfig(
        max_iterations=MAX_SUB_AGENT_ITERATIONS,
        max_tool_calls=50,
        per_tool_retry_limit=parent_config.per_tool_retry_limit,
        total_timeout_seconds=min(parent_config.total_timeout_seconds / 2, 300),
        tool_timeout_seconds=parent_config.tool_timeout_seconds,
        context_window_tokens=parent_config.context_window_tokens,
        reserve_tokens=parent_config.reserve_tokens,
        keep_recent_tokens=parent_config.keep_recent_tokens,
        digest_threshold=parent_config.digest_threshold,
        result_max_chars=parent_config.result_max_chars,
        anchor_tool_result_min_chars=parent_config.anchor_tool_result_min_chars,
        relay_compact_message_chars=parent_config.relay_compact_message_chars,
        max_new_tokens=parent_config.max_new_tokens,
        compaction_timeout=parent_config.compaction_timeout,
        temperature=parent_config.temperature,
        repetition_penalty=parent_config.repetition_penalty,
        enable_parallel_tools=parent_config.enable_parallel_tools,
        enable_cognitive_digest=parent_config.enable_cognitive_digest,
        enable_delegation=False,
    )


def _build_scoped_tools(
    tools: dict[str, AgentTool],
    workspace: str,
) -> dict[str, AgentTool]:
    """Build tool dict for sub-agents: no delegate, read-only plan."""
    from nls.tools.agent_tools.plan import PlanReadOnlyTool

    scoped = {}
    for name, tool in tools.items():
        if name == "delegate":
            continue
        if name == "plan":
            scoped["plan"] = PlanReadOnlyTool(workspace)
        else:
            scoped[name] = tool
    return scoped


def _build_sub_agent_context(
    request: DelegationRequest,
    system_prompt: str = "",
) -> list[dict]:
    """Build initial context for a sub-agent."""
    parts = []
    if system_prompt:
        parts.append({"role": "system", "content": system_prompt})

    user_content = f"DELEGATED TASK: {request.task}"
    if request.sub_plan_context:
        user_content += f"\n\n{request.sub_plan_context}"
    if request.parent_anchor_summary:
        user_content += (
            f"\n\nPARENT CONTEXT SUMMARY:\n{request.parent_anchor_summary}"
        )

    parts.append({"role": "user", "content": user_content})
    return parts


async def _run_sub_agent(
    request: DelegationRequest,
    tools: dict[str, AgentTool],
    config: LoopConfig,
    hooks: LoopHooks,
    vllm_client: Any,
    *,
    abort_signal: asyncio.Event | None = None,
    on_event: Callable | None = None,
    system_prompt: str = "",
) -> DelegationResult:
    """Spawn a single sub-agent by calling run_loop recursively."""
    from .loop import run_loop

    scoped_config = _build_scoped_config(config)
    context = _build_sub_agent_context(request, system_prompt)
    sub_vllm = getattr(config, "delegate_vllm_client", None) or vllm_client

    sub_hooks = LoopHooks(
        transform_context=hooks.transform_context,
        on_tool_success=hooks.on_tool_success,
        on_tool_error=hooks.on_tool_error,
        on_after_tool=hooks.on_after_tool,
        tick_hypothalamus=hooks.tick_hypothalamus,
        ans_tool_learning=hooks.ans_tool_learning,
        ans_on_response=hooks.ans_on_response,
        wm_upsert_digest=hooks.wm_upsert_digest,
        log_event=hooks.log_event,
    )

    await emit(on_event, AgentEvent(
        EventType.DELEGATE_SPAWN,
        {"step_id": request.step_id, "task": request.task},
    ))

    try:
        result: LoopResult = await run_loop(
            context=context,
            tools=tools,
            config=scoped_config,
            hooks=sub_hooks,
            vllm_client=sub_vllm,
            abort_signal=abort_signal,
            user_input=request.task,
        )

        success = result.exit_reason in ("task_complete", "orchestrator_terminated") and not result.aborted

        await emit(on_event, AgentEvent(
            EventType.DELEGATE_COMPLETE if success else EventType.DELEGATE_FAILED,
            {
                "step_id": request.step_id,
                "success": success,
                "iterations": result.iterations,
                "exit_reason": result.exit_reason,
                "response_preview": result.final_response[:500],
            },
        ))

        return DelegationResult(
            step_id=request.step_id,
            success=success,
            response=result.final_response,
            iterations=result.iterations,
            tools_used=result.tools_used,
        )

    except Exception as exc:
        logger.warning("Sub-agent for step %s failed: %s", request.step_id, exc)
        await emit(on_event, AgentEvent(
            EventType.DELEGATE_FAILED,
            {"step_id": request.step_id, "error": str(exc)[:300]},
        ))
        return DelegationResult(
            step_id=request.step_id,
            success=False,
            error=str(exc)[:500],
        )


async def execute_delegations(
    delegations: list[DelegationRequest],
    tools: dict[str, AgentTool],
    config: LoopConfig,
    hooks: LoopHooks,
    plan_store: PlanStore | None,
    vllm_client: Any,
    *,
    abort_signal: asyncio.Event | None = None,
    on_event: Callable | None = None,
    workspace: str = "",
    system_prompt: str = "",
) -> list[DelegationResult]:
    """Execute delegation requests in dependency-aware waves.

    If a PlanStore is provided, uses dependency graph to build waves.
    Otherwise runs delegations sequentially.
    """
    scoped_tools = _build_scoped_tools(tools, workspace)
    results: list[DelegationResult] = []

    if plan_store and delegations and delegations[0].plan_id:
        plan = plan_store.load(delegations[0].plan_id)
        if plan:
            waves = get_delegation_waves(plan)
            delegation_map = {d.step_id: d for d in delegations}

            for wave in waves:
                wave_requests = [
                    delegation_map[s.id]
                    for s in wave
                    if s.id in delegation_map
                ]
                if not wave_requests:
                    continue

                # Process wave in batches of MAX_CONCURRENT_DELEGATIONS
                for batch_start in range(0, len(wave_requests), MAX_CONCURRENT_DELEGATIONS):
                    batch = wave_requests[batch_start:batch_start + MAX_CONCURRENT_DELEGATIONS]

                    if len(batch) == 1:
                        r = await _run_sub_agent(
                            batch[0], scoped_tools, config, hooks, vllm_client,
                            abort_signal=abort_signal,
                            on_event=on_event,
                            system_prompt=system_prompt,
                        )
                        results.append(r)
                    else:
                        tasks = [
                            _run_sub_agent(
                                req, scoped_tools, config, hooks, vllm_client,
                                abort_signal=abort_signal,
                                on_event=on_event,
                                system_prompt=system_prompt,
                            )
                            for req in batch
                        ]
                        wave_results = await asyncio.gather(*tasks, return_exceptions=True)
                        for idx, wr in enumerate(wave_results):
                            if isinstance(wr, Exception):
                                results.append(DelegationResult(
                                    step_id=batch[idx].step_id,
                                    success=False,
                                    error=str(wr)[:500],
                                ))
                            else:
                                results.append(wr)

                    if abort_signal and abort_signal.is_set():
                        break

                if abort_signal and abort_signal.is_set():
                    break

            return results

    for req in delegations:
        r = await _run_sub_agent(
            req, scoped_tools, config, hooks, vllm_client,
            abort_signal=abort_signal,
            on_event=on_event,
            system_prompt=system_prompt,
        )
        results.append(r)
        if abort_signal and abort_signal.is_set():
            break

    return results
