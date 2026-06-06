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

---

## Related

- [Environment variables](environment-variables.md)
- [Installation](../getting-started/installation.md)
