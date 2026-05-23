# Engine package (`nls/engine`)

Legacy package name. **Most files are compatibility shims** re-exporting `brain`, `tools`, `runtime`, `agentic`, `identity`, `knowledge`.

Active product code lives in the modules below — prefer importing canonical packages in new code.

---

## Active modules (use these)

| File | Purpose |
|------|---------|
| `inner_loop.py` | `InnerLoop` — autonomous heartbeat, dreams |
| `events.py` | `AgentEventQueue`, `EventType`, chat WS events |
| `brain_events.py` | `BrainEventBus`, `BrainSignal` |
| `thalamic_router.py` | `ThalamicRouter`, `predict_tools` (idle/channel event depth) |
| `execution_slots.py` | `ExecutionSlotManager` — micro/focus/deep slots |
| `tools.py` | `ToolRegistry`, `ToolExperienceStore` |
| `tool_loader.py` | JSON manifest loader |
| `tools_builtin.py` | `FileReadTool`, `FileWriteTool`, `FileEditTool`, `RequestSleepTool` |
| `micro_inference.py` | Small local inference helpers |

---

## Shims (deprecated paths)

Examples — import from the right package instead:

| Shim | Canonical |
|------|-----------|
| `engine/autonomic.py` | `nls.brain.autonomic` |
| `engine/agentic_bridge.py` | `nls.agentic.bridge` |
| `engine/fact_store.py` | `nls.knowledge.fact_store` |
| `engine/moe_runtime.py` | `nls.runtime.agent_runtime` |
| `engine/agent_tools/*` | `nls.tools.agent_tools/*` |

---

## Server integration

| Module | Import |
|--------|--------|
| `consciousness_scheduler.py` | `nls.engine.inner_loop.InnerLoop` |
| `routes/chat/ws_handler.py` | `nls.engine.events` |
| `routes/filesystem.py` | `nls.engine.tools_builtin` |

---

## Related

- [Inner loop](../inner-loop.md)
- [Tools system](../tools-system.md)
