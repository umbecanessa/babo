# Vision, voice & embeddings

User guide for Babo's **four AI workloads** — configured in the desktop setup wizard and **Settings → Models & AI**.

| Workload | What it powers |
|----------|----------------|
| **Thinking (chat)** | Agentic loop, chat completions |
| **Vision** | Image description, visual cortex, `vision` / `eyes` tools |
| **Transcribe** | Microphone in chat composer |
| **Embeddings** | Semantic search, knowledge retrieval |

Architecture detail: [Capability profiles](../architecture/capability-profiles-and-onboarding.md) · [Production architecture](../architecture/production-architecture-and-onboarding.md)

---

## Where to configure

| Surface | Path |
|---------|------|
| **First install** | Setup wizard steps **Thinking** and **Extras** — [First run & setup](first-run-and-setup.md) |
| **After install** | **Settings → Models & AI** — four capability cards |

Each card picks a **tier**: local, LAN server, Babo Cloud hosted, or BYOK cloud (where applicable).

---

## Thinking (chat inference)

Primary LLM for the agentic loop. Options:

| Tier | Best for |
|------|----------|
| **Babo Cloud** | No local GPU |
| **This computer** | Ollama / local vLLM |
| **My server (LAN)** | Home GPU box on the network |
| **BYOK cloud** | Your OpenRouter/Azure key via relay |

Hybrid installs (local + Babo Cloud) enable per-message routing in the [model picker](chat.md#model-picker). See [Inference providers](../configuration/inference-providers.md).

---

## Vision

Powers image understanding in chat and the Brain **Visual Cortex** tab.

| Tier | Typical backend |
|------|-----------------|
| **Local** | Moondream or bundled VLM on desktop |
| **LAN GPU worker** | Remote worker URL from device scan |
| **Babo Cloud GPU** | `{nestjs}/api/gpu/*` routes |

Configure vision URL in the capability profile; the runtime calls the selected worker when the agent uses `vision`, `screenshot`, or ambient visual cortex.

User-facing Brain tab: [Brain dashboard → Visual Cortex](brain-dashboard.md#perception).

Architecture: [Vision worker](../architecture/vision-worker.md).

---

## Transcribe (voice input)

Hold or click the **microphone** in the chat composer to dictate a message.

| Tier | Behavior |
|------|----------|
| **Local Whisper** | Runs on desktop when CUDA/CPU allows |
| **LAN / GPU worker** | Proxies audio to `NLS_GPU_WORKER_URL` |
| **Babo Cloud** | Hosted transcribe route |

Ops reference: [Transcribe & GPU worker](../configuration/transcribe-and-gpu-worker.md).

**Tip:** Weak laptops often set transcribe to LAN or cloud while keeping chat inference local.

---

## Embeddings

Used by **semantic search** and knowledge indexing — not visible as a separate chat feature, but required for codebase search and fact retrieval quality.

| Tier | Notes |
|------|-------|
| **Local** | Small embed model in runtime venv |
| **LAN / cloud** | Offload to GPU worker or Babo Cloud |

If semantic search returns poor matches, verify the embeddings tier matches your hardware or switch to a hosted embed endpoint in **Models & AI**.

---

## Test Connection

Each capability card includes **Test Connection** (or health probe) before save. Failed vision/transcribe tests usually mean wrong URL, missing API key, or unreachable GPU worker — chat may still work if only thinking tier is configured.

---

## Related

- [Settings](settings.md#models-ai)
- [First run & setup](first-run-and-setup.md)
- [Chat → Voice input](chat.md#voice-input)
- [Desktop configuration](../configuration/desktop.md)
