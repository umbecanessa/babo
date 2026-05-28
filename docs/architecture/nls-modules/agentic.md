# Agentic package (`nls/agentic`)

Multi-turn **agentic loop** (v4/v5): LLM generation, parallel tool execution, compaction, evaluation, plans, **team waves**, delegates.

**Entry:** `nls/agentic/__init__.py` — `run_loop`, `build_config`, `build_hooks`, `PlanStore`, `PermissionManager`.

**Architecture:** [Agentic loop](../agentic-loop.md) · [Orchestration](../orchestration-and-delegation.md) · [2026 release](../2026-cloud-orchestration-release.md)

---

## Key files

| File | Role |
|------|------|
| `loop.py` | `run_loop()` main orchestration, stall/skill boost wiring |
| `bridge.py` | `LoopHooks`, `build_hooks`, Cryptex `compose_context` refresh |
| `types.py` | `AgentMode`, `LoopConfig`, coordinator supplements |
| `generator.py` | `generate()`, adapter routing, context sanitize |
| `executor.py` | `execute_tools()`, `run_delegate_detached()`, SubCryptex spawn |
| `evaluator.py` | `should_complete_v4`, `detect_stall`, directives |
| `compactor.py` | Anchored compaction, relay char limits |
| `plan_store.py` | `Plan`, `PlanStep`, dependencies, tech_stack |
| `team_manager.py` | Waves, completion review, escalation |
| `delegate_manager.py` | Active delegate registry |
| `orchestration_policy.py` | Tool policy, wakes, coordinator phases |
| `tool_mode_policy.py` | Post-tool mode transitions |
| `coordinator_guard.py` | Orchestrator IC guards |
| `wave_coordination.py` | Delegate briefing blocks |
| `skill_discovery_boost.py` | Cryptex ring promotion on stall |
| `recipe_hints.py` | Composition recipe preflight |
| `tool_result_semantics.py` | Bash soft-error detection |
| `wake_coordination.py` | Scheduler wake budget |
| `orchestrator_hint.py` | Hint message helpers |
| `outbound_notify.py` | Milestone channel notifications |
| `plan_work.py` / `plan_goal_hygiene.py` | Plan/todo reconciliation |
| `resume_guidance.py` | Post-crash resume |
| `breadcrumbs.py` | Post-tool navigation hints |
| `hooks.py` | Pre/post tool hooks (CLI redirect, ClawHub nudge) |
| `goals.py` | Goal extraction and scoring |

---

## Modes (`AgentMode`)

Coordinator modes: **planning → delegating → monitoring → evaluating → responding** (plus **executing** for solo IC work). Tool palettes filtered by `orchestration_policy.resolve_allowed_tools()`.

---

## Hooks (`bridge.py`)

Each iteration:

- `update_ring_priorities()` + `compose_context()` refresh WM system messages
- ANS absorb → Cryptex rings
- Hormone / network dynamics injected into phase detector

Persistence: `hypothalamus_state.json`, `ans_state.json`, WM under `agent_dir`.

---

## Server integration

| Module | Usage |
|--------|-------|
| `server/routes/skills.py` | Repair stream, `run_loop` for skill fix |
| `server/routes/teams.py` | `PlanStore`, delegation waves |
| `server/routes/chat/ws_handler.py` | Streaming events, multi-agent |
| `server/routes/chat/helpers.py` | `EventType` mapping |

`AgentRuntime` starts loops via `build_config` / `build_hooks` from bridge.

---

## Related

- [Agentic loop](../agentic-loop.md)
- [Orchestration & delegation](../orchestration-and-delegation.md)
- [Skill discovery & recovery](../skill-discovery-and-recovery.md)
- [Teams API](../../reference/teams-api.md)
