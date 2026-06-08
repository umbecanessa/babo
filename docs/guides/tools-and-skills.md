# Tools & skills

The **Tools** page is your agent's capability control center.

**Route:** `/tools/:agentId`

---

## Page sections

### Pending reviews

When the agent proposes **crystallizing** a heavily used instruction skill, approval cards appear at the top of the Tools page. Review success rate and usage stats before approve/deny.

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

Built-in tools the agentic loop can call. Expand cards on the Tools page for parameter schemas. Full categorized list: [Built-in tool reference](#built-in-tool-reference) below.
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

Tools below are in the **agentic loop** schema (`nls/tools/agent_tools/`). They appear on the Tools page and in chat tool cards.

### Files, shell, web, code

| Category | Tools |
|----------|-------|
| **Files** | `read`, `write`, `edit`, `grep`, `glob`, `list_dir`, `move_file` |
| **Shell** | `bash` (PowerShell 7 on Windows — see [Platform shell](../architecture/platform-shell-and-windows.md)); rewrites `python`/`python3` to the project `.venv` when present |
| **Dependencies** | `project_install`, `server_install` (pip/npm routing; plan-aware; `install_dir` avoids double-nesting when CWD is already the target folder) |
| **Web** | `browser`, `web_search`, `web_fetch` |
| **Code** | `semantic_search` |
| **Comms** | `contacts`, `email_history` (+ channel skill send tools when enabled) |
| **Media** | `vision`, `screenshot`, `eyes` |
| **Scheduling** | `scheduler`, `poller` |
| **Output** | `offer_download` |

### Orchestration & memory

| Category | Tools |
|----------|-------|
| **Orchestration** | `plan`, `team`, `delegate_ring`, `task_complete` |
| **Memory** | `wm` |
| **Meta** | `discover_tools`, `get_tool_schema`, `skill_configure`, `clawhub`, `crystallize_skill`, `skill_install`, `request_restart` |

### Job, channels & chat admin

| Tool | Notes |
|------|-------|
| **`set_job`** | Solo agents on Home only — persist owner-confirmed Job charter after `ask_user()` |
| **`channel_manage`** | Channel-agnostic admin (sync scope, inspect config, grant access) — **preferred over raw vendor REST/curl** when the channel is configured |
| **`channel_inspect`** | Read-only channel/skill status (squad lead may pass `target_agent_id`) |
| **`channel_history`** | Read session + ambient history for a channel thread |
| **`chat_history`** | Search prior Home chat sessions for context |

### Squad tools (fleet)

When an agent is a **squad lead** or member, additional tools apply:

| Tool | Role |
|------|------|
| **`squad`** | Inbox propose/approve, member coordination |
| **`squad_setup`** | Configure squad membership and lead |
| **`squad_message`** | Message squad members |
| **`squad_escalate`** | Escalate blocked work to lead |
| **`squad_report_done`** | Mark squad todo complete |

See [Job, Trust & Squads](job-trust-and-squads.md).

Channel skills add send tools (e.g. `whatsapp_send`, `discord_send`, `slack_send`, Gmail read/write).

**Configured channel + bash:** if a `bash` command hits a vendor REST URL for a channel this agent has linked, the result includes a soft `[CHANNEL HINT]` (and may trigger a loop breadcrumb) pointing at `channel_manage` — not a hard block. Custom channel authors declare match hosts via `rest_api_hosts` in per-agent skill config; see [Add a channel integration](../extension/add-channel-integration.md#step-8-per-agent-config--rest-api-routing).

### Inner-loop JSON registry (not agentic loop)

These live in `nls/config/tools/*.json` and are used by the **AgencyEngine / inner loop** for proactive autonomy — they do **not** appear in the main chat OpenAI tool schema:

| Examples | Purpose |
|----------|---------|
| `pdf_tools`, `docx_tools` | Document processing in background loops |
| `docker` | Container ops in inner loop |
| `cron_scheduler` | Scheduled inner-loop jobs |

See [Tools system](../architecture/tools-system.md#2-json-tool-registry-agency-inner-loop) and `nls/config/tools/README.md`.

### Legacy JSON tools

`nls/config/tools/discord.json` and `slack.json` are **deprecated** when the bundled `discord-channel` / `slack-channel` skills are enabled — filtered from the Tools list. Use channel skill send tools instead.
---

## Related

- [Integrations overview](integrations/index.md)
- [Agentic loop & plans](agentic-loop-and-plans.md)
- [Configuration](../configuration/index.md)
