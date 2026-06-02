# Agents module

**Path:** `backend/src/agents/`

Agent **ownership** in PostgreSQL, UI-facing CRUD, and **proxy** to Python admin/session APIs. Bridges UUID `Agent.id` ↔ string `runtimeAgentId`.

---

## Files

| File | Role |
|------|------|
| `agents.module.ts` | Imports `RuntimeModule` |
| `agents.controller.ts` | JWT `/api/agents/*` |
| `agents.service.ts` | Prisma + `RuntimeService` |
| `tools.controller.ts` | Global tool catalog `/api/tools/*` |
| `dto/create-agent.dto.ts` | Create payload validation |

---

## ID duality

| Field | Used for |
|-------|----------|
| `Agent.id` (UUID) | Nest routes, JWT ownership checks |
| `Agent.runtimeAgentId` | Python paths, relay WS, disk folder name |

`AgentsService.getRuntimeAgentId(userId, dbId)` enforces ownership before proxying.

**Request/response examples:** [NestJS Agents API](../examples/nestjs-agents-api.md) · **Sequence:** [Relay join](../sequences/relay-join.md)

---

## Key HTTP routes

**Agents** (`JwtAuthGuard`):

- CRUD: `POST/GET/DELETE /api/agents`, `GET /api/agents/:id`
- Sync: `POST /api/agents/sync`
- Relay: `GET /api/agents/relay-status`, `GET /api/agents/:id/relay-status`
- Introspection: `.../chain`, `.../facts`, `.../events`, `.../conversation`, `.../config`, hormone/signal history
- Tools: `.../tools`, enable/disable, batch enable + status
- Sessions: `.../sessions`, `.../sessions/:sessionKey`
- Sleep: `POST .../sleep`
- Soul: `.../soul-packages`, `.../soul-packages/latest`
- Lease: `.../lease/*` (acquire, release, heartbeat, force-acquire)
- Fork: `POST .../fork`

**Tools catalog** (no agent id):

- `GET /api/tools/catalog`, `/catalog/v2`, `/bundles`

Full table: [NestJS API](../../reference/nestjs-api.md).

---

## Prisma

- `Agent`, `SoulPackage`, `DeviceLease`

---

## Python proxy map

| Nest endpoint | Python |
|---------------|--------|
| Create | `POST /agents` |
| Status | `GET /agents/{runtimeAgentId}` |
| Admin views | `GET /admin/agents/{id}/...` |
| Sessions | `GET /sessions/{runtimeAgentId}/...` |
| Soul | `POST /admin/agents/{id}/soul/export` (fork, etc.) |

Relay fallback when desktop online — see [Runtime](runtime.md).

---

## Related

- [Device lease](../device-lease.md)
- [Soul packages](../soul-packages.md)
- [Teams API](../../reference/teams-api.md) (Python-only teams routes)
