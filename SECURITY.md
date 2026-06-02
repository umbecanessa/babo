# Security

Report security issues privately to the repository maintainers — do not open public issues for undisclosed vulnerabilities.

## Secrets

- Never commit `.env`, API keys, or GitHub tokens
- Desktop auto-update uses `GH_TOKEN` or `GITHUB_TOKEN` from the environment at build/release time only

## Runtime

- The local Python runtime binds to `127.0.0.1` by default
- Configure NestJS JWT secrets for any exposed backend deployment
