# Babo Python runtime (`server/`)

FastAPI application that hosts the **agent brain**: agentic loop, tools, skills, channels, and admin APIs.

Entry point: `server.main:app` (uvicorn).

---

## Quick start (development)

From the repo root:

```bash
# Create venv and install deps (see docs/development/local-development.md)
pip install -r requirements.txt

export NLS_PRODUCT_MODE=1
export NLS_DATA_DIR=./data

uvicorn server.main:app --host 127.0.0.1 --port 9222 --log-level info
```

Default port in `server/config.py` may differ (`8443`) — desktop and docs standardize on **9222**.

---

## Auth

- **Product mode + loopback:** local trust for chat/channels on `127.0.0.1`
- **Admin routes:** `Authorization: Bearer <token>` or configured shared secret
- See [Auth & access](../docs/architecture/auth-and-access.md)

---

## Key directories

| Path | Role |
|------|------|
| `server/routes/` | HTTP routers (agents, chat, admin, skills, teams, …) |
| `server/services/` | Skill loader, agent manager, genesis seed, vLLM client |
| `server/middleware/` | Auth, CORS |
| `nls/` | Agent runtime, brain, tools (imported by server) |

---

## Documentation

- [Server runtime architecture](../docs/architecture/server.md)
- [Python API reference](../docs/reference/python-api.md)
- [Admin API](../docs/reference/admin-api.md)
- [WebSocket events](../docs/reference/websocket-events.md)
- [Local development](../docs/development/local-development.md)
