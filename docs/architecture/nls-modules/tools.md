# Tools package (`nls/tools`)

**Agent-facing tools** for the agentic loop: coding, browser, plans, scheduler, MCP, vision, ClawHub.

**Entry:** `nls/tools/agent_tools/__init__.py` — `create_coding_tools()`, `tools_to_openai_schema()`, `execute_tool_call()`.

---

## Layout

| Path | Role |
|------|------|
| `agent_tools/base.py` | `AgentTool`, `ToolResult` protocol |
| `agent_tools/bash.py`, `file_*.py` | Workspace file ops |
| `agent_tools/browser.py`, `live_browser.py` | Web automation |
| `agent_tools/plan.py`, `team.py` | Plans and teams |
| `agent_tools/scheduler.py` | `SchedulerManager`, cron jobs |
| `agent_tools/clawhub.py` | Marketplace tool |
| `agent_tools/semantic_search.py` | Embeddings (optional GPU worker) |
| `mcp_bridge.py` | `McpBridge`, `McpToolWrapper` |
| `visual_cortex.py`, `visual_model.py` | Image understanding |
| `skill_manager.py` | Enabled skills → tool wrappers |
| `tool_setup.py` | Onboarding flows |

---

## Key factories

```python
from nls.tools.agent_tools import create_coding_tools, tools_to_openai_schema
```

`SharedCWD` and `FileStateCache` coordinate multi-tool file access per agent.

---

## Disk artifacts

- `enabled_tools.json` — per agent
- `tool_experience.json` — agency learning
- Scheduler job store (under data dir)

---

## Server integration

| Module | Usage |
|--------|-------|
| `main.py` | `SchedulerManager.start()` |
| `routes/admin.py` | Tool catalog alignment |
| `routes/skills.py` | `request_restart`, skill CLI wrappers |
| `services/skill_loader.py` | `SkillCLIWrapperTool` |

---

## Related

- [Tools system](../tools-system.md)
- [Extension: add agent tool](../../extension/add-agent-tool.md)
