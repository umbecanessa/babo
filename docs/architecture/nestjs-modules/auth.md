# Auth module

**Path:** `backend/src/auth/`

JWT authentication for the web UI and protected REST/Socket.IO endpoints.

---

## Files

| File | Role |
|------|------|
| `auth.module.ts` | Registers JWT + Passport |
| `auth.controller.ts` | Public auth routes |
| `auth.service.ts` | Password hashing, token pairs |
| `jwt.strategy.ts` | Bearer validation → `req.user` |
| `jwt-auth.guard.ts` | `AuthGuard('jwt')` |
| `dto/login.dto.ts`, `dto/register.dto.ts` | Validation |

---

## HTTP routes

| Method | Path | Handler |
|--------|------|---------|
| POST | `/api/auth/register` | Create user + tokens |
| POST | `/api/auth/login` | Email/password → tokens |
| POST | `/api/auth/refresh` | Refresh token rotation |

---

## Token payload

Access JWT carries `{ userId, email, role }`. Used by [Chat](chat.md), [Agents](agents.md), [Runtime proxy](runtime-proxy.md), etc.

---

## Environment

| Variable | Default |
|----------|---------|
| `JWT_SECRET` | required |
| `JWT_REFRESH_SECRET` | required |
| `JWT_EXPIRATION` | `15m` |
| `JWT_REFRESH_EXPIRATION` | `7d` |

---

## Prisma

- `User` — `create`, `findUnique` by email

---

## Python runtime

None. Relay auth uses `RUNTIME_SHARED_SECRET`, not user JWT.

See [Auth & access](../auth-and-access.md).

---

## Related

- [API keys](api-keys.md) — separate automation keys for Python
