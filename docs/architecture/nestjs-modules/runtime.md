# Runtime module

**Path:** `backend/src/runtime/`

Central **HTTP and WebSocket client** to the Babo Python (FastAPI) process. Most cloud features that touch the agent brain go through here or through the desktop relay.

---

## Files

| File | Role |
|------|------|
| `runtime.module.ts` | Exports `RuntimeService` |
| `runtime.service.ts` | `fetch` + `ws` implementation |

---

## Key methods

| Method | Python target |
|--------|---------------|
| `getHealth()` | `GET /health` |
| `createAgent(body)` | `POST /agents` |
| `listGenesis()` | `GET /agents/genesis` |
| `getAgent(runtimeId)` | `GET /agents/{id}` (relay fallback) |
| `deleteAgent(runtimeId)` | `DELETE /agents/{id}` |
| `connectChat(runtimeId)` | `WS /ws/chat/{id}` |
| `proxyGet` / `proxyPost` | Arbitrary path with `X-Runtime-Secret` |
| `transcribeAudio(file)` | `POST /transcribe` |

When `ChannelsService.hasRelaySocket(runtimeAgentId)` is true, read operations can use **`proxyHttpViaRelay`** instead of direct HTTP.

---

## Environment

| Variable | Default |
|----------|---------|
| `RUNTIME_URL` or `BABO_RUNTIME_URL` | `http://127.0.0.1:8443` |
| `RUNTIME_SHARED_SECRET` or `BABO_SHARED_SECRET` | → header `X-Runtime-Secret` |

---

## Dependencies

- `ChannelsService` (`forwardRef`) for relay-aware proxy

---

## Consumers

[Agents](agents.md), [Admin](admin.md), [Filesystem](filesystem.md), [Transcribe](transcribe.md), [Channels](channels.md), [Chat](chat.md).

---

## Related

- [Deployment topologies](../deployment-topologies.md)
- [Python API](../../reference/python-api.md)
