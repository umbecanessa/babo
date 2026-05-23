# Taxonomy (`nls/taxonomy` + `nls/knowledge/taxonomy.py`)

**Domain classification seed data** — not a large Python package. Logic lives in `nls/knowledge/taxonomy.py`.

---

## Data file

| Path | Content |
|------|---------|
| `nls/taxonomy/seed_v1.yaml` | Hierarchical domain seeds for routing and LEARN classification |

---

## Code

**`TaxonomySeed`** (`nls/knowledge/taxonomy.py`):

- Load YAML into in-memory tree
- `TaxonomyNode`, `TaxonomyMatch` for ANS routing
- Optional enrichment from DomainDB at factory startup

---

## Wiring

`nls/runtime/factory.py` loads `seed_v1.yaml` at agent build time and attaches to `AutonomicNervousSystem`.

Shim: `nls/engine/taxonomy.py` → re-export for old imports.

---

## Server integration

None direct — effects visible via fact domain paths and admin fact APIs.

---

## Related

- [Bridge & AKU](../bridge-and-aku.md)
- [Knowledge package](knowledge.md)
