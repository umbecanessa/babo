# Tools system

Agent tools are what the **agentic loop** invokes. Two parallel registries exist — do not confuse them.

---

## 1. Agentic loop tools (primary)

**Location:** `nls/tools/agent_tools/`  
**Wiring:** `nls/tools/tool_setup.py` → `AgentRuntime._initialize_tools()`

### Base class

`nls/tools/agent_tools/base.py` — `AgentTool` with:

- `name`, `description`, `parameters` (JSON schema)
- `async execute(self, **kwargs) -> str`

### Core tool set

From `create_coding_tools()` in `nls/tools/agent_tools/__init__.py`:

| Category | Tools |
|----------|-------|
| Files | read, write, edit, grep, glob, list_dir, delete, move |
| Shell | bash |
| Web | web_search, web_fetch, browser |
| Code | semantic_search, offer_download |
| Meta | discover_tools, scheduler, poller, vision (optional) |

### Added in setup_tools

| Tool | Module | Role |
|------|--------|------|
| plan | `plan.py` | Structured plans |
| task_complete | | Mark plan steps done |
| team | `team.py` | Sub-agent orchestration |
| delegate_ring | | Delegate communication |
| contacts | | Cross-channel address book |
| set_job | `set_job.py` | Owner Job charter (Home solo) |
| channel_manage | `channel_manage.py` | Channel admin dispatch |
| channel_inspect | `channel_inspect.py` | Read-only channel status |
| channel_history | `channel_history.py` | Thread + ambient history |
| chat_history | `chat_history.py` | Prior session search |
| squad | `squad.py` | Fleet inbox + coordination |
| skill_configure | | Skill settings |
| skill_install | | Install skill packages |
| request_restart | | Runtime restart request |
| MCP proxies | `mcp_*.py` | Dynamic MCP tools |

### setup_tools pipeline

```text
create_coding_tools(agent_dir/workspace)
  → inject file ledgers into write/edit
  → plan tool with LLM verify callbacks
  → attach SchedulerManager (shared app state)
  → _inject_skill_tools(skill_loader, enabled_skills)
  → MCP wiring from mcp-client skill
  → return (tools, schemas, scheduler, team_manager)
```

---

## 2. JSON tool registry (agency / inner loop)

**Manifests:** `nls/config/tools/*.json` (50+ tools)  
**Loader:** `nls/engine/tool_loader.py`  
**Consumer:** `AgencyEngine` via `factory.py`

Used for **proactive** inner-loop tool use (NLSTool executors: shell, http, file_*, `pdf_tools`, `docx_tools`, `docker`, `cron_scheduler`, …), not the main chat OpenAI schema set.

These tools are **not** listed on the Tools page agent-tools grid unless explicitly bridged. User-facing agent loop tools live in section 1 above.

Documented in `nls/config/tools/README.md`.

---

## 3. Built-in IDE filesystem tools

**Module:** `nls/engine/tools_builtin.py`  
**Routes:** `server/routes/filesystem.py`

Separate from agentic loop — powers **Projects → Files** workspace explorer (former standalone IDE tab).

---

## OpenAI schema export

`AgentRuntime.refresh_tools()` rebuilds function definitions from `_agent_tools` for the inference API.

---

## Scheduler & pollers

**Module:** `nls/tools/agent_tools/scheduler.py`

- Cron-style jobs per agent
- HTTP pollers
- `[AGENT_MSG|...]` injections → consciousness scheduler

---

## MCP tools

**Skill:** `mcp-client`

1. User connects MCP server (command or URL)
2. Skill discovers tools dynamically
3. Proxies appear in agent tool dict as `mcp_{server}_{tool}`

---

## Crystallize (native plugin)

**Tool:** `nls/tools/agent_tools/crystallize.py`

Converts frequent AgentSkill into generated Python under `data/skills/` — UI badge "NLS Plugin".

See [Inference](inference.md).

---

## Platform shell / `bash()` on Windows

On macOS/Linux, `bash()` runs `/bin/bash`. On **Windows**, it runs **PowerShell 7** (bundled or PATH `pwsh`), with Windows PowerShell 5.1 as fallback. Resolution: `nls/platform_shell.py`; execution: `nls/tools/agent_tools/bash.py`. Bare `curl` in PS commands is rewritten to `curl.exe`.

See [Platform shell on Windows](platform-shell-and-windows.md).

---

## Install policy (`project_install` / `server_install`)

| Tool | Target | When |
|------|--------|------|
| `project_install` | Project venv / `package.json` | App dependencies the user is building |
| `server_install` | Babo agent runtime (PyPI) | Extending Babo's own capabilities (`for_agent_runtime=True`) |

`bash()` auto-routes `pip install` and `npm/pnpm/yarn install` to the appropriate tool. During an active plan with a locked tech stack, `server_install` is blocked unless `for_agent_runtime=True` — rules in `nls/tools/agent_tools/install_policy.py`.

---

## Guardrails registry

`AgentGuardrailsRegistry` (`nls/tools/agent_tools/guardrails_registry.py`) appends contract/validation failures to `data/agents/{id}/guardrails_registry.jsonl`. Recent entries are injected into orchestrator Cryptex and new delegate SubCryptex instructions so sub-agents inherit prior tool mistakes.

Wired from the agentic loop via `hooks.guardrails_registry` (`nls/agentic/loop.py`).

---

## Extension

[Add an agent tool](../extension/add-agent-tool.md)

---

## Related

- [Agentic loop](agentic-loop.md)
- [Skills system](skills-system.md)
- [Platform shell on Windows](platform-shell-and-windows.md)
