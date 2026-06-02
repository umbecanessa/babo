# Add an agent tool

Agent tools are invoked by the **agentic loop** during chat.

**Location:** `nls/tools/agent_tools/`  
**Registration:** `nls/tools/tool_setup.py`

---

## Step 1: Implement AgentTool

Create `nls/tools/agent_tools/my_tool.py`:

```python
from nls.tools.agent_tools.base import AgentTool

class MyTool(AgentTool):
    name = "my_tool"
    description = "What the tool does for the model"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "..."},
        },
        "required": ["query"],
    }

    def __init__(self, workspace: str, **kwargs):
        self.workspace = workspace

    async def execute(self, query: str, **kwargs) -> str:
        # Return a string the model can read
        return f"Result for {query}"
```

---

## Step 2: Register in create_coding_tools or setup_tools

**Option A — always available** (`nls/tools/agent_tools/__init__.py`):

```python
from .my_tool import MyTool

def create_coding_tools(...):
    tools = { ... }
    tools["my_tool"] = MyTool(workspace=workspace)
    return tools
```

**Option B — conditional** (`tool_setup.py`):

```python
tools["my_tool"] = MyTool(workspace=agent_dir / "workspace")
```

---

## Step 3: Refresh schemas

`AgentRuntime.refresh_tools()` runs after skill changes. For static tools, restart runtime or reload agent.

---

## Step 4: Test

1. Enable tool in Tools page (if gated) or verify always-on
2. Chat: ask agent to use `my_tool`
3. Watch WS events: `tool_start`, `tool_end`

---

## Tool with LLM callback

Plan tool pattern — pass `verify_fn` / `vllm_client` from `setup_tools` for sub-calls.

See `nls/tools/agent_tools/plan.py`.

---

## Permissions (desktop)

Sensitive tools may need `PermissionManager` approval in Electron — see [IPC reference](../desktop/ipc-reference.md).

---

## Alternative: JSON tool (inner loop only)

For agency/inner-loop only, add `nls/config/tools/my_tool.json` + executor mapping in `nls/engine/tool_loader.py`.

Not exposed to main chat unless also added as AgentTool.

---

## Related

- [Tools system](../architecture/tools-system.md)
- [Agentic loop](../architecture/agentic-loop.md)
