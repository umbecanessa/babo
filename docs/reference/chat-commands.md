# Chat slash commands (WebSocket)

The chat WebSocket (`/ws/chat/{agent_id}`) accepts **slash commands** as JSON messages. Handler: `server/routes/chat/commands.py`.

---

## Wire format

Client sends a message whose payload includes `command` (string). The handler dispatches on that name.

---

## Commands

| Command | Purpose |
|---------|---------|
| `sleep` | Stop inner loop, enqueue `SleepRequest` via `SleepScheduler` |
| `sleep_confirm` | Confirm drowsy sleep prompt (`inner_loop.confirm_sleep`) |
| `sleep_deny` | Deny drowsy sleep (`inner_loop.deny_sleep`) |
| `dream_config` | Get/set DMN dream settings or `action: trigger` for manual dream |
| `dream_findings` | Pop recent dream findings for UI (`runtime.pop_dream_findings`) |
| `abort` | Set agentic abort signal if a task is running |
| `status` | Snapshot: facts, hormones, ANS, heartbeat, optional `sections` filter |

Send as `{ "type": "command", "command": "sleep_confirm" }` (or via `websocket.service.ts` `sendCommand()`).

### Sleep command responses

| Event | When |
|-------|------|
| `sleep_command_result` | After `sleep_confirm` / `sleep_deny` — `{ ok, action: "confirm" \| "deny", content? }` |
| `status` | On successful confirm — `{ agent_status: "sleeping", sleep_reason }` |

If the agent is not drowsy, confirm/deny returns `sleep_command_result` with `ok: false`.

### Natural-language confirm/deny

While drowsy, a normal chat message matching short affirmatives or denials is handled **before** the agentic loop (`try_handle_drowsy_text` in `sleep_negotiation.py`). Examples: `yes`, `go ahead`, `rest up` → confirm; `no`, `stay awake` → deny.

Unknown commands return `{ type: "status", content: "Unknown command: ..." }`.

---

## `dream_config` actions

| `action` | Fields | Effect |
|----------|--------|--------|
| `get` | — | Returns `dmn.active_dream_config` |
| `set` | `enabled`, `probability` | Updates DMN toggles |
| `trigger` | — | Starts active dream via inner loop (agent must be CONSCIOUS) |

---

## Related

- [WebSocket events](websocket-events.md)
- [Inner loop](../architecture/inner-loop.md)
- [Sleep & consolidation](../guides/sleep-and-consolidation.md)
- Admin: `POST /admin/agents/{id}/daydream`, `POST .../sleep`
