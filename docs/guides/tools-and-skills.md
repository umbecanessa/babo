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
- **Discord**
- **Slack**
- Google Workspace

Each card shows connection status, setup button, and settings. Connected **Discord** and **Slack** cards include a **Channel scope** panel (sync, enable toggles, @mention policy). See [Integrations](integrations/index.md).

### Installed skills

Skills loaded for this agent:

| Kind | Setup UX |
|------|----------|
| **Bundled channel skills** (email, Telegram, …) | `skill_configure` + schema forms |
| **ClawHub / AgentSkill** (`SKILL.md`) | **Instruction-only** — read `SKILL.md`, set env vars, run via `bash()` / Python; **do not** use `skill_configure` |

On Windows, API-heavy instruction skills (Discord admin, etc.) work best with a small **Python deploy script** and JSON payloads on disk — see [Platform shell on Windows](../architecture/platform-shell-and-windows.md).

Policy: `nls/skills_setup_policy.py`.

### Agent tools

Built-in tools the loop can call (read, bash, browser, plan, team, squad, **set_job** (solo Home), **channel_manage**, **channel_history**, etc.). Expand cards for parameter schemas.

| Tool | Notes |
|------|-------|
| **`set_job`** | Solo agents on Home only — persist owner-confirmed Job charter after `ask_user()` |
| **`channel_manage`** | Channel-agnostic admin (sync scope, inspect config, grant access) |
| **`channel_history`** | Read session + ambient history for a channel thread |

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
| `conversational` | Telegram, Discord, Slack | Agent guides setup; paste tokens in chat |
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
| **Shell** | `bash` (PowerShell 7 on Windows — see [Platform shell](../architecture/platform-shell-and-windows.md)) |
| **Dependencies** | `project_install`, `server_install` (pip/npm routing; plan-aware) |
| **Web** | `browser`, `web_search`, `web_fetch` |
| **Code** | `semantic_search` |
| **Orchestration** | `plan`, `team`, `delegate_ring`, `task_complete` |
| **Memory** | `wm` |
| **Comms** | `contacts`, `email_history`, `discord_send`, `slack_send` (when channel skills enabled) |
| **Media** | `vision`, `screenshot`, `eyes` |
| **Scheduling** | `scheduler`, `poller` |
| **Meta** | `discover_tools`, `get_tool_schema`, `skill_configure`, `clawhub` |
| **Output** | `offer_download` |

Channel skills add their own tools (e.g. `whatsapp_send`, `discord_send`, `slack_send`, Gmail read/write).

### Legacy JSON tools

`nls/config/tools/discord.json` and `slack.json` are **deprecated** when the bundled `discord-channel` / `slack-channel` skills are enabled for an agent — they are filtered from the Tools list. Use channel skill send tools instead.

---

## Related

- [Integrations overview](integrations/index.md)
- [Agentic loop & plans](agentic-loop-and-plans.md)
- [Configuration](../configuration/index.md)
