# Babo

<p align="center">
  <img src="website/assets/babo.png" alt="Babo" width="120" />
</p>

<p align="center">
  <strong>Self-hosted agent OS</strong> — persistent memory, projects & teams, WhatsApp · Telegram · Google.<br />
  MIT · local-first · no telemetry · <a href="https://babo.agency/">babo.agency</a>
</p>

<p align="center">
  <a href="https://github.com/umbecanessa/babo/releases"><strong>Download desktop</strong></a> ·
  <a href="https://babo.agency/manifesto/">Manifesto</a> ·
  <a href="https://babo.agency/getting-started/installation/">Docs</a> ·
  <a href="https://discord.gg/daCKzkv4z2">Discord</a>
</p>

**A persistent AI agent that works with you — across chat, projects, channels, and tools.**

Babo is a full-stack agent platform: a desktop app, web UI, and self-hostable backend. Each agent has long-term memory, an autonomous agentic loop, project orchestration with sub-agents, and one-click integrations for the channels and tools you already use.

Connect any OpenAI-compatible model (OpenRouter, Ollama, local vLLM, LAN server, Babo Cloud hybrid routing, etc.) and run Babo on your machine or your own server.

**Audiences:** [Builders](https://babo.agency/) (OSS, self-host) vs [Everyone](https://babo.agency/?audience=everyday) (personal AI, no terminal). See [docs/audiences.md](docs/audiences.md) and [PRODUCT.md](PRODUCT.md).

> **Not just a chat UI** — Babo is an **extensible agent platform**: native skills, agent tools, and channels you can add in code (MCP, ClawHub, crystallize). Memory, board, and teams survive sessions. See the [manifesto](https://babo.agency/manifesto/) and [extension guide](https://babo.agency/extension/).

---

## What Babo can do

### Chat with a real agent

- **Streaming conversations** with live tool use, thoughts, and file proposals
- **Agentic loop** — the agent orients, plans, calls tools, evaluates progress, and keeps going until the job is done
- **Structured plans** — multi-step runbooks with sub-plans, acceptance criteria, and verification
- **Slash commands** — e.g. trigger a sleep cycle to consolidate memory on demand
- **Voice input** — speak to your agent (local or remote transcription)
- **Embedded browser** — watch the agent browse and work in its own workspace

### Projects, board, timeline & teams

The **Projects** workspace is where Babo runs serious work:

| View | What you get |
|------|----------------|
| **Overview** | Live team orchestration, activity feed, wave timeline in Teams panel |
| **Board** | Kanban by status — Inbox, Queued, In Progress, Done, Deferred |
| **Files** | Project workspace IDE — explorer + editor for artifacts |

Babo can **spawn sub-agent teams** to work in parallel on plan steps. You see active teams, member progress, failures, and overall plan completion in real time — and can send hints to running delegates.

### Memory that persists

Babo agents don't forget between sessions:

- **Cryptex** — layered long-term memory (identity, user model, project facts, skills, channels, and more)
- **Working memory** — salience-weighted short-term slots for goals, constraints, and active context
- **Knowledge & facts** — searchable domain memory with conflict resolution
- **Merkle chain** — tamper-evident conversation and learning history
- **Episodes & narrative self** — the agent builds a coherent story of your relationship over time
- **Sleep cycles** — background consolidation turns experiences into durable memory

Explore everything in the **Memory** view: overview, knowledge tree, chain, working memory, episodes, and soul.

### Brain dashboard

The **Brain** page shows what your agent is "feeling" and doing internally:

- Hormone and drive dynamics
- Signal history (learning, bonding, reflection, and more)
- Working memory and narrative status
- Theory of mind — the agent's model of you
- Network activity over time

It's the control room for a biologically-inspired runtime — useful, transparent, and optional.

### Skills & one-click integrations

Install and configure capabilities from the **Tools** page:

| Integration | Setup |
|-------------|--------|
| **WhatsApp** | Scan a QR code — connect your personal account |
| **Telegram** | Create a bot via @BotFather; Babo walks you through it |
| **Discord** | Bot in scoped guild channels + DMs |
| **Slack** | App in scoped workspace channels + DMs |
| **Google Workspace** | OAuth modal — Gmail, Calendar, Drive, Sheets |
| **Email** | Auto-provisioned agent inbox for threaded conversations |
| **MCP servers** | Connect any MCP tool server; browse 20,000+ extensions |
| **ClawHub** | Search and install community skills from the ClawHub registry |

Bundled skills include guided onboarding (QR pairing, conversational setup, or in-app OAuth). Connected MCP tools appear as first-class agent tools in the loop.

Agents can also **search ClawHub**, install community skills, and **crystallize** frequently used skills into native plugins.

### Built-in agent tools

Beyond integrations, every agent can use:

- **Browser automation** (navigate, click, snapshot, scrape)
- **Shell & files** — read, write, search the workspace
- **Semantic codebase search**
- **Scheduler & HTTP pollers** — cron jobs and URL monitors
- **Contacts** — cross-channel address book (WhatsApp, Telegram, email)
- **Vision** — describe and ask questions about images
- **Downloads** — offer files back to you in chat

### Desktop & self-hosted

- **Desktop app (Electron)** — bundles the UI + local Python runtime; first-run wizard sets up Python, inference, and backend
- **Web UI (Angular)** — full product in the browser
- **Backend (NestJS + PostgreSQL)** — accounts, agents, WebSocket relay, ClawHub proxy
- **Local runtime (Python)** — agent brain, memory, tools, and FastAPI server on `127.0.0.1:9222`

Run desktop-only, self-host everything, or mix cloud auth with a local runtime.

---

## Quick start

### Desktop (recommended)

**[Download the latest release](https://github.com/umbecanessa/babo/releases)** (Windows / macOS installers from CI).

Or build from source:

```bash
git clone https://github.com/umbecanessa/babo.git
cd babo/desktop
npm install
.\build-local.ps1          # Windows: unpacked app in desktop/release-build/build-*/win-unpacked/
# or: npm run build && npm run dist:win   # NSIS installer
# macOS: npm run dist:mac (see desktop/BUILD-MAC.md)
```

On first launch: set up Python, your inference URL + model (+ optional API key), and backend URL.

### Self-hosted stack

**1. Database + backend**

```bash
docker compose up -d postgres
cd backend
cp .env.example .env   # edit secrets + RUNTIME_URL
npm install
npx prisma migrate deploy
npm run start:dev
```

**2. Agent runtime**

```bash
pip install -r requirements-desktop.txt
export NLS_VLLM_BASE_URL=https://openrouter.ai/api/v1
export NLS_HF_MODEL=openai/gpt-4o-mini
export NLS_INFERENCE_API_KEY=your-key   # if needed
uvicorn server.main:app --host 127.0.0.1 --port 9222
```

**3. Frontend**

```bash
cd frontend
npm install
npm start
```

Open the app, register, create an agent, and start chatting.

See the [documentation](https://babo.agency/getting-started/) for full guides.

---

## Repository layout

| Path | Role |
|------|------|
| `frontend/` | Angular UI — chat, projects, memory, brain, tools |
| `backend/` | NestJS API — auth, agents, relay, ClawHub |
| `desktop/` | Electron shell + local runtime manager |
| `nls/` | Agent brain — memory, agentic loop, skills, tools |
| `server/` | Python FastAPI runtime |

## Documentation

- **[Documentation](https://babo.agency/getting-started/)** — MkDocs site at [babo.agency](https://babo.agency/)
- **[Documentation home](docs/index.md)** — full map (architecture, reference, extension)
- [Deployment topologies](docs/architecture/deployment-topologies.md) — desktop hub + web relay
- [Quickstart](docs/getting-started/quickstart.md)
- [Extension guide](docs/extension/index.md)

The marketing homepage in `website/` is **deployed with the docs** — GitHub Actions overlays it onto the MkDocs build at [babo.agency](https://babo.agency/). See `website/README.md` and `.github/workflows/docs.yml`.

---

## License

MIT — see [LICENSE](LICENSE).
