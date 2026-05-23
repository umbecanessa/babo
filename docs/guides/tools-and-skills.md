# Tools & skills

The **Tools** page is your agent's capability control center.

**Route:** `/tools/:agentId`

---

## Page sections

### Integrations

One-click cards for bundled channel skills:

- Email
- Telegram
- WhatsApp
- Google Workspace

Each card shows connection status, setup button, and settings. See [Integrations](integrations/index.md).

### Installed skills

Skills loaded for this agent — bundled, ClawHub-installed, or AgentSkills (`SKILL.md` format). Configure via schema forms or conversational setup.

### Agent tools

Built-in tools the loop can call (read, bash, browser, plan, team, etc.). Expand cards for parameter schemas.

### Connected extensions

Active **MCP server** connections. Disconnect or inspect health here.

### Community Skills & Extensions

Unified search across:

- **ClawHub** — community skill packages
- **Extensions** — MCP servers from the PulseMCP ecosystem (20,000+)

Filter by category, install or connect in one click.

---

## Skill onboarding types

| Type | Example | UX |
|------|---------|-----|
| `auto` | Email channel | Zero-config provisioning |
| `qr_pair` | WhatsApp | Scan QR in modal |
| `conversational` | Telegram | Agent guides @BotFather steps |
| `ui` | Google Workspace | OAuth modal in app |
| `manual` | MCP client | Enter command or URL |

---

## AgentSkills format

Babo supports the open [AgentSkills](https://agentskills.io/) spec:

- YAML frontmatter + Markdown instructions
- Requirements gating (`requires_bins`, `requires_env`, OS)
- Auto-generated CLI wrapper so the agent calls binaries naturally

---

## MCP client skill

The bundled **mcp-client** skill:

- Connects stdio or HTTP/SSE MCP servers
- Injects remote tools as first-class agent tools
- Auto-reconnects saved servers on startup
- Searches PulseMCP registry from the Tools page

Agent-facing tool: `mcp_manage` (search, connect, list, disconnect).

---

## ClawHub

Agent tool: `clawhub` with actions `search`, `install`, `list`.

Backend proxy: NestJS `/api/clawhub/*` handles auth to the external registry.

See [ClawHub integration](integrations/clawhub.md).

---

## Skill crystallization

When an instruction-based skill is used heavily with high success:

- Minimum ~15 uses, ≥85% success, ≥65% myelination score
- Agent can call `crystallize_skill` to compile it into a native Python plugin
- Faster execution on repeat tasks

---

## Built-in tool reference

| Category | Tools |
|----------|-------|
| **Files** | `read`, `write`, `edit`, `grep`, `glob`, `list_dir`, `move_file` |
| **Shell** | `bash` |
| **Web** | `browser`, `web_search`, `web_fetch` |
| **Code** | `semantic_search` |
| **Orchestration** | `plan`, `team`, `delegate_ring`, `task_complete` |
| **Memory** | `wm` |
| **Comms** | `contacts`, `email_history` |
| **Media** | `vision`, `screenshot`, `eyes` |
| **Scheduling** | `scheduler`, `poller` |
| **Meta** | `discover_tools`, `get_tool_schema`, `skill_configure`, `clawhub` |
| **Output** | `offer_download` |

Channel skills add their own tools (e.g. `whatsapp_send`, Gmail read/write).

---

## Related

- [Integrations overview](integrations/index.md)
- [Agentic loop & plans](agentic-loop-and-plans.md)
- [Configuration](../configuration/index.md)
