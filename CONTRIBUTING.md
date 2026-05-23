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

## Pull requests

- Keep diffs focused
- Do not commit secrets (`.env`, API keys, tokens)
- Update docs when you change setup, env vars, or user-visible behavior
