# The Babo manifesto

**Status:** Living statement of mission and direction (early access).  
**Product:** [Babo](https://github.com/umbecanessa/babo) — open-source agent platform.  
**Research:** [Neural Ledger System (NLS)](https://github.com/umbecanessa/neural-ledger-system) — stateful inference and memory architecture behind the long-term bet.

---

## What we believe

Large language models are one of the most important tools of the next technological revolution — in the same league as electricity, the personal computer, and the internet. Not a toy. Not only a chat box. Infrastructure.

But infrastructure should **serve people**, not drain them. Today much of “AI” is sold like a utility bill that never stops: per-token, per-month, per-seat — optimized for vendor margin, not for your life.

**We refuse that as the default.**

For the overwhelming majority of real work — email, scheduling, projects, home ops, code, research, coordination — you do **not** need the most expensive frontier model on every turn. You need an agent that **remembers**, **acts**, and **runs where you already have compute**: your desk, your LAN, your small server.

You should **pay for power when you choose it**, not rent it forever because the product forgot your context yesterday.

---

## The future we are building toward

We are building toward a world where:

- **Every family** has a computer at home running agent software they control.
- **Every office** has a machine on the LAN — or a dedicated box — running the same class of software for the team.
- That software is not “another app.” It is closer to an **operating system for delegated work**: persistent memory, tools, channels, plans, sleep/consolidation, identity — always on, always *yours*.

Babo is our step one: the **shippable, open-source product** that makes local agents real for early adopters today — desktop wizard, integrations, teams, memory — without requiring everyone to become a DevOps engineer.

Optional **Babo Cloud** exists for convenience (relay, hosted channels, resold models), in the same spirit as [Home Assistant Cloud](https://www.home-assistant.io/cloud/): **the product must work fully without it.**

---

## Principles

### 1. Local-first

Agent memory, workspace, and runtime state live on **your hardware** by default. Cloud is a shortcut, not a requirement.

### 2. Honest economics

- Run **local or LAN inference** (Ollama, vLLM, your GPU box) for everyday work.
- Bring **your own API keys** when you want a hosted model.
- Use **Babo Cloud** only when it genuinely saves you time — not because we locked the product behind it.

The current AI pricing bubble makes vendors rich; it is **not sustainable** for households and small teams. Local inference is already good enough for most tasks — and getting better every quarter.

### 3. Persistent agents, not disposable chats

ChatGPT-style threads reset the world every session. Babo is built around **Cryptex memory**, plans, projects, and consolidation — so the agent’s cost and quality do not scale linearly with “how much you already told it.”

That design choice is the bridge to **[NLS research](https://github.com/umbecanessa/neural-ledger-system)**: treating context as **state** you maintain and retrieve, not as a firehose you re-send on every click.

### 4. Open source, forkable, inspectable

Babo is **MIT**. You can self-host, fork, audit, and run without us. Commercial operator code for paid Babo Cloud stays separate; the agent platform itself stays in the commons.

### 5. Agents that work, not agents that perform

Orchestration, Kanban pickup, coordinator policy, verification gates — less theatre, less drift, fewer tokens burned re-explaining the same job. The agent should **finish work**, not impress you with prose.

### 6. Extensible platform — plug-in capabilities

Babo is built as a **platform**, not a fixed app. The runtime is designed so builders can add:

- **Native NLS skills** — Python packages under `nls/skills/` with tools, webhooks, config, and onboarding (`register(app, ctx)`).
- **Agent tools** — programmatic capabilities in the agentic loop (files, shell, plan, team, custom tools).
- **Channel integrations** — new messaging surfaces (webhook → NestJS → desktop relay).
- **MCP & ClawHub** — attach external tool servers and community skill packages; **crystallize** proven skills into native modules.

Ship a dedicated skill for your stack, a new channel for your org, or a tool that only your agents need — then plug it in. That extensibility is central for innovators and self-hosters who outgrow one-size-fits-all AI workspaces.

See the [Extension guide](extension/index.md).

---

## Babo and NLS

| Layer | What it is |
|-------|------------|
| **Babo** | Product you can install today — UI, runtime, skills, channels, memory, teams. |
| **NLS** | Research program on **stateful inference** — how to keep intelligence useful as history grows without paying frontier prices to re-read the past. |

Babo implements NLS ideas in production-shaped code (memory rings, ledger, sleep, bridge). NLS explores what comes next. They move together; neither replaces the other.

If you care about the science and economics of long-horizon agents, start with the [Neural Ledger System repository](https://github.com/umbecanessa/neural-ledger-system).

---

## What exists today (honest snapshot)

Early access means rough edges. What is real now:

- Desktop app with guided setup (no terminal required for everyday users).
- Persistent memory, agentic loop, projects/teams, WhatsApp/Telegram/Google/email integrations.
- Self-host path (Postgres + NestJS + Python runtime).
- Optional Babo Cloud for hosted convenience.

What we are still proving:

- Onboarding for non-builders at scale.
- Google OAuth and channel reliability in production.
- Hardware story beyond “power user builds a box” (e.g. future **Babo Box** — plug-and-play home agent).

We ship in the open so builders can stress-test the thesis with us.

---

## Who this is for

- **Families and individuals** who want a private agent on their own machine — not another subscription that forgets them.
- **Offices and teams** who want delegated work on a board, with channels and memory, without sending every document to a third-party SaaS brain.
- **Builders and researchers** who believe local inference + persistent state is the next platform shift — and want open code to extend.

---

## Call to action

| If you want to… | Do this |
|-----------------|--------|
| Try Babo | [Download from GitHub Releases](https://github.com/umbecanessa/babo/releases) |
| Read how it works | [Getting started](getting-started/index.md) |
| Self-host | [Self-hosting guide](configuration/self-hosting.md) |
| Explore the research | [Neural Ledger System](https://github.com/umbecanessa/neural-ledger-system) |
| Build with us | [Contributing](https://github.com/umbecanessa/babo/blob/main/CONTRIBUTING.md) · [Discord](https://discord.gg/daCKzkv4z2) |

---

## One line

**LLMs belong on your desk — persistent, affordable, and yours — not on a meter that resets when you close the tab.**

That is the revolution we are betting on. Babo is how we start.
