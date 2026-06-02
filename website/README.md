# Babo marketing site

Early-access homepage overlaid on the docs build for **https://babo.agency/**.

| Path | Source |
|------|--------|
| `/` | `website/index.html` (marketing landing) |
| `/getting-started/…`, `/guides/…`, etc. | MkDocs from `docs/` |

## Audiences (query param)

| URL | Audience |
|-----|----------|
| `/` | **Innovators** (default) |
| `/?audience=everyday` | **Early adopters** |
| `/?audience=home` | Alias for `everyday` |

Also accepts `utm_content=everyday` or `innovator`. Choice persists in `sessionStorage` (`babo_audience`).

Copy lives in `js/audience.js`. Theme tokens match `frontend/src/styles.scss` (`babo_theme` in localStorage).

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
