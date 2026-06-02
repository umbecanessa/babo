# Inner loop

Continuous **autonomous consciousness** for loaded agents — heartbeat + breath rhythms, DMN dreams, and drive-fired actions.

**Source:** `nls/engine/inner_loop.py`  
**Scheduler:** `server/services/consciousness_scheduler.py`

!!! note "Not DaydreamScheduler"
    The old tick-based `Daydream_scheduler.py` was removed. Idle/dream behavior lives in **InnerLoop** only (`server/main.py` sets `daydream_scheduler = None`).

---

## Rhythms

| Rhythm | Frequency | Work | GPU |
|--------|-----------|------|-----|
| **Heartbeat** | Every cycle (~tens of ms) | Hormone decay, drive pressure math, self-state | No |
| **Breath** | Every N heartbeats | DMN check, drive evaluation, proactive dispatch, sleep trigger | Yes (inference) |

Breath interval scales with engagement — busy agents breathe more often.

---

## User interrupt

When the user sends chat:

1. Consciousness scheduler preempts inner loop
2. `AgentRuntime.process_message_agentic_async` runs
3. Inner loop resumes when agent returns to CONSCIOUS idle

User traffic always wins over autonomous work.

When the user sends a **non-orchestration** message, `AgentRuntime` clears `_last_agentic_stall_ts` on both runtime and inner loop so background drives resume immediately after a follow-up.

---

## Post-stall drive suppression

When a foreground agentic loop exits with `exit_reason=stalled`:

1. `ws_handler` sets `_last_agentic_stall_ts` on runtime and inner loop
2. For **30 minutes**, autonomous drives (`curiosity`, `disconfirmation`, etc.) are skipped via `_should_skip_autonomous_drive`
3. Drives also pause while the user has **tactical goals** in working memory or `is_user_busy` is set

This prevents a stall exit from immediately spawning unrelated background work.

---

## DMN (Default Mode Network)

On breath ticks, `nls/brain/dmn.py` may:

- **Replay** — recombine existing memories (consolidation bias)
- **Explore** — probe model knowledge into new domains
- **Active dream** — optional browser/bash with policy gates

Signals (LEARN, REFLECT) feed ANS → future sleep.

Admin can trigger manual daydream: `POST /admin/agents/{id}/daydream`.

---

## Autonomous dispatch

Breath may call `enqueue_autonomous_dispatch` → scheduler message → mini agentic runs (todos, browser, etc.).

Connected to **todo-list** skill idle intentions and **drive** config (`nls/config/drives.json`).

---

## States (consciousness scheduler)

| State | Inner loop |
|-------|------------|
| CONSCIOUS | Running |
| SLEEPING | Stopped during consolidation |
| FROZEN | Not loaded — no loop |

See [Consciousness scheduler](consciousness-scheduler.md).

---

## Disable

```bash
NLS_CONSCIOUSNESS_ENABLED=false
```

---

## Related

- [Sleep & consciousness](sleep-and-consciousness.md)
- [Brain & memory](brain-and-memory.md)
- [Data flow](data-flow.md)
