# Product

## Register

brand

Primary surface: **babo.agency** marketing site (`website/`). The desktop and web app are product UI that shares the same visual system (`frontend/`, `DESIGN.md`).

## Audiences

Babo speaks to two audiences on the homepage. Copy and CTAs switch via `js/audience.js`; the active audience is stored in `sessionStorage` (`babo_audience`).

| ID | Label on site | Who they are | Primary CTA |
|----|---------------|--------------|-------------|
| `innovator` | **Builders** (default) | OSS contributors, self-hosters, agent-stack builders | Star on GitHub |
| `everyday` | **Everyone** | Early adopters who want a personal AI agent without a terminal | Download free |

**URLs**

- `/` → innovator (default)
- `/?audience=everyday` or `/?audience=home` → everyday
- `utm_content=innovator|everyday` also works

Full reference: [docs/audiences.md](docs/audiences.md) and [website/README.md](website/README.md).

## Users

**Innovators** run Babo on their own hardware. They care about local inference, MIT license, extensible skills/MCP, memory, and team orchestration. They tolerate early-access rough edges and read docs.

**Everyday users** want a personal agent on their PC: WhatsApp, Gmail, tasks, guided wizard setup. They should not need to understand MCP, ClawHub, or the terminal.

## Product Purpose

Babo is an open-source, local-first agent runtime: persistent memory, channels, projects, and an extensible skill platform. Success means builders can self-host and extend; everyday users can install and delegate real work without cloud token meters.

## Brand Personality

**Warm infrastructure.** Honest, builder-respectful, local-first. Not hype-SaaS, not sterile enterprise. Voice: direct, slightly opinionated (manifesto), proof over promises (real screenshots).

Three words: **local, capable, honest.**

## Anti-references

- Purple-to-teal gradient mesh “AI agent startup” landing pages
- Inter / Space Grotesk default stacks with glassmorphism cards
- Gradient headline text and uppercase eyebrow on every section
- Chat-wrapper UIs that forget everything and meter every token
- Generic “hero metric + three identical feature cards” templates

## Design Principles

1. **Show the real product** — screenshots and UI chrome, not abstract illustrations.
2. **Local-first is the story** — economics and privacy are features, not footnotes.
3. **Audience-aware copy** — same product, different entry path (builder vs everyone).
4. **Proof before pitch** — product section and download before long capability grids.
5. **Accessible by default** — WCAG AA contrast, keyboard nav, no zoom blocking.

## Accessibility & Inclusion

- Target **WCAG 2.1 AA** on the marketing site
- Keyboard focus visible on all interactive elements
- No `maximum-scale=1` on viewport
- Respect `prefers-reduced-motion` for scroll reveals and ambient animation
- Plain language for everyday audience; technical terms allowed for innovator audience with context
