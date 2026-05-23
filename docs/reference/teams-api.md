# Teams & projects API

REST routes for **sub-agent teams**, plan waves, timeline, and orchestrator commands.

**Router:** `server/routes/teams.py`  
**Prefix:** `/api/agents`

Requires runtime auth (`X-Runtime-Secret` or `nlsk_` bearer). Used by Projects UI via `ApiService` (direct or `/api/rt` proxy).

---

## Teams

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{agent_id}/teams` | List teams |
| POST | `/{agent_id}/teams` | Create team (`plan_id`, `wave`, `name`, `mission`, `briefing`) |
| GET | `/{agent_id}/teams/{team_id}` | Team detail + members |
| POST | `/{agent_id}/teams/{team_id}/advance` | Advance wave / state machine |
| POST | `/{agent_id}/teams/{team_id}/pause` | Pause team |
| POST | `/{agent_id}/teams/{team_id}/resume` | Resume |
| POST | `/{agent_id}/teams/{team_id}/disband` | Tear down |
| POST | `/{agent_id}/teams/{team_id}/brief` | Update briefing (`content`) |
| POST | `/{agent_id}/teams/{team_id}/skip` | Skip current step |
| POST | `/{agent_id}/teams/{team_id}/members/{member_idx}/hint` | User hint to delegate |

---

## Projects / timeline

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{agent_id}/projects/{plan_id}/timeline` | Wave timeline for plan |
| POST | `/{agent_id}/projects/{plan_id}/force-start/{wave_index}` | Force-start a wave |

---

## Orchestrator command

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/{agent_id}/command` | Natural-language command to orchestrator (`message`, optional `context`) |

Used by Projects command bar.

---

## Implementation notes

- Resolves `TeamManager` from loaded `AgentRuntime` (`_team_manager` or `team` tool)
- Agent must be **loaded** in `AgentManager` — 404 if evicted

---

## Related

- [Agentic loop](../architecture/agentic-loop.md)
- [Projects guide](../guides/projects-and-teams.md)
- [Python API](python-api.md)
