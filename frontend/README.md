# Babo frontend

Angular UI for chat, projects, memory, brain dashboard, and integrations.

Full product docs: [docs/index.md](../docs/index.md) · [Documentation site](https://umbecanessa.github.io/babo/)

## Development

```bash
npm install
npm start
```

Open `http://localhost:4200/`.

For Electron builds, use the [desktop app](../desktop/) (`npm run build:angular` runs from `desktop/package.json` with `--configuration=electron`).

## Angular CLI

This project uses [Angular CLI](https://angular.dev/tools/cli) 21.x.

```bash
ng build                  # production web build
ng build --configuration=electron   # bundled into desktop app
ng test
```

See [Angular CLI reference](https://angular.dev/tools/cli) for scaffolding and other commands.
