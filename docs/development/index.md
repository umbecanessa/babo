# Development

- [Local development](local-development.md) — run the stack from source
- [Agent scenario harness](agent-scenario-harness.md) — end-to-end runtime acceptance (45 scenarios, **44/45** pass)
- [Troubleshooting](troubleshooting.md) — relay, deploy, and build issues
- [UI surfaces](ui-surfaces.md) — glass vs context-menu vs modal floating panels
- [Product scope](product-scope.md) — what this repository ships
- [Design notes: Job/Trust/Squads](../brainstorm/job-trust-task-squads.md) — historical design draft (canonical guide is under User guides)
- [Extension guide](../extension/index.md) — add tools, skills, channels
- [Reference](../reference/index.md) — APIs, relay protocol, env vars
- [Contributing](https://github.com/umbecanessa/babo/blob/main/CONTRIBUTING.md) — pull requests and standards

## CI workflows (GitHub Actions)

| Workflow | Trigger | Checks |
|----------|---------|--------|
| **`ci.yml`** | PR / push to `main` | Python 3.12 compileall, `pytest tests/`, `scripts/check-legacy-references.sh` |
| **`docs.yml`** | Push to `main` | MkDocs strict build → GitHub Pages |
| **`release-desktop.yml`** | Tag `v*.*.*` | Genesis regen, desktop build Windows + macOS, GitHub Release |

See [Desktop configuration](../configuration/desktop.md) for release tagging (`scripts/tag-desktop-release.ps1`).
