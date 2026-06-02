# MCP integration

**MCP (Model Context Protocol)** is the open standard for connecting tool servers to AI agents. Babo includes a bundled **mcp-client** skill.

---

## What you get

- Connect any MCP server (**stdio** or **HTTP/SSE**)
- Remote tools appear as **first-class agent tools** in the loop
- **PulseMCP** registry search — 20,000+ public servers
- **Auto-reconnect** saved servers on agent restart
- MCP **resources** and server instruction injection

---

## Connect from the UI

1. **Tools → Community Skills & Extensions**
2. Tab: **Extensions**
3. Search or browse categories
4. Click **Connect** on a server
5. Connected servers appear under **Connected Extensions**

---

## Connect manually

Agent tool: **`mcp_manage`**

| Action | Purpose |
|--------|---------|
| `search` | Find servers in PulseMCP |
| `connect` | Start a server by name or config |
| `list` | Show active connections |
| `disconnect` | Stop a server |

Example chat:

> Connect the filesystem MCP server for this workspace.

---

## AgentSkills + MCP discovery

Installed skills may ship `mcp_servers.json`. Babo **discovers** these on load and offers them in the Tools UI.

---

## Security

MCP servers run with the permissions of the Babo runtime process. Only connect servers you trust. Use allowlists for production deployments.

---

## Related

- [ClawHub](clawhub.md)
- [Tools & skills](../tools-and-skills.md)
