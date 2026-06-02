# Context supersession and read cache

Design for reducing token waste from stale tool failures and cross-delegate
duplicate reads, without breaking tool-calling API pairing or completion review.

## Two-tier memory

| Layer | Role |
|-------|------|
| **Durable** | SubCryptex rings, compaction anchor, ReadIndex |
| **Ephemeral** | Chat tail — last turns full, older turns stubbed |

Assistant `tool_calls` are **never** rewritten. Only `role: tool` bodies are stubbed.

## Supersession policies

| Policy | When |
|--------|------|
| `delegate_aggressive` | Delegate loops |
| `orchestrator_conservative` | Normal EM — duplicate list_dir/read/bash only |
| `completion_review_frozen` | `team_completion_review:*` or pending reviews |
| `disabled` | `enable_context_supersession=false` |

### Intent keys

- **bash** — normalized command shape
- **read/write/edit** — normalized path (+ offset/limit/max_chars for reads)
- **grep/glob/search** — pattern + scope
- **list_dir** — path + depth
- **plan/team** — action + stable ids

### Read rules

1. Never stub the **latest** read of a path.
2. Never point stubs at SubCryptex for full content (preview only).
3. Supersede **re-reads** and bash retries; respect `max_chars` reads.
4. Stubs include recovery: `read(offset=…)` or `force=true`.

## ReadIndex (Tier 1 / Tier 2)

**Tier 1** — `{agent_dir}/read_index.jsonl`: path + mtime + size metadata.
Cross-delegate cache hit returns short response with preview + honest recovery text.

**Tier 2** — `{agent_dir}/.read_cache/`: bounded LRU content store (50MB, 20 files)
for `read(offset=…)` slice serving.

Invalidation: ledger write/edit or mtime/size change.

## Config (`LoopConfig`)

- `enable_context_supersession` (default true)
- `enable_read_index` (default true)
- `keep_recent_tokens` — orchestrator (40k)
- `delegate_keep_recent_tokens` — delegates (20k)

## Metrics (`LoopState`)

- `supersession_stubs_applied`
- `supersession_tokens_saved`
- `read_cache_hits`

## Hook order (each iteration)

1. `generate()` → tools execute → **supersession immediately after tool batch**
2. Next iteration start: **supersession safety net** (idempotent) → `transform_context()` (SubCryptex)
3. `should_compact()` / anchor
4. `generate()`

Post-append supersession ensures the next `generate()` never pays for stale retry
bodies. The iteration-start pass is an idempotent safety net (`[superseded` guard).

## Files

- `nls/agentic/context_supersession.py`
- `nls/tools/agent_tools/read_index.py`
- `nls/agentic/loop.py` — wiring
- `nls/tools/agent_tools/read.py` — cache integration
- `nls/tools/agent_tools/file_ledger.py` — invalidation on write
