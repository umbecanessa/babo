# Product scope

Babo is the **shippable open-source product**: desktop app, web UI, NestJS backend, and local Python runtime.

## In scope

| Area | What ships |
|------|------------|
| **Inference** | Bring your own OpenAI-compatible API (OpenRouter, Ollama, vLLM, etc.) |
| **Memory** | Cryptex, working memory, DomainDB, Merkle chain, episodes |
| **Sleep** | LLM-based consolidation into long-term memory |
| **Agentic loop** | Tools, plans, sub-agents, projects workspace |
| **Integrations** | WhatsApp, Telegram, email, Google Workspace, MCP, ClawHub |
| **Desktop** | Electron shell, bundled runtimes, local uvicorn sidecar |

## Out of scope in this repository

Experimental research tooling, custom inference server plugins, and on-device model training pipelines are **not** part of the open-source product. Use `NLS_PRODUCT_MODE=1` (default) for the supported profile.

## Layout

```text
frontend/   Angular UI
backend/    NestJS + PostgreSQL
desktop/    Electron + release scripts
nls/        Agent brain (memory, loop, skills, tools)
server/     FastAPI runtime
```

## Verify locally

```bash
export NLS_PRODUCT_MODE=1
python -m compileall server nls -q
python -m pytest tests/ -q
```

**End-to-end acceptance:** with Babo desktop running on port 9222, run the [agent scenario harness](agent-scenario-harness.md) (45 scenarios; latest **44/45** pass on `google/gemini-2.5-flash`).

See [Local development](local-development.md) and [Contributing](https://github.com/umbecanessa/babo/blob/main/CONTRIBUTING.md).
