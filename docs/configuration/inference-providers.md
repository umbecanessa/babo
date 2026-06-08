# Inference providers

Babo talks to any **OpenAI-compatible** HTTP API for chat completions.

Set:

```bash
NLS_VLLM_BASE_URL=<base URL>
NLS_HF_MODEL=<model id>
NLS_INFERENCE_API_KEY=<optional bearer token>
```

The client uses `/v1/chat/completions` (streaming supported).

For **Babo Cloud** (`NLS_VLLM_BASE_URL` pointing at `{nestjs}/api/inference/v1`), Nest accepts **session JWT** or an **`nlsk_` API key**. Desktop users signed in to Babo Cloud do **not** need to paste a key manually — the app syncs JWT into `NLS_INFERENCE_API_KEY` at runtime (see [Desktop configuration](desktop.md#babo-cloud-inference-auth)).

---

## Babo Cloud relay

When the capability profile uses **hosted Babo** or **BYOK cloud**, inference goes through NestJS:

```bash
NLS_VLLM_BASE_URL=https://api.babo.agency/api/inference/v1
NLS_HF_MODEL=<model from subscription or BYOK>
# NLS_INFERENCE_API_KEY — set automatically on desktop (JWT or nlsk_)
```

| Auth method | When to use |
|-------------|-------------|
| Session JWT (desktop) | Default for signed-in users — synced by `BaboCloudProvisionService` |
| `nlsk_` API key | Automation, long agentic runs, or scripts — Settings → API keys |
| BYOK upstream key | `byok_cloud` profile — stored in capability settings |

Vision, transcribe, and embed GPU routes on the same Nest host (`/api/gpu/*`) also require Bearer auth; remote VLM calls include the same token when the worker URL is a Babo Cloud GPU endpoint.

---

## Hybrid LAN + cloud (desktop v1.2+)

Many desktop installs run **two inference endpoints at once**:

| Endpoint | Env var | Typical use |
|----------|---------|-------------|
| **Local / LAN** | `NLS_LAN_INFERENCE_URL` | Ollama, vLLM, or a GPU box on your network |
| **Babo Cloud relay** | `NLS_BABO_CLOUD_INFERENCE_URL` | Hosted models via NestJS (`{nestjs}/api/inference/v1`) |
| **Primary install URL** | `NLS_VLLM_BASE_URL` | Fallback client; also used for non-hybrid profiles |

The desktop **capability profile** sets these automatically during setup (tiers **This computer** + Babo Cloud sign-in, or **My server (LAN)** + cloud). You do not paste them manually unless self-hosting the runtime.

### How routing works

Each chat turn can specify which endpoint to use:

1. **Per-message override** — model picker one-shot selection sends `model` + `model_route` (`local` or `cloud`) on the WebSocket
2. **Agent session default** — persisted via `PATCH /agents/{id}/inference` (`orchestrator_model`, `orchestrator_route`, `delegate_model`, `delegate_route`)
3. **Heuristic fallback** — if no route is set, the runtime picks LAN when the model id is served locally, otherwise cloud

**Orchestrator vs sub-agents:** In advanced model picker mode you can set different models for the main loop and delegate loops. `delegate_lock_orchestrator` (default `true`) forces sub-agents to use the orchestrator model.

```bash
# Example hybrid env (set by desktop ConfigManager)
NLS_LAN_INFERENCE_URL=http://192.168.1.50:8000/v1
NLS_BABO_CLOUD_INFERENCE_URL=https://api.babo.agency/api/inference/v1
NLS_HF_MODEL=llama3.2
NLS_INFERENCE_API_KEY=<JWT or nlsk_ for cloud relay>
```

### User-facing model picker

In chat, the model picker groups models:

| Section | Contents |
|---------|----------|
| **Local / LAN inference** | Models from your LAN probe or local Ollama |
| **Popular** | Babo Cloud catalog (hybrid) or curated defaults (cloud-only) |
| **More models** | Remaining ids, alphabetical |

Indicators on the chip:

- **Orange dot** — one-shot override (next message only)
- **Green dot** — agent session default
- **Split badge** — orchestrator and sub-agent models differ

Use **Set as agent default** in the picker footer to persist; **Clear one-shot override** resets the next-message pick.

**Babo Brain (hosted tier):** When the capability profile is fully hosted Babo Brain, all turns route through Babo Cloud regardless of route hints.

See [Chat guide](../guides/chat.md#model-picker) and [Architecture: Inference](../architecture/inference.md).

---

## OpenRouter

```bash
NLS_VLLM_BASE_URL=https://openrouter.ai/api/v1
NLS_HF_MODEL=openai/gpt-4o-mini
NLS_INFERENCE_API_KEY=sk-or-v1-...
```

Good default for quick start — many models, single API key.

---

## Ollama (local)

```bash
NLS_VLLM_BASE_URL=http://127.0.0.1:11434/v1
NLS_HF_MODEL=llama3.2
# no API key needed
```

Run `ollama serve` and `ollama pull llama3.2` first.

---

## vLLM / LiteLLM / other proxies

Point `NLS_VLLM_BASE_URL` at your proxy root:

```bash
NLS_VLLM_BASE_URL=http://localhost:8000/v1
NLS_HF_MODEL=your-served-model
```

Ensure the model id matches what the server expects in `model` field.

---

## Commercial APIs

Any provider with OpenAI-compatible endpoints works (Azure OpenAI, Groq, Together, etc.). Use their base URL, model name, and API key.

---

## Desktop wizard

The setup wizard **Test Connection** calls the inference health/completions endpoint and reports latency.

---

## Tips

| Issue | Fix |
|-------|-----|
| 401 Unauthorized (direct provider) | Set `NLS_INFERENCE_API_KEY` to the provider's key |
| 401 on Babo Cloud / "trouble generating" in desktop | Sign in again, or confirm relay URL is `{nestjs}/api/inference/v1`; check runtime log for empty bearer — desktop should sync JWT on boot |
| Model not found | Match `NLS_HF_MODEL` to provider's exact id |
| Timeout on long tools | Use a model/provider with higher context and rate limits |
| Local only | Ollama or local vLLM — no data leaves machine |
| Empty model picker | Confirm LAN server is reachable; sign in for Babo Cloud catalog; check runtime logs |
| Wrong endpoint used | Pick model from correct section (Local vs Popular); check `model_route` in agent inference settings |
| Hybrid 401 on cloud | JWT sync — sign in again; see [Desktop configuration](desktop.md#babo-cloud-inference-auth) |

---

## Related

- [Environment variables](environment-variables.md)
- [Installation](../getting-started/installation.md)
