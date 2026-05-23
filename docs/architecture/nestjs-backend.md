# NestJS backend

Control plane for Babo: **authentication**, **agent registry**, **relay**, **channel webhooks**, **ClawHub proxy**.

**Entry:** `backend/src/main.ts`  
**Module root:** `backend/src/app.module.ts`

---

## Role in the stack

```text
┌──────────────┐     JWT      ┌──────────────┐    relay WS    ┌─────────────┐
│  Web Angular │ ◄──────────► │   NestJS     │ ◄───────────── │   Desktop   │
└──────────────┘              │   Postgres   │                │   Python    │
                              └──────┬───────┘                └─────────────┘
                                     │ RUNTIME_URL (optional direct)
                                     ▼
                              Co-located Python (self-host only)
```

NestJS does **not** run the agentic loop in the default product model.

---

## Module map

| Module | Responsibility |
|--------|----------------|
| `auth` | Register, login, JWT |
| `users` | Internal service (no public `/api/users` routes) |
| `agents` | CRUD, runtime proxy routes, relay-status |
| `runtime` | HTTP/WS to Python when direct |
| `chat` | Socket.IO gateway |
| `channels` | Relay registry, email, webhooks |
| `runtime-proxy` | `/api/rt/*` JWT proxy |
| `clawhub` | Marketplace |
| `filesystem` | IDE FS proxy (`/api/fs/*`) |
| `terminal` | User PTY Socket.IO |
| `transcribe` | Audio upload |
| `admin` | Admin dashboards |
| `api-keys` | User API keys |
| `settings` | User preferences JSON |
| `prisma` | DB client |

!!! tip "Deep dives"
    Per-module routes, dependencies, and Python integration: **[NestJS modules](nestjs-modules/index.md)**.

---

## Agent ID duality

| ID | Where | Used for |
|----|-------|----------|
| `Agent.id` (UUID) | Postgres | UI routes, JWT ownership |
| `Agent.runtimeAgentId` | String | Python paths, relay key |

`AgentsService.getRuntimeAgentId(userId, dbId)` enforces ownership.

---

## Chat gateway modes

`chat.gateway.ts` on `join`:

```python
if channels.hasRelaySocket(runtimeAgentId):
    relayMode = true   # web → pushChatToRelay
else:
    runtime.connectChat()  # bridge to RUNTIME_URL
```

See [Relay protocol](../reference/relay-protocol.md).

---

## Relay server bootstrap

`main.ts` attaches `WebSocketServer` on HTTP upgrade:

- Path: `/api/channels/relay/{agentId}`
- Auth: query `secret`
- Registers socket in `ChannelsService`

---

## Channel webhooks

External services POST to `/api/channels/webhook/:channel/:agentId`.

Delivery:

1. Immediate relay push if desktop online
2. Else `PendingChannelMessage` queue

---

## ClawHub flow

1. User searches/installs from UI
2. `ClawhubService` fetches package
3. Records in `ClawhubSkill` table
4. `pushSkillInstall` over relay to desktop disk

---

## API reference

Full route tables: [NestJS API](../reference/nestjs-api.md)

---

## Related

- [Deployment topologies](deployment-topologies.md)
- [Frontend application](frontend-application.md)
- [Database schema](../reference/database-schema.md)
