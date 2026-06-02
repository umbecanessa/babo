# Skill discovery & recovery

How Babo helps agents **escape failure loops** — especially on GitHub setup, auth, and unfamiliar tooling — by surfacing **skills**, **ClawHub**, and **composition recipes**.

---

## Problem

Smaller models (e.g. GPT-4o-mini) often repeat failing `bash` commands (notably `gh repo create` without auth) instead of:

1. Checking bundled / installed skills
2. Searching ClawHub
3. Using `discover_tools` for deferred tool schemas

Root causes addressed on this branch:

| Gap | Fix |
|-----|-----|
| `gh` exits 0 with “run gh auth login” | `bash.py` + `tool_result_semantics.py` treat as error |
| Stall nudges omitted skills | Evaluator messages mention `clawhub` / `discover_tools` |
| ERROR_RECOVERY directive unused | Injected after 2+ consecutive tool errors in `loop.py` |
| Recipes never loaded | `recipe_hints.py` → preflight for GitHub tasks |
| Skills ring buried in prompt | **Skill discovery boost** raises ring priority on stall/hint |

---

## Skill discovery boost (Cryptex)

**Module:** `nls/agentic/skill_discovery_boost.py`

**Triggers:**

- Stall detection (`detect_stall`)
- ERROR_RECOVERY directive
- Orchestrator hint (pre- or post-tool)

**Effects:**

1. Sets `skill_discovery_boost` on loop state ref (~6 iterations)
2. `CryptexMemory.update_ring_priorities()` applies **stuck** profile — skills & tools rings near top
3. Upserts high-salience slot on skills ring with ClawHub / discover_tools steps
4. `_render_skills()` moves content to **msg0** (top of WM) with expanded instruction excerpts
5. **SubCryptex** delegates get `activate_skill_discovery_boost()` — skills ring priority + banner header

```text
Normal executing phase:     INSTRUCTIONS (1.0) > SKILLS (0.75)
After stall/hint boost:     SKILLS (1.0) > TOOLS_MCP (0.97) > INSTRUCTIONS (0.85)
```

---

## Discovery tools

| Tool | When to use |
|------|-------------|
| `clawhub(action='search', query='...')` | Find community skills by keyword |
| `clawhub(action='install', slug='...')` | Load skill instructions into runtime |
| `discover_tools(query='...')` | Unlock deferred tool schemas trimmed for context |
| `skill_configure` | Enable/configure bundled skills |
| `wm(action='borrow', domain='Project.Credential.*')` | Retrieve stored tokens for gh/API |

Orchestrator coordinator supplement and delegate `_SUB_AGENT_SUPPLEMENT` both document **approach order**: skill → ClawHub → web_search → bash.

---

## Composition recipes

JSON recipes under `nls/config/recipes/` (e.g. `devops/github_repo.json`) describe multi-step procedures.

**Preflight injection:** `match_recipe_hints()` in `recipe_hints.py` — when task text mentions GitHub/repo, delegates and orchestrator preflight include:

- Step-by-step gh CLI flow
- Auth recovery (`echo TOKEN | gh auth login --with-token`)
- Pointer to ClawHub search

Recipes are **not** auto-installed skills; they are **instruction templates** in WM preflight.

---

## GitHub auth specifically

| Symptom | Agent should |
|---------|--------------|
| `gh auth login` in output | Run `bash('echo TOKEN \| gh auth login --with-token')` with user-provided PAT |
| No token in context | `escalate()` to orchestrator; user may add credential to WM vault |
| Repeated `gh repo create` | Stop after first auth failure; authenticate then `gh auth status` |

Bash tool sets agent-local `GH_CONFIG_DIR` and reads `hosts.yml` token into `GH_TOKEN` after successful login.

---

## Evaluator & stall paths

| Path | Message |
|------|---------|
| `detect_stall` repeat | `_REPEAT_NUDGE_MESSAGE` + ClawHub suggestion |
| 2+ consecutive errors | `_STALL_NUDGE_MESSAGE` + ERROR_RECOVERY directive |
| Plan step stuck 4+ iters | EXPLORE directive (ClawHub pivot) |
| Plan step stuck 8+ iters | ERROR_RECOVERY directive |

---

## Orchestrator responsibilities

On delegate `escalate()` for GitHub/auth:

- Wake message includes hint template (gh token auth, ClawHub search)
- `team(hint, message='...')` with **one** concrete command
- Optionally search ClawHub at orchestrator level before hinting

See [Orchestration & delegation](orchestration-and-delegation.md).

---

## Related

- [Tools system](tools-system.md)
- [Skills system](skills-system.md)
- [ClawHub guide](../guides/integrations/clawhub.md)
- [Brain & memory — ring priorities](brain-and-memory.md)
