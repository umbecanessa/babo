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

## Step 9: `manage_channel` / `channel_manage`

Agents call the unified **`channel_manage`** tool (`nls/tools/agent_tools/channel_manage.py`). Dispatch lives in `nls/runtime/channel_manage.py`.

**Bundled skills:** implement `manage_channel(agent_id, action, params)` on the adapter and optionally `channel_manage_actions()` for the action list. See `discord-channel/adapter.py` and `slack-channel/adapter.py`.

**Custom skills:** either implement `manage_channel` on the adapter or register at skill load:

```python
from nls.runtime.channel_manage import register_channel_manage_handler

async def my_manage(agent_id: str, action: str, params: dict) -> tuple[bool, str]:
    ...

my_manage.manage_actions = ["sync", "list", "enable"]  # optional
register_channel_manage_handler("my-channel-key", my_manage)
```

Or from `SkillContext`:

```python
ctx.register_channel_manage("my-channel-key", my_manage)
```

Never expose raw credentials in tool results — read tokens server-side from saved skill config.

---

## Step 8: Per-agent config & REST API routing

Each agent stores channel credentials under:

```text
data/skills/{skill-dir}/agents/{runtimeAgentId}.json
```

Bundled skills use keys from `nls/runtime/channel_policy_profiles.py` (`discord` → `discord-channel`, etc.). **Custom skills** use the same layout: folder `{name}-channel`, channel key `{name}` (see `resolve_channel_skill_dir()` in `nls/runtime/channel_agent_config.py`).

### Minimum for a custom channel author

| Requirement | Detail |
|-------------|--------|
| Skill package | `data/skills/myplatform-channel/` (or bundled under `nls/skills/bundled/`) |
| Per-agent config | `agents/{runtimeAgentId}.json` with `"enabled": true` and your credential fields |
| Admin surface | `manage_channel` on the adapter **or** `register_channel_manage_handler("myplatform", …)` |
| Optional REST hints | `"rest_api_hosts": ["api.myplatform.com/v1"]` in agent config (see below) |

No code changes to Babo core are required for discovery — configured custom channels appear in `channel_inspect(action='list')` once the skill is loaded and agent config exists.

### `rest_api_hosts` (optional)

When agents might call your vendor REST API via `bash`/`curl`, declare matchable hosts in per-agent config so Babo can nudge toward `channel_manage` instead of raw scripts:

```json
{
  "enabled": true,
  "bot_token": "...",
  "rest_api_hosts": [
    "api.myplatform.com/v1",
    "re:api\\.myplatform\\.com/.+"
  ]
}
```

- Plain strings are substring-matched (case-insensitive).
- Prefix with `re:` for a regex pattern.

Built-in patterns (no config needed) cover common vendors — see `nls/runtime/channel_api_routing.py`:

| Channel key | Default REST match |
|-------------|-------------------|
| `discord` | `discord.com/api` |
| `slack` | `slack.com/api` |
| `telegram` | `api.telegram.org` |
| `whatsapp` | `graph.facebook.com/.../whatsapp` |

### Agent guidance (soft — not blocked)

When a configured channel’s REST surface appears in a `bash` command:

1. **Soft suffix** on the tool result — `[CHANNEL HINT]` steering to `channel_manage` / `channel_inspect`
2. **Post-tool breadcrumb** — `[BREADCRUMB]` on the next loop turn
3. **Cryptex / static tool hints** — `bash`, `channel_inspect`, and tools ring text

Raw REST is still allowed when `channel_manage` has no matching action (probes, edge cases). There is **no hard preflight block**.

Implementation: `nls/runtime/channel_api_routing.py` · wired from `bash.py`, `shell_hints.py`, `nls/agentic/breadcrumbs.py`.

Register bundled profiles in `CHANNEL_POLICY_PROFILES` when shipping a first-party channel; custom skills only need the skill folder + agent JSON + optional `rest_api_hosts`.

---

## Step 10: Legacy tool deprecation

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
7. Test `channel_manage(channel='...', action='help')` lists expected actions

---

## Related

- [Relay protocol](../reference/relay-protocol.md)
- [Add a bundled skill](add-bundled-skill.md)
- [Discord integration](../guides/integrations/discord.md)
- [Slack integration](../guides/integrations/slack.md)
