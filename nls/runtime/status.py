"""Runtime status and configuration queries.

Standalone functions that build status dicts and resolve config values
from runtime subsystems.  AgentRuntime delegates here.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_WAKE_PROMPT = (
    "You have just been initialized. Greet the user warmly and concisely. "
    "If you do not have a name yet, ask what they would like to call you. "
    "Then ask what they would like to work on. Do NOT mention internal architecture "
    "or technical details. Do NOT introduce yourself with an ID or UUID."
)


def get_wake_prompt(config: dict) -> str | None:
    """Return the first-message wake prompt, or None if disabled."""
    fm_cfg = config.get("first_message", {})
    if not fm_cfg.get("enabled", True):
        return None
    custom = fm_cfg.get("prompt")
    if custom:
        return custom
    return _DEFAULT_WAKE_PROMPT


def is_agentic_enabled(config: dict) -> bool:
    return config.get("agency", {}).get(
        "agentic_loop", {},
    ).get("enabled", False)


def get_agentic_config(config: dict) -> Any:
    """Build an AgenticConfig from the agency section of config."""
    from nls.agentic.types import AgenticConfig
    cfg = config.get("agency", {}).get("agentic_loop", {})
    return AgenticConfig(
        max_iterations=cfg.get("max_iterations", 100),
        tool_timeout_seconds=cfg.get("tool_timeout_seconds", 30),
        max_context_chars=cfg.get("max_context_chars", 80_000),
        result_max_chars=cfg.get("result_max_chars", 12_000),
        max_continuation_passes=cfg.get("max_continuation_passes", 2),
        cortisol_redirect_threshold=cfg.get("cortisol_redirect_threshold", 0.45),
        cortisol_abort_threshold=cfg.get("cortisol_abort_threshold", 0.75),
    )


def get_status(
    *,
    agent_id: str,
    agent_name: str | None,
    config: dict,
    hypothalamus: Any | None,
    ans: Any | None,
    calibrator: Any | None,
    domain_db: Any | None,
    self_state: Any | None,
    working_memory: Any | None,
    narrative_self: Any | None,
    theory_of_mind: Any | None,
    predictive: Any | None,
    network_dynamics: Any | None,
    turn_count: int,
    sleep_count: int,
    last_interaction: float | None,
    sections: set[str] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serialisable agent status dict.

    When *sections* is provided, only the named sections are included.
    """
    def _want(name: str) -> bool:
        return sections is None or name in sections

    status: dict[str, Any] = {}

    if _want("core"):
        status["agent_id"] = agent_id
        status["agent_name"] = agent_name or agent_id[:8]
        status["turn_count"] = turn_count
        status["sleep_count"] = sleep_count
        last = last_interaction
        if last:
            status["last_interaction"] = datetime.fromtimestamp(last).isoformat()

    if _want("hormones") and hypothalamus is not None:
        try:
            status["hormones"] = {
                k: round(v.level, 4)
                for k, v in hypothalamus.hormones.items()
            }
        except Exception:
            pass

    if _want("ans") and ans is not None:
        try:
            status["ans"] = {
                "is_sleeping": ans.is_sleeping,
                "signal_count": getattr(ans, "learnable_signal_count", 0),
                "circadian_phase": getattr(
                    getattr(ans, "circadian", None), "current_phase", "unknown"
                ),
            }
        except Exception:
            pass

    if _want("thalamus") and calibrator is not None:
        try:
            bands = getattr(calibrator, "bands", None)
            if bands:
                status["thalamus_bands"] = {
                    k: round(v, 4) for k, v in bands.items()
                }
        except Exception:
            pass

    if _want("heartbeat") and self_state is not None:
        try:
            ss = self_state
            hb: dict[str, Any] = {
                "bpm": round(getattr(ss, "bpm", 0.0), 2),
                "beat_count": getattr(ss, "beat_count", 0),
                "alive": True,
                "energy": round(getattr(ss, "energy", 1.0), 3),
                "mood_label": getattr(ss, "mood_label", "neutral"),
                "valence": round(getattr(ss, "valence", 0.0), 3),
                "arousal": round(getattr(ss, "arousal", 0.5), 3),
                "engagement": round(getattr(ss, "engagement", 0.0), 3),
                "bonding": round(getattr(ss, "bonding", 0.0), 3),
                "coherence": round(getattr(ss, "coherence", 0.0), 3),
                "flow": getattr(ss, "flow", False),
                "delta_valence": round(getattr(ss, "delta_valence", 0.0), 5),
                "delta_arousal": round(getattr(ss, "delta_arousal", 0.0), 5),
                "delta_coherence": round(getattr(ss, "delta_coherence", 0.0), 5),
                "felt_idle": getattr(ss, "felt_idle", ""),
                "momentum": getattr(ss, "momentum", ""),
                "narrative_coherence": round(getattr(ss, "narrative_coherence", 0.0), 3),
                "coherence_label": getattr(ss, "coherence_label", ""),
                "episode_arc": getattr(ss, "episode_arc", ""),
                "conv_temperature": round(getattr(ss, "conv_temperature", 0.5), 3),
                "conv_temperature_label": getattr(ss, "conv_temperature_label", ""),
                "dominant_network": getattr(ss, "dominant_network", ""),
                "network_ecn": round(getattr(ss, "network_ecn", 0.0), 3),
                "network_sn": round(getattr(ss, "network_sn", 0.0), 3),
                "network_dmn": round(getattr(ss, "network_dmn", 0.0), 3),
                "prediction_error": round(getattr(ss, "prediction_error", 0.0), 3),
            }
            status["heartbeat"] = hb
        except Exception:
            pass

    if _want("working_memory") and working_memory is not None:
        try:
            status["working_memory"] = working_memory.get_summary()
        except Exception:
            pass

    if _want("knowledge") and domain_db is not None:
        try:
            status["fact_count"] = domain_db.fact_count()
        except Exception:
            pass

    if _want("narrative_self") and narrative_self is not None:
        try:
            status["narrative_self"] = narrative_self.get_summary()
        except Exception:
            pass

    if _want("theory_of_mind") and theory_of_mind is not None:
        try:
            status["theory_of_mind"] = theory_of_mind.get_summary()
        except Exception:
            pass

    if _want("predictive") and predictive is not None:
        try:
            status["predictive"] = predictive.get_summary()
        except Exception:
            pass

    if _want("network_dynamics") and network_dynamics is not None:
        try:
            status["network_dynamics"] = network_dynamics.get_summary()
        except Exception:
            pass

    return status
