"""Runtime factory — builds all brain subsystems for AgentRuntime.

Extracted from the legacy ServerRuntime.initialize() method.
Creates subsystems from per-agent config files, restores persisted
state, and returns everything needed to construct an AgentRuntime.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NLS_ROOT = Path(__file__).resolve().parent.parent


def _load_agent_config(agent_dir: Path, filename: str) -> dict:
    """Load JSON config from agent's per-agent config dir, fallback to global."""
    agent_config = agent_dir / "config" / filename
    if agent_config.exists():
        with open(agent_config, "r", encoding="utf-8") as f:
            return json.load(f)
    global_config = _NLS_ROOT / "config" / filename
    if global_config.exists():
        with open(global_config, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def build_subsystems(
    agent_id: str,
    agent_dir: Path,
    config: dict,
    *,
    vllm_client: Any = None,
    on_sleep_requested: Any = None,
) -> dict[str, Any]:
    """Build all brain subsystems from config files and return them as a dict.

    The dict keys match AgentRuntime constructor parameter names so the
    caller can do ``AgentRuntime(**result)``.
    """
    t0 = time.perf_counter()

    # Resolve base model from ledger
    manifest_path = agent_dir / "ledger.yaml"
    if manifest_path.exists():
        try:
            from nls.ledger.manifest import load_manifest as _lm
            _agent_state = _lm(agent_dir)
            if _agent_state.base_model:
                config.setdefault("inference", {})["hf_model"] = _agent_state.base_model
        except Exception:
            pass

    # -- 1. Hypothalamus ---------------------------------------------------
    hypothalamus = None
    from nls.brain.hypothalamus import HypothalamusEngine
    hormones_cfg = agent_dir / "config" / "hormones.json"
    if hormones_cfg.exists():
        hypothalamus = HypothalamusEngine.from_config(hormones_cfg)
    else:
        global_h = _NLS_ROOT / "config" / "hormones.json"
        if global_h.exists():
            hypothalamus = HypothalamusEngine.from_config(global_h)
    if hypothalamus is not None:
        state_file = agent_dir / "hypothalamus_state.json"
        if state_file.exists():
            hypothalamus.load_state(state_file)
            logger.info("Agent %s: loaded persisted hormonal state", agent_id)

    # -- 2. ANS ------------------------------------------------------------
    ans = None
    taxonomy = None
    ans_data = _load_agent_config(agent_dir, "autonomic.json")
    if ans_data:
        from nls.brain.autonomic import AutonomicNervousSystem, AutonomicConfig
        ans_config = AutonomicConfig(**ans_data)

        from nls.knowledge.taxonomy import TaxonomySeed
        taxonomy_path = _NLS_ROOT / "taxonomy" / "seed_v1.yaml"
        if taxonomy_path.exists():
            taxonomy = TaxonomySeed()
            taxonomy.load(taxonomy_path)
            facts_dir = _NLS_ROOT / "curricula" / "facts"
            if facts_dir.is_dir():
                taxonomy.enrich_from_fact_bank(facts_dir)

        ans = AutonomicNervousSystem(config=ans_config, taxonomy=taxonomy)
        ans_state = agent_dir / "ans_state.json"
        if ans_state.exists():
            ans.load_state(ans_state)

        from nls.brain.autonomic import AgentState
        if ans._state != AgentState.AWAKE:
            logger.info("Agent %s: ANS was %s, forcing wake", agent_id, ans._state.value)
            ans._state = AgentState.AWAKE
            ans._state_entered_at = datetime.utcnow()
            ans._current_sleep_start = None

    # -- 3. Domain / skill experience tracker ------------------------------
    from nls.runtime.domain_experience import ExperienceTracker

    calibrator = ExperienceTracker()
    if calibrator.load_state(agent_dir):
        logger.info("Agent %s: loaded domain experience state", agent_id)
    else:
        calibrator.save_state(agent_dir)

    # -- 4. DomainDB -------------------------------------------------------
    from nls.ledger.domain_db import DomainDB
    domain_db = DomainDB(agent_dir / "knowledge.db", agent_id=agent_id)
    n_facts = domain_db.fact_count()
    logger.info("Agent %s: DomainDB loaded (%d facts)", agent_id, n_facts)

    if taxonomy is not None and taxonomy.loaded and n_facts > 0:
        taxonomy.enrich_from_domain_db(domain_db)

    # -- 4b. Reasoning Distiller -------------------------------------------
    reasoning_distiller = None
    try:
        from nls.knowledge.reasoning import ReasoningDistiller
        reasoning_distiller = ReasoningDistiller(None, None, config)
    except Exception:
        pass

    # -- 5. DMN ------------------------------------------------------------
    dmn = None
    dmn_data = _load_agent_config(agent_dir, "dmn.json")
    if dmn_data:
        from nls.brain.dmn import DefaultModeNetwork
        dmn = DefaultModeNetwork(config_data=dmn_data, domain_db=domain_db)
        dmn.v5_signals = config.get("v5_signal_probes", False)

    # -- 6. Drive Engine ---------------------------------------------------
    drive_engine = None
    if config.get("drives", {}).get("enabled", False):
        drives_data = _load_agent_config(agent_dir, "drives.json")
        if drives_data:
            from nls.brain.drives import DriveEngine
            drive_engine = DriveEngine(drives_data, agent_dir=agent_dir)
            drive_engine.domain_db = domain_db
            drive_engine.load_state(agent_dir)
            logger.info("Agent %s: drive engine initialized", agent_id)

    # -- 7. Agency Engine --------------------------------------------------
    agency = None
    agency_cfg = config.get("agency", {})
    if agency_cfg:
        try:
            from nls.brain.agency import AgencyEngine
            from nls.tools.browser import BrowserConfig, BrowserEngine
            from nls.engine.tools import ToolRegistry, WebBrowseTool

            tools = ToolRegistry()
            tools_cfg = config.get("tools", {})
            ws_cfg = tools_cfg.get("web_search", {})
            browser_cfg_dict = tools_cfg.get("browser", {})

            enabled_tools_set: set[str] | None = None
            enabled_tools_path = agent_dir / "enabled_tools.json"
            if enabled_tools_path.exists():
                try:
                    with open(enabled_tools_path, "r", encoding="utf-8") as f:
                        enabled_tools_set = set(json.load(f).get("enabled", []))
                except Exception:
                    pass

            ws_enabled = ws_cfg.get("enabled", True)
            if enabled_tools_set is not None:
                ws_enabled = ws_enabled and "web_search" in enabled_tools_set
            if ws_enabled:
                browser_config = BrowserConfig.from_dict({
                    **browser_cfg_dict,
                    "max_content_chars": ws_cfg.get("max_content_chars", 4000),
                })
                browser_engine = BrowserEngine(browser_config)
                tools.register(WebBrowseTool(
                    browser_engine=browser_engine,
                    max_results=ws_cfg.get("max_results", 5),
                    max_content_chars=ws_cfg.get("max_content_chars", 4000),
                    max_pages_per_browse=browser_cfg_dict.get("max_pages_per_browse", 3),
                ))

            from nls.engine.tool_loader import load_tools_from_directory, load_tool_from_json
            tools_dir = _NLS_ROOT / "config" / "tools"
            if tools_dir.exists():
                if enabled_tools_set is not None:
                    for tool_file in sorted(tools_dir.glob("*.json")):
                        if tool_file.stem in enabled_tools_set and tool_file.stem != "web_search":
                            load_tool_from_json(tools, tool_file)
                else:
                    for tool_file in sorted(tools_dir.glob("*.json")):
                        if tool_file.stem in {"wikipedia"} and tool_file.stem != "web_search":
                            load_tool_from_json(tools, tool_file)

            tool_exp_path = agent_dir / "tool_experience.json"
            tools.experience.load(tool_exp_path)

            try:
                from nls.engine.tools_builtin import RequestSleepTool
                tools.register(RequestSleepTool(ans=ans))
            except Exception:
                pass

            agency = AgencyEngine(config=agency_cfg, tools=tools)
        except Exception as exc:
            logger.warning("Agent %s: agency engine init failed: %s", agent_id, exc)

    # -- 10. Self State ----------------------------------------------------
    from nls.brain.self_state import SelfState
    self_state = SelfState()
    state_path = agent_dir / "self_state.json"
    if state_path.exists():
        self_state.load(state_path)
    else:
        self_state.collect_all(
            hypothalamus=hypothalamus,
            drive_engine=drive_engine,
            ans=ans,
        )

    # -- N. Temporal Self --------------------------------------------------
    from nls.identity.temporal_self import TemporalSelf
    temporal_cfg = _load_agent_config(agent_dir, "temporal_self.json")
    temporal_self = TemporalSelf(config=temporal_cfg or {})
    ts_path = agent_dir / "temporal_self_state.json"
    if temporal_self.load(ts_path):
        self_state.energy = temporal_self.energy
        self_state.mood_label = temporal_self.get_mood_label()

    # -- O. OFC ------------------------------------------------------------
    from nls.brain.ofc import OrbitofrontalCortex
    ofc_cfg = _load_agent_config(agent_dir, "ofc.json")
    ofc = OrbitofrontalCortex(config=ofc_cfg or {})
    ofc.load(agent_dir / "ofc_state.json")

    # -- P. Working Memory -------------------------------------------------
    from nls.brain.cryptex import CryptexMemory
    wm_cfg = _load_agent_config(agent_dir, "working_memory.json")
    dual_wm = CryptexMemory(config=wm_cfg or {})
    dual_wm.load(agent_dir)
    working_memory = dual_wm

    # -- Q. Narrative Self -------------------------------------------------
    from nls.identity.narrative_self import NarrativeSelf
    narr_cfg = _load_agent_config(agent_dir, "narrative_self.json")
    narrative_self = NarrativeSelf(config=narr_cfg or {})
    narrative_self.load(agent_dir / "narrative_self_state.json")
    if not narrative_self.soul_wish:
        try:
            meta_path = agent_dir / "agent_meta.json"
            if meta_path.exists():
                _meta = json.loads(meta_path.read_text(encoding="utf-8"))
                _sw = _meta.get("soul_wish", "")
                if _sw:
                    narrative_self.set_soul_wish(_sw)
        except Exception:
            pass

    # -- R. Theory of Mind -------------------------------------------------
    from nls.identity.theory_of_mind import TheoryOfMind
    tom_cfg = _load_agent_config(agent_dir, "theory_of_mind.json")
    theory_of_mind = TheoryOfMind(config=tom_cfg or {})
    theory_of_mind.load(agent_dir / "theory_of_mind_state.json")

    # -- S. Predictive Processing ------------------------------------------
    from nls.brain.predictive import PredictiveProcessor
    pp_cfg = _load_agent_config(agent_dir, "predictive_processing.json")
    predictive = PredictiveProcessor(config=pp_cfg or {})
    predictive.load(agent_dir / "predictive_state.json")

    # -- T. Network Dynamics -----------------------------------------------
    from nls.brain.network_dynamics import NetworkDynamics
    nd_cfg = _load_agent_config(agent_dir, "network_dynamics.json")
    network_dynamics = NetworkDynamics(config=nd_cfg or {})
    network_dynamics.load(agent_dir / "network_dynamics_state.json")

    # -- U. Visual Cortex --------------------------------------------------
    visual_cortex = None
    try:
        from nls.tools.visual_cortex import VisualCortex, VisualCortexConfig
        vc_cfg = _load_agent_config(agent_dir, "visual_cortex.json")
        visual_cortex = VisualCortex(
            VisualCortexConfig.from_dict(vc_cfg) if vc_cfg else None,
        )
    except Exception:
        pass

    # -- Cross-component wiring --------------------------------------------
    if ans is not None:
        try:
            ans.set_brain_refs(
                theory_of_mind=theory_of_mind,
                working_memory=working_memory,
            )
        except Exception:
            pass

    # -- Agent name --------------------------------------------------------
    agent_name = None
    try:
        meta_path = agent_dir / "agent_meta.json"
        if meta_path.exists():
            _meta = json.loads(meta_path.read_text(encoding="utf-8"))
            raw_name = _meta.get("agent_name") or ""
            if raw_name and raw_name != agent_id[:8]:
                agent_name = raw_name
    except Exception:
        pass

    elapsed = time.perf_counter() - t0
    logger.info(
        "Agent %s: subsystems built in %.1fms (facts=%d, dmn=%s, drives=%s)",
        agent_id, elapsed * 1000, n_facts,
        dmn is not None, drive_engine is not None,
    )

    return {
        "agent_id": agent_id,
        "agent_dir": agent_dir,
        "config": config,
        "vllm_client": vllm_client,
        "calibrator": calibrator,
        "ans": ans,
        "domain_db": domain_db,
        "hypothalamus": hypothalamus,
        "working_memory": working_memory,
        "agent_name": agent_name,
        "on_sleep_requested": on_sleep_requested,
        "reasoning_distiller": reasoning_distiller,
        "visual_cortex": visual_cortex,
        "theory_of_mind": theory_of_mind,
        "narrative_self": narrative_self,
        "predictive": predictive,
        "network_dynamics": network_dynamics,
        "self_state": self_state,
        "temporal_self": temporal_self,
        "ofc": ofc,
        "drive_engine": drive_engine,
        "dual_wm": dual_wm,
        "dmn": dmn,
        "agency": agency,
    }
