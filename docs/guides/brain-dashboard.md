# Brain dashboard

The **Brain** page is a live window into your agent's internal state.

**Route:** `/brain/:agentId`

Optional but powerful for understanding *why* an agent acted a certain way.

---

## Header

- Agent name and **status badge** (awake, sleeping, frozen, drowsy, etc.)
- **Sleep** button — trigger consolidation from Brain (same as `/sleep` in chat)
- Real-time updates via WebSocket

---

## Tab groups

Brain organizes twelve tabs into four groups:

### State

| Tab | What you see |
|-----|--------------|
| **Overview** | Summary cards — status, hormone snapshot, recent signals |
| **Self-State** | Internal self-model and current cognitive posture |
| **Network** | Aggregate activity across brain subsystems |
| **Hormones** | Line charts for six hormones over time |

**Hormones:**

| Hormone | Rough meaning |
|---------|---------------|
| **Dopamine** | Reward, satisfaction |
| **Norepinephrine** | Alertness, curiosity |
| **Serotonin** | Calm, stability |
| **Cortisol** | Stress, error pressure |
| **Oxytocin** | Trust, bonding |
| **Acetylcholine** | Learning, daydream modulation |

Hormones decay toward baselines with configurable half-lives. Spikes correlate with signals in chat.

### Cognition

| Tab | What you see |
|-----|--------------|
| **Working Memory** | Slot count, top salience items, consolidation pressure |
| **Theory of Mind** | Agent's model of **your** mental state |
| **Narrative** | Ongoing self-narrative and episode summaries |
| **Predictions** | Anticipated outcomes and confidence |

### Perception

| Tab | What you see |
|-----|--------------|
| **Visual Cortex** | Ambient vision pipeline status, recent image analyses |

Enable vision workloads in [Settings → Models & AI](settings.md#models-ai) or see [Vision, voice & embeddings](vision-voice-and-embeddings.md). Architecture: [Vision worker](../architecture/vision-worker.md).

### System

| Tab | What you see |
|-----|--------------|
| **Signals** | History of `LEARN`, `EVALUATE:*`, `REFLECT`, `BOND`, etc. |
| **Events** | Scheduler and runtime event log |
| **Schedule** | Sleep windows, consciousness scheduler config |
| **Config** | Brain parameter overrides (advanced) |

The **Schedule** tab links to [Sleep & consolidation](sleep-and-consolidation.md).

---

## Relationship to chat

The chat **signal sidebar** and **hormone panel** are lightweight subsets of Brain data — useful during conversation without switching pages.

---

## Drives (background)

**Drives** (homeostasis, curiosity, competence, social, self-direction) build pressure in the background. High curiosity + browser tools → autonomous web exploration during idle periods. High social + channel skills → proactive outreach when appropriate.

---

## Consciousness states

The runtime tracks **CONSCIOUS**, **SLEEPING**, **DROWSY**, and **FROZEN** states. User messages **preempt** — a frozen or sleeping agent wakes for your chat. Drowsy negotiation shows the amber confirm card in chat.

---

## Related

- [Memory](memory.md)
- [Sleep & consolidation](sleep-and-consolidation.md)
- [Core concepts](../getting-started/concepts.md)
- [Vision worker](../architecture/vision-worker.md)
