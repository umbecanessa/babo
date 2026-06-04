# Memory

Babo agents remember. The **Memory** page exposes every layer.

**Route:** `/memory/:agentId`

---

## Tabs

| Tab | What you see |
|-----|--------------|
| **Overview** | Summary stats, recent activity |
| **Knowledge** | Domain tree of facts (`DomainDB`) |
| **Chain** | Merkle-linked block history |
| **Working memory** | Active WM slots with salience |
| **Episodes** | Narrative episodes and highlights |
| **Soul** | Values, axioms, identity package |

---

## Cryptex rings

Long-term context is organized as **13 rotating rings**:

### Fixed (always visible)

- **Identity** — who the agent is (includes **Job** title/mission from `job.json` when synced)
- **User model** — who you are, preferences
- **Consolidation** — compressed long-term summaries
- **Emotional / hormonal** — affect snapshot
- **Strategic goals** — north-star objectives

### Project (rotate per project)

- **Orchestration** — team/plan state
- **Instructions** — project-specific rules
- **Project facts** — scoped knowledge
- **Credentials** — project secrets (access-controlled)
- **Tactical goals** — near-term objectives

### Domain (rotate per capability area)

- **Behavioral** — habits and patterns (includes **Trust** boundaries and squad context at `ACCESS_SYSTEM`)
- **Skills** — installed skill state
- **Tools + MCP** — connected tool servers
- **Channels** — WhatsApp/Telegram/email config summaries

Switching projects **rotates** project rings so parallel work stays isolated.

---

## Working memory slots

Short-term slots with types:

`fact`, `goal`, `constraint`, `feeling`, `intention`, `user_state`, `prediction`, `schema`, `perception`, `instruction`, `credential`

Slots decay by salience except instructions and credentials. The agent can inspect its own WM via the `wm` tool (`scan`, `rotate`, `search`, `snapshot`).

---

## Knowledge store

Facts live in a hierarchical **domain path** (dot notation), e.g. `User.Work.Project.Stack`.

Features:

- Search and browse in the Knowledge tab
- Conflict detection when new facts contradict old ones
- Confidence scores
- Promotion from working memory during sleep

---

## Merkle chain

Each conversation block can register in an hash-linked **chain** for integrity and replay. The Chain tab shows blocks, turn indices, and hashes.

Useful for auditing what the agent learned and when.

---

## Episodes & narrative self

**Episodes** chunk experience into story units. **Narrative self** maintains ongoing first-person continuity — how the agent understands your shared history.

---

## Soul

The **soul** package holds founding values, boundaries, and axioms rendered as natural prose in identity context — not a system prompt you maintain manually.

From the **Soul** tab you can:

| Action | Purpose |
|--------|---------|
| View | Inspect values, axioms, identity prose |
| Export | Download `.soul.zip` portable archive |
| Import | Restore soul from archive |
| Fork | New agent branched at a chain height |
| Snapshot / restore | Point-in-time soul checkpoints |

Cloud metadata sync uses NestJS soul-packages when the desktop relay is connected. Details: [Soul packages](../architecture/soul-packages.md).

---

## Chat Cryptex visualization

In **Chat**, the **Cryptex viz** widget shows which rings are active and key slot previews without opening the full Memory page.

---

## Consolidation

Sleep cycles merge low-salience WM into Cryptex consolidation rings and promote durable facts to DomainDB. See [Sleep & consolidation](sleep-and-consolidation.md).

---

## Related

- [Job, Trust & Squads](job-trust-and-squads.md) — owner charter in identity and behavioral rings
- [Core concepts](../getting-started/concepts.md)
- [Brain dashboard](brain-dashboard.md)
- [Chat](chat.md)
