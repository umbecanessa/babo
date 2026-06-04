# Data directory

Runtime state root: **`NLS_DATA_DIR`** (default `./data`).

Desktop packaged apps use Electron `userData/data/`.

### Desktop userData (Electron)

| Path | Purpose |
|------|---------|
| `nls-config.json` | Inference, backend, capability profile |
| `python-env/` | Python venv |
| `node-standalone/` | Bundled Node.js (skill bridges) |
| `powershell-standalone/` | Bundled PowerShell 7 (Windows) |
| `data/` | Agent runtime root (`NLS_DATA_DIR`) |
| `setup.log`, `runtime.log` | Setup and runtime logs |
| Settings → Support & Debug | In-app export of logs and agent state (preferred over manual copy) |

See [Desktop configuration](../configuration/desktop.md#desktop-userdata-layout) and [Desktop support & debug export](../guides/desktop-support-debug.md).

---

## Top-level layout

```text
data/
├── genesis/                    # Template library (seeded from genesis_templates/)
│   └── standard-v1/
│       ├── manifest.json
│       └── config/
├── agents/
│   └── {runtimeAgentId}/       # Per-agent state (primary)
├── squads/                     # Persistent squad registry (fleet)
│   ├── index.json              # agent_id → squad_id
│   └── {squad_id}.json         # inbox, escalations, checkback settings
├── skills/                     # User-installed skills (override bundled)
└── (other runtime caches)
```

---

## Per-agent directory (`data/agents/{runtimeAgentId}/`)

| Path | Purpose |
|------|---------|
| `agent_meta.json` | Name, genesis version, timestamps |
| `config/` | Brain JSON (hormones, drives, circadian, signals, …) |
| `workspace/` | Agent-accessible files (tools read/write here) |
| `memory/` | Cryptex, DomainDB, chain persistence |
| `sessions/` | Chat session logs |
| `skills/` | Per-agent skill config overrides |
| `plans/` | Active/historical plans |
| `teams/` | Orchestration state |
| `soul/` | Soul packages and snapshots |
| `guardrails_registry.jsonl` | Shared orchestrator/delegate contract hints |
| `job.json` | Owner Job charter (title, mission, scope, default profile) |
| `trust.json` | Owner Trust rails (tools, channel overlays) |

Exact filenames evolve — treat `agent_meta.json` + `config/` as required after genesis copy.

---

## Squads directory (`data/squads/`)

| Path | Purpose |
|------|---------|
| `index.json` | Maps each `runtimeAgentId` to at most one `squad_id` |
| `{squad_id}.json` | Squad name, lead, members, inbox, escalations, checkback settings |

See [Job, Trust & Squads](../guides/job-trust-and-squads.md) and [Job, Trust & Squad API](job-trust-squad-api.md).

---

## Genesis templates (`data/genesis/{version}/`)

Copied from bundled seed on first boot (`server/services/genesis_seed.py`).

Creating an agent copies template → `data/agents/{new_id}/`.

See [Genesis templates](../architecture/genesis.md).

---

## Skills directory (`data/skills/{name}/`)

Writable skill installs (ClawHub, manual). **Overrides** `nls/skills/bundled/{name}/` when names collide.

---

## Backup checklist

For disaster recovery, copy:

1. Entire `data/agents/` tree
2. `data/skills/` if using custom skills
3. Postgres (NestJS) separately — agent UUIDs and accounts

---

## Related

- [Job, Trust & Squads](../guides/job-trust-and-squads.md)
- [Persistence](../architecture/persistence.md)
- [Database schema](database-schema.md)
