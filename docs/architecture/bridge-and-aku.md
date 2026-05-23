# Bridge & AKU (knowledge units)

Facts enter long-term memory as **AKUs** (Atomic Knowledge Units) after validation and routing through the agentic bridge.

---

## Agentic bridge

**Source:** `nls/agentic/bridge.py` (re-exported from deprecated `nls/engine/agentic_bridge.py`)

The bridge wires the multi-step agentic loop to runtime hooks:

| Export | Role |
|--------|------|
| `build_config` / `build_config_v4` | Loop configuration from genesis + runtime |
| `build_hooks` / `build_hooks_v4` | Pre/post step hooks (signals, tools, memory) |

`AgentRuntime` calls these when starting an agentic session (`nls/runtime/agent_runtime.py`).

---

## AKU module

**Source:** `nls/bridge/aku.py`

| Function | Purpose |
|----------|---------|
| `validate_domain_path` | Enforce dot-notation paths (`User.Tech.Framework`, min depth 2) |
| `classify_fact_scope` | Global vs project-scoped facts |
| Quality gate | Reject numeric-only, meta, or low-salience content before DomainDB write |

AKUs tie to the Merkle chain: each learned fact references a **domain path**, block height, and optional project id.

---

## Flow: LEARN signal → DomainDB

```text
Agentic step emits LEARN signal
    → aku quality gate (prefrontal filter)
    → validate_domain_path
    → DomainDB.upsert_fact (nls/ledger/domain_db.py)
    → optional block append on chain
```

`nls/knowledge/fact_store.py` imports AKU helpers for consolidation and conflict checks.

---

## Domain path rules

| Rule | Example |
|------|---------|
| Min 2 segments | `User.Tech` ✓ — `User` ✗ |
| Segments start with letter | `User.tech` ✗ |
| Organic prefixes | No fixed whitelist in OSS — agent grows taxonomy |

Established domains gain stronger retrieval routing over time (see [Brain & memory](brain-and-memory.md)).

---

## Related

- [Ledger & DomainDB](ledger-and-domain-db.md)
- [Agentic loop](agentic-loop.md)
- [Brain & memory](brain-and-memory.md)
