# Auth & access

How users, browsers, desktop, and automation authenticate to Babo.

---

## Actors

| Actor | Auth mechanism | Talks to |
|-------|----------------|----------|
| **Web user** | JWT (NestJS) | NestJS → relay → desktop |
| **Desktop user** | JWT for NestJS; local runtime on localhost | NestJS + `127.0.0.1:9222` |
| **Python runtime** | `X-Runtime-Secret` | NestJS hooks, email send |
| **Relay WebSocket** | `?secret=` query param | NestJS `/api/channels/relay/{id}` |
| **API automation** | `Bearer nlsk_...` | Python runtime directly |
| **Channel webhooks** | Provider secrets; public URLs | NestJS `/api/channels/webhook/...` |

---

## JWT flow (NestJS)

```text
POST /api/auth/register  →  user row in Postgres
POST /api/auth/login     →  access JWT + refresh token
POST /api/auth/refresh   →  new access token

Frontend stores tokens (AuthService)
JwtInterceptor adds Authorization: Bearer <access>
```

**Guards:** `JwtAuthGuard` on `/api/agents`, `/api/rt`, `/api/clawhub`, Socket.IO `/chat`, etc.

**Refresh:** `JWT_REFRESH_SECRET`, configurable TTL (`JWT_EXPIRATION`, `JWT_REFRESH_EXPIRATION`).

---

## Desktop first-run

1. User registers/logs in against NestJS (`environment.electron.ts` → `apiUrl`)
2. Setup wizard configures inference + saves `nestjsUrl`
3. Python spawned with `NESTJS_URL` + matching secrets
4. `ChannelRelayClient` connects outbound relay

`setupGuard` blocks main routes until desktop setup completes.

---

## Runtime secret (NestJS ↔ Python)

| NestJS | Python |
|--------|--------|
| `RUNTIME_SHARED_SECRET` | `NLS_SHARED_SECRET` |

Used for:

- Relay WebSocket upgrade auth
- `POST /api/channels/email/send` from runtime
- Internal channel routes with `X-Runtime-Secret`

---

## Direct Python API keys

Users create keys in **Settings → API keys** (`/settings/api-keys`).

- Prefix `nlsk_` (`NLS_API_KEY_PREFIX`)
- Stored hashed in Postgres (`ApiKey` model)
- Python middleware accepts `Authorization: Bearer nlsk_...`

Use for scripts hitting `http://127.0.0.1:9222` without NestJS.

---

## Socket.IO chat auth

```typescript
io(`${wsUrl}/chat`, { auth: { token: accessToken } })
```

Gateway validates JWT before `join` / `message`.

---

## Relay vs direct chat

On `join`:

- **Relay mode** — desktop online; web chat uses `pushChatToRelay`
- **Direct mode** — `RUNTIME_URL` reachable; NestJS bridges WS to Python

See [Deployment topologies](deployment-topologies.md).

---

## Admin

`AdminAuthGuard` — users with `role: admin` in Postgres.

Routes under `/api/admin/*` for operators (stats, user management, runtime introspection by `runtimeAgentId`).

---

## Related

- [NestJS API](../reference/nestjs-api.md)
- [Settings & API keys guide](../guides/settings-and-api-keys.md)
- [Relay protocol](../reference/relay-protocol.md)
