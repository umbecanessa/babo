# Models (`nls/models.py`)

Shared **Pydantic types** used across brain, server, and API layers. Single module (not a package directory).

---

## Core types

| Type | Purpose |
|------|---------|
| `AKU` | Atomic knowledge unit payload |
| `Fact` | DomainDB fact row API shape |
| `Block`, `BlockMetadata`, `BlockType` | Merkle chain blocks |
| `ChainState` | Ledger head summary |
| `SleepRequest` | Sleep scheduler queue item |
| `AgentStatus` | High-level agent state enum |
| `SovereigntyMode` | Data residency / bridge policy |

---

## Usage

| Consumer | Examples |
|----------|----------|
| `nls/ledger/*` | Blocks, facts |
| `nls/bridge/aku.py` | AKU validation |
| `server/services/sleep_scheduler.py` | `SleepRequest` |
| `server/routes/agents.py` | Status responses |
| `server/routes/admin.py` | Fact PATCH bodies |

---

## Related

- [Glossary](../../reference/glossary.md)
- [Database schema](../../reference/database-schema.md) — Postgres models are separate (Prisma)
