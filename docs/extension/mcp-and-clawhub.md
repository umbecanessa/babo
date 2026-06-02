# MCP & ClawHub

Two ways to add **external** capabilities without shipping Python in core.

---

## MCP (Model Context Protocol)

**Skill:** `nls/skills/bundled/mcp-client/`

### User flow

1. Tools → Connected extensions → Add MCP server
2. Enter command (stdio) or URL (SSE/HTTP)
3. Skill discovers tools dynamically
4. Tools appear as `mcp_{server}_{tool_name}` in agentic loop

### Architecture

```text
Agentic loop → MCP proxy tool → mcp-client skill → subprocess/HTTP → MCP server
```

Desktop Electron may stub MCP IPC (`mcp:list` in preload) — primary path is Python skill.

### Security

- MCP server runs with **desktop OS permissions**
- Only connect trusted servers
- Use allowlists in production

Guide: [MCP integration](../guides/integrations/mcp.md)

---

## ClawHub

**Backend:** `backend/src/clawhub/`  
**UI:** Tools → Community search

### Install flow

```text
UI search → GET /api/clawhub/search
Install → POST /api/clawhub/install
       → DB ClawhubSkill row
       → pushSkillInstall(slug, files) over relay
       → written to data/skills/{slug}/
       → SkillLoader picks up on next load / refresh
```

### Uninstall

`DELETE /api/clawhub/uninstall/:slug`

### Offline desktop

Install queues until relay connects; or user installs from desktop-only path via skills API.

Guide: [ClawHub](../guides/integrations/clawhub.md)

---

## AgentSkill format (ClawHub packages)

Many community skills ship `SKILL.md` only:

- Parsed by `nls/skills/agentskill_parser.py`
- Instructions injected into system prompt
- Optional `requires_bins` → CLI wrappers in `tool_setup.py`

---

## Crystallize to native plugin

Frequent AgentSkill use → `crystallize` tool generates Python module.

See [Inference](../architecture/inference.md).

---

## Related

- [Skills system](../architecture/skills-system.md)
- [Tools & skills guide](../guides/tools-and-skills.md)
