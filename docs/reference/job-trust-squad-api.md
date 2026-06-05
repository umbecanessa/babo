# Job, Trust & Squad API

REST endpoints on the **Python FastAPI runtime** (`server/main.py`). Hosted web clients call the same paths through the NestJS **`/api/rt`** proxy (see [Deployment topologies](../architecture/deployment-topologies.md)).

There are **no** NestJS controllers for job, trust, or squads — do not add parallel proxies unless product requirements change.

**User guide:** [Job, Trust & Squads](../guides/job-trust-and-squads.md)

---

## Authentication

Same as other runtime routes: `X-Runtime-Secret` (relay) or `Authorization: Bearer nlsk_...` ([Python API](python-api.md)).

**Owner-only squad mutations** (desktop shell or NestJS backend — not agent API keys):

| Auth type | Source |
|-----------|--------|
| `local_trust` | Loopback TCP client (desktop app → `127.0.0.1`) |
| `shared_secret` | `X-Runtime-Secret` header |

Agent API keys (`auth_type: api_key`) may read squads they belong to and use squad **tools**, but cannot create squads, resolve pending actions, or mutate membership without identifying as the squad lead via `caller_agent_id`.

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
| `GET /api/squads` | All squads (owner auth) | Squads where caller is a member |
| `GET /api/squads/{id}` | Open read | Requires membership |
| `GET /api/squads/{id}/kanban` | — | Requires membership |
| `POST /api/squads` | **Owner dashboard only** | — |
| `PATCH` checkback / membership / name | **Owner dashboard** or **lead** (`caller_agent_id`) | Lead when caller set |
| `POST .../pending-actions/{id}/resolve` | **Owner dashboard only** | — |
| `DELETE /api/squads/{id}` | **Owner dashboard** or **lead** | Lead when caller set |

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

| Method | Path | Body / query | Auth | Response |
|--------|------|--------------|------|----------|
| GET | `/api/squads` | `?caller_agent_id=` | Any | `{ squads: Squad[] }` with `job_titles` map |
| POST | `/api/squads` | `SquadCreate` | Owner | Created squad |
| GET | `/api/squads/{squad_id}` | `?caller_agent_id=` | Any | Squad + `job_titles` |
| GET | `/api/squads/{squad_id}/kanban` | `?caller_agent_id=` (required for auth) | Member | Kanban aggregate |
| PATCH | `/api/squads/{squad_id}` | `SquadUpdate` + caller | Lead or owner | Updated squad |
| POST | `/api/squads/{squad_id}/pending-actions/{action_id}/resolve` | `PendingActionResolve` | Owner | Resolved action |
| DELETE | `/api/squads/{squad_id}` | `?caller_agent_id=` | Lead or owner | `{ deleted: squad_id }` |
| GET | `/api/squads/by-agent/{agent_id}` | — | Any | `{ squad, is_lead }` |

### `SquadCreate` / `SquadUpdate`

```json
{
  "name": "Discord moderators",
  "lead_agent_id": "agent-uuid-lead",
  "member_agent_ids": ["agent-uuid-mod", "agent-uuid-qa"]
}
```

`SquadUpdate` optional fields: `name`, `lead_agent_id`, `member_agent_ids`, `checkback_enabled`, `checkback_interval_seconds`, `proposal_sla_seconds`.

### `PendingActionResolve`

```json
{
  "approved": true,
  "resolution_note": "optional note"
}
```

Owner approves or denies lead-requested destructive actions (currently `delete_agent`). When the squad has only one member, approving delete also disbands the squad (`squad_deleted` in response).

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
| `pending_actions` | `SquadPendingAction[]` | Owner-approval queue (lead-initiated deletes) |
| `checkback_enabled` | boolean | Default `true` |
| `checkback_interval_seconds` | int | Default `1800` (30 min) |
| `proposal_sla_seconds` | int | Default `14400` (4 h) |
| `last_checkback_at` | number | Last lead checkback wake |
| `created_at` / `updated_at` | number | Timestamps |

#### `SquadPendingAction`

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | `pending_*` id |
| `action_type` | string | `delete_agent`, `patch_job`, `patch_trust` |
| `target_agent_id` | string | Agent affected on approve |
| `payload` | object | `{ job: {...} }` or `{ trust: {...} }` for patch actions |
| `requested_by` | string | Lead agent id |
| `title` / `description` | string | Owner-facing summary |
| `status` | string | `pending` \| `approved` \| `rejected` |
| `delete_squad_on_approve` | boolean | True when sole member — disband on approve |
| `created_at` / `resolved_at` | number | Timestamps |
| `resolution_note` | string | Owner note on resolve |

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

`server/routes/squads.py` and `SquadManager.apply_roster_change` sync every affected agent:

- `SquadManager.sync_agent_runtime()` → `runtime.sync_job_trust(squad=...)` + `runtime.sync_squad_tools()`
- Lead WM hooks via `SquadManager.set_hooks()` when lead runtime is loaded
- Roster add/remove dispatches `[SQUAD ROSTER UPDATE]` / leave notices to loaded agents
- **Agent load** (`AgentManager.load_agent`) calls `sync_agent_runtime(..., lookup_squad=True)` so unloaded agents pick up membership when started

---

## Dispatch sources (squad)

Registered in `nls/runtime/dispatch_sources.py`:

| Prefix | Wake target | Profile |
|--------|-------------|---------|
| `squad_checkback:{squad_id}` | Lead | `squad_lead` |
| `squad_escalation:{squad_id}` | Lead | `squad_lead` |
| `squad_item_done:{squad_id}` | Lead | `squad_lead` |
| `squad_roster:{squad_id}` | All members | Job-driven |
| `squad_roster_left:{squad_id}` | Removed member | Job-driven |
| `squad_wake:{agent_id}` | Member | Job-driven |

Enqueue path: `SquadManager._wake_lead` → consciousness scheduler inner loop (`server/main.py` lifespan hooks).

Dedup: pending dispatch queue skips new squad wakes if any `squad_*:{squad_id}` is already queued for that lead.

---

## `squad` tool actions

Defined in `nls/tools/agent_tools/squad.py`:

**Bootstrap (pre-squad):** `squad_setup(action='create')` — requires `owner_confirmed=true` after `ask_user()`.

**All members:** `inspect`, `list_inbox`, `propose`

**Lead only:** `approve`, `reject`, `assign`, `reassign`, `resolve_escalation`, `brief`, `checkback`, `pause`, `resume`, `status`, `add_member`, `remove_member`, `disband_member`, `pause_member`, `resume_member`, `spawn_member`, `set_member_job`, `set_lead_job` (owner_confirmed), `request_trust_change`, `request_delete_member`, `list_pending`, `inspect_member_config`, `configure_member`, `sync_member_channels`, `check_channel_readiness`, `invite_squad_bots`

Triage may emit hint `fleet:squad_candidate` when the owner describes a multi-agent fleet; the loop injects a bootstrap nudge.

### Lead fleet management (tool)

| Action | Description |
|--------|-------------|
| `squad_setup` / `create` | Create squad with self as lead (owner_confirmed) |
| `add_member` | Add existing agent to squad |
| `remove_member` / `disband_member` | Remove member (promotes new lead if lead removed) |
| `set_member_job` | Lead updates member job charter directly |
| `set_lead_job` | Lead updates own job with owner_confirmed after ask_user |
| `request_trust_change` | Queue trust patch — owner approves on dashboard |
| `pause_member` / `resume_member` | Pause/resume member consciousness loop |
| `spawn_member` | Create agent (`genesis_version` optional), set job, add to squad, brief |
| `inspect_member_config` | Lead inspects member skill config (schema-aware, secrets masked) |
| `configure_member` | Lead applies skill config on member (`skill_name` or `channel`, `skill_config`, `owner_confirmed=true` for secrets); wires Discord gateway when `bot_token` saved |
| `sync_member_channels` | `{ target_agent_id, channel }` — sync Discord/Slack scope for a member; mirrors lead scope when member has none |
| `check_channel_readiness` | `{ channel_id }` — per-bot guild visibility, send permission, Babo scope for lead + all members |
| `invite_squad_bots` | `{ channel_id }` — OAuth invite URLs for squad bots not in the guild (lead bot needs Manage Channels to grant access) |
| `request_delete_member` | Queue owner-approved delete; sole member disbands squad on approve |
| `list_pending` | List pending owner actions |

### `channel_manage` tool

Defined in `nls/tools/agent_tools/channel_manage.py`; dispatch in `nls/runtime/channel_manage.py`.

| Param | Description |
|-------|-------------|
| `channel` | Channel key: `discord`, `slack`, `telegram`, … |
| `action` | Run `channel_manage(channel='discord')` with no action for help text |
| `channel_id` | Guild/channel snowflake when action requires it |
| `config` | Partial skill config for `configure` actions |

Bundled adapters implement `manage_channel` on the skill adapter. Custom skills may call `register_channel_manage_handler()` at register time.

**Deprecated:** per-channel `discord_manage` — use `channel_manage(channel='discord', ...)`.

---

## Frontend (`ApiService`)

| Method | Runtime path |
|--------|----------------|
| `getJob` / `patchJob` | `/agents/{id}/job` |
| `getTrust` / `patchTrust` | `/agents/{id}/trust` |
| `listSquads` | `/api/squads` |
| `getSquadKanban` | `/api/squads/{id}/kanban` |
| `createSquad` / `updateSquad` / `deleteSquad` | `/api/squads` |
| `resolveSquadPendingAction` | `POST /api/squads/{id}/pending-actions/{actionId}/resolve` |

Components: `squads-panel`, `agent-charter-modal`, agent card job/squad labels.

---

## Related

- [Python HTTP & WebSocket API](python-api.md)
- [Teams & projects API](teams-api.md) — separate from squads
- [Data directory](data-directory.md)
- [Glossary](glossary.md)
