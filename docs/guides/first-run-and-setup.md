# First run & setup wizard

The desktop app walks you through first launch at **`/setup`**. Web-only users skip this — they sign in and connect to a desktop runtime via relay.

**Platform:** Electron desktop only. After setup completes, settings live under [Settings](settings.md) → **Models & AI**.

---

## Wizard phases

| Step | Name | What happens |
|------|------|--------------|
| 0 | **Welcome** | Intro and start |
| 1 | **Prepare** | Python venv, dependencies, bundled tools (Node/PowerShell on Windows) |
| 2 | **Device scan** | GPU/RAM detection; optional LAN inference probe |
| 3 | **Thinking** | Choose where chat inference runs |
| 4 | **Extras** | Vision, transcribe, embeddings workloads |
| 5 | **Account sync** | Optional — skipped when fully on Babo Cloud thinking |
| 6 | **Sign in** | Babo Cloud account (optional for self-host) |
| 7 | **Billing** | Shown when Babo Cloud subscription is required |
| 8 | **Ready** | Summary of choices; **Test Connection** on inference |
| 9 | **Name agent** | First agent name; launches runtime and opens chat |

Decision dots on steps 3–6 mark choices that affect your capability profile.

---

## Brain tier choices (step 3)

| Tier | Inference | Best for |
|------|-----------|----------|
| **Babo Cloud** | Hosted relay through NestJS | No local GPU; fastest start |
| **This computer** | Local Ollama / vLLM | Privacy; offline-capable |
| **My server (LAN)** | Remote OpenAI-compatible box on your network | Weak laptop + home GPU server |
| **BYOK cloud** | Your OpenRouter/Azure/etc. key through Babo relay | Own provider billing |

Hybrid installs (local/LAN **plus** Babo Cloud) enable the [model picker](chat.md#model-picker) to route per message — see [Inference providers](../configuration/inference-providers.md#hybrid-lan-cloud-desktop-v12).

---

## Device scan (step 2)

The scanner reports:

- CPU, RAM, GPU name and VRAM
- Whether a **LAN inference server** responds at a discovered or entered URL
- Recommended capability tiers based on hardware

If a LAN box is found, **My server** becomes the natural default for thinking while Babo Cloud can still supply cloud models.

---

## Test Connection

Before launch, **Test Connection** calls your inference `/v1/chat/completions` (or health) and shows latency. Fix URL, model id, or API key before continuing — a failed test usually means chat will fail immediately after setup.

---

## After setup

1. Runtime starts on `127.0.0.1:9222` (default)
2. First agent is created from the genesis template
3. You land on **`/chat/:agentId`**
4. **Day 1 coach** may offer guided tips in chat for new installs

Change inference, backend URL, or workloads later: [Settings → Models & AI](settings.md#models-ai) · [Vision, voice & embeddings](vision-voice-and-embeddings.md).

---

## Self-hosted / skip wizard

Developers running the stack from source typically:

1. Start Postgres + NestJS + Python runtime manually
2. Open the web UI at `http://localhost:4200`
3. Register and create an agent via [Creating agents](creating-agents.md)

See [Installation](../getting-started/installation.md) and [Local development](../development/local-development.md).

---

## Related

- [Installation](../getting-started/installation.md)
- [Quickstart](../getting-started/quickstart.md)
- [Settings](settings.md)
- [Desktop configuration](../configuration/desktop.md)
- [Capability profiles](../architecture/capability-profiles-and-onboarding.md)
