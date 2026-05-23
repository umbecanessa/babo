# Admin API (Python runtime)

Prefix: **`/admin/`** — runtime inspection, tools, soul ops, analytics.

**Source:** `server/routes/admin.py`  
**Auth:** `X-Runtime-Secret` or `Authorization: Bearer nlsk_...`

---

## System & safety

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/safe-to-update` | Whether agents can reload (no active agentic tasks) |
| GET | `/admin/system/adapters` | Adapter registry snapshot |
| GET | `/admin/analytics/overview` | Fleet-level stats |
| GET | `/admin/analytics/agents/compare` | Compare agents |

---

## Per-agent introspection

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/agents/{id}/chain` | Merkle chain summary |
| GET | `/admin/agents/{id}/facts` | DomainDB facts |
| PATCH | `/admin/agents/{id}/facts/{fact_id}/fluid` | Toggle fluid flag |
| GET | `/admin/agents/{id}/events` | Event log |
| GET | `/admin/agents/{id}/conversation` | Conversation export |
| GET | `/admin/agents/{id}/config` | Runtime config |
| PATCH | `/admin/agents/{id}/config/circadian` | Circadian schedule |
| GET | `/admin/agents/{id}/memory-tiers` | Cryptex tier stats |
| GET | `/admin/agents/{id}/wm` | Working memory slots |
| GET | `/admin/agents/{id}/hormones/history` | Hormone time series |
| GET | `/admin/agents/{id}/network/history` | Network dynamics history |
| GET | `/admin/agents/{id}/signals/history` | ANS signal history |
| GET | `/admin/agents/{id}/visual-cortex/buffer` | Visual buffer preview |

---

## Sleep, dreams, feedback

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/admin/agents/{id}/sleep` | Enqueue sleep cycle |
| POST | `/admin/agents/{id}/daydream` | Manual daydream trigger |
| GET | `/admin/agents/{id}/ans/context` | ANS context slots |
| DELETE | `/admin/agents/{id}/ans/context/{index}` | Remove slot |
| PATCH | `/admin/agents/{id}/ans/context/{index}` | Edit slot |
| POST | `/admin/agents/{id}/feedback` | User feedback signal |
| POST | `/admin/agents/{id}/safety-net` | Safety-net recovery hook |

---

## Tools (JSON registry)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/tools/catalog` | Tool catalog v1 |
| GET | `/admin/tools/catalog/v2` | Tool catalog v2 |
| GET | `/admin/tools/bundles` | Bundle definitions |
| GET | `/admin/agents/{id}/tools` | Per-agent tool status |
| GET | `/admin/agents/{id}/tools/{tool}/status` | Single tool status |
| POST | `/admin/agents/{id}/tools/{tool}/enable` | Enable tool |
| POST | `/admin/agents/{id}/tools/{tool}/disable` | Disable tool |
| POST | `/admin/agents/{id}/tools/batch-enable` | Batch enable (async job) |
| GET | `/admin/agents/{id}/tools/batch/{batch_id}/status` | Batch job status |

---

## Soul packages

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/admin/agents/{id}/soul/export` | Export `.soul.zip` |
| POST | `/admin/agents/{id}/soul/import` | Import archive |
| POST | `/admin/agents/{id}/soul/fork` | Fork agent at chain height |
| POST | `/admin/agents/{id}/soul/snapshot` | Create snapshot |
| GET | `/admin/agents/{id}/soul/snapshots` | List snapshots |
| POST | `/admin/agents/{id}/soul/snapshot/restore` | Restore snapshot |

See [Soul packages](../architecture/soul-packages.md).

---

## Skills admin

Global skill registry routes live under **`/admin/skills/`** — see [Skills admin API](skills-admin-api.md).

Per-agent skill enablement: **`/admin/agents/{id}/skills`**.

---

## Related

- [Python API](python-api.md)
- [Skills admin API](skills-admin-api.md)
- [Brain dashboard](../guides/brain-dashboard.md)
