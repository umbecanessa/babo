# API keys module

**Path:** `backend/src/api-keys/`

Per-user API keys for automation (`nlsk_` prefix). Stored bcrypt-hashed in Postgres.

---

## Files

| File | Role |
|------|------|
| `api-keys.module.ts` | Controller + service |
| `api-keys.controller.ts` | JWT routes |
| `api-keys.service.ts` | Create, list, revoke, delete |
| `dto/create-key.dto.ts` | Label, scopes |

---

## HTTP routes

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/api-keys` | Create (returns plaintext once) |
| GET | `/api/api-keys` | List metadata |
| DELETE | `/api/api-keys/:id` | Delete |
| POST | `/api/api-keys/:id/revoke` | Revoke without delete |

---

## Prisma

- `ApiKey` — `userId`, hash, label, `revokedAt`

---

## Python runtime

Keys validate on Python via `server/middleware/auth.py` as `Authorization: Bearer nlsk_...` when calling desktop/runtime directly.

NestJS does not forward keys to Python in this module — see [Settings & API keys guide](../../guides/settings-and-api-keys.md).

---

## Related

- [Auth & access](../auth-and-access.md)
