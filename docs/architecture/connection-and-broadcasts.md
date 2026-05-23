# Connection manager & broadcasts

Real-time events from Python to connected UIs.

**Source:** `server/services/connection_manager.py`

---

## Role

When the agentic loop, sleep, or inner loop produces UI-visible events, the runtime **broadcasts** JSON messages to:

1. **Local WebSocket** clients (`/ws/chat/{agent_id}`)
2. **ChannelRelayClient** → NestJS → Socket.IO clients in relay mode

---

## Registration

Per-agent set of WebSocket connections. Chat handler registers on connect; unregisters on disconnect.

---

## Event types (examples)

| Type | When |
|------|------|
| `thought` | Streaming reasoning |
| `response_chunk` / `response_end` | Chat output |
| `tool_start` / `tool_end` | Tool execution |
| `signal` | Learning sidebar |
| `plan_update` | Plan progress |
| `sleep_start` / `sleep_cycle` / `sleep_complete` | Sleep |
| `batch_update` | Batched inner events (relay) |

Full list: [WebSocket events](../reference/websocket-events.md).

---

## Relay batching

`broadcast` messages may wrap multiple events:

```json
{ "type": "broadcast", "event": { "type": "batch_update", "events": [ ... ] } }
```

NestJS `handleRelayInbound` unwraps and fans out to Socket.IO listeners.

---

## Priority

Connection manager supports prioritized delivery for urgent events (errors, user-visible failures) over noisy debug streams.

---

## Related

- [WebSocket events](../reference/websocket-events.md)
- [Relay protocol](../reference/relay-protocol.md)
- [Data flow](data-flow.md)
