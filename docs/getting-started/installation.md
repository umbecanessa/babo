# Installation

## Option A: Desktop app (recommended)

The desktop app bundles the Babo UI and manages a local Python runtime automatically.

### Download (pre-built installers)

**[Download the latest release](https://github.com/umbecanessa/babo/releases)** from GitHub Releases.

Choose the installer for your platform from the **Assets** section of the latest release:

- **Windows** — `Babo-Setup-x.y.z.exe` (NSIS)
- **macOS** — `.dmg` (universal or arch-specific per release notes)

**Linux** installers are not published by GitHub Actions today. Build locally: `cd desktop && npm run dist:linux` (see [Desktop configuration](../configuration/desktop.md)).

After install, the setup wizard walks you through device scan, capability placement (Thinking + Extras in the UI), optional Babo Cloud sign-in, and first agent — no terminal required for normal use.

Step-by-step: [First run & setup](../guides/first-run-and-setup.md). Architecture macro phases (Environment → Scan → Capabilities → Account → Launch) map to those wizard screens — “Capabilities” in architecture docs = **Thinking + Extras** steps in the app.

The app checks for updates on launch (see [Desktop configuration — Updates](../configuration/desktop.md#updates)).

### Build from source

```bash
git clone https://github.com/umbecanessa/babo.git
cd babo
python scripts/regenerate-genesis.py
python scripts/ensure-desktop-icons.py
cd desktop
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

Installers appear under `desktop/release/` (CI) or `desktop/release-build/` when using `build-local.ps1`.

### First launch

The setup wizard includes:

1. **Environment prep** — Python venv, pip deps, Playwright, bundled Node (and PowerShell 7 on Windows)
2. **Device scan** — local GPU/RAM and optional LAN inference probe
3. **Thinking & Extras** — where chat inference, vision, transcribe, and embeddings run
4. **Account & backend** — Babo Cloud sign-in (optional) and NestJS URL
5. **Launch & name agent** — local runtime on `127.0.0.1:9222`

See [First run & setup](../guides/first-run-and-setup.md) for the full wizard map.

See [Desktop configuration](../configuration/desktop.md) for the full setup pipeline and userData paths.

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
