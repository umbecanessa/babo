# Agentic loop & plans

Babo's **agentic loop** is the engine behind multi-step autonomous work.

---

## Loop phases

Each turn cycles through:

1. **Orient** — gather Cryptex, working memory, plan state, tool schemas
2. **Augment** — inject breadcrumbs, goals, team status
3. **LLM** — model chooses reply and/or tool calls
4. **Execute** — run tools (parallel when safe)
5. **Digest** — fold tool output into context
6. **Evaluate** — check guards, completion criteria, plan progress
7. **Complete** — finish or loop again
8. **Compact** — shrink history if approaching token limits

The loop stops when the evaluator marks the task complete, you abort, or guard conditions trigger (e.g. repeated failures).

---

## Agent modes

The runtime switches **modes** based on context:

| Mode | Typical use |
|------|-------------|
| **CHAT** | Conversation, Q&A |
| **PLANNING** | Decompose work, create/update plans |
| **DELEGATING** | Spawn sub-agents and teams |
| **EXECUTING** | Heavy tool use |
| **MONITORING** | Watch delegate progress |
| **EVALUATING** | Verify outputs against criteria |

Modes restrict which tools are primary, keeping the agent focused.

---

## Plans

The **plan** tool manages structured runbooks:

| Action | Purpose |
|--------|---------|
| `create` | New plan with steps |
| `add_step` | Append steps |
| `sub_plan` | Nested plan for a step |
| `update` | Change step status or notes |
| `verify` | Audit step completion |
| `complete` | Mark plan done |
| `read` | Inspect current plan |

Steps can be marked **delegatable** — eligible for sub-agent teams.

### Orchestration floor

When an active plan has **multiple delegatable steps** or running team waves, triage enforces a minimum profile of **`orchestrated`**. You cannot drop to `solo_structured` or `conversational` from the chat chip until that plan completes. The runtime re-checks the floor on every loop entry — not only on the first triage call.

### Solo circumvention guard

If a team plan is in progress, `plan(create)` with **every step `delegatable=false`** is blocked — the orchestrator must use `team(create/launch)` for remaining delegatable work, not replace the plan with a solo rebuild.

---

### Plan ↔ Todo link

Plans sync with the **Kanban board**:

- Starting a plan step can move linked todo to **in progress**
- Completing a plan can mark todo **done**

---

## Sub-agents (delegates)

The **delegate manager** runs child loops with:

- Scoped **SubCryptex** memory (task, progress, knowledge, borrowed skills)
- Token budgets; `escalate()` for auth/budget help
- Types: explore, bash, general (via delegate spec)

Parent agents use **team(hint/intervene)** and **delegate_ring** to steer workers.

### Team waves (orchestrator)

For multi-step projects the orchestrator should use **team**, not solo IC tools:

1. `plan(create)` — master plan with `project_dir` and dependencies  
2. `team(create, wave=N)` — batch members for ready steps  
3. `team(launch)` — start delegates  
4. `await_delegates` / wake on completion — review, then `team(advance)`  

See [Orchestration architecture](../architecture/orchestration-and-delegation.md).

---

## When stuck

Babo promotes **skills** and **ClawHub** in working memory when:

- The same tool call repeats with errors  
- The evaluator fires ERROR_RECOVERY  
- The orchestrator sends a hint to a delegate  

Agents should call `clawhub(action='search')` or `discover_tools(query='...')` before retrying the same bash command.

See [Skill discovery & recovery](../architecture/skill-discovery-and-recovery.md).

---

## Context compaction

Long sessions use **anchored compaction** — preserve system instructions, plan summary, and recent turns while summarizing older history. Compaction triggers automatically based on token pressure.

---

## Crash recovery

Before each LLM call, Babo writes a **journal snapshot**. If the runtime restarts mid-task, it can recover in-flight loop state.

---

## Credential safety

Outgoing context is **scrubbed** for secrets (API keys, tokens) via regex patterns before reaching the inference API.

---

## Built-in tools (selection)

| Tool | Purpose |
|------|---------|
| `read`, `write`, `edit`, `grep`, `glob`, `list_dir` | Filesystem |
| `bash` | Shell commands |
| `browser` | Playwright automation |
| `web_search`, `web_fetch` | Internet |
| `semantic_search` | Meaning-based codebase search |
| `plan`, `team` | Orchestration |
| `scheduler`, `poller` | Background jobs |
| `contacts` | Cross-channel address book |
| `vision` | Image Q&A |
| `wm` | Agent self-inspection of memory |

Installed skills and MCP tools appear alongside these automatically.

---

## Related

- [Projects & teams](projects-and-teams.md)
- [Tools & skills](tools-and-skills.md)
- [Chat](chat.md)
