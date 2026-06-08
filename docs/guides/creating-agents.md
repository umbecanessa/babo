# Creating agents

Create new Babo agents from **genesis templates** — starting personalities, values, and default tool/skill bundles.

**Route:** `/create` · **From dashboard:** **New Agent** or empty fleet card

---

## Genesis templates

Each template (`nls/config/genesis/`) seeds:

- Identity and narrative starting point
- Default hormone baselines and drives
- Tool registry and bundled skills enabled at birth
- Standard Cryptex ring structure (empty knowledge, fresh working memory)

Templates differ in tone (professional, creative, technical) and default orchestration depth. Pick the closest match — the agent diverges quickly from your conversations.

---

## Creation flow

1. Open **Create** from the dashboard or nav
2. Choose a genesis path (template card)
3. Enter a **display name** — becomes the agent identity label
4. Optional: set initial model in the creation wizard model picker
5. Confirm — Babo allocates a runtime agent id, seeds `data/agents/{id}/`, and registers with NestJS

Creation takes a few seconds. You are redirected to **Chat** when complete.

---

## What gets created on disk

| Path | Contents |
|------|----------|
| `identity/` | Name, genesis id, owner linkage |
| `cryptex/` | Empty memory rings |
| `session_meta.json` | Session defaults (model, routes) |
| `teams/`, `plans/` | Empty until first project |

See [Data directory](../reference/data-directory.md) and [Genesis architecture](../architecture/genesis.md).

---

## Multiple agents

One Babo install supports many agents. Each has:

- Isolated memory and workspace
- Separate chat threads and Projects board
- Own integrations on the Tools page

Manage the fleet from [Dashboard & fleet](dashboard-and-fleet.md).

---

## Model defaults at creation

The creation flow model picker sets the install default before the agent exists. After creation, per-agent defaults are stored in `session_meta.json` and editable via chat model picker → **Set as agent default**.

Hybrid installs show **Local / Popular / More** groups — see [Chat → Model picker](chat.md#model-picker).

---

## Related

- [Quickstart](../getting-started/quickstart.md)
- [Core concepts](../getting-started/concepts.md)
- [Genesis templates](../architecture/genesis.md)
- [Dashboard & fleet](dashboard-and-fleet.md)
