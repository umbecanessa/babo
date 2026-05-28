# NestJS API

Global prefix: **`/api`** (`backend/src/main.ts`).

**Auth:** Most routes use `JwtAuthGuard` (Bearer access token). Exceptions noted below.

---

## Auth (`/api/auth`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/register` | Public | Create user |
| POST | `/login` | Public | Access + refresh tokens |
| POST | `/refresh` | Public | Refresh access token |

---

## Agents (`/api/agents`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/` | Create agent (proxies runtime + DB row) |
| GET | `/` | List user's agents |
| POST | `/sync` | Register desktop-created agent in DB |
| GET | `/genesis` | Genesis templates (proxy) |
| GET | `/relay-status` | All agents + desktop online flag |
| GET | `/:id/relay-status` | Single agent relay online |
| GET | `/:id/status` | Runtime status (relay or direct) |
| GET | `/:id/chain`, `/facts`, `/events`, `/conversation`, `/config` | Memory proxies |
| GET | `/:id/hormones/history`, `/signals/history` | Brain metrics |
| GET/POST | `/:id/tools/*` | Tool enable/disable, batch |
| GET | `/:id/sessions`, `/:id/sessions/:sessionKey` | Chat sessions |
| POST | `/:id/sleep` | Force sleep cycle |
| POST | `/:id/fork` | Fork agent at chain height |
| GET | `/:id/soul-packages` | List soul package metadata |
| POST | `/:id/soul-packages` | Register soul package |
| GET | `/:id/soul-packages/latest` | Latest soul package |
| GET | `/:id/lease` | Active device lease |
| POST | `/:id/lease/acquire` | Acquire exclusive desktop lease |
| POST | `/:id/lease/release` | Release lease |
| POST | `/:id/lease/force-acquire` | Take lease from another device |
| POST | `/:id/lease/heartbeat` | Lease keep-alive |

See [Device lease](../architecture/device-lease.md), [Soul packages](../architecture/soul-packages.md).

---

## Babo Cloud (`/api/inference/v1`, `/api/gpu`, `/api/cloud`)

OpenAI-compatible inference relay + GPU workers. Auth: JWT or `nlsk_` API key. Requires trial/subscription when `BABO_CLOUD_MODE=true`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/inference/v1/models` | List models |
| POST | `/inference/v1/chat/completions` | Chat completions (SSE) |
| POST | `/gpu/transcribe` | Audio → text |
| POST | `/gpu/vision/describe` | Image describe |
| POST | `/gpu/embed` | Embeddings |
| GET | `/cloud/subscription` | Usage / plan status |

See [Babo Cloud module](../architecture/nestjs-modules/babo-cloud.md).

---

## Runtime proxy (`/api/rt`)

Catch-all **`/api/rt/*`** → Python path via relay (`runtime-proxy.controller.ts`).

Requires JWT. Maps DB `agentId` UUID → `runtimeAgentId`.

Example: browser `GET /api/rt/agents/{runtimeId}/status` → relay `http_proxy` to desktop.

---

## Channels (`/api/channels`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/email/activate/:agentId` | JWT | Provision Resend alias |
| POST | `/email/send` | `X-Runtime-Secret` | Runtime sends email |
| POST | `/webhook/:channel/:agentId` | Public | Telegram/WhatsApp/etc. ingress |
| GET | `/email/status/:agentId` | JWT | Email channel status |
| POST | `/email/webhook` | Public | Resend inbound |
| GET | `/pending/:agentId` | JWT | Queued messages while offline |
| POST | `/pending/drain` | JWT | Drain queue when relay up |
| POST | `/internal/agents/:agentId/soul-packages` | `X-Runtime-Secret` | Post-sleep metadata sync |

**Relay WebSocket (not REST):** upgrade to `/api/channels/relay/{runtimeAgentId}`.

---

## ClawHub (`/api/clawhub`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/search` | Search marketplace |
| GET | `/skill/:slug` | Skill metadata |
| POST | `/install` | Install to agent (+ relay push) |
| GET | `/installed` | List installed |
| DELETE | `/uninstall/:slug` | Remove |

---

## Admin (`/api/admin`)

`AdminAuthGuard` — admin role users.

Runtime introspection by **`runtimeAgentId`** (not DB UUID): status, chain, facts, sleep, evict.

User admin: `GET /admin/stats`, `GET/PATCH/DELETE /admin/users/*`, `GET/DELETE /admin/agents/db/*`, `GET /admin/analytics/overview`, `GET /admin/analytics/compare`.

---

## Settings, API keys, filesystem, transcribe

| Module | Prefix | Purpose |
|--------|--------|---------|
| `settings` | `/api/settings` | User JSON preferences |
| `api-keys` | `/api/api-keys` | Create, list, revoke user API keys (`nlsk_`) |
| `filesystem` | `/api/fs/*` | Proxy IDE FS to runtime (note: `fs`, not `filesystem`) |
| `transcribe` | `/api/transcribe` | Audio upload → runtime |

---

## WebSocket (Socket.IO)

| Namespace | Gateway | Purpose |
|-----------|---------|---------|
| `/chat` | `chat.gateway.ts` | Agent chat (direct or relay mode) |
| `/terminal` | `terminal.gateway.ts` | User PTY shell |

See [WebSocket events](websocket-events.md).

---

## Health

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Backend + optional runtime check |

---

## Environment

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Postgres |
| `JWT_SECRET`, `JWT_REFRESH_SECRET` | Tokens |
| `RUNTIME_URL` | Direct Python HTTP (optional) |
| `RUNTIME_SHARED_SECRET` | Relay auth + runtime hooks |
| `RESEND_*` | Email channel |

Full list: [Environment (complete)](environment-complete.md).

---

## Related

- [NestJS backend architecture](../architecture/nestjs-backend.md)
- [Database schema](database-schema.md)
