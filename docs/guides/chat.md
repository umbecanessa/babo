# Chat

The chat view is your primary interface to a Babo agent.

**Route:** `/chat/:agentId`

---

## Interface layout

| Area | Purpose |
|------|---------|
| **Message list** | Conversation history with streaming replies |
| **Composer** | Text input, attachments, voice |
| **Model picker** | Session model, route (local/cloud), delegate override — see [Model picker](#model-picker) |
| **Orchestration chip** | Profile depth + live mode (`chat-orchestration-profile-picker/`) |
| **Run panel** | Live tool calls and orchestration timeline (`run-panel/`) — glass side dock |
| **Signal sidebar** | Live learning signals (`LEARN`, `EVALUATE`, etc.) |
| **Cryptex viz** | Snapshot of active memory rings |
| **Hormone panel** | Current affective state (optional) |
| **Left dock** | **Workbench** (file proposals, tool cards) and **Agent browser** tabs |
| **Conversation breadcrumb** | Home vs channel destination (e.g. Discord `#general`) |
| **Surface inbox** | Pending messages from other surfaces while on Home thread |

### Model picker

The model picker (`chat-model-picker/`) uses opaque [context-menu panels](../development/ui-surfaces.md). It appears when at least one model is available in the catalog.

**Catalog sources (v1.2+ hybrid):**

| Section | Models |
|---------|--------|
| **Local / LAN inference** | Ollama, vLLM, or LAN server from device scan |
| **Popular** | Babo Cloud catalog on hybrid installs; curated defaults on cloud-only |
| **More models** | Remaining ids alphabetically |

**Chip indicators:**

| Indicator | Meaning |
|-----------|---------|
| Orange dot | One-shot override — affects **next message only** |
| Green dot | Agent session default (persisted) |
| Split badge | Orchestrator and sub-agent models differ |

**Advanced mode** (toggle in picker footer):

- **Orchestrator** tab — main loop model + route
- **Sub-agents** tab — delegate model when unlocked
- **Lock sub-agents to orchestrator model** — default on; sub-agents follow orchestrator pick

Footer actions: **Set as agent default**, **Clear agent default**, **Clear one-shot override**.

Each selection sends `model` and optional `model_route` (`local` | `cloud`) on the wire. See [Inference providers](../configuration/inference-providers.md#hybrid-lan-cloud-desktop-v12).

### Orchestration chip

Next to the composer, one chip shows:

- **Profile** — conversational, solo_structured, orchestrated, or squad_lead (picker uses opaque context-menu panels — see [UI surfaces](../development/ui-surfaces.md))
- **Mode** — live runtime mode (planning, delegating, executing, …)

When a **team plan** is active, triage enforces an **orchestration floor** — the chip shows when your pick was raised to `orchestrated` and cannot go below that until the plan completes. The mode label updates only after a successful mode switch, not on rejected attempts.

**Blocked mode switches:** The runtime may reject `switch_mode(executing)` when a team awaits launch, waves are still running, or completion review is pending. The chip **reverts** to the previous mode on failure.

---

## Interactive prompts (`ask_user`)

When the agent needs input mid-loop, an **`ask_user`** card appears in the message list with choices or a free-text field. Your answer resumes the loop without starting a new turn. Common in Job/Trust flows and squad coordination.

---

## Left dock: Workbench & Agent browser

Toggle the left dock to switch between:

| Tab | Purpose |
|-----|---------|
| **Workbench** | File change proposals, expanded tool output, plan summaries |
| **Agent browser** | Live view when the agent uses the browser tool (isolated Playwright context) |

Workbench state restores after app restart alongside chat transcript (v1.2.4+).

---

## Streaming events

While the agent works, you see real-time events:

| Event | Meaning |
|-------|---------|
| **Thought** | Internal reasoning (when exposed) |
| **Tool started / completed** | A tool invocation and its result |
| **Turn completed** | One loop iteration finished |
| **File change proposed** | Agent suggests editing or creating a file |

Click tool cards to expand JSON output. Download cards appear when the agent uses `offer_download`.

---

## Slash commands

| Command | Action |
|---------|--------|
| `/sleep` | Start a consolidation sleep cycle |
| `/sleep_confirm` | Confirm sleep when the drowsy card is shown |
| `/sleep_deny` | Decline sleep and keep the agent awake |

Sleep is useful after long teaching sessions or before ending work for the day.

### Drowsy card

When signal pressure is high, the agent may enter **drowsy** state. An amber inline card appears in the message list with **Rest up** and **Stay awake**. Either button sends `sleep_confirm` / `sleep_deny` over the WebSocket. Short replies like "yes" or "go ahead" also confirm while drowsy.

The **signal sidebar** records a `drowsy` activity entry when negotiation starts. After confirm, status shows **sleeping** and consolidation events stream as usual.

Full command list and wire format: **[Chat slash commands](../reference/chat-commands.md)**.

---

## Voice input

Babo supports speech-to-text for hands-free messaging. Transcription runs locally when possible, with fallback engines if configured.

Hold or click the microphone control in the composer (desktop and supported browsers).

---

## Agent browser panel

When the agent uses the **browser tool**, the **Agent browser** panel shows its workspace — navigated pages, snapshots, and actions. This is separate from your desktop; the agent operates in an isolated Playwright context.

---

## Sessions

Conversations are grouped into **sessions** with titles. History persists across restarts. Resume prior sessions from the chat history controls.

**Shared with Projects:** Home chat transcript and agentic tool traces sync with the Projects chat sidebar — open either surface and see the same main-thread history.

**Transcript restore (v1.2+):** On reload, Babo rebuilds:

- User messages with **attachment cards** (images, files)
- Agentic traces with per-iteration **mid-loop prose** (assistant updates during long runs)
- Tool cards with expanded metadata (plan steps, team actions)
- Workbench summaries linked to agentic events

Disconnect mid-run preserves partial progress server-side; reconnect continues streaming where possible.

**Per-agent threads:** Channel threads (Telegram, Discord, Slack, …) are scoped to the current agent — switching agents resets the sidebar thread list so squad members do not see each other's channel sessions.

**Conversation breadcrumb:** When replying on a channel thread, the breadcrumb shows the destination (e.g. `Home › Discord › #general`). The composer hint reflects private Home vs surface send.

When another surface messages the agent while you are chatting on Home, pending items may appear in the **surface inbox** — the agent can steer on them without starting a parallel deep loop.

---

## Scrolling during agentic runs

While the agent streams tool calls and replies, the message list **follows the bottom** by default. Scroll up (or use the mouse wheel) to read earlier messages — follow mode turns off until you scroll back to the bottom. This avoids the view snapping away while you review history mid-run.

---

## Tips

**Teach explicitly.** Say "Remember that…" for preferences you want in long-term memory.

**Ask for plans.** "Break this into steps and execute" triggers the plan tool and possibly team delegation.

**Watch signals.** Frequent `LEARN` signals mean the agent is capturing useful facts.

**Use Projects chat sidebar.** For board work or Teams panel steering, open Projects and toggle the chat sidebar to keep context in one screen.

---

## Related

- [Agentic loop & plans](agentic-loop-and-plans.md)
- [Memory](memory.md)
- [Sleep & consolidation](sleep-and-consolidation.md)
- [WebSocket events](../reference/websocket-events.md)
