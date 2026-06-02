# WhatsApp integration

Connect your agent to **WhatsApp** using your personal account — no separate business number required.

**Skill name:** `whatsapp-channel`

---

## Setup

1. Open **Tools → Integrations → WhatsApp**
2. Click **Connect** — a **QR code** appears
3. On your phone: WhatsApp → Settings → Linked devices → Link a device
4. Scan the QR code
5. Confirm pairing succeeded in Babo

Setup type: `qr_pair`.

---

## Configuration

| Field | Purpose |
|-------|---------|
| **Owner identity** | Your phone number (required) |
| **Linked phone** | Set automatically after pairing |
| **DM policy** | `open`, `allowlist`, or `disabled` |
| **Group policy** | Control group message handling |

Use **skill_configure** in chat or the Tools settings form to adjust policies.

---

## Architecture

- **Baileys** Node.js bridge runs as a sidecar (default port `9223`)
- Outbound messages via bridge API
- Inbound messages → webhook → agent loop
- Bridge managed by Babo (start, health check, restart)

---

## Usage

Once connected, message your WhatsApp number from another device — the agent replies using full memory and tools (subject to policy).

Ask in chat:

> Send a WhatsApp message to [contact] saying the deploy finished.

The agent uses WhatsApp tools exposed by the skill.

---

## Related

- [Integrations overview](index.md)
- [Contacts](../agentic-loop-and-plans.md) — `contacts` tool
