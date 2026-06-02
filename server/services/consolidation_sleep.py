"""Consolidation-only sleep — inference + memory DB, no weight training."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _extract_signal_text(signal: Any) -> str:
    parts: list[str] = []
    if hasattr(signal, "pipe_fact") and signal.pipe_fact:
        parts.append(str(signal.pipe_fact))
    if hasattr(signal, "content") and signal.content:
        parts.append(str(signal.content))
    if hasattr(signal, "signal_type"):
        parts.append(str(signal.signal_type))
    return " | ".join(p for p in parts if p)[:300]


async def _summarize_consolidation(
    vllm_client: Any,
    agent_id: str,
    signal_texts: list[str],
    *,
    adapter_name: str | None = None,
) -> str | None:
    if not vllm_client or not signal_texts:
        return None
    joined = "\n".join(f"- {t}" for t in signal_texts[:20])
    prompt = (
        "Summarize these memory signals from a waking period into a short "
        "consolidation note (2-4 sentences). Focus on durable facts and themes.\n\n"
        f"{joined}"
    )
    try:
        from nls.runtime.inference_compat import prepare_micro_inference

        _micro_msgs, _micro_body = prepare_micro_inference(
            [
                {
                    "role": "system",
                    "content": "You write concise memory consolidation summaries.",
                },
                {"role": "user", "content": prompt},
            ],
            vllm_client=vllm_client,
            adapter_name=adapter_name,
        )
        result = await vllm_client.generate(
            adapter_name=adapter_name,
            messages=_micro_msgs,
            max_tokens=256,
            temperature=0.3,
            top_p=0.9,
            extra_body=_micro_body,
        )
        text = result.text if hasattr(result, "text") else str(result)
        return text.strip() or None
    except Exception as exc:
        logger.warning("Agent %s: consolidation summary failed: %s", agent_id, exc)
        return None


async def run_consolidation_cycle(
    *,
    agent_id: str,
    agent_dir: Path,
    runtime: Any,
) -> dict[str, Any]:
    """Run one consolidation sleep cycle (no weight training)."""
    t0 = time.perf_counter()
    ans = getattr(runtime, "ans", None)
    if ans is None:
        logger.warning("Agent %s: no ANS — skipping consolidation", agent_id)
        return {"success": False, "signals_processed": 0, "consolidation_time": 0.0}

    hypothalamus = getattr(runtime, "hypothalamus", None)
    wm = getattr(runtime, "working_memory", None) or getattr(runtime, "dual_wm", None)
    if wm is not None and hasattr(ans, "absorb_signals_to_rings"):
        try:
            ans.absorb_signals_to_rings(wm)
        except Exception as exc:
            logger.debug("Agent %s: pre-sleep ring absorption: %s", agent_id, exc)

    from nls.brain.autonomic import AgentState

    if ans._state != AgentState.AWAKE:
        ans._state = AgentState.AWAKE
    ans.begin_sleep(hypothalamus)
    try:
        ans.save_state(agent_dir / "ans_state.json")
    except Exception:
        pass

    triaged = ans.triage()
    priority_order = ans.config.sleep_phases.triage.priority_order
    all_signals: list[Any] = []
    for priority in priority_order:
        all_signals.extend(getattr(triaged, priority, []) or [])

    max_aku = ans.config.sleep_phases.consolidation.max_aku_per_cycle
    signals_to_process = all_signals[:max_aku]

    if not signals_to_process:
        ans.wake(hypothalamus)
        return {
            "success": True,
            "signals_processed": 0,
            "consolidation_time": time.perf_counter() - t0,
            "mode": "consolidation",
        }

    stored = 0
    store_fn = getattr(runtime, "_store_learn_signals", None)
    if store_fn is not None:
        try:
            store_fn(signals_to_process, user_input="[sleep consolidation]")
            stored = len(signals_to_process)
        except Exception as exc:
            logger.warning("Agent %s: fact store during sleep failed: %s", agent_id, exc)

    from nls.runtime.inference_compat import resolve_agent_inference

    vllm_client, adapter_name = resolve_agent_inference(runtime)
    texts = [_extract_signal_text(s) for s in signals_to_process if _extract_signal_text(s)]
    summary = await _summarize_consolidation(
        vllm_client, agent_id, texts, adapter_name=adapter_name,
    )
    if summary:
        narrative = getattr(runtime, "narrative_self", None)
        if narrative is not None and hasattr(narrative, "append_consolidation_note"):
            try:
                narrative.append_consolidation_note(summary)
            except Exception:
                pass

    ans.wake(hypothalamus)
    notify = getattr(runtime, "notify_sleep_complete", None)
    if notify is not None:
        try:
            notify(
                sleep_type="sleep",
                consolidation_summary=summary or "",
                signals_processed=len(signals_to_process),
            )
        except Exception as exc:
            logger.debug("Agent %s: notify_sleep_complete: %s", agent_id, exc)

    elapsed = time.perf_counter() - t0
    logger.info(
        "Agent %s: consolidation sleep done (signals=%d stored=%d time=%.1fs)",
        agent_id, len(signals_to_process), stored, elapsed,
    )
    return {
        "success": True,
        "signals_processed": len(signals_to_process),
        "consolidation_time": elapsed,
        "stored": stored,
        "summary": summary or "",
        "mode": "consolidation",
    }
