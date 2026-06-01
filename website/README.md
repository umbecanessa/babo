# Babo marketing site

Early-access homepage: [umbecanessa.github.io/babo](https://umbecanessa.github.io/babo/)

## Audiences (query param)

| URL | Audience |
|-----|----------|
| `/babo/` | **Innovators** (default) — token cost, local inference, builders |
| `/babo/?audience=everyday` | **Early adopters** — PA, no terminal, beyond ChatGPT |
| `/babo/?audience=home` | Alias for `everyday` |

Also accepts `utm_content=everyday` or `innovator`. Choice persists in `sessionStorage` (`babo_audience`).

Copy lives in `js/audience.js`. Theme tokens match `frontend/src/styles.scss` (`babo_theme` in localStorage).

## Local preview

From the repo root (after a docs build, or copy assets manually):

```powershell
# Option A: simple static server in website/
cd website
npx --yes serve -l 3456
```

Open http://localhost:3456 — doc links will 404 until you merge with MkDocs output.

```powershell
# Option B: full Pages layout
pip install -r requirements-docs.txt
mkdocs build
Copy-Item website/index.html site/index.html -Force
Copy-Item -Recurse website/css, website/js, website/assets site/
npx --yes serve site -l 3456
```

## Deploy

The [Deploy documentation](../../.github/workflows/docs.yml) workflow builds MkDocs, then overlays `website/` as the site root (`index.html`, `css/`, `js/`, `assets/`). Documentation remains at paths like `/babo/getting-started/installation/`.
