# Job, Trust & Squads

Babo separates **who an agent is at work** (Job), **what they may do** (Trust), and **what someone asked for right now** (Task). For agents on public channels, Job and Trust stay stable and owner-controlled so casual users cannot reshape identity or permissions through chat.

**Squads** are a **dashboard fleet** feature: a persistent group of full agents with a **lead**, shared inbox, and cross-member todos. They are **not** the same as **Teams** in Projects (ephemeral sub-agents inside one parent loop).

**API surface:** Python FastAPI runtime only (`server/routes/job_trust.py`, `server/routes/squads.py`). The hosted cloud browser reaches these routes through the existing **`/api/rt` runtime proxy** — there are **no** dedicated NestJS routes for job, trust, or squads.

**Design history:** [Brainstorm: Job, Trust, Task & Squads](../brainstorm/job-trust-task-squads.md) · **API reference:** [Job, Trust & Squad API](../reference/job-trust-squad-api.md)

---

## Job vs Trust vs Task

| Layer | Owner? | Lifetime | Purpose |
|-------|--------|----------|---------|
| **Job** | Yes (UI / REST) | Until owner PATCH | Employment charter: title, mission, persona, scope, refusal voice |
| **Trust** | Yes | Until owner PATCH | Tool allow/deny, action classes, per-channel caps |
| **Task** | No (turn/plan) | Ephemeral | Current user message, plan step, channel dispatch |

```mermaid
flowchart TB
  Soul[Soul / genesis]
  Job[Job — job.json]
  Trust[Trust — trust.json]
  Task[Task — goals / plan / todo]
  Loop[Agentic loop]

  Soul --> Job
  Job --> Trust
  Trust --> Task
  Task --> Loop
```

**Rule:** Tasks must not override Job. A hostile channel message may produce tactical goals; Trust blocks tool execution; Job supplies the refusal wording.

---

## Job (employment charter)

### What it controls

- **Title** — shown on the agent dashboard card (default: *General helpful assistant*).
- **Mission, persona, playbook** — injected into Cryptex identity and instruction rings.
- **In scope / out of scope** — boundary lists for reasoning and public-channel evaluation.
- **Refusal template** — short reply pattern for out-of-scope or policy-blocked requests.
- **Default orchestration profile** — e.g. `conversational`, `solo_structured`, `orchestrated`, `squad_lead`.

### Persistence

`data/agents/{runtimeAgentId}/job.json`

On load and after owner PATCH, `AgentRuntime.sync_job_trust()` copies Job (and Trust) into Cryptex **`ACCESS_SYSTEM`** slots so **task-epoch hygiene** does not clear them.

### Editing in the UI

On the **Dashboard** → **Squads** panel (or agent cards in squads):

- **Job** — opens the charter modal **Job** tab.
- **Trust** — opens the **Trust** tab.

Each tab has its own **Save** button (job and trust are patched independently).

### Cryptex domains (summary)

| Job content | Typical Cryptex domain |
|-------------|------------------------|
| Title, mission | `Job.Title`, `Job.Mission` |
| Persona | `Job.Persona` |
| Playbook | `Job.Playbook.*` |
| Boundaries | `RING_BEHAVIORAL` via trust sync |
| Strategic priorities | `Goal.Strategic.Job.*` |

---

## Trust (action rails)

### Global tool policy

- **`tools_allow`** — if non-empty, only listed tools are permitted (except explicit denies).
- **`tools_deny`** — hard deny before execution (logged).
- **`action_classes_allow` / `action_classes_deny`** — reserved for action-class matrices (v1 primarily uses tool lists).

Enforcement runs in the agentic **executor** via `is_tool_denied_by_trust()`.

### Channel overlays

Per **`channel_key`** (matched against `dispatch_source`, e.g. `user:channel:discord-general`):

| Field | Effect |
|-------|--------|
| `profile_cap` | Ceiling orchestration profile (e.g. `conversational` on public Discord) |
| `tools_allow` / `tools_deny` | Extra allow/deny on that channel |
| `public_channel` | Stricter out-of-scope evaluation using Job refusal template |

**Public channel guard:** before the loop runs tools, `evaluate_public_channel_request()` may force a **conversational** profile and refusal goals when the message matches `out_of_scope` patterns on a public overlay.

### Editing overlays

Trust tab → **Channel overlays** → add channel key, profile cap, tools allow/deny, **Public channel** checkbox.

---

## Squads (persistent fleet)

### Squad vs Team

| | **Team** (Projects) | **Squad** (Dashboard) |
|--|---------------------|------------------------|
| Lifetime | Ephemeral wave in one agent’s loop | Persistent until deleted |
| Members | Sub-agent delegates | Full agents (separate runtimes, memory, Job) |
| UI | `/projects/:agentId` board & timeline | Dashboard **Squads** panel |
| Coordination | `team` tool, `TeamManager` | `squad` tool, `SquadManager` |

Squad membership **adds** coordination tools; it does **not** remove `plan`, `team`, `delegate`, or other tools. Job and Trust still gate each member.

### Creating a squad

**Dashboard** → **Squads** → **Create squad**:

1. Name the squad.
2. Pick a **lead** agent.
3. Select **members** (lead is always included).

Persistence: `data/squads/{squad_id}.json` plus `data/squads/index.json` (agent → squad mapping). **One squad per agent** (enforced by registry).

### Visibility

| Caller | List squads | Squad detail / Kanban | Mutate settings / delete |
|--------|-------------|----------------------|---------------------------|
| No `caller_agent_id` | All squads (admin-style) | Member check if caller provided | Lead required when caller set |
| Squad member | Only their squads | Yes | — |
| Squad lead | Yes | Yes | Checkback settings, delete squad |

Pass identity as query **`caller_agent_id`** or header **`X-Babo-Agent-Id`**. The dashboard uses the squad **lead** id for lead-only operations.

### Two-tier work: inbox → approve → member todo

```mermaid
flowchart LR
  subgraph inbox [Squad inbox]
    P[proposed]
    A[approved]
    R[rejected]
  end
  subgraph member [Member todo store]
    Q[queued / in_progress / done]
  end

  Member -->|squad propose| P
  Lead -->|squad approve| A
  A --> Q
  Q -->|squad_report_done| Lead
```

1. **Propose** — any member (or lead) adds an inbox item (`proposed`).
2. **Approve / reject** — lead only; approve creates a **todo-list** item on the assignee with `squad_id`, `squad_inbox_id`, `idle_eligible`.
3. **Assign / reassign** — lead can assign directly or move work between members.
4. **Done** — member uses `squad_report_done`; lead may get `squad_item_done:{squad_id}` dispatch.

Inbox and escalations live on the squad JSON record; member todos stay in each agent’s todo-list store (no shared file — avoids cross-process write races).

### Squad tools (runtime)

**Bootstrap (not yet in a squad):** agents get `squad_setup` (`action='create'`) after triage detects `fleet:squad_candidate`. Flow: propose structure → `ask_user()` → `squad_setup(action='create', owner_confirmed=true)` → `adopt_orchestration_profile(profile='squad_lead')` → `spawn_member` / `set_member_job`.

Registered on agents that belong to a squad (`AgentRuntime.sync_squad_tools()`):

| Tool | Typical caller | Purpose |
|------|----------------|---------|
| `squad_setup` | Solo agent (pre-squad) | **`create`** squad with self as lead (requires `owner_confirmed` after `ask_user`) |
| `squad` | Lead (full); members (read/propose) | inbox, fleet ops, member config, Discord readiness, … |
| `channel_manage` | Any agent with skill enabled | Channel admin: sync scope, inspect config, grant access |
| `channel_inspect` | Lead (optional `target_agent_id`) | Read-only skill/channel status for self or member |
| `squad_escalate` | Members | Wake lead with open escalation |
| `squad_message` | Any member | Internal peer message (optional wake) |
| `squad_report_done` | Members | Complete approved squad todo → notify lead |

**Lead fleet management**

- **`spawn_member`** — creates a new agent from default genesis, sets Job title/mission, adds to squad, and `brief`s them (target spawn flow: owner talks to lead, lead builds the team).
- **`set_member_job`** — lead updates a **member's** job charter directly (title, mission, persona, playbook).
- **`set_lead_job`** — lead updates **own** job only with `owner_confirmed=true` after `ask_user()`.
- **`request_trust_change`** — queues trust patch for **any** squad member (including lead); **owner approves/denies** on dashboard.
- **`add_member` / `remove_member`** — roster changes push `[SQUAD ROSTER UPDATE]` dispatches and refresh Cryptex membership on all affected runtimes.
- **`pause_member` / `resume_member`** — per-agent consciousness pause (lead only).
- **`request_delete_member`** — queues a pending action; **owner approves/denies** on the dashboard squad card before the agent is deleted.
- **`inspect_member_config` / `configure_member`** — lead reads or writes a member's bundled skill config (schema-driven; `owner_confirmed=true` for secrets).
- **`sync_member_channels`** — lead triggers Discord/Slack scope sync for a member (mirrors lead channels when member scope is empty).
- **`check_channel_readiness` / `invite_squad_bots`** — audit and fix multi-bot guild channel access before cross-bot @mention tests.

### Dashboard squad UI

- Unassigned agents appear as standalone cards; squad members appear only inside their squad card as **expandable rows** (same vitals/activity as agent cards).
- Collapsed row: status, name, job, Pause/Delete, remove-from-squad, and **chat** icon.
- **Add agent** dropdown on each squad card adds unassigned agents.
- Pending delete/trust requests from the lead show **Approve / Deny** buttons for the owner.

### `squad_lead` orchestration profile

Fourth depth alongside `conversational`, `solo_structured`, and `orchestrated`:

- **Default** for `squad.lead_agent_id` (also settable via Job `default_profile`).
- **Forced** on squad orchestration dispatches: `squad_checkback:*`, `squad_escalation:*`, `squad_item_done:*`.
- Extends orchestrated behavior with squad behavioral domains and stricter completion on squad wakes (must resolve inbox/escalations, not stop at prose).

Members use normal triage profiles unless channel Trust caps them.

### Lead checkback (scheduler)

`SquadCheckbackScheduler` ticks every 60s and may wake the lead when:

- Checkback **interval** elapsed (default 30 minutes, minimum 5 minutes between any wakes), or
- **Pending inbox proposals** exist, or
- **Proposal SLA** exceeded (default 4 hours), or
- **Open escalations** exist.

Configure per squad: **Dashboard** → squad card → **Checkback** (enable, interval minutes, proposal SLA hours).

Duplicate wakes are suppressed if the lead already has a pending `squad_*:{squad_id}` dispatch.

### Aggregated squad board (UI)

**Board** on a squad card opens a modal:

- Inbox columns (proposed / approved)
- Open escalations
- Per-member squad-scoped todos

Backed by `GET /api/squads/{squad_id}/kanban?caller_agent_id=...`.

### Owner channel pattern

Typical fleet layout:

| Channel | Agent |
|---------|--------|
| Public Discord `#general` | Community moderator |
| `#bugs` | QA agent |
| Staff / audit | Channel admin (often **squad lead**) |
| Owner WhatsApp / Telegram | **Lead only** |

The lead coordinates via squad tools; members escalate to the lead, not the owner’s private channel unless Trust allows.

### Discord multi-face squads

Each squad member is a **separate Discord bot application** (separate token, separate snowflake). The lead bot and member bots are not the same identity.

| Role | Discord identity | Typical use |
|------|------------------|-------------|
| Lead | Lead bot token | Owner-facing coordination, `squad` tools, shared inbox |
| Member | Member bot token | Domain work in scoped guild channels |

**Per-member setup:** create a Discord app → connect in Babo (Tools → Discord) for that **member agent** → enable `discord-channel` → sync channel scope → invite the bot to the guild.

**Lead-driven member channel setup** (member loop not required):

```python
squad(action='inspect_member_config', target_agent_id='member-uuid')
squad(action='configure_member', target_agent_id='member-uuid',
      skill='discord-channel', config={'enabled': True, 'require_mention': True})
squad(action='sync_member_channels', target_agent_id='member-uuid', channel='discord')
```

**Guild readiness before cross-bot @mentions:**

```python
squad(action='check_channel_readiness', channel_id='123456789012345678')
squad(action='invite_squad_bots', channel_id='123456789012345678')
```

Readiness checks each connected squad bot: guild membership, channel visibility, send permission, and Babo scope. `invite_squad_bots` returns OAuth invite URLs when bots are missing from the guild.

**Multi-face testing:** the lead sends as **itself** and @mentions member snowflakes from `squad(action='inspect')`:

```python
discord_send(channel_id='...', text='<@MOD_BOT_ID> please ack')
```

Use `channel_manage(channel='discord', ...)` for Discord admin — **never** paste bot tokens into bash or Python.

| Tool | Purpose |
|------|---------|
| `channel_manage` | Unified channel admin (`help`, `inspect`, `sync`, `configure`, …) |
| `channel_inspect` | Read-only channel/skill status (lead may pass `target_agent_id`) |

---

## Example: three-agent Discord fleet

See the role matrix in the [brainstorm doc](../brainstorm/job-trust-task-squads.md#reference-roles-discord--telegram-fleet). In production:

1. Create three agents with distinct **Job** charters (admin, moderator, QA).
2. Tighten **Trust** per channel (public caps on `#general`).
3. **Create squad** with admin as lead, mod + QA as members.
4. Bind channels to the right agents in **Tools / integrations**.
5. Enable **checkback** on the squad so the lead periodically reviews inbox and member health.

---

## Cloud browser vs desktop

| Client | Job / Trust / Squad API |
|--------|-------------------------|
| **Electron desktop** | `ApiService` → `runtimeUrl` (e.g. `http://127.0.0.1:9222`) |
| **Hosted web** | `ApiService` → `/api/rt` proxy to the user’s paired Python runtime |

NestJS continues to handle auth, agent metadata, and channel relay. Job, trust, and squad documents live on the **runtime data disk** next to agent memory.

---

## Code map

| Area | Path |
|------|------|
| Job / Trust models & Cryptex sync | `nls/runtime/job_trust.py` |
| Profile resolution (squad lead) | `nls/runtime/agent_profile.py` |
| Public channel guard | `nls/runtime/public_channel_guard.py` |
| Squad registry / manager | `nls/agentic/squad_registry.py`, `squad_manager.py` |
| Checkback scheduler | `nls/agentic/squad_checkback_scheduler.py` |
| Squad tools | `nls/tools/agent_tools/squad.py` |
| Channel admin | `nls/tools/agent_tools/channel_manage.py`, `nls/runtime/channel_manage.py` |
| Discord squad readiness | `nls/runtime/discord_squad_readiness.py` |
| Member skill config | `nls/runtime/skill_config_service.py` |
| Profile spec | `nls/agentic/orchestration_profile_spec.py` (`squad_lead`) |
| Depth nudges | `nls/agentic/profile_depth_policy.py` |
| REST | `server/routes/job_trust.py`, `squads.py`, `squad_access.py` |
| UI | `frontend/.../squads-panel/`, `agent-charter-modal/` |

---

## Related

- [Projects & teams](projects-and-teams.md) — ephemeral Teams and Kanban (single agent)
- [Agentic loop & plans](agentic-loop-and-plans.md)
- [Orchestration & delegation](../architecture/orchestration-and-delegation.md)
- [Memory](memory.md) — Cryptex rings and ACCESS_SYSTEM
- [Data directory](../reference/data-directory.md)
- [Integrations](integrations/index.md) — channel binding
