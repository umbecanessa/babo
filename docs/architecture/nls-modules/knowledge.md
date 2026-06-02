# Knowledge package (`nls/knowledge`)

**Fact persistence** from LEARN signals, reasoning schema distillation, taxonomy-assisted classification.

---

## Key files

| File | Class / function |
|------|------------------|
| `fact_store.py` | `FactStore.store_learn_signals()` |
| `reasoning.py` | `ReasoningDistiller`, `ReasoningSchema` |
| `taxonomy.py` | `TaxonomySeed`, `TaxonomyMatch` |

---

## Data flow

```text
ANS LEARN signal
  → bridge/aku quality gate
  → FactStore
  → DomainDB (ledger/domain_db.py)
```

`FactStore` receives an injected `DomainDB` instance from `factory.py`.

---

## Taxonomy seed

Loaded from `nls/taxonomy/seed_v1.yaml` at startup — see [Taxonomy](taxonomy.md).

---

## Server integration

Indirect via `AgentRuntime` / admin fact endpoints. Admin uses `nls.models.Fact` for API shapes.

---

## Related

- [Ledger package](ledger.md) · [Ledger & DomainDB](../ledger-and-domain-db.md)
- [Bridge & AKU](../bridge-and-aku.md)
