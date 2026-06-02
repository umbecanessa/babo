# Extension guide

How to extend Babo as a contributor or integrator.

---

## Choose your extension type

| Goal | Guide | Difficulty |
|------|-------|------------|
| New capability for the agentic loop | [Add an agent tool](add-agent-tool.md) | Medium |
| New installable package (tools + API + UI) | [Add a bundled skill](add-bundled-skill.md) | High |
| New messaging surface | [Add a channel integration](add-channel-integration.md) | High |
| External tool server | [MCP & ClawHub](mcp-and-clawhub.md) | Low–Medium |
| JSON-only inner-loop tool | `nls/config/tools/README.md` in the repository | Low |

---

## Prerequisites

1. [Local development](../development/local-development.md) stack running
2. `NLS_PRODUCT_MODE=1`
3. Read [Deployment topologies](../architecture/deployment-topologies.md) — know where your code runs (desktop Python vs NestJS)

---

## Design rules

1. **Agent tools** must be async-safe and return strings (or structured JSON strings) for the LLM.
2. **Skills** that need Node.js use `ctx.on_startup` to spawn bridges (see WhatsApp).
3. **Channel webhooks** must target NestJS, not desktop IP.
4. **No secrets** in repo — use env vars and per-agent skill config.
5. **Product mode** — use `NLS_PRODUCT_MODE=1`; rely on OpenAI-compatible HTTP inference only.

---

## Verification checklist

```bash
export NLS_PRODUCT_MODE=1
python -m compileall server nls -q
python -m pytest tests/ -q
cd backend && npx nest build
cd frontend && npm run build
```

---

## Related

- [Product scope](../development/product-scope.md)
- [Contributing](https://github.com/umbecanessa/babo/blob/main/CONTRIBUTING.md)
- [Reference index](../reference/index.md)
