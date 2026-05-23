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

### Plan ↔ Todo link

Plans sync with the **Kanban board**:

- Starting a plan step can move linked todo to **in progress**
- Completing a plan can mark todo **done**

---

## Sub-agents (delegates)

The **delegate manager** runs child loops with:

- Scoped **SubCryptex** memory
- Token budgets
- Types: `explore`, `bash`, `general`

Parent agents use **delegate_ring** to read or inject delegate memory (task, progress, knowledge, credentials, etc.).

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
