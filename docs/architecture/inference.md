# Inference

Babo calls models through a standard **OpenAI-compatible chat completions** client (`server/services/vllm_client.py`).

## Configure

```bash
NLS_VLLM_BASE_URL=https://openrouter.ai/api/v1
NLS_HF_MODEL=openai/gpt-4o-mini
NLS_INFERENCE_API_KEY=sk-...
```

Despite the `VLLM` prefix on env vars, you are **not** required to run vLLM. Any provider that implements `/v1/chat/completions` with streaming works (OpenRouter, Ollama, LiteLLM, etc.).

**Babo Cloud:** when `NLS_VLLM_BASE_URL` is `{nestjs}/api/inference/v1`, the bearer may be a provider key, an `nlsk_` API key, or a **user JWT**. Desktop syncs JWT into `NLS_INFERENCE_API_KEY` automatically; `vllm_client.set_api_key()` can hot-reload without restarting uvicorn.

See [Inference providers](../configuration/inference-providers.md).

---

## Product mode

With `NLS_PRODUCT_MODE=1` (default), inference is **HTTP only** — no custom model runtimes or weight training in this repository. Sleep consolidation uses the same API to summarize and write into Cryptex / DomainDB.

See [Product mode](../configuration/product-mode.md).

---

## Crystallized skills (UI badge)

In the Tools UI, a **crystallized** skill is an instruction-based **AgentSkill** converted into a **native Python skill module** under `nls/skills/`. This is optional optimization; most skills stay as JSON + executor definitions in `nls/config/tools/`.

---

## Tools vs skills vs MCP

| Layer | Definition | Discovery |
|-------|------------|-----------|
| **Agent tools** | JSON in `nls/config/tools/` + Python executors | Always available to the loop |
| **Bundled skills** | `nls/skills/bundled/` | Tools page, onboarding |
| **MCP** | External MCP server | User connects URL/command; tools appear dynamically |
| **ClawHub** | Remote skill packages | Backend proxy + install to agent |

---

## torch / transformers in requirements

`requirements-desktop.txt` includes `torch` and `transformers` for tokenizer formatting and optional local vision models. They are **not** used for on-device weight training in product mode.

---

## Related

- [Deployment topologies](deployment-topologies.md)
- [Tools & skills guide](../guides/tools-and-skills.md)
- [Product scope](../development/product-scope.md)
