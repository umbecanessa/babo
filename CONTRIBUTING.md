# Contributing to Babo

Thanks for helping improve Babo.

## Scope

This repository is the **product stack**: `frontend/`, `backend/`, `desktop/`, `nls/`, and `server/`.

Use [Product scope](docs/development/product-scope.md) for what belongs here. Training pipelines, custom inference plugins, and lab-only research code are out of scope.

## Development

1. Fork and clone the repo
2. Set `NLS_PRODUCT_MODE=1` when running the Python runtime
3. Point inference at OpenRouter, Ollama, or another OpenAI-compatible endpoint
4. Run backend + runtime + frontend as described in the [README](README.md) and [local development](docs/development/local-development.md) guide

## Documentation

User and architecture docs live in `docs/` and publish to [babo.agency](https://babo.agency/) via MkDocs.

When you change user-visible behavior, env vars, setup flows, or APIs:

1. Update the relevant page under `docs/` (see [documentation index](docs/index.md))
2. Add new pages to `mkdocs.yml` nav if they are user-facing guides
3. Verify locally:

```bash
pip install -r requirements-docs.txt
mkdocs build --strict
```

The CI **`docs.yml`** workflow runs the same strict build and overlays `website/` as the marketing homepage.

**Design drafts** in `docs/brainstorm/` are not in the nav — link from canonical guides only when useful for history.

## Pull requests

- Keep diffs focused
- Do not commit secrets (`.env`, API keys, tokens)
- Update docs when you change setup, env vars, or user-visible behavior
