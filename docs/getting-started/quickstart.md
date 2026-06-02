# Quickstart

Create your first Babo agent and have a conversation in about five minutes.

---

## Desktop path

### 1. Launch Babo

[Download and install](https://github.com/umbecanessa/babo/releases) the desktop app, then open Babo. See [installation](installation.md) if you build from source.

### 2. Complete setup

Walk through the onboarding wizard:

| Phase | What to do |
|-------|------------|
| **Prepare** | Wait for Python environment setup (venv, dependencies, bundled Node/PowerShell on Windows) |
| **Device scan** | Review detected GPU/RAM; optional LAN server probe |
| **Capabilities** | Choose where inference, features, and vision run |
| **Inference** | URL, model, API key — e.g. OpenRouter `https://openrouter.ai/api/v1`, model `openai/gpt-4o-mini` |
| **Backend** | NestJS URL — `http://localhost:3000` for self-host or your Babo Cloud URL |
| **Launch** | Start the local runtime |

Use **Test Connection** on inference before continuing. Settings → Capabilities can be changed later.

### 3. Register and sign in

Create an account on your backend (local or hosted).

### 4. Create an agent

Go to **Create** (or `/create`). Choose a genesis path — each template shapes personality and defaults. Pick a name and confirm.

Agent creation takes a few seconds. Babo seeds a standard brain configuration locally.

### 5. Chat

Open the agent from the **Dashboard**. Type a message:

> Remember that my favorite stack is FastAPI on the backend and Angular on the frontend.

Babo streams the reply and may emit learning signals (visible in the signal sidebar). The fact is stored in working memory and queued for consolidation.

### 6. Explore

| Page | URL pattern | What to try |
|------|-------------|-------------|
| **Chat** | `/chat/:agentId` | Ask follow-up questions; watch tool calls live |
| **Memory** | `/memory/:agentId` | Knowledge tab, working memory slots |
| **Tools** | `/tools/:agentId` | Browse integrations |
| **Projects** | `/projects/:agentId` | Board and timeline |
| **Brain** | `/brain/:agentId` | Hormone and signal charts |

### 7. Trigger sleep (optional)

In chat, send:

```
/sleep
```

Babo consolidates recent experiences into long-term memory. After sleep completes, ask:

> What stack did I say I prefer?

The agent should recall from memory, not guess.

---

## Self-hosted path

If you run [the full stack](installation.md#option-b-self-hosted-stack):

1. Register at `http://localhost:4200/auth/register`
2. Sign in → **Dashboard** → **New Agent**
3. Same chat and exploration flow as above

Ensure `RUNTIME_URL` in the backend points at your Python server.

---

## Connect an integration (optional)

1. Open **Tools** for your agent
2. Under **Integrations**, pick **WhatsApp**, **Telegram**, or **Google Workspace**
3. Follow the guided setup (QR scan, bot token, or OAuth modal)
4. Message your agent from that channel — it replies using the same memory and brain

See [Integrations overview](../guides/integrations/index.md).

---

## What happened under the hood

1. **Genesis** — Babo created an agent directory with identity template, empty memory stores, and tool registry.
2. **Chat** — your message entered the agentic loop: context assembly (Cryptex + history), LLM call, signal extraction, working memory update.
3. **Sleep** — consolidation summarized experiences, merged facts, and updated long-term rings.

No manual prompt engineering required — memory is built into the runtime.

---

## Next steps

- [Core concepts](concepts.md)
- [Chat guide](../guides/chat.md)
- [Memory guide](../guides/memory.md)
- [Projects & teams](../guides/projects-and-teams.md)
