# Product scope

Babo is the **shippable open-source product**: desktop app, web UI, NestJS backend, and local Python runtime.

## In scope

| Area | What ships |
|------|------------|
| **Inference** | Bring your own OpenAI-compatible API (OpenRouter, Ollama, vLLM, etc.) |
| **Memory** | Cryptex, working memory, DomainDB, Merkle chain, episodes |
| **Sleep** | LLM-based consolidation into long-term memory (no on-device weight training) |
| **Agentic loop** | Tools, plans, sub-agents, projects workspace |
| **Integrations** | WhatsApp, Telegram, email, Google Workspace, MCP, ClawHub |
| **Desktop** | Electron shell, Python venv setup, local uvicorn sidecar |

## Out of scope in this repository

- Custom vLLM plugins or router injection
- On-device model adapter training
- Remote GPU training fleets or school-style curriculum pipelines
- Research calibration harnesses and bench artifacts

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

See [Local development](local-development.md) and [Contributing](https://github.com/umbecanessa/babo/blob/main/CONTRIBUTING.md).
