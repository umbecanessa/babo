# Audiences

The public homepage at [babo.agency](https://babo.agency/) serves **two audiences** with the same product and different copy.

## Innovators (default)

**Who:** Builders, self-hosters, and open-source contributors who want a full agent stack on their hardware.

**What they care about:** Local inference (Ollama, vLLM), MIT license, memory, team waves, MCP/ClawHub extensibility, no per-token meter.

**Homepage CTA:** Star on GitHub, join Discord, read extension docs.

**URL:** `/` (default). Query `?audience=innovator` also works.

## Everyday (early adopters)

**Who:** People who want a **personal AI agent** on their computer without living in a terminal.

**What they care about:** WhatsApp and Gmail, task board, guided desktop installer, privacy on their machine.

**Homepage CTA:** Download free, see how it works, quickstart guide.

**URL:** `/?audience=everyday` or `/?audience=home`.

## How switching works

| Mechanism | Detail |
|-----------|--------|
| Site UI | **Builders** / **Everyone** toggle in the homepage nav |
| Query param | `audience=everyday` or `utm_content=everyday` |
| Persistence | `sessionStorage` key `babo_audience` |
| Copy source | [`website/js/audience.js`](https://github.com/umbecanessa/babo/blob/main/website/js/audience.js) |

After the first visit, the last chosen audience is restored even without a query param.

## Docs and product alignment

- Strategic context: [PRODUCT.md](https://github.com/umbecanessa/babo/blob/main/PRODUCT.md)
- Visual tokens: [DESIGN.md](https://github.com/umbecanessa/babo/blob/main/DESIGN.md)
- Marketing site notes: [website/README.md](https://github.com/umbecanessa/babo/blob/main/website/README.md)

**Everyday users** should start with [Quickstart](getting-started/quickstart.md) and [First run & setup](guides/first-run-and-setup.md).

**Innovators** should start with [Installation](getting-started/installation.md), [Self-hosting](configuration/self-hosting.md), and the [Extension guide](extension/index.md).
