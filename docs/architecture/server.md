# Server runtime

The Python **FastAPI server** (`server/`) is Babo's agent runtime — one process that loads agents, runs the brain, and exposes HTTP/WebSocket APIs.

**Default URL:** `http://127.0.0.1:9222`

---

## Role in the stack

```text
┌─────────────────────────────────────────┐
│  NestJS backend (:3000)                 │
│  Auth · agent registry · WS relay       │
└──────────────────┬──────────────────────┘
                   │ RUNTIME_URL
┌──────────────────▼──────────────────────┐
│  Python runtime (:9222)                 │
│  server/ + nls/                         │
└──────────────────┬──────────────────────┘
                   │ OpenAI-compatible API
┌──────────────────▼──────────────────────┐
│  Inference provider                     │
│  (OpenRouter, Ollama, vLLM, …)          │
└─────────────────────────────────────────┘
```

The runtime owns **memory, tools, skills, and sleep**. It does not host the LLM weights — it calls your configured inference endpoint.

---

## Entry point

`server/main.py` — FastAPI app startup:

- Load settings (`server/config.py`)
- Seed genesis template `standard-v1` if missing
- Initialize agent manager, consciousness scheduler, sleep scheduler
- Register routes and WebSocket handlers
- Optional channel relay to NestJS

Run locally:

```bash
uvicorn server.main:app --host 127.0.0.1 --port 9222
```

---

## Core services

| Service | File | Purpose |
|---------|------|---------|
| **AgentManager** | `agent_manager.py` | Create, load, unload, delete agents |
| **DualModelManager** | `dual_model_manager.py` | Inference client (OpenAI-compatible HTTP) |
| **SleepScheduler** | `sleep_scheduler.py` | Queue and run consolidation cycles |
| **ConsciousnessScheduler** | `consciousness_scheduler.py` | CONSCIOUS / SLEEPING / FROZEN states |
| **Inner loop** | `nls/engine/inner_loop.py` | Idle DMN / drives (via consciousness scheduler) |
| **Genesis seed** | `genesis_seed.py` | Bundled `standard-v1` template |

---

## API surface (selection)

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Liveness + component status |
| `GET/POST /agents` | List, create agents |
| `GET /agents/{id}/…` | Status, memory, brain metrics |
| `WS /ws/chat/{agent_id}` | Streaming chat + tool events |
| `POST /v1/chat/completions` | OpenAI-compatible (when enabled) |
| `/skills/…` | Skill webhooks and REST (todo board, MCP, channels) |
| `/api/clawhub/…` | ClawHub proxy |

Exact routes live in `server/routes/`.

---

## Inference client

`server/services/vllm_client.py` wraps chat completions:

- Bearer auth via `NLS_INFERENCE_API_KEY`
- Streaming SSE
- Model id from `NLS_HF_MODEL`
- Retries on transient errors

No custom vLLM plugins required in product mode.

---

## Multi-agent operation

One runtime process can serve **multiple agents**:

- Each agent has isolated `data/agents/{id}/` storage
- **Consciousness scheduler** limits concurrent "awake" inner loops
- User messages **preempt** — chatting wakes a frozen agent immediately
- Sleep cycles serialize per agent via sleep scheduler queue

---

## Product mode defaults

When `NLS_PRODUCT_MODE=1` (default):

- External inference only
- Consolidation sleep enabled
- Genesis template `standard-v1`
- Sleep uses LLM consolidation only (no on-device model training)

See [Environment variables](../configuration/environment-variables.md).

---

## Deployment notes

| Scenario | Recommendation |
|----------|----------------|
| Desktop | Bind `127.0.0.1` only — Electron spawns process |
| Self-host | Private network + `RUNTIME_SHARED_SECRET` if exposed |
| Scale-out | One runtime per machine; agents are directory-portable |

---

## Related

- [Architecture overview](overview.md)
- [Data flow](data-flow.md)
- [Persistence](persistence.md)
- [Consciousness scheduler](consciousness-scheduler.md)
