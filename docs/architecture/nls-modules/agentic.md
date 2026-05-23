# Agentic package (`nls/agentic`)

Multi-turn **agentic loop** (v4/v5): LLM generation, parallel tool execution, compaction, evaluation, plans, delegates, teams.

**Entry:** `nls/agentic/__init__.py` — `run_loop`, `build_config`, `build_hooks`, `PlanStore`, `PermissionManager`.

---

## Key files

| File | Role |
|------|------|
| `loop.py` | `run_loop()` main orchestration |
| `bridge.py` | `LoopHooks`, `build_hooks`, persistence hooks |
| `types.py` | `AgentMode`, `LoopConfig`, `AgenticConfig`, events |
| `generator.py` | `generate()`, thinking selection, context sanitize |
| `executor.py` | `execute_tools()`, detached delegates |
| `evaluator.py` | `evaluate_turn()`, guards, `Directive` |
| `compactor.py` | Context compaction between steps |
| `plan_store.py` | `Plan`, `PlanStep`, delegation waves |
| `team_manager.py` | `Team`, `TeamManager` |
| `delegate_manager.py` | Sub-agent delegates |
| `permissions.py` | Tool/skill permission gates |
| `goals.py` | Goal extraction and scoring |

---

## Modes (`AgentMode`)

Includes conversational, **Evaluating**, autonomous, and channel-specific modes — see [Agentic loop](../agentic-loop.md).

---

## Hooks (`bridge.py`)

Persist after steps:

- `hypothalamus_state.json`, `ans_state.json`
- Working memory / Cryptex paths under `agent_dir`

---

## Server integration

| Module | Usage |
|--------|-------|
| `server/routes/skills.py` | Repair stream, `run_loop` for skill fix |
| `server/routes/teams.py` | `PlanStore`, delegation waves |
| `server/routes/chat/helpers.py` | `EventType` mapping |

`AgentRuntime` starts loops via `build_config` / `build_hooks` from bridge.

---

## Related

- [Agentic loop](../agentic-loop.md)
- [Teams API](../../reference/teams-api.md)
