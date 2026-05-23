# Runtime package (`nls/runtime`)

Production **agent process**: chat turns, subsystem wiring, session I/O, channel relay client, inference helpers.

**Entry:** `nls/runtime/__init__.py` exports `AgentRuntime`.

---

## Key files

| File | Responsibility |
|------|----------------|
| `agent_runtime.py` | Main class — chat, agentic session, status, broadcasts |
| `factory.py` | `build_subsystems()` — wires brain, ledger, identity, tools |
| `session.py` | `conversation_history.json`, session keys |
| `status.py` | `get_status()`, wake prompts |
| `channels.py` | `ChannelRelayClient`, `PolicyEnforcer`, relay WS to NestJS |
| `domain_experience.py` | Domain/skill usage tracker (`ExperienceTracker`) |
| `inference.py` | `InferenceInterceptor`, domain hints |

---

## Key classes

- **`AgentRuntime`** — one loaded agent; owns subsystems and tool loop entry
- **`ChannelRelayClient`** — outbound WS to `/api/channels/relay/{runtimeAgentId}`
- **`ExperienceTracker`** — `domain_tracker.json` / skill usage (via `calibrator` alias in factory)
- **`build_subsystems(agent_dir, ...)`** — constructs ANS, Cryptex, DomainDB, ToM, etc.

---

## Disk artifacts (per agent)

| Path | Content |
|------|---------|
| `ledger.yaml` | Chain manifest |
| `knowledge.db` | DomainDB |
| `conversation_history.json` | Chat log |
| `agent_meta.json` | Name, metadata |
| `config/*.json` | Merged brain configs |
| `enabled_skills.json`, `enabled_tools.json` | Capability flags |
| Brain state JSONs | See [Brain](brain.md) |

---

## Imported by

- `server/services/agent_manager.py` — primary loader
- `server/main.py` — relay registration
- `server/routes/agents.py`

---

## Related

- [Agent runtime](../agent-runtime.md)
- [Data directory](../../reference/data-directory.md)
