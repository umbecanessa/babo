# Agent runtime

The **AgentRuntime** is the per-agent orchestrator for Babo. One instance exists per loaded agent.

**Source:** `nls/runtime/agent_runtime.py`  
**Constructed by:** `server/services/agent_manager.py` + `nls/runtime/factory.py`

---

## Lifecycle

```text
POST /agents (or auto_load on startup)
    → AgentManager.create_agent / load_agent
    → factory.build_subsystems(agent_id, agent_dir, ...)
    → AgentRuntime(**subsystems)
    → runtime.initialize()
    → register with SleepScheduler, ConsciousnessScheduler, ConnectionManager
```

Unload/evict removes runtime from memory; disk state remains under `data/agents/{id}/`.

---

## Subsystems injected from factory

`nls/runtime/factory.py` wires the biological stack:

| Subsystem | Module | Role |
|-----------|--------|------|
| ANS | `nls/brain/autonomic.py` | Signal buffer, sleep requests |
| Hypothalamus | `nls/brain/hypothalamus.py` | Hormone dynamics |
| Working memory / Cryptex | `nls/brain/working_memory.py` | Short + long context rings |
| DMN | `nls/brain/dmn.py` | Idle replay / exploration |
| Drives | `nls/brain/drives.py` | Homeostatic pressure |
| Agency | `nls/brain/agency.py` | Proactive tool use (inner loop) |
| Narrative / ToM / Temporal | `nls/identity/*` | Self and user models |
| Visual cortex | `nls/engine/visual_cortex.py` | Optional vision |
| VLLM client | `server/services/vllm_client.py` | Inference HTTP |

---

## Public entry points

| Method | Use case |
|--------|----------|
| `process_message_async` | Single-turn chat |
| `process_message_stream_async` | Streaming single-turn |
| `process_message_agentic_async` | Full agentic loop (primary UI path) |
| `generate` / `generate_stream_async` | Raw LLM calls |
| `save_state` | Persist brain + session |
| `refresh_tools` | Rebuild tool schemas after skill change |

### Agentic path

**Sequence diagram:** [Agentic loop](sequences/agentic-loop.md) · **API examples:** [Agent runtime API](examples/agent-runtime-api.md)

Inference routing uses BYO API only (`thalamic_route()` adds no extra kwargs; thinking via `classify_thinking_need()`).

```text
process_message_agentic_async()
  → _run_agentic_locked()          # per-agent mutex
  → nls.agentic.loop.run_loop()
        ├── generator (LLM)
        ├── executor (tools)
        ├── compactor (context)
        └── evaluator (stop conditions)
```

Config/hooks: `nls/agentic/bridge.py` (`build_config_v4`, `build_hooks_v4`).

---

## Prompt assembly

| Stage | Function | Output |
|-------|----------|--------|
| System prompt | `_build_system_prompt()` | Identity, soul, skill instructions |
| Context | `build_composed_context()` | Cryptex rings + WM + retrieval |
| Tools | `_get_tool_directory()` | OpenAI function schemas |
| Channel | `channel_type` param | Web vs WhatsApp tone adapters |

**Thalamic routing** (`thalamic_route()`): in product mode returns `{}` — standard tool routing only.

---

## Tools on runtime

Initialized in `_initialize_tools()`:

```text
nls/tools/tool_setup.setup_tools()
  → create_coding_tools()
  → skill_loader factories
  → plan, team, MCP proxies, channel tools
```

Returns `(tools dict, openai_schemas, scheduler_manager, team_manager)`.

---

## Sleep integration

ANS can call `on_sleep_requested` → `SleepScheduler.enqueue_sync`.

After consolidation: `notify_sleep_complete()` → consciousness scheduler wake.

---

## Inference

Uses shared `VLLMInferenceClient` from app state — OpenAI-compatible streaming.

Env: `NLS_VLLM_BASE_URL`, `NLS_HF_MODEL`, `NLS_INFERENCE_API_KEY`.

---

## Related

- [Agentic loop](agentic-loop.md)
- [Brain & memory](brain-and-memory.md)
- [Python API](../reference/python-api.md)
- Factory source: `nls/runtime/factory.py` in the repository
