# Settings module

**Path:** `backend/src/settings/`

Per-user **JSON preferences** stored in PostgreSQL (UI theme, defaults, etc.).

---

## Files

| File | Role |
|------|------|
| `settings.module.ts` | Module wiring |
| `settings.controller.ts` | JWT routes |
| `settings.service.ts` | Get/merge `UserSettings.data` |

---

## HTTP routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/settings` | Return merged JSON blob |
| PUT | `/api/settings` | Patch/replace preferences |

---

## Prisma

- `UserSettings` — `userId` unique, `data` JSON column

---

## Python runtime

None — agent config lives on desktop (`agent_dir/config/`). User settings are cloud UI state only.

---

## Related

- [Settings & API keys guide](../../guides/settings-and-api-keys.md)
