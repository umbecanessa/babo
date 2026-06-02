# Babo marketing site (optional)

Static early-access homepage — **separate from the open-source documentation**.

| Artifact | Purpose |
|----------|---------|
| `website/` | Marketing HTML/CSS/JS (audience variants in `js/audience.js`) |
| `docs/` + `mkdocs.yml` | Technical documentation (published via [Deploy documentation](../../.github/workflows/docs.yml)) |

Documentation is published at **https://babo.agency/** (MkDocs from `docs/`). The marketing site is not overlaid on that build.

## Local preview (marketing only)

```powershell
cd website
npx --yes serve -l 3456
```

## Local preview (docs only)

```powershell
pip install -r requirements-docs.txt
mkdocs serve
```

## Custom domain

If you host marketing on a custom domain, configure DNS and GitHub Pages separately from the docs workflow. Update `site_url` in `mkdocs.yml` only when the **documentation** origin changes.

Privacy policy for OAuth may live on your public marketing domain; keep a copy under `docs/legal/` for the docs site.
