# Projects & teams

The **Projects** workspace is where Babo manages multi-step work with boards, timelines, and sub-agent **teams**.

**Route:** `/projects/:agentId` (also `/tasks/:agentId`)

> **Not squads:** **Teams** here are ephemeral delegate waves inside **one** agent’s loop. **Squads** are persistent multi-agent groups on the **Dashboard** (shared inbox, squad lead, `squad` tools). See [Job, Trust & Squads](job-trust-and-squads.md).

---

## Views

### Overview

Split layout:

- **Teams panel** — active and completed delegation waves
- **Activity panel** — recent plan steps, todo changes, team events

The **orchestration ribbon** at the top shows:

- Active team count
- Members done / total
- Failed members (if any)
- Overall plan progress %

### Board

Kanban-style **task board** with lists:

- Inbox
- Projects
- Research
- Creative
- Custom lists you define

Drag cards between columns. Tasks link to plans when the agent creates them. WebSocket updates refresh the board in real time.

### Timeline

**Wave-based timeline** of team execution:

- Each **wave** corresponds to a plan delegation round
- States: queued, running, completed, failed
- Elapsed time and per-member progress bars

Use timeline to see what sub-agents are doing without reading raw logs.

### Files

Project **workspace files** and artifacts the agent created or referenced.

---

## Teams panel details

Each wave card shows:

- Linked plan steps
- Team status (`running`, `completed`, `failed`, `partial`)
- Member list with individual progress
- **Hint** action — send guidance to a running delegate

Teams persist across sleep cycles (`teams/team_{id}.json` on disk).

---

## Command bar

The header **command bar** sends natural-language instructions to the agent in project context — e.g. "Move the API task to in progress and delegate the frontend step."

Commands route through the same agentic loop with project-scoped Cryptex rings active.

---

## Chat sidebar

Toggle the **chat sidebar** to talk to the agent while viewing the board or timeline. Useful for steering orchestration without leaving Projects.

---

## Typical workflow

1. Ask in chat: *"Create a plan to launch the marketing site"*
2. Agent creates plan + todo cards appear on the board
3. Delegatable steps spawn **teams** — visible on Overview and Timeline
4. Monitor progress; send hints if a sub-agent stalls
5. Completed steps mark todos done; plan progress hits 100%

---

## Todo ↔ idle execution

The todo-list skill connects board items to **idle-mode execution**: when you're away, the agent can pick up intention slots and work on queued tasks via the default mode network (daydream pipeline), subject to drive and schedule gates.

---

## Related

- [Job, Trust & Squads](job-trust-and-squads.md) — persistent fleet (vs Teams here)
- [Agentic loop & plans](agentic-loop-and-plans.md)
- [Chat](chat.md)
- [Memory](memory.md)
