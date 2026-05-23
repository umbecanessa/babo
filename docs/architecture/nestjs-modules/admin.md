# Admin module

**Path:** `backend/src/admin/`

Platform **admin** dashboards: Postgres user/agent management plus **unscoped** Python admin proxy by `runtimeAgentId`.

---

## Files

| File | Role |
|------|------|
| `admin.module.ts` | `RuntimeModule`, `PrismaModule` |
| `admin.controller.ts` | `/api/admin/*` |
| `admin.service.ts` | Prisma + proxy |
| `admin-auth.guard.ts` | JWT + `role === 'admin'` |

---

## HTTP routes (admin role required)

| Area | Examples |
|------|----------|
| Stats | `GET /api/admin/stats` |
| Users | `GET/PATCH/DELETE /api/admin/users/:id` |
| DB agents | `GET/DELETE /api/admin/agents/db/:id` |
| Runtime introspection | `GET /api/admin/agents/:runtimeAgentId/chain`, `facts`, `events`, `conversation`, `config`, `memory-tiers`, hormone/signal history |
| Actions | `POST .../evict`, `POST .../sleep` |
| System | `GET /api/admin/system/health`, `adapters` |
| Analytics | `GET /api/admin/analytics/overview`, `compare?ids=` |

Mirrors Python `/admin/agents/{id}/*` without per-user ownership checks (platform operators only).

---

## Prisma

`User`, `Agent`, `ApiKey` — lists and counts

---

## Python

Same paths as [Agents](agents.md) admin proxy, keyed by **runtime** ID. Also `GET /health`, `POST /agents/{id}/evict`.

---

## Related

- [Admin API](../../reference/admin-api.md) (Python route table)
- [Auth](auth.md) — admin role on JWT
