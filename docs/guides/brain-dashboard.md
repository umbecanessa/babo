# Brain dashboard

The **Brain** page is a live window into your agent's internal state.

**Route:** `/brain/:agentId`

Optional but powerful for understanding *why* an agent acted a certain way.

---

## Header

- Agent name and **status badge** (awake, sleeping, frozen, etc.)
- Real-time updates via WebSocket

---

## Sections

### Hormones

Line charts for six hormones over time:

| Hormone | Rough meaning |
|---------|---------------|
| **Dopamine** | Reward, satisfaction |
| **Norepinephrine** | Alertness, curiosity |
| **Serotonin** | Calm, stability |
| **Cortisol** | Stress, error pressure |
| **Oxytocin** | Trust, bonding |
| **Acetylcholine** | Learning, daydream modulation |

Hormones decay toward baselines with configurable half-lives. Spikes correlate with signals in chat.

### Signals

History of extracted **signals** — `LEARN`, `EVALUATE:*`, `REFLECT`, `BOND`, etc. Filter by time range.

### Working memory status

Current slot count, top salience items, consolidation pressure.

### Narrative self

Snippet of the agent's ongoing self-narrative and recent episode summaries.

### Theory of mind

The agent's model of **your** mental state — what it believes you know, want, or feel (best-effort inference).

### Network dynamics

Aggregate activity metrics across brain subsystems.

---

## Relationship to chat

The chat **signal sidebar** and **hormone panel** are lightweight subsets of Brain dashboard data — useful during conversation without switching pages.

---

## Drives (background)

While not always shown as primary charts, **drives** (homeostasis, curiosity, competence, social, self-direction) build pressure in the background. High curiosity + browser tools → autonomous web exploration during idle periods. High social + channel skills → proactive outreach when appropriate.

---

## Consciousness states

The runtime tracks **CONSCIOUS**, **SLEEPING**, and **FROZEN** states. User messages **preempt** — a frozen or sleeping agent wakes for your chat.

---

## Related

- [Memory](memory.md)
- [Sleep & consolidation](sleep-and-consolidation.md)
- [Core concepts](../getting-started/concepts.md)
