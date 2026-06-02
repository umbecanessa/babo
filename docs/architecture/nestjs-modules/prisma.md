# Prisma module

**Path:** `backend/src/prisma/`

Global database access for NestJS. Every module that persists users, agents, or channel state depends on this layer.

---

## Files

| File | Role |
|------|------|
| `prisma.module.ts` | `@Global()` module exporting `PrismaService` |
| `prisma.service.ts` | `PrismaClient` subclass with lifecycle hooks |

---

## Key classes

**`PrismaService`** — extends `PrismaClient`, connects on `onModuleInit`, disconnects on `onModuleDestroy`.

No HTTP routes; injected into services/controllers.

---

## Schema models

Defined in `backend/prisma/schema.prisma`:

| Model | Purpose |
|-------|---------|
| `User` | Accounts, roles (`user` / `admin`) |
| `UserSettings` | JSON preferences blob |
| `Agent` | `id` (UUID) + `runtimeAgentId` (Python key) |
| `SoulPackage` | Cloud soul metadata |
| `DeviceLease` | Exclusive desktop session |
| `ChannelAlias` | Email channel aliases |
| `PendingChannelMessage` | Queue when relay offline |
| `ClawhubSkill` | Installed marketplace skills |
| `ApiKey` | Hashed `nlsk_*` keys |

See [Database schema](../../reference/database-schema.md).

---

## Environment

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection string |

---

## Python runtime

None — Postgres is the control plane only; agent brain files stay on desktop disk.

---

## Related

- [Agents](agents.md) · [Auth](auth.md) · [Channels](channels.md)
