# Chat

The chat view is your primary interface to a Babo agent.

**Route:** `/chat/:agentId`

---

## Interface layout

| Area | Purpose |
|------|---------|
| **Message list** | Conversation history with streaming replies |
| **Composer** | Text input, attachments, voice |
| **Model picker** | Session model and delegate override (`chat-model-picker/`) — opaque context-menu panel |
| **Orchestration chip** | Profile depth + live mode (`chat-orchestration-profile-picker/`) |
| **Run panel** | Live tool calls and orchestration timeline (`run-panel/`) — glass side dock |
| **Signal sidebar** | Live learning signals (`LEARN`, `EVALUATE`, etc.) |
| **Cryptex viz** | Snapshot of active memory rings |
| **Hormone panel** | Current affective state (optional) |
| **Agent browser** | Embedded view of the agent's browser workspace |
| **Workbench** | File proposals and tool output cards |

### Orchestration chip

Next to the composer, one chip shows:

- **Profile** — conversational, solo_structured, orchestrated, or squad_lead (picker uses opaque context-menu panels — see [UI surfaces](../development/ui-surfaces.md))
- **Mode** — live runtime mode (planning, delegating, executing, …)

When a **team plan** is active, triage enforces an **orchestration floor** — the chip shows when your pick was raised to `orchestrated` and cannot go below that until the plan completes. The mode label updates only after a successful mode switch, not on rejected attempts.

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

**Shared with Projects:** Home chat transcript and agentic tool traces sync with the Projects chat sidebar — open either surface and see the same main-thread history. Reload restores completed tool cards; disconnect mid-run preserves partial agentic progress server-side.

**Per-agent threads:** Channel threads (Telegram, Discord, Slack, …) are scoped to the current agent — switching agents resets the sidebar thread list so squad members do not see each other's channel sessions.

When another surface messages the agent while you are chatting on Home, pending items may appear in the **surface inbox** — the agent can steer on them without starting a parallel deep loop.

---

## Scrolling during agentic runs

While the agent streams tool calls and replies, the message list **follows the bottom** by default. Scroll up (or use the mouse wheel) to read earlier messages — follow mode turns off until you scroll back to the bottom. This avoids the view snapping away while you review history mid-run.

---

## Tips

**Teach explicitly.** Say "Remember that…" for preferences you want in long-term memory.

**Ask for plans.** "Break this into steps and execute" triggers the plan tool and possibly team delegation.

**Watch signals.** Frequent `LEARN` signals mean the agent is capturing useful facts.

**Use Projects chat sidebar.** For board/timeline work, open Projects and toggle the chat sidebar to keep context in one screen.

---

## Related

- [Agentic loop & plans](agentic-loop-and-plans.md)
- [Memory](memory.md)
- [Sleep & consolidation](sleep-and-consolidation.md)
- [WebSocket events](../reference/websocket-events.md)
