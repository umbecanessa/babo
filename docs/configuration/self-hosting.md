# Self-hosting

Run Babo entirely on your infrastructure.

---

## Architecture

```text
Users → Angular frontend (:4200 or static CDN)
     → NestJS backend (:3000) → PostgreSQL
     → Python runtime (:9222) → Inference API
```

WebSocket chat may flow: Browser → NestJS → Runtime (relay) or Browser → Runtime (direct in some desktop configs).

---

## Step-by-step

### 1. Postgres

```bash
docker compose up -d postgres
```

### 2. Backend

```bash
cd backend
cp .env.example .env
# Edit DATABASE_URL, JWT_SECRET, JWT_REFRESH_SECRET, RUNTIME_URL
npm install
npx prisma migrate deploy
npm run build
npm run start:prod   # or start:dev for development
```

### 3. Runtime

On the same or dedicated machine:

```bash
pip install -r requirements-desktop.txt
export NLS_VLLM_BASE_URL=...
export NLS_HF_MODEL=...
uvicorn server.main:app --host 0.0.0.0 --port 9222
```

Set `RUNTIME_URL` on the backend to this address. Use `RUNTIME_SHARED_SECRET` if exposing runtime beyond localhost.

### 4. Frontend

**Development:**

```bash
cd frontend && npm install && npm start
```

**Production:**

```bash
cd frontend && npm run build
# Serve dist/ via nginx, or nest static middleware
```

Update `environment.prod.ts` API URLs before building.

---

## HTTPS

Production deployments should terminate TLS at nginx/Caddy:

- `api.yourdomain.com` → NestJS
- `app.yourdomain.com` → Angular static
- Runtime often stays on private network only — backend relays

---

## Multi-user

Each user gets agents in Postgres; runtime loads agent data from `data/agents/{id}/`. Single runtime process can serve multiple agents (consciousness scheduler manages load).

---

## ClawHub proxy

Backend `clawhub` module proxies community skill API — ensure outbound HTTPS allowed.

---

## Backups

Backup:

- PostgreSQL database
- `data/agents/` directory (memory, configs, workspaces)

---

## Related

- [Environment variables](environment-variables.md)
- [Installation](../getting-started/installation.md)
- [Architecture — server](../architecture/server.md)
