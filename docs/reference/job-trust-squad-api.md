# Job, Trust & Squad API

REST endpoints on the **Python FastAPI runtime** (`server/main.py`). Hosted web clients call the same paths through the NestJS **`/api/rt`** proxy (see [Deployment topologies](../architecture/deployment-topologies.md)).

There are **no** NestJS controllers for job, trust, or squads — do not add parallel proxies unless product requirements change.

**User guide:** [Job, Trust & Squads](../guides/job-trust-and-squads.md)

---

## Authentication

Same as other runtime routes: `X-Runtime-Secret` (relay) or `Authorization: Bearer nlsk_...` ([Python API](python-api.md)).

---

## Squad caller identity

Squad routes accept optional visibility and authorization via:

| Mechanism | Parameter |
|-----------|-----------|
| Query | `caller_agent_id` |
| Header | `X-Babo-Agent-Id` |

Implementation: `server/routes/squad_access.py`.

| Endpoint behavior | Without caller | With caller |
|-------------------|----------------|-------------|
| `GET /api/squads` | All squads | Squads where caller is a member |
| `GET /api/squads/{id}` | Open read | Requires membership |
| `GET /api/squads/{id}/kanban` | — | Requires membership |
| `PATCH` checkback fields | — | Requires **lead** |
| `PATCH` name / members / lead | — | Requires **lead** when caller set |
| `DELETE /api/squads/{id}` | — | Requires **lead** |

---

## Job API

Prefix: `/agents/{agent_id}/job`

| Method | Path | Body | Notes |
|--------|------|------|-------|
| GET | `/agents/{agent_id}/job` | — | Returns `JobDocument` JSON |
| PATCH | `/agents/{agent_id}/job` | Partial fields | Saves `job.json`, syncs Cryptex if runtime loaded |

### `job.json` schema (v1)

| Field | Type | Description |
|-------|------|-------------|
| `version` | string | `"1.0"` |
| `title` | string | Display title (default: General helpful assistant) |
| `mission` | string | Core mission statement |
| `persona` | string | Voice / tone |
| `playbook` | string | Operating procedures |
| `in_scope` | string[] | Allowed work themes |
| `out_of_scope` | string[] | Refusal triggers (also used on public channels) |
| `refusal_template` | string | Short refusal wording |
| `refusal_examples` | string[] | Optional few-shot examples |
| `escalation_paths` | string[] | Who to escalate to |
| `default_profile` | string | `conversational` \| `solo_structured` \| `orchestrated` \| `squad_lead` |
| `strategic_priorities` | string[] | Long-lived priorities |
| `updated_at` | number | Unix timestamp |

File: `data/agents/{agent_id}/job.json`

---

## Trust API

Prefix: `/agents/{agent_id}/trust`

| Method | Path | Body | Notes |
|--------|------|------|-------|
| GET | `/agents/{agent_id}/trust` | — | Returns `TrustDocument` JSON |
| PATCH | `/agents/{agent_id}/trust` | Partial fields | Saves `trust.json`, syncs Cryptex |

### `trust.json` schema (v1)

| Field | Type | Description |
|-------|------|-------------|
| `version` | string | `"1.0"` |
| `tools_allow` | string[] | Allowlist (empty = no allowlist restriction) |
| `tools_deny` | string[] | Denied tool names |
| `action_classes_allow` | string[] | Reserved |
| `action_classes_deny` | string[] | Reserved |
| `channel_overlays` | object[] | Per-channel caps (below) |
| `updated_at` | number | Unix timestamp |

#### `channel_overlays[]` entry

| Field | Type | Description |
|-------|------|-------------|
| `channel_key` | string | Matches channel segment in `dispatch_source` |
| `profile_cap` | string | Max orchestration profile on that channel |
| `tools_allow` | string[] | Channel-specific allowlist |
| `tools_deny` | string[] | Channel-specific denylist |
| `public_channel` | boolean | Enable stricter refusal evaluation |

File: `data/agents/{agent_id}/trust.json`

---

## Squad API

Prefix: `/api/squads`

| Method | Path | Body / query | Response |
|--------|------|--------------|----------|
| GET | `/api/squads` | `?caller_agent_id=` | `{ squads: Squad[] }` with `job_titles` map |
| POST | `/api/squads` | `SquadCreate` | Created squad |
| GET | `/api/squads/{squad_id}` | `?caller_agent_id=` | Squad + `job_titles` |
| GET | `/api/squads/{squad_id}/kanban` | `?caller_agent_id=` (required for auth) | Kanban aggregate |
| PATCH | `/api/squads/{squad_id}` | `SquadUpdate` + caller | Updated squad |
| DELETE | `/api/squads/{squad_id}` | `?caller_agent_id=` | `{ deleted: squad_id }` |
| GET | `/api/squads/by-agent/{agent_id}` | — | `{ squad, is_lead }` |

### `SquadCreate` / `SquadUpdate`

```json
{
  "name": "Discord moderators",
  "lead_agent_id": "agent-uuid-lead",
  "member_agent_ids": ["agent-uuid-mod", "agent-uuid-qa"]
}
```

`SquadUpdate` optional fields: `name`, `lead_agent_id`, `member_agent_ids`, `checkback_enabled`, `checkback_interval_seconds`, `proposal_sla_seconds`.

### Squad record (`data/squads/{squad_id}.json`)

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | `squad_*` id |
| `name` | string | Display name |
| `lead_agent_id` | string | Squad lead runtime agent id |
| `member_agent_ids` | string[] | Members (lead deduped into list) |
| `paused` | boolean | Squad freeze |
| `inbox` | `SquadInboxItem[]` | Shared inbox |
| `escalations` | `SquadEscalation[]` | Member → lead escalations |
| `checkback_enabled` | boolean | Default `true` |
| `checkback_interval_seconds` | int | Default `1800` (30 min) |
| `proposal_sla_seconds` | int | Default `14400` (4 h) |
| `last_checkback_at` | number | Last lead checkback wake |
| `created_at` / `updated_at` | number | Timestamps |

Index: `data/squads/index.json` maps `agent_id → squad_id`.

### Kanban response (`GET .../kanban`)

```json
{
  "squad_id": "squad_abc123",
  "inbox": {
    "proposed": [],
    "approved": [],
    "rejected": []
  },
  "member_todos": {
    "agent-id": [{ "id": "...", "title": "...", "status": "in_progress", "squad_id": "squad_abc123" }]
  },
  "open_escalations": [{ "member_agent_id": "...", "reason": "...", "status": "open" }],
  "job_titles": { "agent-id": "Community Moderator" }
}
```

### Side effects on squad CRUD

`server/routes/squads.py` calls `_sync_agent_squad` per affected agent:

- `runtime.sync_job_trust(squad=...)`
- `runtime.sync_squad_tools()` — add/remove squad tools
- `SquadManager.set_hooks()` for lead when lead runtime has active agentic hooks

---

## Dispatch sources (squad)

Registered in `nls/runtime/dispatch_sources.py`:

| Prefix | Wake target | Profile |
|--------|-------------|---------|
| `squad_checkback:{squad_id}` | Lead | `squad_lead` |
| `squad_escalation:{squad_id}` | Lead | `squad_lead` |
| `squad_item_done:{squad_id}` | Lead | `squad_lead` |
| `squad_wake:{agent_id}` | Member | Job-driven |

Enqueue path: `SquadManager._wake_lead` → consciousness scheduler inner loop (`server/main.py` lifespan hooks).

Dedup: pending dispatch queue skips new squad wakes if any `squad_*:{squad_id}` is already queued for that lead.

---

## `squad` tool actions

Enum on `squad(action=...)` (`nls/tools/agent_tools/squad.py`):

`inspect`, `list_inbox`, `propose`, `approve`, `reject`, `assign`, `reassign`, `resolve_escalation`, `brief`, `checkback`, `pause`, `resume`, `status`

Lead-only: `approve`, `reject`, `assign`, `reassign`, `resolve_escalation`, `brief`, `checkback`, `pause`, `resume` (enforced in `SquadManager`).

---

## Frontend (`ApiService`)

| Method | Runtime path |
|--------|----------------|
| `getJob` / `patchJob` | `/agents/{id}/job` |
| `getTrust` / `patchTrust` | `/agents/{id}/trust` |
| `listSquads` | `/api/squads` |
| `getSquadKanban` | `/api/squads/{id}/kanban` |
| `createSquad` / `updateSquad` / `deleteSquad` | `/api/squads` |

Components: `squads-panel`, `agent-charter-modal`, agent card job/squad labels.

---

## Related

- [Python HTTP & WebSocket API](python-api.md)
- [Teams & projects API](teams-api.md) — separate from squads
- [Data directory](data-directory.md)
- [Glossary](glossary.md)
