# Babo Documentation

Welcome to the Babo docs. Babo is a persistent AI agent platform with long-term memory, autonomous task execution, project orchestration, and one-click channel integrations.

**Product site:** [umbecanessa.github.io/babo](https://umbecanessa.github.io/babo/) — early-access homepage with vision, capabilities, and download links.

**Documentation map:** you are on the docs home (also linked from the site nav as *Documentation*). If you landed on the marketing homepage, open [Getting started → Installation](getting-started/installation.md) to install Babo.

**Latest architecture (feature branch):** [2026 Cloud + orchestration release](architecture/2026-cloud-orchestration-release.md) documents everything new vs `main` — Babo Cloud, team waves, capability onboarding, multi-agent UI.

---

## How Babo works (30 seconds)

```text
Your desktop runs the agent brain (Python).
The cloud runs auth + relay (NestJS).
The website is a remote control panel (Angular).
```

Details: **[Deployment topologies](architecture/deployment-topologies.md)**

---

## I want to…

| Goal | Start here |
|------|------------|
| Install & first agent | [Installation](getting-started/installation.md) → [Quickstart](getting-started/quickstart.md) |
| Understand concepts | [Core concepts](getting-started/concepts.md) |
| Deploy backend to Railway | [Cloud deployment](configuration/cloud-deployment.md) |
| See how chat/memory/tools work | [User guides](guides/index.md) |
| Understand the full architecture | [Architecture index](architecture/index.md) |
| See what changed vs main (2026) | [2026 release architecture](architecture/2026-cloud-orchestration-release.md) |
| Look up APIs & relay protocol | [Reference](reference/index.md) |
| Add a tool or skill | [Extension guide](extension/index.md) |
| Fix relay / build errors | [Troubleshooting](development/troubleshooting.md) |

---

## Documentation map

### Getting started

| Guide | Description |
|-------|-------------|
| [Installation](getting-started/installation.md) | Desktop, self-host, cloud hub |
| [Quickstart](getting-started/quickstart.md) | First agent in minutes |
| [Core concepts](getting-started/concepts.md) | Agents, memory, sleep, loop |

### User guides

| Guide | Description |
|-------|-------------|
| [Chat](guides/chat.md) | Streaming, voice, commands |
| [Agentic loop & plans](guides/agentic-loop-and-plans.md) | Autonomy, plans, sub-agents |
| [Projects & teams](guides/projects-and-teams.md) | Board, timeline, orchestration |
| [Memory](guides/memory.md) | Cryptex, knowledge, soul |
| [Brain dashboard](guides/brain-dashboard.md) | Hormones, drives, signals |
| [Sleep & consolidation](guides/sleep-and-consolidation.md) | Long-term memory |
| [Tools & skills](guides/tools-and-skills.md) | Integrations, MCP, ClawHub |
| [Settings & API keys](guides/settings-and-api-keys.md) | Account prefs, automation keys |
| [Integrations](guides/integrations/index.md) | WhatsApp, Telegram, Google, email |

### Architecture (deep)

| Guide | Description |
|-------|-------------|
| [Deployment topologies](architecture/deployment-topologies.md) | Desktop hub + web relay |
| [Agent runtime](architecture/agent-runtime.md) | Per-agent orchestrator |
| [Agentic loop](architecture/agentic-loop.md) | LLM + tools cycle |
| [Brain & memory](architecture/brain-and-memory.md) | Cognitive stack |
| [NestJS backend](architecture/nestjs-backend.md) | Control plane |
| [Frontend](architecture/frontend-application.md) | Angular modes |
| [Skills](architecture/skills-system.md) · [Tools](architecture/tools-system.md) | Extension points |
| [Full architecture index](architecture/index.md) | All architecture pages |
| [2026 Cloud + orchestration](architecture/2026-cloud-orchestration-release.md) | Branch delta vs main |

### Reference

| Guide | Description |
|-------|-------------|
| [Glossary](reference/glossary.md) | Terms |
| [Python API](reference/python-api.md) | FastAPI routes |
| [NestJS API](reference/nestjs-api.md) | REST + Socket.IO |
| [Relay protocol](reference/relay-protocol.md) | Desktop ↔ cloud WS |
| [Environment (complete)](reference/environment-complete.md) | All env vars |
| [Reference index](reference/index.md) | Full list |

### Extension & development

| Guide | Description |
|-------|-------------|
| [Extension guide](extension/index.md) | Add tools, skills, channels |
| [Local development](development/local-development.md) | Run from source |
| [Troubleshooting](development/troubleshooting.md) | Common failures |
| [Product scope](development/product-scope.md) | In / out of repo |

---

## Repository layout

```text
frontend/   Angular UI (web + Electron renderer)
backend/    NestJS API + PostgreSQL + relay
desktop/    Electron shell + runtime lifecycle
nls/        Agent brain (memory, loop, skills, tools)
server/     Python FastAPI runtime
docs/       This documentation (MkDocs)
```

---

## Contributing

See [CONTRIBUTING.md](https://github.com/umbecanessa/babo/blob/main/CONTRIBUTING.md) and [Extension guide](extension/index.md).
