# Persistence

Every Babo agent is a **self-contained directory** on disk. Copy the folder to clone an agent; back it up to preserve memory and config.

---

## Top-level layout

```text
data/
├── genesis/
│   └── standard-v1/          # Shared template (seeded on first server start)
└── agents/
    └── {agent_id}/           # Per-agent data (the important part)
```

---

## Genesis template (`data/genesis/standard-v1/`)

Created by `server/services/genesis_seed.py` on first boot:

```text
standard-v1/
├── manifest.json             # Template metadata
├── config/                   # Default brain JSON configs
│   ├── hormones.json
│   ├── autonomic.json
│   ├── drives.json
│   ├── dmn.json
│   └── …
└── defaults/                 # Initial runtime state snapshots
```

Genesis templates are **config-only** (JSON). Personality and behavior come from soul config, memory, and your chosen inference model.

---

## Agent directory (`data/agents/{agent_id}/`)

```text
{agent_id}/
├── agent_meta.json           # Name, genesis version, created_at
├── config/                   # Editable brain configs (copied from genesis)
├── workspace/                # Agent file workspace (tools, projects)
├── knowledge.db              # SQLite DomainDB (structured facts)
├── conversation_history.json # Session transcripts
├── hypothalamus_state.json   # Hormone levels
├── ans_state.json            # Sleep/wake state machine
├── teams/                    # Persisted team orchestration JSON
├── plans/                    # Active plan store snapshots
├── skills/                   # Per-agent skill config + installed skills
├── cryptex/                  # Ring slot persistence
├── events/                   # Event log (optional analytics)
└── soul/                     # Identity package data
```

Exact files vary by agent age and features used.

---

## What survives restart

| Data | Persists? | Location |
|------|-----------|----------|
| Long-term facts | Yes | `knowledge.db`, Cryptex files |
| Working memory slots | Yes | Cryptex / WM store |
| Conversation history | Yes | `conversation_history.json` + chain |
| Hormone levels | Yes | `hypothalamus_state.json` |
| Plans & teams | Yes | `plans/`, `teams/` |
| Skill configs | Yes | `skills/` |
| In-flight loop journal | Yes | Recovered on crash if journal written |
| KV cache | No | Rebuilt per session |

---

## Backend (PostgreSQL)

NestJS stores **account and registry** data:

- Users, JWT sessions
- Agent records (id, owner, display name)
- Channel aliases (email addresses)
- ClawHub auth tokens (server-side)

Agent **memory** lives on the runtime machine unless you centralize `data/agents/` on shared storage.

---

## Backup strategy

**Minimum backup set:**

1. `data/agents/` — all agent memory
2. Postgres dump — users and agent registry
3. `backend/.env` — secrets (store securely)

**Restore:** restore Postgres, copy `data/agents/`, point `RUNTIME_URL` at runtime with matching agent ids.

---

## Portability

To move an agent to another machine:

1. Copy `data/agents/{agent_id}/`
2. Register same agent id in backend (or create new and swap directory name)
3. Start runtime with same genesis template available

---

## Related

- [Genesis templates](genesis.md)
- [Agent lifecycle](lifecycle.md)
- [Memory guide](../guides/memory.md)
