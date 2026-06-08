# Reference

Canonical technical reference for Babo. Use this section when you need **exact behavior**, **file paths**, or **wire formats** — not tutorials.

---

## Quick links

| Topic | Document |
|-------|----------|
| Terms & acronyms | [Glossary](glossary.md) |
| All environment variables | [Environment (complete)](environment-complete.md) |
| On-disk layout | [Data directory](data-directory.md) |
| FastAPI routes | [Python HTTP & WebSocket API](python-api.md) |
| Admin routes | [Admin API](admin-api.md) |
| Skills & ClawHub routes | [Skills admin API](skills-admin-api.md) |
| Chat WS commands | [Chat slash commands](chat-commands.md) |
| Teams / projects REST | [Teams & projects API](teams-api.md) |
| Job, Trust, Squads REST | [Job, Trust & Squad API](job-trust-squad-api.md) |
| NestJS modules & routes | [NestJS API](nestjs-api.md) — includes `/api/rt` runtime proxy |
| Relay WebSocket protocol | [Relay protocol](relay-protocol.md) |
| Postgres models | [Database schema](database-schema.md) |
| Socket.IO chat events | [WebSocket events](websocket-events.md) |
| Desktop IPC | [Electron IPC](../desktop/ipc-reference.md) |

---

## Source code map

| Path | Role |
|------|------|
| `nls/` | Agent brain — memory, loop, skills, identity |
| `server/` | FastAPI process — HTTP, WS, schedulers |
| `backend/` | NestJS — auth, relay, channel webhooks |
| `frontend/` | Angular UI |
| `desktop/` | Electron shell + runtime lifecycle |

---

## Related

- [Architecture index](../architecture/index.md)
- [Extension guide](../extension/index.md)
