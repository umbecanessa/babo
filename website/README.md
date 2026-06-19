# Babo marketing site

Early-access homepage overlaid on the docs build for **https://babo.agency/**.

| Path | Source |
|------|--------|
| `/` | `website/index.html` (marketing landing) |
| `/getting-started/…`, `/guides/…`, etc. | MkDocs from `docs/` |

## Audiences

Documented in [PRODUCT.md](../PRODUCT.md), [docs/audiences.md](../docs/audiences.md), and implemented in `js/audience.js`.

| Audience | Site label | URL | Who |
|----------|------------|-----|-----|
| **Innovators** (default) | Builders | `/` | Self-hosters, OSS contributors, agent-stack builders |
| **Early adopters** | Everyone | `/?audience=everyday` | Personal AI agent users (no terminal) |

Also accepts `?audience=home` (alias for everyday) and `utm_content=innovator|everyday`. Choice persists in `sessionStorage` (`babo_audience`). The nav **Builders / Everyone** toggle switches copy without a full reload.

## Design tokens

Marketing tokens: `css/tokens.css`. Product UI: `frontend/src/styles.scss`. Both follow [DESIGN.md](../DESIGN.md) (glassmorphic violet/teal mesh, Inter + Space Grotesk). Theme preference: `babo_theme` in `localStorage`.

## Local preview

```powershell
# Marketing only
cd website
npx --yes serve -l 3456
```

```powershell
# Full Pages layout (marketing + docs)
pip install -r requirements-docs.txt
mkdocs build
Copy-Item website/index.html site/index.html -Force
Copy-Item -Recurse website/css, website/js, website/assets site/
npx --yes serve site -l 3456
```

## Deploy

The [Deploy documentation](../../.github/workflows/docs.yml) workflow builds MkDocs, then **overlays** `website/` onto `site/` (`index.html`, `css/`, `js/`, `assets/`). Without that step, `babo.agency` would show only the docs home page.

## Custom domain

See `CNAME.example` — copy to `website/CNAME` with your hostname so deploys keep the domain. Update `site_url` in `mkdocs.yml` when the public docs origin changes.

Privacy policy: **https://babo.agency/legal/privacy-policy/** (from `docs/legal/`).
