# Identity package (`nls/identity`)

**Self-models** rendered into prompts: soul axioms, narrative autobiography, theory of mind, temporal continuity.

---

## Key files

| File | Types |
|------|-------|
| `soul.py` | `SOUL_AXIOMS`, founding values constants |
| `narrative_self.py` | `NarrativeSelf`, `Episode`, `NarrativeBlock` |
| `theory_of_mind.py` | `TheoryOfMind`, `UserModel`, `ConversationalTemperature` |
| `temporal_self.py` | `TemporalSelf` — continuity across sessions |
| `agent_identity.py` | `detect_name_from_signals()` |

---

## Disk artifacts

| File | Content |
|------|---------|
| `narrative_self_state.json` | Episodes and narrative blocks |
| `temporal_self_state.json` | Session continuity |
| Theory-of-mind state | User model snapshots |

Soul **archives** (export/import) live in `nls/ledger/soul_package.py` — see [Soul packages](../soul-packages.md).

---

## Wiring

`nls/runtime/factory.py` constructs identity subsystems; `nls/brain/brain_context.py` merges narrative + ToM into generation context.

No direct `server/` imports — all via `AgentRuntime`.

---

## Related

- [Brain & memory](../brain-and-memory.md)
- [Genesis templates](../genesis.md)
