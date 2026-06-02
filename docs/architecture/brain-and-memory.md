# Brain & memory

Babo's cognitive stack lives under `nls/brain/` and `nls/identity/`, orchestrated by **AgentRuntime**.

---

## Layered model

```text
┌─────────────────────────────────────────┐
│  User message + channel context         │
└──────────────────┬──────────────────────┘
                   ▼
┌─────────────────────────────────────────┐
│  Working memory (salience slots)        │
│  Cryptex rings (composed into prompt)   │
└──────────────────┬──────────────────────┘
                   ▼
┌─────────────────────────────────────────┐
│  Agentic loop + tools                   │
└──────────────────┬──────────────────────┘
                   ▼
┌─────────────────────────────────────────┐
│  Signals → ANS → Hormones → Drives      │
└──────────────────┬──────────────────────┘
                   ▼
┌─────────────────────────────────────────┐
│  Sleep → DomainDB / Cryptex merge       │
└─────────────────────────────────────────┘
```

---

## Working memory & Cryptex

**Module:** `nls/brain/working_memory.py`

| Concept | Description |
|---------|-------------|
| **WM slots** | Short-term goals, constraints, active facts |
| **Cryptex rings** | 15 rotating context layers (identity, orchestration, skills, …) |
| **Salience** | Decay and promotion rules |
| **Ring priorities** | `update_ring_priorities()` — cognitive phase / agent mode reorders prompt |
| **SubCryptex** | Per-delegate lightweight rings (task, progress, skills borrow) |

UI: Memory page tabs — see [Memory guide](../guides/memory.md).

### Dynamic ring ordering

`CryptexMemory.compose_context()` renders rings **highest priority first** so the LLM sees the most relevant instructions at the top.

| Phase / signal | Rings promoted |
|----------------|----------------|
| `planning` | Behavioral, instructions |
| `monitoring` | Orchestration, wake attention |
| `executing` | Instructions, behavioral, skills |
| `stuck` (stall/hint) | **Skills**, tools_mcp, credentials |

**Skill discovery boost:** on stall or orchestrator hint, a high-salience slot is upserted on the skills ring and priorities switch to the `stuck` profile for several iterations. Delegates get the same via `SubCryptex.activate_skill_discovery_boost()`.

See [Skill discovery & recovery](skill-discovery-and-recovery.md).

---

## DomainDB & facts

**Core store:** `nls/ledger/domain_db.py`  
**Orchestration:** `nls/knowledge/fact_store.py`  
**AKU parsing:** `nls/bridge/aku.py`

- Structured facts with domains (taxonomy from `nls/taxonomy/seed_v1.yaml`)
- Conflict resolution on sleep merge
- Searchable knowledge tree in UI

---

## Merkle chain

**Module:** `nls/ledger/`

Tamper-evident log of blocks (conversation + learning events).

Admin/UI: Chain tab, `GET /admin/agents/{id}/chain`.

---

## Signals & hormones

**Config:** `nls/config/signals.json`, `nls/config/hormones.json`

| Stage | Component |
|-------|-----------|
| Event occurs | Tool result, LEARN, REFLECT, BOND, … |
| Signal emitted | Magnitude + type |
| ANS buffer | `nls/brain/autonomic.py` |
| Hypothalamus | Updates dopamine, cortisol, serotonin, … |
| Cross-regulation | Hormone graph in config |

**Drives** (`nls/config/drives.json`, `nls/brain/drives.py`): homeostatic pressures (curiosity, competence, social) → idle actions.

---

## Identity

| Module | Role |
|--------|------|
| `nls/identity/soul.py` | Values, axioms |
| `nls/identity/narrative_self.py` | Episodes, story |
| `nls/identity/theory_of_mind.py` | User model |
| `nls/identity/temporal_self.py` | Time perspective |

Soul import/export: admin routes + UI.

---

## DMN & idle behavior

**Module:** `nls/brain/dmn.py`  
**Triggered by:** [Inner loop](inner-loop.md) breath ticks when consciousness scheduler has the agent CONSCIOUS

When idle and drives permit:

- Hippocampal replay (recombine memories)
- Spontaneous exploration (probe model knowledge)
- Active dreams (browser/bash with policy)

Modulated by **acetylcholine** and circadian config.

---

## Sleep & consolidation

Not weight training — LLM summarization pipeline:

1. `SleepScheduler` queues job
2. `consolidation_sleep.run_consolidation_cycle()`
3. Facts routed to Cryptex / DomainDB
4. ANS buffer cleared partially

See [Sleep & consciousness](sleep-and-consciousness.md).

---

## Thalamus / routing

Domain classification for LEARN signals and retrieval bias. Product mode does not inject router weights into vLLM.

---

## Persistence

All brain state under `data/agents/{id}/` — see [Data directory](../reference/data-directory.md).

---

## Related

- [Brain dashboard guide](../guides/brain-dashboard.md)
- [Sleep guide](../guides/sleep-and-consolidation.md)
- [Persistence](persistence.md)
