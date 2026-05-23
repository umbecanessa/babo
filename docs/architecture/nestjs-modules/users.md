# Users module

**Path:** `backend/src/users/`

Internal user profile service. **No HTTP controller** — exported for injection by other modules.

---

## Files

| File | Role |
|------|------|
| `users.module.ts` | Exports `UsersService` |
| `users.service.ts` | Safe profile reads |

---

## Key API

| Method | Purpose |
|--------|---------|
| `findById(id)` | Load user row |
| `getProfile(id)` | Public-safe field subset (no password hash) |

---

## Dependencies

- `PrismaService` (global)

---

## Prisma

- `User`

---

## Python runtime

None.

---

## Related

- [Auth](auth.md) — registration creates users
- [Settings](settings.md) — preferences keyed by `userId`
