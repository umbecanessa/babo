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

The [Deploy documentation](../../.github/workflows/docs.yml) workflow builds MkDocs, then overlays `website/` as the site root (`index.html`, `css/`, `js/`, `assets/`). Documentation remains at paths like `/getting-started/installation/` on a custom domain, or under `/babo/` on the default GitHub project URL.

## Custom domain

Today the default URL is a **GitHub Pages project site**:

`https://umbecanessa.github.io/babo/`

With a custom domain (e.g. `babo.dev` or `www.getbabo.com`), GitHub serves the **same build at the domain root** — not under `/babo/`. Relative links in `website/` (`getting-started/…`, `css/…`) already work on a root domain.

### 1. GitHub Pages

1. Repo **Settings → Pages → Custom domain** — enter the hostname (usually `www.`; apex is optional, see DNS).
2. Enable **Enforce HTTPS** once the certificate is issued (can take up to 24h).
3. Optional but recommended: commit the domain in the repo so deploys do not drop it:
   - Create `website/CNAME` with a single line, e.g. `www.babo.dev` (no `https://`, no path).
   - The docs workflow copies `website/CNAME` into the Pages artifact when present.

### 2. DNS (at your registrar)

| Host | Type | Value |
|------|------|--------|
| `www` | `CNAME` | `umbecanessa.github.io` |
| `@` (apex) | `A` | `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153` |

For apex-only sites, GitHub also supports `ALIAS`/`ANAME` to `umbecanessa.github.io` where your DNS provider allows it. Prefer `www` + redirect apex → `www` if apex DNS is awkward.

Check current records: [GitHub Docs — Managing a custom domain](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site).

### 3. MkDocs canonical URL

Update `site_url` in `mkdocs.yml` to your public origin (trailing slash), e.g.:

```yaml
site_url: https://www.babo.dev/
```

That fixes sitemaps, social previews, and absolute links MkDocs generates. Re-run the docs workflow after changing it.

### 4. Marketing URLs

| Audience | URL |
|----------|-----|
| Innovators (default) | `https://www.babo.dev/` |
| Everyday | `https://www.babo.dev/?audience=everyday` |

The old `github.io/babo/` URL can stay live in parallel; point ads and README at the custom domain when you are ready.

### 5. Checklist

- [ ] DNS propagated (`dig www.babo.dev CNAME`)
- [ ] Pages shows “DNS check successful”
- [ ] HTTPS enforced
- [ ] `site_url` updated in `mkdocs.yml`
- [ ] `website/CNAME` committed (if not using UI-only domain)
- [ ] Smoke-test: `/`, docs nav, `/?audience=everyday`, Discord link
