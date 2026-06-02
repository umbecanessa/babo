# Ledger package (`nls/ledger`)

Agent **identity chain** and SQLite **knowledge graph**: genesis minting, Merkle blocks, soul import/export.

---

## Key files

| File | Key API |
|------|---------|
| `manifest.py` | `load_manifest`, `save_manifest`, `append_block`, `initialize_agent` |
| `domain_db.py` | `DomainDB` — facts, blocks, projects, credentials vault |
| `merkle.py` | `verify_chain`, `verify_soul_integrity`, `hash_file` |
| `genesis.py` | `create_agent_from_genesis`, `list_genesis_templates_detail` |
| `soul_package.py` | `export_soul`, `import_soul`, `fork_at_height` |

---

## Disk layout

| Path | Role |
|------|------|
| `ledger.yaml` | Chain head metadata |
| `knowledge.db` | DomainDB SQLite |
| `epochs/`, `deltas/` | Block payloads |
| `events/` | Event log segments |
| `data/genesis/{version}/` | Template bundles (server-wide) |

---

## Server integration

| Module | Usage |
|--------|-------|
| `agent_manager.py` | `create_agent_from_genesis` |
| `routes/agents.py` | Genesis listing |
| `routes/admin.py` | Soul export/import/fork, chain reads |

---

## Related

- [Ledger & DomainDB](../ledger-and-domain-db.md)
- [Bridge & AKU](../bridge-and-aku.md)
- [Genesis templates](../genesis.md)
