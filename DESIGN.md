# Design

Babo visual system for marketing (`website/css/tokens.css`) and product UI (`frontend/src/styles.scss`). Keep these files aligned when changing brand colors or fonts.

## Color strategy

**Committed** — copper accent on cool slate neutrals. Infrastructure/workshop lane, not purple “AI SaaS.”

| Role | Light | Dark |
|------|-------|------|
| Background | `#eef1f5` | `#080a0f` |
| Surface | `#ffffff` | `#161a24` |
| Ink | `#12182a` | `#eef0f5` |
| Muted text | `#5a6178` (≥4.5:1 on bg) | `#8b92a8` |
| Primary accent | `#b85c1a` | `#e89448` |
| Secondary accent | `#2a7a72` | `#2a7a72` |

App screenshot frames stay dark (`--app-shell-bg: #0c0d14`) regardless of theme.

## Typography

| Role | Family | Notes |
|------|--------|-------|
| Display | Bricolage Grotesque | Headlines, section titles |
| Body | Source Sans 3 | UI copy, paragraphs |
| Mono | JetBrains Mono | Tags, meta, code |

Pairing axis: geometric display + humanist body. Do not revert to Inter / Space Grotesk on marketing surfaces.

Scale: body `1.0625rem`, hero `clamp(2.75rem, 7vw, 5rem)` max, section titles `clamp(1.85rem, 3.8vw, 2.75rem)`. Letter-spacing on display ≥ `-0.04em`.

## Components

- **Surfaces:** solid `--bg-surface` cards with `--glass-border`; avoid decorative glassmorphism
- **Buttons:** pill radius (`--radius-pill`), primary copper, ghost outline, Discord brand purple for community CTAs
- **Nav:** floating pill bar, 44px icon targets
- **Showcase:** tabbed screenshot viewer with lightbox; dark app chrome inside frames

## Layout

Marketing funnel (top → bottom):

1. Hero + audience switcher
2. Product screenshots
3. Download
4. Why Babo (3-step timeline)
5. Capabilities
6. Pricing
7. Manifesto teaser
8. Community

Capabilities use a featured first card + two-column grid. Download uses a featured desktop card spanning two columns on wide viewports.

## Motion

- Scroll reveal: `translateY(28px)` → visible, disabled under `prefers-reduced-motion`
- No bounce/elastic easing
- Hero product tilt on fine-pointer hover only

## Tokens source of truth

- Marketing: `website/css/tokens.css`
- Product: `frontend/src/styles.scss` (`:root` and `_theme-dark`)

When updating brand colors, edit both files.
