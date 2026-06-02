# Bridge package (`nls/bridge`)

**AKU** (Atomic Knowledge Unit) parsing, validation, and PII sanitization for the knowledge pipeline.

---

## Key files (published)

| File | Role |
|------|------|
| `aku.py` | `validate_domain_path`, `parse_akus_from_json`, dedup vs DomainDB |
| `sanitizer.py` | `PIISanitizer` before external API calls |

---

## Domain path rules (`aku.py`)

- Minimum depth 2 segments (`User.Tech`)
- Segments: letter-first alphanumeric + underscore
- Organic domain prefixes (no fixed whitelist in OSS)

The quality gate rejects low-salience content before DomainDB writes.

---

## Product path

Chat and sleep consolidation use **inline LEARN** signals → `nls/knowledge/fact_store.py` → DomainDB. AKU helpers validate paths and format at write time.

---

## Related

- [Bridge & AKU](../bridge-and-aku.md)
- [Ledger package](ledger.md)
