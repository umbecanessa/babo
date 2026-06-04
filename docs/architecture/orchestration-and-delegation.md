# Orchestration & delegation

How Babo runs **multi-step projects** with an orchestrator agent, **team waves**, and isolated **delegate loops**.

**Code roots:** `nls/agentic/` · **User guide:** [Agentic loop & plans](../guides/agentic-loop-and-plans.md)

---

## Roles

| Role | Who | Does | Must not |
|------|-----|------|----------|
| **Orchestrator** | Main agent loop (`coordinator_mode=true`) | Plan, Kanban, `team(create/launch/advance)`, hints, accept partial | Implement delegatable steps with `bash`/`write` while wave runs |
| **Delegate** | Sub-agent (`run_delegate_detached`) | Execute one plan step / sub-plan | Create plans, message user directly, spawn teams |
| **User** | Human | Goals, credentials, approvals | — |

The orchestrator is an **engineering manager**; delegates are **IC engineers**.

---

## Lifecycle

```mermaid
sequenceDiagram
  participant U as User
  participant O as Orchestrator
  participant T as TeamManager
  participant D as Delegates

  U->>O: Build X (PRD)
  O->>O: plan(create) — master plan + project_dir
  O->>T: team(create, wave=0)
  O->>T: team(launch)
  T->>D: spawn_batch (SubCryptex each)
  loop Each delegate
    D->>D: run_loop (EXECUTING)
    D-->>O: escalate / complete
  end
  T->>O: wave complete wake
  O->>O: team(advance) / evaluate deliverables
  O->>T: team(launch) wave N+1
  O->>U: communicate(summary)
```

---

## Agent modes & tool policy

Modes (`nls/agentic/types.py` → `AgentMode`) gate which tools appear in the schema list.

| Mode | Orchestrator use | Primary tools |
|------|------------------|---------------|
| `planning` | Decompose PRD, `plan(create)` | `plan`, `todo`, `read`, `semantic_search` |
| `delegating` | `team(create)`, `team(launch)` | `team`, `plan`, `delegate_ring` |
| `monitoring` | Wait for wave; handle escalations | `team`, `await_delegates`, `communicate` |
| `evaluating` | Review artifacts, `plan(verify/complete)` | `plan`, `read`, `team(inspect/intervene)` |
| `executing` | Solo work (no active delegatable plan) | Full IC toolkit |
| `responding` | Answer user while teams run in background | Comms + skills + `switch_mode` back |

**Policy engine:** `orchestration_policy.py`

- `resolve_allowed_tools()` — mode + coordinator phase → frozenset of tool names
- `refresh_tool_schemas()` — strips IC tools from orchestrator during active waves
- Wake messages — compact system injections on scheduler / escalation / wave complete

**Mode transitions:** `tool_mode_policy.py` — successful `team(launch)` → MONITORING, etc.

---

## Teams & waves

**Team tool** (`nls/tools/agent_tools/team.py`) + **TeamManager** (`team_manager.py`).

| Concept | Description |
|---------|-------------|
| **Wave** | One batch of delegates for a set of plan steps with satisfied dependencies |
| **Member** | Single delegate bound to a step label |
| **Completion review** | Orchestrator must `team(intervene, decision=approve)` before member marked done |
| **Auto-advance** | Policy may launch next prepared wave when no active delegates |

**Wave coordination** (`wave_coordination.py`): file ownership blocks, tech stack hints injected into delegate SubCryptex.

---

## Delegate runtime

Each delegate runs `run_loop()` with:

| Isolation | Mechanism |
|-----------|-----------|
| Memory | **SubCryptex** — task, progress, knowledge, skills (borrowed from parent) |
| Tools | `_DELEGATE_EXCLUDED` — no `plan`, `team`, channel sends, `ask_user` |
| CWD | Plan `project_dir` pre-set on bash + file tools |
| Budget | `max_steps` / iteration cap; `escalate()` for more budget or auth help |
| Hints | Orchestrator `team(hint)` → steering queue → next delegate iteration |

**Executor entry:** `run_delegate_detached()` in `executor.py`.

---

## Escalation & hints

Delegates call virtual tool `escalate(reason, message)` when blocked (auth, budget, file access).

| Orchestrator action | When |
|---------------------|------|
| `team(intervene, decision=extend)` | Delegate needs more iterations |
| `team(intervene, decision=hint)` | Concrete next step (e.g. gh auth command) |
| `team(grant_paths)` | File access request |

Wake source `team_member_escalation:` injects priority system message. See orchestration policy `build_orchestration_wake_message()`.

---

## Plans & Kanban

**PlanStore** (`plan_store.py`) + **plan_work** / **plan_goal_hygiene**:

- Master plan with `project_dir`, `tech_stack`, step dependencies
- Sub-plans for complex steps
- Todo board sync via todo-list skill (delegates read-only)
- Recovery helpers for failed / partial steps

Rule: **one user request → one master plan → one project folder**.

---

## Coordinator guards

`coordinator_guard.py` prevents common failure modes:

- Orchestrator implementing delegatable steps after `team(launch)`
- Raw `delegate()` when plan requires team delegation
- Plan completion while steps still pending

---

## Related modules

| File | Role |
|------|------|
| `wake_coordination.py` | Scheduler wakes, token budget |
| `orchestrator_hint.py` | Hint formatting |
| `outbound_notify.py` | Channel notifications on milestones |
| `resume_guidance.py` | Post-crash loop resume |
| `delegate_manager.py` | Active delegate registry |
| `bridge.py` | `LoopHooks`, Cryptex `compose_context` each iteration |

---

## Squads (persistent fleet)

Separate from **Teams** above: **Squads** coordinate **multiple full agents** via `SquadManager`, `squad` tools, and the `squad_lead` profile. Work flows through a shared **inbox** (propose → lead approve → member todos). Event-driven wakes use `squad_checkback`, `squad_escalation`, and `squad_item_done` dispatch sources.

**Guide:** [Job, Trust & Squads](../guides/job-trust-and-squads.md) · **API:** [Job, Trust & Squad API](../reference/job-trust-squad-api.md)

---

## Further reading

- [Agentic loop](agentic-loop.md)
- [nls/agentic module](nls-modules/agentic.md)
- [Sequence: agentic loop](sequences/agentic-loop.md)
- [Skill discovery & recovery](skill-discovery-and-recovery.md)
- [Job, Trust & Squads](../guides/job-trust-and-squads.md)
