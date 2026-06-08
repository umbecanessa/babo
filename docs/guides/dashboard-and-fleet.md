# Dashboard & fleet

The **Dashboard** is home for your agent fleet — status, squads, and quick actions.

**Route:** `/dashboard` · **Nav label:** Agents

---

## Layout

| Area | Purpose |
|------|---------|
| **Stats bar** | Total, Active, Paused, Sleeping counts |
| **Agent grid** | Unassigned agents as cards |
| **Squads panel** | Persistent multi-agent groups |
| **Footer** (desktop) | Version and early-access chips |

When every agent belongs to a squad, a message replaces the empty grid.

---

## Agent cards

Each card shows:

- **Online status** — runtime connected (desktop) or relay reachable (web)
- **Name** and quick open to Chat
- **Pause** — stops autonomous background work
- **Delete** — removes agent and data (confirm dialog)
- **Charter** — opens Job/Trust modal for owner charter and tool rails

On desktop, the dashboard waits up to ~3 minutes for the Python runtime on first load after boot.

---

## Squads panel

**Squads** are persistent groups of full agents (not the same as Projects **teams** / delegate waves).

| Action | Effect |
|--------|--------|
| **Create squad** | Name + member selection |
| **Squad board modal** | Kanban: inbox, escalations, member todos |
| **Checkback settings** | Configure squad lead check-in intervals |
| **Open agent** | Jump to member chat |

Squads use shared inbox, `squad` tools, and `squad_lead` profile on the lead agent. Full guide: [Job, Trust & Squads](job-trust-and-squads.md).

---

## Job & Trust charter modal

From any agent card, **Charter** edits:

- Owner job description (`job.json`)
- Trust rails (`trust.json`) — which tools need approval
- Background execution preferences

Changes apply on next loop entry.

---

## Creating agents

**New Agent** → `/create` genesis wizard. See [Creating agents](creating-agents.md).

---

## Relay status (web)

Web users see **offline** cards when no desktop runtime is connected for that agent. Chat history may load from Postgres; live tool execution requires relay. See [Remote mode & relay](remote-mode-and-relay.md).

---

## Related

- [Creating agents](creating-agents.md)
- [Job, Trust & Squads](job-trust-and-squads.md)
- [Projects & teams](projects-and-teams.md) — delegate waves inside one agent
- [Remote mode & relay](remote-mode-and-relay.md)
