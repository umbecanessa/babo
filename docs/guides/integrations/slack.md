# Slack integration

Connect a **Slack app** so users can @mention your agent in workspace channels and DM it.

**Skill name:** `slack-channel`

---

## What you get

- Two-way messaging in **workspace channels** the app has joined and you enable in Babo
- **DMs** with policy controls (`open`, `allowlist`, `disabled`)
- **Channel scope UI** in Tools — list channels, toggle listening, sync from Slack
- **Thread replies** via `thread_ts` on outbound sends
- **Contacts** integration — store `slack_id` for outbound targeting

---

## Prerequisites

| Requirement | Why |
|-------------|-----|
| **Slack app** with bot token (`xoxb-…`) | Install app to workspace |
| **Signing secret** | Verifies Events API payloads at NestJS |
| **Public HTTPS NestJS URL** | Slack Event Subscriptions Request URL |
| **Babo Desktop online** | Relay delivers events to the sidecar in real time |

**Runtime agent id:** Webhook and register URLs use `{runtimeAgentId}` — the Python directory name under `data/agents/`, visible on the agent card in **Dashboard** (**Agents** nav). See [Dashboard & fleet](../dashboard-and-fleet.md).

---

## Setup

### 1. Create the Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App**
2. **OAuth & Permissions → Bot Token Scopes** (minimum):

   | Scope | Purpose |
   |-------|---------|
   | `app_mentions:read` | @mention events |
   | `chat:write` | Outbound messages |
   | `channels:history` | Read public channel context |
   | `im:history` | DM history |
   | `channels:read` | List public channels |
   | `groups:read` | List private channels |
   | `channels:join` | Join channel when you enable scope in Babo |
   | `files:write` | File uploads (v2 upload API) |

3. **Event Subscriptions → Enable Events**

   **Request URL:**

   ```text
   https://<your-nestjs-host>/api/channels/webhook/slack/{runtimeAgentId}
   ```

   Replace `{runtimeAgentId}` with your agent's Python id (directory name under `data/agents/`).

   Slack sends `url_verification`; NestJS responds with the challenge automatically.

4. **Subscribe to bot events:**

   - `app_mention`
   - `message.im`
   - `member_joined_channel`
   - `member_left_channel`

5. **Install App to Workspace** → copy **Bot User OAuth Token**
6. **Basic Information → Signing Secret** → copy for Babo

### 2. Connect in Babo

**Tools → Integrations → Slack → Setup in Chat**, or call `slack_setup` with:

```json
{
  "bot_token": "xoxb-...",
  "signing_secret": "..."
}
```

This validates `auth.test`, saves config, registers the signing secret with NestJS, and connects the relay.

### 3. Configure policy and scope

Open the **Slack** integration card when connected:

| Area | Purpose |
|------|---------|
| **Config form** | Token, signing secret (masked), owner, DM policy |
| **Channel scope panel** | Enable channels, `@mention only`, **Sync channels** |

Invite the app to channels in Slack (`/invite @YourBot`), then **Sync channels** in Tools.

---

## Channel scope (two-way sync)

Same model as Discord — see [Channels & webhooks — Channel scope](../../architecture/channels-and-webhooks.md#channel-scope).

| Field | Meaning |
|-------|---------|
| `enabled_desired` | Enabled in Babo Tools |
| `platform_access` | App is a member of the channel in Slack |
| `effective_enabled` | Both true — bot listens here |
| `require_mention` | Guild/workspace channels: respond only on @mention (default: on) |

### Sync behavior

| Event | What happens |
|-------|----------------|
| **Sync channels** | Lists channels where the bot is already a member |
| **`member_joined_channel` / `member_left_channel`** | Auto-sync; newly joined channels can auto-enable |
| **First bulk sync after setup** | Channels listed but not auto-enabled until you toggle or join events fire |

When you **enable** a channel in Tools, Babo calls `conversations.join`. When you disable, it calls `conversations.leave`.

---

## Message flow

```text
Slack Events API
    → POST /api/channels/webhook/slack/{runtimeAgentId}
    → NestJS verifies X-Slack-Signature (signing secret registered by sidecar)
    → channel_message on relay WS
    → POST http://127.0.0.1:9222/skills/slack-channel/webhook/{runtimeAgentId}
    → PolicyEnforcer + scope check
    → Agentic loop
    → slack_send outbound
```

If desktop is offline, events may queue in NestJS `PendingChannelMessage` (same as Telegram) when the agent exists in Postgres.

---

## NestJS signing secret registration

The sidecar registers the signing secret so NestJS can verify Slack before relaying:

```http
POST /api/channels/slack/register/{runtimeAgentId}
X-Runtime-Secret: <RUNTIME_SHARED_SECRET>
Content-Type: application/json

{ "signing_secret": "..." }
```

On skill shutdown: `POST /api/channels/slack/unregister/{runtimeAgentId}`.

The Python skill webhook also verifies signatures when Slack headers are present on direct requests; relay copies from NestJS omit headers by design.

---

## Agent tools

| Tool | Purpose |
|------|---------|
| `slack_setup` | Validate token + signing secret |
| `slack_send` | Outbound text/files (`channel_id`, optional `thread_ts`) |
| `contacts` | `slack_id` field |
| `skill_configure` | Owner, DM policy, allowlist |

### Outbound guard

When enabled, sends are restricted to effective scoped channels, known senders, contacts, and allowlist — same pattern as Discord.

---

## Channel scope API (runtime)

Base: `http://127.0.0.1:9222/skills/slack-channel/`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/channels/{agentId}` | List scoped channels |
| POST | `/channels/{agentId}/sync` | Refresh from Slack |
| PATCH | `/channels/{agentId}/{channelId}` | Update desired scope |
| GET | `/status/{agentId}` | Connection summary |

---

## Legacy `slack` JSON tool

The older **`slack`** tool in `nls/config/tools/slack.json` is **deprecated**. When `slack-channel` is enabled, it is hidden from the agent tools list. Use `slack_send` instead.

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Request URL verification fails | NestJS public URL correct? Signing secret registered (`slack/register`)? |
| No events | App installed? Subscribed events? Bot invited to channel? |
| Ignored messages | Channel not **effective enabled**? `@mention only` on? |
| `401 Invalid Slack signature` | Re-run setup to refresh signing secret on NestJS |

---

## Related

- [Integrations overview](index.md)
- [Discord](discord.md)
- [Channels & webhooks](../../architecture/channels-and-webhooks.md)
- [NestJS Channels API examples](../../architecture/examples/nestjs-channels-api.md)
