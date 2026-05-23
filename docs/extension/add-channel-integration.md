# Add a channel integration

Channels let users talk to agents via WhatsApp, Telegram, email, etc.

---

## Architecture constraints

1. **Webhooks land on NestJS** — `POST /api/channels/webhook/{channel}/{agentId}`
2. **Desktop receives via relay** — `channel_message` on outbound WS
3. **Processing stays local** — adapter POSTs to `127.0.0.1:9222/skills/.../webhook/...`

Read [Channels & webhooks](../architecture/channels-and-webhooks.md) first.

---

## Step 1: Create channel skill

`nls/skills/bundled/my-channel/`:

- `adapter.py` — inbound parse, outbound send, policy checks
- `__init__.py` — `register()` mounts webhook router if needed locally

---

## Step 2: Webhook handler on Python

Typical route (mounted by skill):

```text
POST /skills/my-channel/webhook/{agent_id}
```

Handler should:

1. Validate payload signature (provider-specific)
2. Normalize to internal message format
3. Call `AgentRuntime.process_message_agentic_async` or channel processor
4. Send reply via outbound API

---

## Step 3: Register provider webhook at NestJS URL

```text
https://<your-api>/api/channels/webhook/my-channel/{runtimeAgentId}
```

Telegram pattern: `register_webhook_relay()` in `telegram-channel/adapter.py`.

---

## Step 4: Relay registration

Ensure desktop sets `NESTJS_URL` so `ChannelRelayClient` connects.

Without relay, use queue + long-poll fallback (see Telegram adapter).

---

## Step 5: Tools UI card

Add integration card in frontend Tools page (`features/tools/`) — follow WhatsApp/Telegram patterns:

- Setup wizard / QR / OAuth
- Status indicator
- Link to skill config schema

---

## Step 6: DM policy & contacts

Use shared contacts tool for cross-channel identity.

Respect `open` / `allowlist` / `disabled` policies in adapter.

---

## Email-specific

Uses NestJS Resend APIs — see `email-channel` + `channels.controller.ts` `email/send`.

---

## Testing

1. Start desktop + verify relay online on dashboard
2. Send test webhook with curl to NestJS
3. Confirm local skill webhook receives payload (desktop logs)
4. Verify agent reply on provider

---

## Related

- [Relay protocol](../reference/relay-protocol.md)
- [Add a bundled skill](add-bundled-skill.md)
