# Brain package (`nls/brain`)

Cognitive subsystems: autonomic nervous system, hormones, working memory, Cryptex rings, drives, DMN, agency, predictive processing.

**Entry:** `nls/brain/__init__.py` (module catalog; import submodules directly).

---

## Key modules

| Module | Key types |
|--------|-----------|
| `autonomic.py` | `AutonomicNervousSystem`, `NerveSignal`, `AgentState`, `SleepMode` |
| `hypothalamus.py` | `HypothalamusEngine`, hormone levels |
| `cryptex.py` / `working_memory.py` | `CryptexMemory`, `WMSlot`, rings |
| `self_state.py` | `SelfState` — consciousness scheduler input |
| `dmn.py` | `DefaultModeNetwork` — dreams, idle cognition |
| `drives.py` | `DriveEngine` — curiosity, social, etc. |
| `agency.py` | `AgencyEngine` — tool preference learning |
| `ofc.py` | `OrbitofrontalCortex` — value / risk |
| `predictive.py` | `PredictiveProcessor` |
| `network_dynamics.py` | Graph metrics for dashboard |
| `circadian.py` | `CircadianClock` — schedule gates |
| `crystallization.py` | Skill crystallization readiness |
| `brain_context.py` | `build_brain_context()` for prompts |
| `dream_findings.py` | `DreamFinding` for UI broadcast |

---

## Disk artifacts

Per `agent_dir`:

- `ans_state.json`, `hypothalamus_state.json`, `self_state.json`
- `ofc_state.json`, `working_memory_state.json`
- Cryptex directory tree
- `tool_experience.json` (with agency path)

---

## Server integration

| Service | Usage |
|---------|-------|
| `consciousness_scheduler.py` | `InnerLoop` + `SelfState` |
| `consolidation_sleep.py` | `AgentState` during sleep |
| `routes/admin.py` | Circadian patch, signal history |

---

## Related

- [Brain & memory](../brain-and-memory.md)
- [Inner loop](../inner-loop.md)
- [Sleep & consciousness](../sleep-and-consciousness.md)
