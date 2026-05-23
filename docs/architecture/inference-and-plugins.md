# Inference & plugins

## Inference (how Babo calls models)

The product uses a standard **OpenAI-compatible chat completions** client (`server/services/vllm_client.py`).

Configure:

```bash
NLS_VLLM_BASE_URL=https://openrouter.ai/api/v1
NLS_HF_MODEL=openai/gpt-4o-mini
NLS_INFERENCE_API_KEY=sk-...
```

Despite the `VLLM` prefix on env vars, you are **not** required to run vLLM. Any provider that implements `/v1/chat/completions` with streaming works (OpenRouter, Ollama, LiteLLM, etc.).

See [Inference providers](../configuration/inference-providers.md).

---

## What is *not* in the open-source product

The historical **NLS vLLM plugin** (custom router injection, MoE xargs, DeltaNet memory injection into vLLM) is **out of scope** for this repository.

Product mode (`NLS_PRODUCT_MODE=1`):

- No custom vLLM plugins
- No on-device LoRA / QLoRA training during sleep
- No remote GPU worker training fleet

Sleep consolidation uses the **same inference API** to summarize and write into Cryptex / DomainDB.

---

## “NLS plugin” in the UI

In the Tools UI you may see a badge **“NLS Plugin”** on crystallized skills. That means a skill was converted from an instruction-based **AgentSkill** into a **native Python skill module** under `nls/skills/` — not a vLLM plugin.

Flow (high level):

1. Agent uses a ClawHub or bundled instruction skill repeatedly.
2. **Crystallize** tool generates a native plugin package.
3. Runtime loads it like other bundled skills.

This is optional optimization; most skills stay as JSON + executor definitions in `nls/config/tools/`.

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

`requirements-desktop.txt` includes `torch` and `transformers` for:

- Tokenizer formatting
- Optional **Moondream** vision paths

They are **not** used for weight training in product mode.

---

## Related

- [Deployment topologies](deployment-topologies.md)
- [Tools & skills guide](../guides/tools-and-skills.md)
- [Product scope](../development/product-scope.md)
