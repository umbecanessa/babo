# UI surfaces (floating panels)

Design contract for **floating overlays** in the Angular UI (`frontend/`). Introduced in v1.1.x to fix readability when menus sat on top of chat text and the composer.

**Styles:** `frontend/src/app/shared/styles/_context-menu.scss` · **Tokens:** `frontend/src/styles.scss`

---

## Problem

Early glassmorphic panels used `--glass-bg` (translucent) everywhere. Dropdowns with dense labels — model lists, orchestration profiles, workspace file menus — became hard to read when they floated over the message list or composer.

Modals already used an opaque `--modal-bg`. Floating menus needed the same readability without turning the whole app flat.

---

## Three surface tiers

| Tier | CSS token | Typical use |
|------|-----------|-------------|
| **Glass** | `--glass-bg` + blur | Structural chrome, toasts, run panel, inline plan cards — sparse background or corner overlays |
| **Context menu** | `--context-menu-bg` (opaque) | Dropdowns, pickers, right-click menus — anything over dense UI text |
| **Modal** | `--modal-bg` | Dialogs, drawers, full sheets on `--backdrop-scrim` |

Tokens (light/dark in `styles.scss`):

```text
--context-menu-bg      /* opaque panel fill — defaults to --bg-secondary */
--context-menu-z       /* 1000 — above chat, below modals */
--context-menu-shadow  /* elevated drop shadow + hairline border */
```

**Rule of thumb:** if the panel shows more than one line of selectable text over the chat stream, use **context menu**, not glass.

---

## Shared stylesheet

`@use "./app/shared/styles/context-menu"` in `styles.scss` exposes global classes:

| Class / mixin | Purpose |
|---------------|---------|
| `@mixin context-menu-panel-shell` | Opaque shell (bg, border, shadow, blur isolation) |
| `.context-menu-panel` | Positioned floating panel |
| `.context-menu-item` | Row action; `.active` / `[aria-selected]` for selection |
| `.context-menu-title` | Section label (uppercase muted) |
| `.context-menu-hint` | Inset hint block |
| `.context-menu-search-wrap` | Search field inside a menu |
| `.context-menu-tabs` / `.context-menu-tab` | Segmented control inside a menu |
| `.context-menu-option` | Label + description stack for profile/model rows |
| `.context-menu-backdrop` | Click-outside dismiss layer |
| `.context-menu` | Explorer right-click alias (min-width 160px) |

Active/hover states use `color-mix` against `--context-menu-bg` so selection tints stay readable on both themes.

---

## Migrated components (v1.1.x)

These were moved from bespoke translucent SCSS to the shared contract:

| Component | Path | Notes |
|-----------|------|-------|
| Model picker | `chat-model-picker/` | Search + model list; LAN/Popular/More groups; orchestrator/sub-agent tabs |
| Orchestration profile picker | `chat-orchestration-profile-picker/` | Profile depth + per-agent overrides |
| Thread / session menu | `chat.component.html` | Uses `.context-menu-panel` |
| Workspace explorer menu | `workspace-explorer/` | Right-click file actions |
| Creation wizard menus | `creation.component.scss` | Dropdown panels |
| Activity detail cards | `activity-panel/` | Opaque overlay cards in Projects |

Dead thread-switcher styles were removed from `chat.component.scss` when the menu moved to shared classes.

---

## Intentional glass exceptions

Not every floater belongs on opaque panels:

| Surface | Why glass |
|---------|-----------|
| **Toasts** | Top corner, short text, sparse chrome behind |
| **Run panel** | Side dock; plan/tool timeline reads better as glass over workspace |
| **Plan viewer cards** | Inline in chat stream; glass matches message bubbles |

Do **not** revert these to `--context-menu-bg` without checking contrast on both themes.

---

## Adding new floating UI

1. Pick the tier using the table above.
2. For menus/pickers, wrap content in `.context-menu-panel` and use `.context-menu-item` for actions.
3. Set `z-index: var(--context-menu-z)` only via the shared mixin — modals use `--modal-z` (1100+).
4. Avoid duplicating panel shell CSS in feature SCSS; extend `_context-menu.scss` if a new pattern repeats twice.

---

## Related

- [Frontend application](../architecture/frontend-application.md)
- [Chat guide](../guides/chat.md) — composer chip, scroll pinning
- [Projects & teams](../guides/projects-and-teams.md) — chat sidebar
