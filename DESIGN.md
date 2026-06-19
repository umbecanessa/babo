# Design

Babo visual system for marketing (`website/css/tokens.css`) and product UI (`frontend/src/styles.scss`). **These files must stay in sync** — the homepage should feel like the app, not a separate template.

## Identity

**Glassmorphic agent OS** — translucent panels, backdrop blur, animated purple/teal mesh, dark app chrome in screenshots. This is Babo's look in both the desktop app and babo.agency.

Impeccable-style "anti-AI-slop" palette swaps (copper-on-slate, flat opaque cards, stripped ambient layers) are **not** our brand. They made the site look like a generic skill output.

## Color strategy

| Role | Light | Dark |
|------|-------|------|
| Background | `#eef0f7` | `#06070d` |
| Glass surface | `rgba(255,255,255,0.52)` + 24px blur | `rgba(30,33,48,0.50)` + blur |
| Ink | `#1a1d2e` | `#f2f3f8` |
| Primary accent | `#7c5bf5` (violet) | `#7c5bf5` |
| Secondary accent | `#14b8a6` (teal) | `#14b8a6` |

Mesh gradients use the same violet/teal/gold radials as `frontend/src/styles.scss` (`body::before` / marketing `.mesh-a`).

## Typography

| Role | Family |
|------|--------|
| Display | Space Grotesk |
| Body | Inter |
| Mono | JetBrains Mono |

## Audience visual lanes

Copy switches in `website/js/audience.js`; **design** should reinforce who we're talking to:

| Audience | Visual lane |
|----------|-------------|
| **Builders** (`innovator`) | Violet-dominant mesh, purple gradient headline, GitHub CTA glow, extension/platform emphasis |
| **Everyone** (`everyday`) | Teal-warm mesh, teal→violet headline gradient, teal primary buttons, download card glow |

Audience lanes via `?audience=everyday` and `sessionStorage`. See [docs/audiences.md](docs/audiences.md).

## Components

- **Surfaces:** `var(--glass-bg)` + `backdrop-filter: blur(var(--glass-blur))` — never flat opaque `--bg-surface` on marketing cards
- **Ambient:** `.mesh-a`, `.mesh-b`, `.noise`, `.grid-bg` on homepage
- **Nav:** floating glass pill bar
- **Hero:** animated gradient accent line (`--gradient-hero`)
- **Showcase:** tabbed screenshots in dark app shell

## Layout funnel

Hero → Product → Download → Why Babo → Capabilities → Pricing → Manifesto → Community

## Tokens source of truth

- Marketing: `website/css/tokens.css`
- Product: `frontend/src/styles.scss`

Edit both when changing brand colors, glass values, or fonts.
