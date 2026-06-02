# Architecture overview

Babo is a **three-tier agent platform** with a biologically-inspired runtime at the core.

---

## High-level diagram

```text
┌──────────────────────────────────────────────────────────────┐
│                        User interfaces                        │
│   Angular web UI  ·  Electron desktop  ·  Channel webhooks   │
└────────────────────────────┬─────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────┐
│                     NestJS backend (:3000)                    │
│   Auth · Users · Agent registry · WebSocket relay · ClawHub   │
└────────────────────────────┬─────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────┐
│              Python runtime — server/ + nls/ (:9222)            │
│  Agentic loop · Cryptex memory · Skills · Tools · Sleep       │
└────────────────────────────┬─────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────┐
│              Inference API (OpenAI-compatible)                │
└──────────────────────────────────────────────────────────────┘
```

---

## Repository components

| Path | Responsibility |
|------|----------------|
| `frontend/` | Angular SPA — chat, projects, memory, brain, tools |
| `backend/` | NestJS + Prisma + PostgreSQL |
| `desktop/` | Electron — venv, runtime lifecycle, updates |
| `nls/` | Agent brain — memory, agentic loop, skills, identity |
| `server/` | FastAPI — HTTP/WebSocket API, schedulers, agent manager |

---

## Runtime brain (nls/)

Major subsystems:

| Subsystem | Role |
|-----------|------|
| **agentic/** | Loop, plans, delegates, teams, compaction |
| **brain/** | Cryptex, working memory, hormones, drives, DMN, ANS |
| **tools/** | Built-in agent tools + MCP bridge |
| **skills/** | Bundled and installed skill packages |
| **identity/** | Soul, narrative self, theory of mind |
| **ledger/** | Genesis templates, chain helpers |

---

## Request path (chat)

1. User message → WebSocket `/ws/chat/{agent_id}`
2. Runtime loads agent — Cryptex, WM, history, tools
3. Agentic loop runs — LLM + tools until complete
4. Signals extracted → hormones, learning buffer
5. Response streamed to UI
6. Sleep scheduler may queue consolidation

See [Data flow](data-flow.md) for detail.

---

## Persistence

| Data | Location |
|------|----------|
| User accounts | PostgreSQL (backend) |
| Agent metadata | PostgreSQL + runtime files |
| Memory, facts, chain | `data/agents/{id}/` |
| Skill configs | Per-agent skill store |
| Teams / plans | Agent workspace JSON |

See [Persistence](persistence.md).

---

## Design philosophy

Babo maps software components to cognitive neuroscience — not as marketing, but as **architecture discipline**:

- **Thalamus** — routing and gating
- **Hypothalamus** — hormones
- **Hippocampus** — domain memory
- **ANS** — sleep/wake
- **PFC** — orchestration and plans

This yields predictable behavior: sleep consolidates, drives motivate, signals teach.

---

## Further reading

- [Data flow](data-flow.md)
- [Server runtime](server.md)
- [Desktop app](desktop.md)
- [Persistence](persistence.md)
- [Core concepts](../getting-started/concepts.md)

