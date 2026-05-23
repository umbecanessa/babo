# Database schema

PostgreSQL schema for the NestJS backend.

**Source:** `backend/prisma/schema.prisma`

Apply migrations:

```bash
cd backend && npx prisma migrate deploy
```

---

## Entity relationship (conceptual)

```text
User 1──* Agent 1──* ChannelAlias
              ├──* PendingChannelMessage
              ├──* SoulPackage
              ├──* DeviceLease
              └──* ClawhubSkill

User 1──1 UserSettings
User 1──* ApiKey
```

Agent **memory and brain state are not in Postgres** — they live in `data/agents/{runtimeAgentId}/` on the desktop (or co-located runtime host).

---

## User

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `email` | String | Unique login |
| `passwordHash` | String | bcrypt |
| `displayName` | String? | Shown in UI |
| `role` | String | Default `user` |
| `createdAt` / `updatedAt` | DateTime | |

---

## Agent

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | **UI / API id** — used in routes like `/chat/:agentId` |
| `userId` | UUID | Owner |
| `runtimeAgentId` | String | **Python id** — directory name, relay key |
| `name` | String? | Display name |
| `genesisVersion` | String | e.g. `standard-v1` |
| `status` | String | e.g. `alive` |
| `createdAt` | DateTime | |

**Critical:** Always resolve `id` → `runtimeAgentId` before talking to Python or relay.

---

## UserSettings

| Column | Type | Notes |
|--------|------|-------|
| `userId` | UUID | Unique FK to User |
| `data` | JSON | Opaque preferences blob |

---

## ChannelAlias

Maps external channel addresses to agents (email addresses, Telegram ids, etc.).

---

## PendingChannelMessage

Queued webhook payloads when desktop relay is offline. Drained when relay reconnects.

---

## ClawhubSkill

Tracks skills installed from ClawHub per user/agent (`slug`, metadata).

---

## ApiKey

User-issued API keys (`nlsk_` prefix) with hashed secret and optional rate limits.

---

## DeviceLease

Optional single active device lease per agent (concurrency control for desktop connections).

---

## SoulPackage

Versioned soul archive metadata when uploaded through backend flows.

---

## Related

- [NestJS API](nestjs-api.md)
- [Deployment topologies](../architecture/deployment-topologies.md)
- [Data directory](data-directory.md)
