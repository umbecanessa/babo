# Architecture

Technical documentation for how Babo is built and how data flows.

---

## Start here

0. **[2026 release: Babo Cloud + orchestration](2026-cloud-orchestration-release.md)** — **branch delta vs `main`** (read first if upgrading).
1. **[Production architecture & onboarding](production-architecture-and-onboarding.md)** — ship-ready map + wizard UX (read first for release).
2. **[Babo Cloud personas & commercial design](babo-cloud-personas-and-commercial-design.md)** — who runs what, BYO vs hosted, email/Google, pricing direction.
3. **[Deployment topologies](deployment-topologies.md)** — desktop hub, hosted web, NestJS relay.
4. **[System overview](overview.md)** — repository map and request path.
5. **[Data flow](data-flow.md)** — messages, signals, sleep.

---

## Runtime (Python + `nls/`)

| Document | Topic |
|----------|-------|
| [Agent runtime](agent-runtime.md) | `AgentRuntime` lifecycle and APIs |
| [Agentic loop](agentic-loop.md) | Multi-step LLM + tools |
| [Orchestration & delegation](orchestration-and-delegation.md) | Teams, waves, coordinator policy |
| [Skill discovery & recovery](skill-discovery-and-recovery.md) | Stall, ClawHub, recipes, ring boost |
| [Brain & memory](brain-and-memory.md) | Cryptex, hormones, DomainDB, identity |
| [Bridge & AKU](bridge-and-aku.md) | Knowledge units, agentic bridge |
| [Ledger & DomainDB](ledger-and-domain-db.md) | SQLite facts, Merkle chain |
| [ClawHub proxy](clawhub-proxy.md) | Python vs NestJS marketplace |
| [Sleep & consciousness](sleep-and-consciousness.md) | Schedulers, inner loop |
| [Skills system](skills-system.md) | Bundled skills, loader, bridges |
| [Tools system](tools-system.md) | Agent tools vs JSON registry |
| [NLS modules](nls-modules/index.md) | Per-package deep dives (`nls/`) |
| [NestJS modules](nestjs-modules/index.md) | Per-module deep dives (`backend/src/`) |
| [Server runtime](server.md) | FastAPI, startup, routes |
| [Inference & plugins](inference-and-plugins.md) | BYO API vs lab plugins |
| [Capability profiles & onboarding](capability-profiles-and-onboarding.md) | Composable local / LAN / hosted setup |
| [Babo Cloud personas & commercial design](babo-cloud-personas-and-commercial-design.md) | Personas, placements, platform BYO, metering & pricing direction |
| [Vision worker](vision-worker.md) | Ambient Moondream: local, GX10 container, hosted |

---

## Cloud & clients

| Document | Topic |
|----------|-------|
| [NestJS backend](nestjs-backend.md) | Auth, relay, ClawHub |
| [Frontend application](frontend-application.md) | Angular, remote vs Electron |
| [Channels & webhooks](channels-and-webhooks.md) | Telegram, WhatsApp, email |
| [Auth & access](auth-and-access.md) | JWT, relay secret, API keys |
| [Inner loop](inner-loop.md) | Autonomous DMN / drives |
| [Device lease](device-lease.md) | Exclusive desktop access |
| [Soul packages](soul-packages.md) | Export, fork, cloud metadata |
| [Connection & broadcasts](connection-and-broadcasts.md) | Real-time UI events |
| [Desktop app](desktop.md) | Electron, venv, IPC |
| [Persistence](persistence.md) | Disk layout |
| [Agent lifecycle](lifecycle.md) | Create, load, evict |
| [Genesis templates](genesis.md) | New agent bootstrap |
| [Consciousness scheduler](consciousness-scheduler.md) | State machine summary |

---

## Examples & sequences

| Document | Topic |
|----------|-------|
| [NestJS Agents API](examples/nestjs-agents-api.md) | Request/response samples |
| [NestJS Channels API](examples/nestjs-channels-api.md) | Webhooks, relay, email |
| [Agent runtime API](examples/agent-runtime-api.md) | Python WS & REST |
| [Relay join sequence](sequences/relay-join.md) | Web chat + desktop relay |
| [Agentic loop sequence](sequences/agentic-loop.md) | One turn inside `run_loop` |

---

## Reference & extension

- [Reference index](../reference/index.md) — APIs, relay wire format, env vars
- [Extension guide](../extension/index.md) — add tools, skills, channels

---

## User-facing docs

For using Babo (not building it), see the [documentation index](../index.md) and [user guides](../guides/index.md).
