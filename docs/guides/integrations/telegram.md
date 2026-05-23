# Telegram integration

Connect a **Telegram bot** so users (and groups) can talk to your agent on Telegram.

**Skill name:** `telegram-channel`

---

## Setup

Setup type: **conversational** — the agent walks you through:

1. Open Telegram, message **@BotFather**
2. Create a new bot (`/newbot`), copy the **HTTP API token**
3. Paste the token in Babo (Tools page or chat when prompted)
4. Optional: set webhook URL for inbound messages (Babo configures this in server mode)

---

## Configuration

| Field | Purpose |
|-------|---------|
| **Bot token** | From @BotFather |
| **DM policy** | `open`, `allowlist`, `disabled` |
| **Group policy** | Group mention requirements |
| **Owner identity** | Your Telegram user id |

---

## Groups

In groups, the bot typically responds when **mentioned** or replied to — configurable via group policy.

---

## Usage

- DM the bot directly for private agent chat
- Add bot to group for team scenarios (mind policy settings)

Same memory as web chat — Telegram is just another channel into the brain.

---

## Related

- [Integrations overview](index.md)
