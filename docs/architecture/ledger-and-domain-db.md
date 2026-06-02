# Ledger & DomainDB

Persistent knowledge lives in **`knowledge.db`** (DomainDB) plus the Merkle **block chain** under each agent's data directory.

**Source:** `nls/ledger/domain_db.py`

---

## What DomainDB stores

| Table | Purpose |
|-------|---------|
| `blocks` | Merkle chain entries (`delta` / `epoch`) |
| `facts` | Current value per `(domain_path, project_id)` |
| `fact_history` | Archived values on updates |
| `projects` | Project-scoped fact isolation (schema v2) |

Schema version: `_SCHEMA_VERSION = 2`.

---

## On-disk layout

Per agent (typical desktop path):

```text
{NLS_DATA_DIR}/agents/{agent_id}/
  knowledge.db          # DomainDB (SQLite)
  chain/                # Block files / chain metadata
  cryptex/              # Ring buffers (separate from DomainDB)
  soul/                 # Soul package files
```

Cloud NestJS stores **metadata** (users, agents, soul package records) in PostgreSQL — not the full `knowledge.db`. The desktop runtime owns the brain files unless you run a self-hosted all-in-one stack.

---

## Fact lifecycle

1. **Learn** — AKU validated → upsert into `facts`
2. **Fluidity** — `flip_count` / `is_fluid` guard ping-pong updates
3. **Conflict** — domain-scoped lookup before overwrite
4. **Sleep** — consolidation promotes WM / Cryptex content into durable facts
5. **Project scope** — `project_id` column isolates team/project knowledge

Admin API exposes facts for debugging: `GET /admin/agents/{id}/facts`, `PATCH .../fluid`.

---

## Credentials vault

DomainDB includes encoded credential storage for skill integrations (domain-scoped, not plain-text in chat logs).

---

## Related

- [Bridge & AKU](bridge-and-aku.md)
- [Persistence](persistence.md)
- [Data directory](../reference/data-directory.md)
- [Admin API](../reference/admin-api.md)
