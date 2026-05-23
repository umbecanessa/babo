# Genesis templates

Genesis templates let Babo **create new agents in seconds** from a shared starting configuration.

**Default template:** `standard-v1`

---

## What is genesis?

A genesis template is a packaged **brain bootstrap**:

- Default hormone, drive, ANS, and DMN configs
- Soul / identity starting point
- Signal taxonomy defaults
- Empty memory stores — ready to personalize through use

Genesis is **configuration-based**: JSON templates and default brain state. Babo does not ship model weight files or run training when you create an agent.

---

## Template location

```text
data/genesis/standard-v1/
├── manifest.json
├── config/
│   ├── hormones.json
│   ├── autonomic.json
│   ├── drives.json
│   ├── dmn.json
│   └── signals.json
└── defaults/
    ├── hypothalamus_state.json
    └── ans_state.json
```

Seeded automatically by `server/services/genesis_seed.py` when the runtime starts if missing.

---

## Creating an agent

**From UI (`/create`):**

1. User picks a genesis **path** (personality archetype)
2. Names the agent
3. Backend + runtime create `data/agents/{id}/` from template

**Environment default:**

```bash
NLS_DEFAULT_GENESIS=standard-v1
```

**API:**

```http
POST /agents
{ "genesis_version": "standard-v1", "name": "My Agent" }
```

---

## What diverges per agent

After creation, each agent independently accumulates:

| Diverges | Shared at genesis only |
|----------|------------------------|
| Conversation history | Initial config snapshot |
| Cryptex ring content | — |
| DomainDB facts | — |
| Installed skills | — |
| Plans, teams, workspace files | — |
| Narrative self & episodes | — |

Copying `data/agents/{id}/` clones the agent's entire learned state.

---

## Genesis paths in UI

The creation flow ("Choose a Mind") presents **paths** — UX labels for templates with different starting tone or specialty. Under the hood each maps to a genesis version or profile config.

Model selection in creation refers to **inference model** (which LLM API to use), not a local weights path.

---

## Custom templates

Advanced operators can add templates under `data/genesis/{version}/` and reference them by version string. Required:

- Valid `manifest.json`
- Complete `config/` JSON set
- Compatible `defaults/` state files

Restart runtime or call genesis reload after adding templates.

---

## Related

- [Agent lifecycle](lifecycle.md)
- [Persistence](persistence.md)
- [Create agent quickstart](../getting-started/quickstart.md)
