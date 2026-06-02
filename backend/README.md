# Babo backend

NestJS API for accounts, agents, WebSocket chat, **desktop relay**, and integration proxies (ClawHub, email, channel webhooks).

In the default product model, the Python agent runtime runs on the user's machine (desktop app). The backend does **not** execute agents; it authenticates users and tunnels requests to desktops via an outbound WebSocket relay.

See [Deployment topologies](../docs/architecture/deployment-topologies.md).

## Setup

```bash
docker compose up -d postgres   # from repo root
cd backend
cp .env.example .env            # JWT secrets, optional RUNTIME_SHARED_SECRET
npm install
npx prisma migrate deploy
npm run start:dev
```

Default listen port: **3000**.

## Environment

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL |
| `JWT_SECRET` / `JWT_REFRESH_SECRET` | Auth tokens |
| `RUNTIME_SHARED_SECRET` | Relay WS auth + runtime hooks (`X-Runtime-Secret`) |
| `RUNTIME_URL` | Optional direct Python URL (self-host without desktop relay) |

## Deploy (Railway)

Build command (monorepo root example):

```bash
cd backend && npm install && npx prisma migrate deploy && npm run build
```

Start: `npm run start:prod` with `DATABASE_URL` and JWT vars set.

Details: [Cloud deployment](../docs/configuration/cloud-deployment.md).

## Docs

- [Environment variables](../docs/configuration/environment-variables.md)
- [Self-hosting](../docs/configuration/self-hosting.md)
- [Troubleshooting](../docs/development/troubleshooting.md)
