# Sleep & consolidation

Sleep is how Babo **turns experience into lasting memory**.

---

## Why sleep exists

During conversation, facts sit in working memory and signal buffers. Sleep:

- Summarizes and compresses experiences
- Merges facts into **Cryptex** and **DomainDB**
- Clears short-term buffers so the agent wakes uncluttered
- Updates narrative episodes

You don't need to manage this manually — it runs automatically — but you can trigger it with `/sleep`.

---

## Phases

### 1. Triage

Sort pending signals by priority. Error corrections and high-confidence learning rank above noise.

### 2. Consolidation

LLM-driven summarization:

- Compound compression of learning buffer (~target 800 chars for batches)
- Fact routing to correct Cryptex rings
- DomainDB merge with conflict handling

### 3. Integration

- ANS state transitions to waking
- Thalamus / routing refresh where applicable
- Team and plan checkpoints preserved

---

## Triggers

| Trigger | When |
|---------|------|
| **Scheduled** | Circadian bedtime / nap windows (if configured) |
| **Signal pressure** | Enough LEARN/REFLECT signals accumulated |
| **Manual** | `/sleep` in chat |
| **Voluntary (drowsy)** | Agent requests sleep via `request_sleep` tool when signal buffer is full; owner confirms or denies |
| **Post-orchestration** | After heavy team runs (runtime-dependent) |

---

## Drowsy sleep negotiation

When signal pressure crosses the drowsy threshold, the inner loop enters **drowsy** state and the UI shows an amber card with **Rest up** / **Stay awake** buttons.

| Path | Behavior |
|------|----------|
| **Confirm** | `sleep_confirm` command, drowsy card **Rest up**, or short natural-language yes (`yes`, `go ahead`, `rest up`, …) |
| **Deny** | `sleep_deny` command, **Stay awake**, or short no (`no`, `stay awake`, …) |
| **Timeout** | If no response within ~2 minutes, sleep proceeds anyway (same as pre-negotiation behavior) |

Implementation: `server/routes/chat/sleep_negotiation.py` — shared handlers for slash commands, WebSocket drowsy text, agentic `user_answer`, and copilot paths.

After confirm, the runtime broadcasts `sleep_command_result` (ok) and `sleep_start` with `sleep_reason`. Deny clears drowsy state without sleeping.

The agent may also call the built-in **`request_sleep`** tool during a loop when it wants consolidation — same drowsy negotiation applies.

---

## During sleep

- New user messages can **queue wake** depending on scheduler policy
- Agents in active conversation may **defer** sleep until idle
- UI status badge shows **sleeping**
- While **drowsy** (awaiting confirm), status shows drowsy; sidebar activity records the negotiation

---

## After sleep

Ask about things you taught before sleep — the agent should recall from consolidated memory, not treat you as a stranger.

Check **Memory → Knowledge** for new or updated facts.

---

## Circadian schedule

Agents can have timezone-aware **bedtime**, **wake time**, and optional **nap windows** — like a human sleep schedule. Configure via runtime settings / agent config.

---

## Related

- [Memory](memory.md)
- [Brain dashboard](brain-dashboard.md)
- [Chat](chat.md) — `/sleep` command
