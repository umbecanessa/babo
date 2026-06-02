# WebSocket events

Two chat transports exist depending on platform.

---

## A. Electron → Python (raw WebSocket)

**URL:** `ws://127.0.0.1:9222/ws/chat/{runtimeAgentId}`

**Client:** `frontend/src/app/core/services/websocket.service.ts` (`useRawWs = true`)

Events are JSON messages with a `type` field. Chat streaming types are sent directly from `server/routes/chat/ws_handler.py`; background tasks also push via `ConnectionManager.broadcast()`.

### Streaming & agentic (ws_handler)

| `type` | Meaning |
|--------|---------|
| `token` | Streaming assistant text |
| `reasoning_token` / `reasoning_end` | Exposed chain-of-thought chunks |
| `response_end` | Turn complete (includes `response`, optional `nls`) |
| `response_replace` | Replace prior streamed text |
| `agentic_start` / `agentic_iteration` / `agentic_complete` | Multi-step agentic loop |
| `agentic_token` / `tool_call_delta` | Streaming agentic output |
| `tool_execution_start` / `tool_execution_end` | Tool invocation lifecycle |
| `tool_use` | Legacy single-turn tool cards (web search, etc.) |
| `tool_output_chunk` | Live bash / tool stdout |
| `agentic_plan` / `plan_step_update` | Plan checklist progress |
| `activity_status` | Short status line ("Running: …") |
| `turn_thinking` | Per-iteration thinking summary |
| `probe_signal` | Learning-signal vector for sidebar |
| `delegate_start` / `delegate_end` / `delegate_progress` | Sub-agent delegation |
| `browser_navigation` | Agent browser workspace update |
| `ask_user` / `user_answer` / `communicate` | Interactive prompts |
| `history` | Session history on connect |
| `status` / `error` / `pong` | Control & errors |

### Background broadcasts (ConnectionManager)

| `type` | Meaning |
|--------|---------|
| `sleep_start` / `sleep_cycle` / `sleep_complete` | Sleep pipeline |
| `sleep_triggered` / `drowsy` | Sleep prompts |
| `daydream` / `dream_finding` | DMN / dream events |
| `drive_action` / `reach_out` | Autonomous drive engine |
| `safety_net_learned` | Safety-net capture |
| `consciousness_state` | Consciousness scheduler |
| `batch_update` | Batched low-priority events |
| `channel_event` / `connection_request` | Channel skill notifications |

**Client → server:** `{"type": "message", "content": "…"}` or `{"type": "command", "command": "…"}` (slash commands — see [Chat slash commands](chat-commands.md)).

---

## B. Browser → NestJS (Socket.IO `/chat`)

**URL:** `{wsUrl}/chat` with `auth: { token: jwt }`

### Client → server (emit)

| Event | Payload | Purpose |
|-------|---------|---------|
| `join` | `{ agentId }` | DB agent UUID; gateway resolves relay vs direct |
| `message` | `{ type?, content?, command? }` | Chat or slash command |
| `remote_chat` | `{ content, sessionKey? }` | Alternate chat entry |
| `subscribe_broadcasts` | — | Receive runtime broadcasts in relay mode |

### Server → client (on)

| Event | Purpose |
|-------|---------|
| `joined` | `{ agentId, runtimeAgentId }` |
| `runtime` | Unified envelope for stream events (same inner types as Python) |
| `runtime_disconnected` | Python or relay dropped |
| `error` | `{ message }` |

### Relay mode behavior

When desktop relay is online (`hasRelaySocket`):

- `message` → `pushChatToRelay` → desktop → `chat_response` → emitted as `runtime` with `response_end`
- Broadcasts from desktop (`type: broadcast`) fan out to subscribed clients

When relay offline and no direct `RUNTIME_URL`:

- `error`: "Agent desktop is not connected"

### Direct mode behavior

When `RUNTIME_URL` reachable and no relay:

- `RuntimeService.connectChat` bridges NestJS Socket.IO ↔ Python `/ws/chat/{runtimeAgentId}`

---

## Terminal Socket.IO (`/terminal`)

User's **own shell** — not agent tools.

| Client emit | Server emit |
|-------------|-------------|
| `terminal:input` | `terminal:output` |
| `terminal:resize` | `terminal:ready` |
| `terminal:cwd` | `terminal:exit` |

---

## Related

- [Relay protocol](relay-protocol.md)
- [Frontend application](../architecture/frontend-application.md)
- [Chat guide](../guides/chat.md)
