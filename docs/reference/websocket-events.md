# WebSocket events

Two chat transports exist depending on platform.

---

## A. Electron → Python (raw WebSocket)

**URL:** `ws://127.0.0.1:9222/ws/chat/{runtimeAgentId}`

**Client:** `frontend/src/app/core/services/websocket.service.ts` (`useRawWs = true`)

Events mirror Python `ConnectionManager` broadcasts — JSON messages with a `type` field. Common types:

| `type` | Meaning |
|--------|---------|
| `thought` | Model reasoning chunk |
| `response_chunk` | Streaming text |
| `response_end` | Final message |
| `tool_start` / `tool_end` | Tool execution |
| `signal` | Learning signal for sidebar |
| `plan_update` | Plan progress |
| `sleep_start` / `sleep_cycle` / `sleep_complete` | Sleep pipeline |
| `status` | Informational |
| `error` | Failure |

Exact shapes are defined in `server/routes/chat/ws_handler.py` and agentic stream helpers.

**Client → server:** JSON chat messages (content, session key, commands) per ws_handler contract.

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
