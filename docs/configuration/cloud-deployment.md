# Cloud deployment

Guide for hosting the **NestJS backend** and **web UI** while users run the **desktop hub** locally.

---

## Architecture summary

```text
Users' browsers  →  Hosted Angular  →  NestJS (Railway)  →  Postgres
                              ↑
                    relay WebSocket
                              ↑
Users' desktops  →  Python runtime (:9222)  →  Inference API
```

The Python runtime is **not** required on the same Railway project for the default product model. See [Deployment topologies](../architecture/deployment-topologies.md).

---

## Railway: NestJS backend

### 1. Create services

- **PostgreSQL** plugin → copy `DATABASE_URL`
- **Node** service from this repo (`backend/` as root or monorepo with build command)

### 2. Environment variables

```env
DATABASE_URL=${{Postgres.DATABASE_URL}}
PORT=3000
JWT_SECRET=<64+ random chars>
JWT_REFRESH_SECRET=<64+ random chars>
JWT_EXPIRATION=4h
JWT_REFRESH_EXPIRATION=7d
RUNTIME_SHARED_SECRET=<shared with desktop if using relay auth>
# RUNTIME_URL only if you also run Python reachable from Railway (unusual for desktop-hub)
```

For **desktop-hub only**, you may leave `RUNTIME_URL` unset or pointed at a placeholder; chat from the web UI uses the **relay**, not direct HTTP to Python.

### 3. Build & start

```bash
npm install
npx prisma migrate deploy
npm run build
npm run start:prod
```

Railway should use `backend` as the working directory if deploying from monorepo root:

```bash
cd backend && npm install && npx prisma migrate deploy && npm run build
```

Start command: `npm run start:prod` (from `backend/`).

### 4. Health check

`GET /api/health` should return OK when Postgres and app boot succeed.

---

## Railway / static: Angular frontend

Build from repo root:

```bash
cd frontend
npm install
npm run build
```

Serve `frontend/dist/` (or `browser/` output per Angular version) as static files.

Set `environment.prod.ts` before build:

- `apiUrl: '/api'` if same host proxies API to NestJS
- Or full URL `https://api.yourdomain.com/api`

The web app **requires** users to run the desktop app for agent execution unless you operate a separate reachable Python runtime (advanced).

---

## Desktop client configuration

In the desktop setup wizard:

| Field | Example |
|-------|---------|
| Backend URL | `https://your-app.up.railway.app` |
| Inference URL | `https://openrouter.ai/api/v1` |
| Model | `openai/gpt-4o-mini` |

The desktop sets `NESTJS_URL` on the Python process so the relay connects automatically.

Verify relay:

1. Log in on web → dashboard shows agent **online** when desktop is running.
2. `GET /api/agents/:id/relay-status` → `{ "online": true }`

---

## Secrets alignment

| NestJS | Desktop Python |
|--------|----------------|
| `RUNTIME_SHARED_SECRET` | `NLS_SHARED_SECRET` (same value) |

Used for relay WebSocket auth (`?secret=`) and runtime HTTP hooks from NestJS.

---

## Email / Resend (optional)

```env
RESEND_API_KEY=re_...
RESEND_INBOUND_DOMAIN=inbox.yourdomain.com
```

Required for the email channel on cloud webhooks.

---

## Troubleshooting deploys

| Symptom | Likely cause |
|---------|----------------|
| Build fails on `nest build` | TypeScript errors in `backend/src` |
| 502 on `/api/rt/...` | Desktop offline or relay not connected |
| Web chat: “desktop not connected” | Relay WS down; check `NESTJS_URL` on desktop |
| Prisma errors on boot | Run `migrate deploy` against production DB |

More: [Troubleshooting](../development/troubleshooting.md).

---

## Related

- [Environment variables](environment-variables.md)
- [Self-hosting](self-hosting.md)
- [Deployment topologies](../architecture/deployment-topologies.md)
