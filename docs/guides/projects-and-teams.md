# Projects & teams

The **Projects** workspace is where Babo manages multi-step work with boards, delegation waves, and sub-agent **teams**.

**Route:** `/projects/:agentId` (alias `/tasks/:agentId`)

> **Not squads:** **Teams** here are ephemeral delegate waves inside **one** agent's loop. **Squads** are persistent multi-agent groups on the **Dashboard**. See [Job, Trust & Squads](job-trust-and-squads.md).

---

## Tabs

Projects has three top-level tabs (query param `?tab=`):

| Tab | Purpose |
|-----|---------|
| **Overview** | Teams panel, activity feed, orchestration ribbon |
| **Board** | Kanban task board |
| **Files** | Workspace IDE — explorer + editor for project artifacts |

There is no separate Timeline tab — wave execution history lives in the **Teams panel** on Overview.

---

## Overview

Split layout:

- **Teams panel** — active, completed, and planned delegation **waves** with per-member progress
- **Activity panel** — recent plan steps, todo changes, team events

The **orchestration ribbon** at the top shows:

- Active team count
- Members done / total
- Failed members (if any)
- Overall plan progress %

### Wave timeline (Teams panel)

Each wave card shows:

| State | Meaning |
|-------|---------|
| **awaiting_launch** | Created but not yet launched — orchestrator must `team(launch)` |
| **running** | Delegates executing |
| **completed** / **failed** / **partial** | Wave finished |

Expand a running member tile to see iterations, tools used, and hint controls.

---

## Board

Kanban-style **task board** with **status columns** (not topic lists):

| Column | Typical use |
|--------|-------------|
| **Inbox** | New or un triaged items |
| **Queued** | Accepted, waiting to start |
| **In Progress** | Active work |
| **Done** | Completed (collapsible) |
| **Deferred** | Paused or blocked |

Drag cards between columns. Tasks link to plans when the agent creates them. WebSocket updates refresh the board in real time.

**Todo lists** (Research, Creative, Projects, etc.) exist in the todo-list skill as **`list_id` metadata** on cards — they are not separate board columns. The agent can filter or assign list labels via the todo tool; the UI columns always reflect **status**.

---

## Files

Project **workspace** with file explorer and CodeMirror editor. The agent reads/writes here via file tools; you can inspect and edit artifacts directly. Former standalone IDE tab — now folded into Projects.

---

## Teams panel — hints & ack

Each wave card shows linked plan steps, team status, and members.

**Send Hint:** Expand a running member → type guidance → Enter. Hints use default delivery **`both`** — written to the delegate SubCryptex ring **and** injected as `[ORCHESTRATOR HINT]` in the delegate chat loop.

**Last response:** After the delegate acknowledges, the expanded tile shows **Last response:** with a snippet of the delegate's next prose (up to ~150 chars). This confirms the hint was received.

Orchestrator-side recovery when waves stall: [Orchestration & delegation](../architecture/orchestration-and-delegation.md#wave-selection-and-create-guards).

Panel actions also include pause, resume, disband, skip, and force-start where policy allows.

Teams persist across sleep cycles (`teams/team_{id}.json` on disk).

---

## Command bar

The header **command bar** sends natural-language instructions to the agent in project context — e.g. "Move the API task to in progress and delegate the frontend step."

Commands route through the same agentic loop with project-scoped Cryptex rings active.

---

## Chat sidebar

Toggle the **chat sidebar** to talk to the agent while viewing the board or files.

**Same transcript as Chat:** the sidebar shows the shared **Home** thread — messages, tool traces, attachments, mid-loop prose, and streaming state match `/chat/:agentId`.

Channel threads in the sidebar are **per-agent** only.

---

## Typical workflow

1. Ask in chat: *"Create a plan to launch the marketing site"*
2. Agent creates plan + todo cards appear on the board
3. Delegatable steps spawn **teams** — visible on Overview → Teams panel
4. Monitor wave progress; send hints if a sub-agent stalls; watch **Last response** for ack
5. Completed steps mark todos done; plan progress hits 100%

---

## Todo ↔ idle execution

The todo-list skill connects board items to **idle-mode execution**: when you're away, the agent can pick up intention slots and work on queued tasks via the default mode network (daydream pipeline), subject to drive and schedule gates.

---

## Related

- [Job, Trust & Squads](job-trust-and-squads.md)
- [Agentic loop & plans](agentic-loop-and-plans.md)
- [Chat](chat.md)
- [Orchestration & delegation](../architecture/orchestration-and-delegation.md)
