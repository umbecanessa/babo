# Soul packages

Portable **agent state archives** (`.soul.zip`) and Postgres metadata for versioning.

Two layers:

| Layer | Where | Purpose |
|-------|-------|---------|
| **Runtime soul ops** | Python `server/routes/admin.py` | Export/import/fork/snapshot on disk |
| **DB soul packages** | NestJS + `SoulPackage` table | Cloud metadata, latest pointer, post-sleep sync |

---

## Runtime (Python admin)

| Endpoint | Purpose |
|----------|---------|
| `POST /admin/agents/{id}/soul/export` | Build downloadable archive |
| `POST /admin/agents/{id}/soul/import` | Restore from archive |
| `POST /admin/agents/{id}/soul/fork` | New agent from chain height |
| `POST /admin/agents/{id}/soul/snapshot` | Point-in-time snapshot |
| `GET /admin/agents/{id}/soul/snapshots` | List snapshots |
| `POST .../soul/snapshot/restore` | Restore snapshot |

**UI:** Memory → Soul tab (`features/memory/memory.component.ts`).

---

## NestJS (cloud metadata)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/agents/:id/soul-packages` | List versions |
| `POST /api/agents/:id/soul-packages` | Register new package |
| `GET /api/agents/:id/soul-packages/latest` | Latest metadata |

**Internal (runtime → cloud):**

`POST /api/channels/internal/agents/:agentId/soul-packages`  
(`X-Runtime-Secret`) — called after sleep/consolidation to sync chain height metadata.

---

## Memory fork (NestJS)

`POST /api/agents/:id/fork` — create new DB agent from chain snapshot at `forkHeight` (coordinates with runtime).

---

## When to use what

| Goal | Use |
|------|-----|
| Backup / move agent to another machine | Soul **export/import** (zip) |
| Branch personality at a point in time | **Fork** or snapshot |
| Cloud tracking of versions | **soul-packages** API |
| Disaster recovery | Export zip + backup `data/agents/` |

---

## Related

- [Memory guide](../guides/memory.md)
- [Persistence](persistence.md)
- [Python API](../reference/python-api.md)
- [NestJS API](../reference/nestjs-api.md)
