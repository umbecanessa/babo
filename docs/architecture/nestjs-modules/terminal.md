# Terminal module

**Path:** `backend/src/terminal/`

**User-local** PTY on the NestJS host (node-pty) — **not** the agent's bash tool or Python `/ws/terminal`.

---

## Files

| File | Role |
|------|------|
| `terminal.module.ts` | `JwtModule` only |
| `terminal.gateway.ts` | `@WebSocketGateway({ namespace: '/terminal' })` |

---

## Socket.IO `/terminal`

| Event | Direction | Purpose |
|-------|-----------|---------|
| (connect) | in | JWT validate → spawn shell |
| `terminal:input` | in | Keystrokes |
| `terminal:resize` | in | cols/rows |
| `terminal:cwd` | in | Working directory hint |
| `terminal:ready` | out | Session started |
| `terminal:output` | out | PTY stdout |
| `terminal:exit` | out | Process ended |

---

## Environment

| Variable | Purpose |
|----------|---------|
| `JWT_SECRET` | Auth |
| `SHELL` | Default shell (non-Windows) |

---

## Python runtime

**None** — intentionally separate from agent execution for security and deployment simplicity.

Agent shell access uses Python `server/routes/terminal_ws.py` on desktop only.

---

## Related

- [Electron IPC](../../desktop/ipc-reference.md)
