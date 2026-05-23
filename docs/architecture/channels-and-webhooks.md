# Channels & webhooks

External messaging surfaces connect through **channel skills** and the **NestJS relay**.

---

## End-to-end path

```text
External provider (Telegram, WhatsApp, Resend, …)
    → POST https://<nestjs>/api/channels/webhook/{channel}/{runtimeAgentId}
    → ChannelsService.pushToRelayByAgentId (if desktop online)
    → ChannelRelayClient receives channel_message
    → HTTP POST http://127.0.0.1:9222/skills/{channel}-channel/webhook/{id}
    → Channel adapter processes → agentic response
    → Outbound send via skill adapter API
```

If desktop offline: payload stored in **PendingChannelMessage**, drained on relay reconnect.

---

## Bundled channels

| Channel | Skill | Ingress | Outbound |
|---------|-------|---------|----------|
| WhatsApp | `whatsapp-channel` | Webhook + Baileys bridge | Baileys send |
| Telegram | `telegram-channel` | Webhook or long-poll | Bot API |
| Email | `email-channel` | Resend inbound | Resend send API via NestJS |
| Google | `google-workspace` | OAuth APIs | Gmail/Calendar/Drive/Sheets |

Setup guides: [Integrations index](../guides/integrations/index.md).

---

## Webhook registration (Telegram example)

1. User completes @BotFather setup in Tools UI
2. Adapter calls `register_webhook_relay(nestjs_base, agent_id)`
3. Telegram API webhook URL = `{nestjs}/api/channels/webhook/telegram/{runtimeAgentId}`
4. Desktop must be online (relay) to receive events in real time

Fallback: long-polling on desktop when relay unavailable.

---

## Email special case

- **Activate:** `POST /api/channels/email/activate/:agentId` (JWT) — creates alias
- **Send:** `POST /api/channels/email/send` (`X-Runtime-Secret`) — Python requests outbound send through Resend

Inbound: Resend webhook → NestJS → relay → email-channel skill.

---

## DM policies

Per-skill config:

- `open` — respond to anyone
- `allowlist` — approved contacts only
- `disabled` — ignore inbound

Owner identity fields link channels to the logged-in user.

---

## Shared memory

All channels feed the **same AgentRuntime** — WhatsApp and web chat share Cryptex and WM.

---

## Python `/webhooks` vs NestJS `/api/channels/webhook`

| Ingress | Path | When |
|---------|------|------|
| **NestJS (production)** | `/api/channels/webhook/{channel}/{runtimeAgentId}` | Hosted Telegram/email; requires desktop relay online |
| **Python direct** | `/webhooks/telegram/{agent_id}` | Self-hosted stacks exposing port 9222 (`server/routes/webhooks.py`) |
| **Skill-mounted** | `/skills/{channel}-channel/webhook/{agent_id}` | Baileys WhatsApp bridge, skill adapters |

Prefer NestJS webhooks for cloud deployments so pending messages queue when the desktop is offline. Use Python `/webhooks` only for local/self-hosted stacks that expose port 9222 publicly (with `NLS_WEBHOOK_SECRET`).

---

## Security

- Webhook URLs contain `runtimeAgentId` — use unguessable ids
- Configure `RUNTIME_SHARED_SECRET` on relay
- Only connect trusted MCP servers

---

## Related

- [Relay protocol](../reference/relay-protocol.md)
- [Skills system](skills-system.md)
- [NestJS backend](nestjs-backend.md)
