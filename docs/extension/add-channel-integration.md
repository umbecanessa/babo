# Add a channel integration

Channels let users talk to agents via WhatsApp, Telegram, Discord, Slack, email, etc.

Reference implementations: `telegram-channel`, `discord-channel`, `slack-channel`.

---

## Architecture constraints

1. **Ingress lands on NestJS** (or local fallback) — webhooks or Gateway workers
2. **Desktop receives via relay** — `channel_message` on outbound WS
3. **Processing stays local** — adapter POSTs to `127.0.0.1:9222/skills/.../webhook/...`

Read [Channels & webhooks](../architecture/channels-and-webhooks.md) first.

---

## Step 1: Create channel skill

`nls/skills/bundled/my-channel/`:

| File | Role |
|------|------|
| `adapter.py` | Inbound normalize, outbound send, policy, scope sync |
| `webhook.py` | FastAPI router — inbound relay target |
| `__init__.py` | `SkillMeta`, `register()` mounts router + tool factories |

Register in `nls/tools/skill_manager.py` (`BUNDLED_SKILLS`, `PRE_SHIPPED_CHANNEL_SKILLS`).

---

## Step 2: Channel scope (workspace channels)

For Discord/Slack-style integrations, use **`nls/skills/channel_scope.py`**:

- `merge_observed_channels()` — platform → Babo sync
- `apply_desired_channel()` — Tools UI / API → config
- `compile_groups_policy()` — feeds `PolicyEnforcer`
- `effective_channel_ids()` — inbound routing gate

Expose HTTP routes (pattern from `discord-channel/webhook.py`):

```text
GET    /skills/my-channel/channels/{agent_id}
POST   /skills/my-channel/channels/{agent_id}/sync
PATCH  /skills/my-channel/channels/{agent_id}/{channel_id}
```

Add **Channel scope** panel in `frontend/src/app/features/tools/tools.component.ts` when connected.

---

## Step 3: Webhook handler on Python

```text
POST /skills/my-channel/webhook/{agent_id}
```

Handler should:

1. Validate payload signature (provider-specific)
2. Normalize to internal message format (`session_key`, `sender_id`, `content`, …)
3. Check `should_respond()` — DM policy + effective channel scope
4. Call channel processor → agentic loop
5. Send reply via outbound adapter API
6. `register_known_sender()` for outbound guard

Shared helpers: `nls/skills/channel_adapter_util.py`, `nls/skills/channel_processing.py`.

---

## Step 4: NestJS ingress

### HTTP webhook pattern (Telegram, Slack)

```text
https://<your-api>/api/channels/webhook/my-channel/{runtimeAgentId}
```

- Slack: register signing secret via `POST /api/channels/slack/register/{id}`
- Verify signatures at NestJS before relay (see `channels.controller.ts`)

### Gateway pattern (Discord)

No public message webhook. Add a NestJS service (see `discord-gateway.service.ts`):

```text
POST /api/channels/discord/register/{runtimeAgentId}   # sidecar, X-Runtime-Secret
POST /api/channels/discord/unregister/{runtimeAgentId}  # shutdown
```

Wait for provider `READY` before returning success so desktop can skip duplicate local listeners.

---

## Step 5: Relay registration

Ensure desktop sets `NESTJS_URL` so `ChannelRelayClient` connects:

```text
wss://<nestjs>/api/channels/relay/{runtimeAgentId}?secret=
```

Without relay:

- Queue in `PendingChannelMessage` (cloud + Postgres agent)
- Or long-poll / local Gateway fallback (Telegram, Discord)

---

## Step 6: Tools UI card

Add integration card in `frontend/src/app/features/tools/`:

- `integration-card` + `platform-integrations.util.ts` setup steps
- Status polling via `GET /skills/my-channel/status/{runtimeAgentId}`
- Connected state: config form + **channel scope** panel (if applicable)

Follow Discord/Slack patterns in `tools.component.ts`.

---

## Step 7: DM policy, contacts, outbound guard

- Respect `open` / `allowlist` / `disabled` via `PolicyEnforcer`
- Register with contacts plugin (`ContactChannelSpec` on `SkillMeta`)
- Outbound send tool: restrict targets when skill enabled (effective channels + known senders + contacts)

Add identity field to contacts (`my_id` column pattern: `discord_id`, `slack_id`).

---

## Step 8: Legacy tool deprecation

If replacing a JSON tool in `nls/config/tools/`, filter it in `server/routes/admin.py` `get_agent_tools` when the bundled skill is enabled.

---

## Email-specific

Uses NestJS Resend APIs — see `email-channel` + `channels.controller.ts` `email/send`.

---

## Testing

1. Start desktop + verify relay online
2. Send test event (curl webhook or trigger provider event)
3. Confirm skill webhook receives payload (desktop logs)
4. Verify agent reply on provider
5. Test channel scope API + Tools UI toggles
6. Test outbound guard (send to disallowed ID should fail)

---

## Related

- [Relay protocol](../reference/relay-protocol.md)
- [Add a bundled skill](add-bundled-skill.md)
- [Discord integration](../guides/integrations/discord.md)
- [Slack integration](../guides/integrations/slack.md)
