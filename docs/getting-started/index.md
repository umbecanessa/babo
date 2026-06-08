# Getting started

Babo can run in three common setups:

| Setup | Best for |
|-------|----------|
| **Desktop app** | Personal use — [download from GitHub Releases](https://github.com/umbecanessa/babo/releases), local runtime, guided setup |
| **Self-hosted** | Teams — your own NestJS backend + Postgres + runtime |
| **Hybrid** | Hosted auth/backend + local runtime on each machine |

All setups use the same agent brain. You bring your own LLM via any **OpenAI-compatible API** (OpenRouter, Ollama, local vLLM, etc.).

---

## Prerequisites

### Everyone

- An inference endpoint URL and model name
- Optional API key (required by most hosted providers)

### Desktop

- **Windows 10+** or **macOS 12+**
- **Python 3.12** bundled with the desktop app (standalone install managed by the wizard)
- Manual install from source: **Python 3.11+**
- ~2 GB disk for the Python environment

### Self-hosted

- **Node.js 20+** (backend + frontend build)
- **PostgreSQL 15+** (or use Docker Compose from the repo root)
- **Python 3.11+** for self-hosted runtime (desktop bundles 3.12 automatically)

---

## Recommended path

1. [Download Babo](https://github.com/umbecanessa/babo/releases) · [Install guide](installation.md)
2. [Quickstart — first agent](quickstart.md)
3. [Core concepts](concepts.md)
4. Pick a guide: [First run & setup](../guides/first-run-and-setup.md) · [Vision, voice & embeddings](../guides/vision-voice-and-embeddings.md) · [Chat](../guides/chat.md) · [Projects](../guides/projects-and-teams.md) · [Integrations](../guides/integrations/index.md)

---

## What you get out of the box

Every Babo agent includes:

- **Persistent memory** across sessions (Cryptex + working memory + knowledge store)
- **Agentic loop** — plans, tools, evaluation, multi-turn execution
- **Projects workspace** — Kanban board, teams panel (wave timeline), workspace files IDE
- **Tools page** — WhatsApp, Telegram, Discord, Slack, Google Workspace, email, MCP, ClawHub
- **Brain dashboard** — transparent view of internal state (optional)
- **Sleep cycles** — background consolidation of experiences into durable memory

See the [documentation index](../index.md) for the full guide list.
