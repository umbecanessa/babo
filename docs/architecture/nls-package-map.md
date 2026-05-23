# `nls/` package map

Source tree for the agent brain (`nls/`).

!!! tip "Deep dives"
    Per-package documentation: **[NLS modules](nls-modules/index.md)**.

---

## Packages

| Package | Path | Responsibility |
|---------|------|----------------|
| **agentic** | `nls/agentic/` | v5 loop: generate, execute tools, compact, evaluate, teams |
| **brain** | `nls/brain/` | ANS, hormones, WM/Cryptex, DMN, drives, agency |
| **engine** | `nls/engine/` | Inner loop, thalamic router, tool_loader, tools_builtin, visual cortex |
| **runtime** | `nls/runtime/` | `AgentRuntime`, factory, channels (`ChannelRelayClient`) |
| **tools** | `nls/tools/` | Agentic-loop tools (`agent_tools/`), MCP, tool_setup |
| **skills** | `nls/skills/` | Skill SDK, bundled skills, channel_processing |
| **identity** | `nls/identity/` | Soul, narrative, theory of mind |
| **ledger** | `nls/ledger/` | Genesis mint, Merkle chain |
| **knowledge** | `nls/knowledge/` | DomainDB, fact store, distiller |
| **bridge** | `nls/bridge/` | AKU extraction (local/cloud) |
| **config** | `nls/config/` | Default JSON brain configs, tool JSON manifests |
| **taxonomy** | `nls/taxonomy/` | Domain seed YAML for classification |
| **models** | `nls/models.py` | Shared Pydantic types |

Placeholder dirs (no active product code): `miner/`, `training/`, `analytics/`.

---

## Server bridge

Python HTTP/WS is **`server/`**, not `nls/` — but it imports `nls` everywhere.

| Server module | Uses nls |
|---------------|----------|
| `agent_manager.py` | factory → AgentRuntime |
| `skill_loader.py` | skills SDK |
| `sleep_scheduler.py` | consolidation via runtime |
| `consciousness_scheduler.py` | inner_loop |
| `vllm_client.py` | inference |

---

## Related

- [Agent runtime](agent-runtime.md)
- [System overview](overview.md)
