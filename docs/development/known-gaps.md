# Known product gaps & bugs

Living tracker from field observations (orchestration, WM/Cryptex, UI, shell).  
Each item includes **severity**, **symptom**, and **likely fix direction**.

---

## P0 — False task completion (agent `984f57dd` local-run session)

| ID | Symptom | Root cause | Fix direction |
|----|---------|------------|---------------|
| **GAP-EVAL-01** | Loop exits `task_complete` after user asked to run locally; backend still broken; user got "Task completed. Recent tool activity…" | Build plan had all steps `done` but stayed `in_progress`; **follow-up task** ("run locally") inherited artifact completion from the stale plan. | **Fixed (2026-06-08):** `plan_ledger_complete_at_loop_start` blocks artifact auto-complete; `follow_up_delivery_verified` requires runtime proof (not bare `project_install`); phase boundary trim. |
| **GAP-EVAL-02** | `plan(complete)` not auto-called despite `exit=task_complete` | Stale `plan.audit.issues` listed files as missing that exist on disk. | **Fixed (2026-06-08):** `prune_stale_audit_issues` drops resolved `File missing:` issues when path exists; runs before complete gate + auto-complete. |
| **GAP-MODEL-01** | Partial fix to `transcript.py` — backend still won't import | Fixed `status` field order but left `created_at` (no Python default, only `server_default`) **after** fields with `default=` → same dataclass error, now on `created_at`. `SpeakerUtterance` likely has similar issues. | One-line fix: `created_at: Mapped[datetime] = mapped_column(..., server_default=func.now(), default=None)` or reorder; scan all models. |

**Session stats (loop `359ef5a2da68`):** 41 iterations, 801s, ~1.73M tokens, 54 tool calls. User extended budget +40 at iter 40; iter 41 ended immediately.

**Not done:** frontend `npm run dev`, `.env`, health check, `plan(complete)`.

---

| ID | Symptom | Root cause (observed) | Fix direction |
|----|---------|----------------------|---------------|
| **GAP-PLAN-01** | All plan steps `done`, Projects shows 100%, but plan JSON `status` stays `in_progress`; chat plan card still looks "live". | Agent never calls `plan(complete)`; auto-complete only runs on loop exit (`task_complete` / `complete`) and passes `can_complete_plan()` gate. | **Fixed (2026-06-08):** `prepare_stale_plan_for_closure` auto-verify + complete on follow-up dispatch and loop exit; UI wrap-up when all steps done. |
| **GAP-WM-01** | WM shows `[PLAN POSITION — 10/10 done]` while Cryptex still has `Project.Status: Database Schema & Models` and `Project.NextStep: Authentication`. | Session consolidation + Cryptex facts not superseded when plan advances; REPLACES reconciliation incomplete across domains. | **Fixed (2026-06-08):** scrub `Project.Status` / `Project.NextStep` in `plan_wm_sync._STALE_FACT_DOMAINS`. |
| **GAP-WM-02** | Plan `audit.issues` lists files as missing long after they exist on disk. | `plan(verify)` snapshot not refreshed or audit not cleared on accept_partial / step done. | **Fixed (2026-06-08):** see GAP-EVAL-02 prune on verify/complete/sync paths. |
| **GAP-CTX-01** | Old `[COMPLETION REVIEW — DELEGATE #2]` messages remain in loop context when user starts a new phase ("run locally"). | Transcript not trimmed at phase boundary; solo_structured depth injected but history kept. | **Fixed (2026-06-08):** `trim_context_for_phase_boundary` on follow-up user dispatch. |
| **GAP-CTX-02** | Agent tries `team(create, wave=1)` after all waves done; thinks delegates #2–#4 still running. | Stale breadcrumb + WM team summaries vs live `team(inspect)` showing `completed`. | **Partial (2026-06-08):** `has_orchestrator_blocking_team` ignores stale `active` teams with no running/pending members. |

---

## P1 — Plan / run panel UX

| ID | Symptom | Root cause | Fix direction |
|----|---------|------------|---------------|
| **GAP-UI-01** | No dismiss (X) on plan card when work is finished. | `RunViewService.isLive()` false + `needsWrapUp()` true only shows expanded banner text. | **Fixed (2026-06-08):** dismiss (×) on collapsed wrap-up bar via `dismissWrapUp()`. |
| **GAP-UI-02** | No archive flow for finished projects/plans. | `plan_store.archive()` exists; UI only sets `_archived` on delete/supersede. | Open — full Projects archive tab still TODO. |
| **GAP-UI-03** | Live spinner on collapsed plan card while steps are all done. | `isLive` = running delegates OR step status `active`; plan-level `in_progress` does not flip UI to wrap-up until delegates idle. | **Fixed (2026-06-08):** `isLive` false when all steps terminal or `planStatus === 'done'`. |

---

## P1 — Shell / CWD confusion (agent `984f57dd` session)

| ID | Symptom | Root cause | Fix direction |
|----|---------|------------|---------------|
| **GAP-SHELL-01** | `Error: redundant cd — you are already inside backend/` | `preflight_bash_command()` blocks `cd backend` when SharedCWD basename matches. Models habitually prefix `cd backend &&`. | **Fixed (2026-06-08):** CWD prefix banner on bash results when CWD ≠ workspace root. |
| **GAP-SHELL-02** | `ModuleNotFoundError: fastapi` when using `..\.venv\Scripts\python` from `backend/`. | Monorepo has **two venvs**: root `.venv` (root `requirements.txt` = anthropic only) vs `backend/.venv` (full stack). | **Fixed (2026-06-08):** bash footer shows active/missing venv per CWD; root install breadcrumb points at subdir. |
| **GAP-SHELL-03** | `project_install` loop at repo root installs only `anthropic` repeatedly. | Default CWD = project root; agent doesn't pass `install_dir=backend`. | **Fixed (2026-06-08):** `format_post_root_install_hint` after root-only install when single scaffold subdir exists. |
| **GAP-SHELL-04** | `edit` fails: `File not found: ai-powered-icf-coaching-session/backend/app/...` while CWD is `backend/`. | Path normalization in write/read strips project prefix when CWD locked; agent still uses WM-stored full paths. | **Fixed (2026-06-08):** `strip_path_through_cwd_segment` + edit error suggests CWD-relative path. |
| **GAP-SHELL-05** | Uvicorn traceback but bash `error=False` ("command succeeded despite deprecation/warning"). | Crash output captured before daemon detach heuristic; SQLAlchemy import error treated as non-fatal. | **Fixed (2026-06-08):** traceback/crash in server output fails bash; daemon detach aborted when crash detected after startup banner. |

---

## P2 — Orchestration lifecycle

| ID | Symptom | Fix direction |
|----|---------|---------------|
| **GAP-ORCH-01** | `skipped plan auto-complete — active team(s) running` while teams JSON already `completed`. | Team manager "blocking" predicate may include terminal teams; tighten `has_orchestrator_blocking_team()`. | **Fixed (2026-06-08):** stale `active` teams with all members terminal no longer block. |
| **GAP-ORCH-02** | DMN suppressed for incomplete plan while steps are actually done. | `runtime_has_open_plan_work()` should treat all-steps-done + plan open as "closure pending", not "open work". | **Fixed (2026-06-08):** `plan_needs_closure` excluded from `work_plan_has_open_steps`. |

---

## Field log — agent `984f57dd` (2026-06-08)

**Plan:** `plan_43cf77f5` — 10/10 steps done, status still `in_progress`.

**Local run session (iter ~19–35):**

1. Repeated `project_install` at root → anthropic-only venv (user stop message broke loop).
2. `cd backend && ..\.venv\python` → fastapi missing (wrong venv).
3. Redundant `cd backend` blocked by preflight.
4. Uvicorn start → `transcript.py` dataclass field order error.
5. Edit with full project path failed twice; succeeded with `app/models/transcript.py` after glob.
6. Iter 35: uvicorn re-run after fix — detached as long-running server.

**Evidence:** `runtime.log` lines ~198816–201390; loop journal iter 24–35.

---

## Next engineering priorities (suggested)

1. **Stale memory hygiene** — REPLACES + audit refresh on plan step completion (GAP-WM-01, GAP-WM-02, GAP-CTX-01).
2. **Plan closure** — auto `plan(complete)` when gate passes at end of user turn (GAP-PLAN-01).
3. **Run panel** — wrap-up + archive UX (GAP-UI-01–03).
4. **Shell ergonomics** — venv clarity + edit path hints (GAP-SHELL-02, GAP-SHELL-04, GAP-SHELL-05).
