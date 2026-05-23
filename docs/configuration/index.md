# Configuration overview

Babo has four configuration surfaces:

```text
┌─────────────┐     ┌─────────────┐     ┌──────────────────┐
│  Frontend   │────▶│   Backend   │────▶│  Python runtime  │
│  (Angular)  │     │  (NestJS)   │     │  (FastAPI/nls)   │
└─────────────┘     └─────────────┘     └────────┬─────────┘
       │                    │                      │
       │                    │                      ▼
       │                    │              Inference API
       ▼                    ▼                 (OpenAI-compat)
  environment.ts      backend/.env
  (build-time URLs)   RUNTIME_URL, JWT, DB
```

---

## Guides

| Topic | Document |
|-------|----------|
| All env vars | [Environment variables](environment-variables.md) |
| Product mode | [Product mode](product-mode.md) |
| Voice / GPU worker | [Transcribe & GPU worker](transcribe-and-gpu-worker.md) |
| LLM providers | [Inference providers](inference-providers.md) |
| Desktop app | [Desktop](desktop.md) |
| Full stack | [Self-hosting](self-hosting.md) |

---

## Minimum config to run

**Desktop:** inference URL, model, optional API key, backend URL — via setup wizard.

**Self-hosted:**

```bash
# backend/.env
DATABASE_URL=postgresql://...
JWT_SECRET=...
RUNTIME_URL=http://127.0.0.1:9222

# runtime shell
NLS_VLLM_BASE_URL=https://openrouter.ai/api/v1
NLS_HF_MODEL=openai/gpt-4o-mini
NLS_INFERENCE_API_KEY=sk-...
```

---

## Frontend URLs

| File | When |
|------|------|
| `environment.ts` | Local dev (`localhost:3000`) |
| `environment.prod.ts` | Production web (relative `/api`) |
| `environment.electron.ts` | Desktop (local runtime on 9222) |

Rebuild frontend after changing production URLs.

---

## Agent-level config

Per-agent settings live under `data/agents/{agent_id}/` — hormones.json, circadian schedule, skill configs, workspace path. Usually managed through UI, not hand-edited.

---

## Related

- [Installation](../getting-started/installation.md)
- [Architecture overview](../architecture/overview.md)
