# Runtime proxy module

**Path:** `backend/src/runtime-proxy/`

Browser-mode **catch-all**: forwards `/api/rt/*` to desktop Python **only via relay** (no direct `RUNTIME_URL` from the browser).

---

## Files

| File | Role |
|------|------|
| `runtime-proxy.module.ts` | Imports global `ChannelsService` |
| `runtime-proxy.controller.ts` | `@All('{*path}')` under `rt` |

---

## HTTP

| Method | Path | Guard |
|--------|------|-------|
| ALL | `/api/rt/*` | JWT |

Flow:

1. Resolve Nest `Agent.id` in path segments → `runtimeAgentId`
2. Verify user owns agent
3. `ChannelsService.proxyHttpViaRelay(method, path, body)` — desktop must be online

Example: `GET /api/rt/admin/agents/{runtimeId}/chain` → Python `GET /admin/agents/{runtimeId}/chain`.

---

## When to use

Hosted web UI when the user's desktop is the brain but the browser cannot reach `127.0.0.1:9222`. Electron/desktop UI talks to Python directly instead.

---

## Dependencies

- `PrismaService`, `ChannelsService`

---

## Related

- [Deployment topologies](../deployment-topologies.md)
- [Device lease](../device-lease.md)
