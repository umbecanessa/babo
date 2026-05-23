# Installation

## Option A: Desktop app (recommended)

The desktop app bundles the Babo UI and manages a local Python runtime automatically.

### Build from source

```bash
git clone https://github.com/umbecanessa/babo.git
cd babo/desktop
npm install
npm run build
```

**Windows**

```bash
npm run dist:win
```

**macOS**

```bash
npm run dist:mac
```

Installers appear under `desktop/release/` (or your electron-builder output directory).

### First launch

The setup wizard walks through:

1. **Python environment** — creates a venv and installs runtime dependencies
2. **Inference provider** — URL, model name, optional API key
3. **Backend URL** — NestJS API (localhost for self-host, or your hosted backend)
4. **Launch** — starts the local runtime on `127.0.0.1:9222`

See [Desktop configuration](../configuration/desktop.md) for advanced options.

---

## Option B: Self-hosted stack

Run the full product in the browser with your own infrastructure.

### 1. Database

From the repo root:

```bash
docker compose up -d postgres
```

This starts PostgreSQL on port `5432` with database `nls`, user `nls`, password `nls`.

### 2. Backend (NestJS)

```bash
cd backend
cp .env.example .env
```

Edit `.env`:

- `DATABASE_URL` — Postgres connection string
- `JWT_SECRET` / `JWT_REFRESH_SECRET` — random strings
- `RUNTIME_URL` — where the Python runtime listens (e.g. `http://127.0.0.1:9222`)

```bash
npm install
npx prisma migrate deploy
npm run start:dev
```

Backend listens on **http://localhost:3000**.

### 3. Agent runtime (Python)

```bash
pip install -r requirements-desktop.txt

export NLS_VLLM_BASE_URL=https://openrouter.ai/api/v1
export NLS_HF_MODEL=openai/gpt-4o-mini
export NLS_INFERENCE_API_KEY=sk-...   # if your provider requires it

uvicorn server.main:app --host 127.0.0.1 --port 9222
```

### 4. Frontend (Angular)

```bash
cd frontend
npm install
npm start
```

Open **http://localhost:4200**, register an account, and create an agent.

Full details: [Self-hosting guide](../configuration/self-hosting.md).

---

## Option C: Cloud hub + desktop brain (recommended for teams)

This is the **primary product topology**:

- **Hosted NestJS + Postgres** — auth, agent registry, relay, channel webhooks
- **Hosted Angular** — use Babo from any browser
- **Desktop on each machine** — Python runtime, memory, inference; connects **outbound** to NestJS via relay WebSocket

Users sign in on the web, but agents run on the machine where the desktop app is open.

Full diagram: [Deployment topologies](../architecture/deployment-topologies.md).

Deploy NestJS to Railway or similar: [Cloud deployment](../configuration/cloud-deployment.md).

---

## Verify installation

| Check | How |
|-------|-----|
| Backend healthy | `curl http://localhost:3000/api/health` |
| Runtime healthy | `curl http://127.0.0.1:9222/health` |
| Inference reachable | Use **Test Connection** in the desktop setup wizard |

---

## Next steps

- [Quickstart](quickstart.md)
- [Inference providers](../configuration/inference-providers.md)
- [Environment variables](../configuration/environment-variables.md)
