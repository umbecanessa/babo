# Chat

The chat view is your primary interface to a Babo agent.

**Route:** `/chat/:agentId`

---

## Interface layout

| Area | Purpose |
|------|---------|
| **Message list** | Conversation history with streaming replies |
| **Composer** | Text input, attachments, voice |
| **Signal sidebar** | Live learning signals (`LEARN`, `EVALUATE`, etc.) |
| **Cryptex viz** | Snapshot of active memory rings |
| **Hormone panel** | Current affective state (optional) |
| **Agent browser** | Embedded view of the agent's browser workspace |
| **Workbench** | File proposals and tool output cards |

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
| `/sleep_confirm` | Confirm sleep when prompted |

Sleep is useful after long teaching sessions or before ending work for the day.

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
