# Core concepts

This page explains how Babo works in user terms. No ML jargon required.

---

## Agent

An **agent** is your persistent AI — one identity, one memory store, one workspace. You can run multiple agents on the same Babo install (each with separate memory and configuration).

Agents are created from a **genesis template** that sets starting personality, values, and defaults. After creation, each agent diverges based on your conversations and experiences.

---

## The agentic loop

When you send a message that requires more than a one-line answer, Babo enters the **agentic loop**:

```text
Orient → Augment context → LLM → Execute tools → Digest results → Evaluate → Complete
         ↑__________________________________________|
                    (repeat until done)
```

**What this means for you:**

- The agent can call **tools** (read files, browse the web, run commands, send messages, etc.)
- It **evaluates progress** against plans and goals before stopping
- You see **streaming events**: thoughts, tool starts/completions, file proposals
- Long tasks **compact context** automatically so they don't hit token limits

The loop supports multiple **modes** (chat, planning, delegating, executing, monitoring) — the agent switches mode based on what you asked for.

---

## Plans

Complex work is tracked as **plans**: ordered steps with optional sub-plans, acceptance criteria, and verification.

- Created by the agent (via the `plan` tool) or implied by your request
- Linked to **todo items** on the Kanban board
- Drive **team waves** — groups of sub-agents working in parallel on delegatable steps

You inspect plan progress in **Projects → Overview** and the orchestration ribbon.

---

## Teams and sub-agents

For large tasks, the orchestrator spawns **sub-agents** (delegates) with isolated context and their own tool access.

- Each **team** maps to a delegation wave from the plan
- **Timeline** shows which wave is running, completed, or failed
- You can send **hints** to running delegates from the Teams panel
- Sub-agents write to **SubCryptex** — memory isolated from the parent but inheriting shared rings

Think of it as a project manager (main agent) coordinating specialists (sub-agents).

---

## Memory layers

Babo memory is layered, like human cognition:

### Working memory

Short-term **slots** for active goals, constraints, feelings, instructions, and perceptions. Slots have **salience** (importance) and decay over time unless reinforced.

### Cryptex

Long-term **rotating rings** — each ring holds a category of context:

| Category | Rings (examples) |
|----------|------------------|
| **Fixed** | Identity, user model, consolidation, strategic goals |
| **Project** | Orchestration, instructions, project facts, credentials, tactical goals |
| **Domain** | Behavioral, skills, tools/MCP, channels |

When you switch projects, **project rings rotate** so parallel work doesn't cross-contaminate.

### Knowledge store (DomainDB)

Structured **facts** with domains, confidence, and conflict resolution. Searchable from the Memory UI and injected into context when relevant.

### Merkle chain

A tamper-evident **history** of conversation blocks and learning events. Powers audit, replay, and integrity checks in the Memory → Chain tab.

### Narrative self & soul

The agent maintains a **first-person narrative** of your relationship and a **soul** package (values, boundaries, axioms). Visible in Memory → Soul and episodes.

---

## Sleep and consolidation

**Sleep** is how Babo turns recent experience into durable memory.

During sleep:

1. **Triage** — prioritize signals (learning, corrections, reflections)
2. **Consolidation** — LLM summarizes and merges facts into Cryptex and DomainDB
3. **Integration** — reset buffers, update narrative, wake refreshed

Sleep runs on a schedule, when signal pressure builds, or when you send `/sleep` in chat.

After sleep, the agent **remembers** without you repeating context every session.

---

## Signals

Every interaction can produce **signals** — tagged learning events such as:

| Signal | Meaning |
|--------|---------|
| `LEARN` | New fact or preference detected |
| `EVALUATE:correct` | Agent believes it answered well |
| `EVALUATE:incorrect` | Agent detected an error |
| `REFLECT` | Introspective update |
| `BOND` | Relationship / trust marker |

Signals feed hormones, drives, and sleep queues. Power users watch them in the chat signal sidebar and Brain dashboard.

---

## Hormones and drives

Babo models **motivation** internally:

**Hormones** (dopamine, norepinephrine, serotonin, cortisol, oxytocin, acetylcholine) shift based on signals and decay over time. They influence curiosity, stress, bonding, and daydream frequency.

**Drives** (homeostasis, curiosity, competence, social, self-direction) build pressure when needs aren't met. When pressure is high enough, the agent may autonomously search, verify knowledge, reflect, or reach out — if it has the tools to act.

This is optional transparency, not something you must configure.

---

## Skills and tools

**Tools** are capabilities the agent invokes during the loop (builtin or from integrations).

**Skills** are installable packages that add:

- Channel adapters (WhatsApp, Telegram, email)
- OAuth integrations (Google Workspace)
- MCP server connections
- Community packages from **ClawHub**

Skills can use onboarding flows: QR pairing, conversational setup, or in-app OAuth modals.

**Crystallization** — skills used repeatedly with high success can be promoted to native plugins for faster execution.

---

## Channels

Once integrated, external channels route into the same agent brain:

- WhatsApp DMs and groups (policy-controlled)
- Telegram bot messages
- Agent email inbox
- Google Workspace events (Gmail polling, calendar, etc.)

**Contacts** unify address book across channels.

---

## Projects workspace

The **Projects** page is the command center for serious work:

| Tab | Purpose |
|-----|---------|
| **Overview** | Teams panel + activity feed |
| **Board** | Kanban lists and cards |
| **Timeline** | Wave execution history |
| **Files** | Workspace artifacts |

A **command bar** sends instructions to the agent in project context. **Chat sidebar** keeps conversation alongside the board.

---

## Glossary

| Term | Definition |
|------|------------|
| **Agent** | Persistent AI identity with memory and tools |
| **Genesis** | Starting template for a new agent |
| **Cryptex** | Rotating-ring long-term memory system |
| **Agentic loop** | Multi-turn execute-evaluate cycle |
| **Plan** | Structured multi-step runbook |
| **Team / wave** | Sub-agent group executing plan steps |
| **Delegate** | Sub-agent with scoped memory and tools |
| **Skill** | Installable capability package |
| **MCP** | Model Context Protocol — standard for tool servers |
| **ClawHub** | Community skill registry |
| **Sleep** | Consolidation cycle for durable memory |
| **Signal** | Tagged learning event from an interaction |

---

## Next steps

- [Quickstart](quickstart.md)
- [Memory guide](../guides/memory.md)
- [Agentic loop guide](../guides/agentic-loop-and-plans.md)
- [Architecture overview](../architecture/overview.md)
