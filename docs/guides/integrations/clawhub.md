# ClawHub integration

**ClawHub** is Babo's community skill marketplace — discover, install, and manage shared skills.

---

## Browse in the UI

**Tools → Community Skills & Extensions**

- Tab: **Skills** (ClawHub)
- Search by keyword
- Category chips for filtering
- **Install** button per result

Installed skills appear under **Installed skills** with configuration forms.

---

## Agent tool

The agent can autonomously extend itself:

**Tool:** `clawhub`

| Action | Purpose |
|--------|---------|
| `search` | Find skills by query |
| `install` | Install by slug |
| `list` | Show installed ClawHub skills |

Example:

> Search ClawHub for a skill that monitors RSS feeds and install the best match.

---

## Backend proxies

ClawHub is proxied in **two places** depending on where you run:

| Where | Base path | Best for |
|-------|-----------|----------|
| **Python runtime** | `http://127.0.0.1:9222/api/clawhub/*` | Desktop, agent `clawhub` tool |
| **NestJS** | `https://<api>/api/clawhub/*` | Hosted web UI, per-user install DB |

See [ClawHub dual proxy](../../architecture/clawhub-proxy.md) for install paths and when to use each.

---

## AgentSkills compatibility

Installed ClawHub packages using the AgentSkills (`SKILL.md`) format integrate automatically — instructions load into context, CLI wrappers generate as needed.

---

## Crystallization

Heavily used ClawHub skills can be **crystallized** into native plugins. See [Tools & skills](../tools-and-skills.md).

---

## Related

- [MCP integration](mcp.md)
- [Tools & skills](../tools-and-skills.md)
