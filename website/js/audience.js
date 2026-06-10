/**
 * Landing copy per audience — ?audience=innovator|everyday (default: innovator)
 * Documented in PRODUCT.md, docs/audiences.md, website/README.md
 */
const BABO_DOWNLOAD_URL = "download/";
const BABO_RELEASES_URL = BABO_DOWNLOAD_URL;
const BABO_DISCORD_URL = "https://discord.gg/daCKzkv4z2";
const BABO_GITHUB_URL = "https://github.com/umbecanessa/babo";

window.BABO_AUDIENCE = {
  innovator: {
    id: "innovator",
    meta: {
      title: "Babo — Local agent runtime. Stop paying per thought.",
      description:
        "Open-source persistent agents: local inference, memory, team waves, channels. Cut token bills. MIT early access.",
    },
    badges: [
      { text: "Early access · Open source", type: "soft" },
      { text: "Always conscious", type: "alive" },
    ],
    hero: {
      title: "Stop paying",
      titleGradient: "per thought.",
      lead:
        "Frontier models charge per token. Chat UIs forget everything.<br><strong>Babo</strong> is an always-on agent on your hardware with memory, channels, and agent squads. No meter running.",
      ctaPrimary: { href: BABO_GITHUB_URL, label: "Star on GitHub", variant: "github" },
      ctaSecondary: { href: BABO_DISCORD_URL, label: "Join Discord", variant: "discord" },
      metaHtml: '<a href="' + BABO_DOWNLOAD_URL + '">Download desktop app</a> · Ollama · Babo Cloud optional',
      visual: {
        src: "assets/screenshots/chat.png",
        alt: "Babo agent chat and workbench",
      },
      chips: [
        { label: "Always on", pos: "tl" },
        { label: "Ollama", pos: "br" },
      ],
    },
    trust: [
      { type: "github-stars", prefix: "★", suffix: " stars" },
      { type: "text", value: "MIT · Local-first" },
      { type: "text", value: "Early access" },
    ],
    sticky: {
      primary: { href: BABO_GITHUB_URL, label: "GitHub", variant: "github" },
      secondary: { href: BABO_DISCORD_URL, label: "Discord", variant: "discord" },
    },
    punchesLabel: "Why Babo",
    punchesTitle: "Problem → solution → outcome",
    punches: [
      {
        step: "The problem",
        title: "Tokens, amnesia, terminal walls",
        text: "Cloud AI bills add up. Every chat starts from zero. Real agent stacks hide behind CLIs most people won't touch.",
      },
      {
        step: "The solution",
        title: "Always-on agent on your machine",
        text: "Babo stays running with local models, persistent memory, and real channels. Like Home Assistant, but for AI agents.",
      },
      {
        step: "What you get",
        title: "Build, extend, ship",
        text: "Ollama today. Telegram and Discord wired in. Agent squads and Kanban. MIT license: fork it, add Python skills, own the stack.",
      },
    ],
    productLead: "Real early-access UI: chat, board, integrations, and brain views.",
    captions: {
      chat: "Agentic chat, workbench, live state.",
      projects: "Teams panel, status board, sub-agent waves.",
      memory: "Memory rings and durable facts.",
      tools: "Google, WhatsApp, Discord, Slack, MCP, ClawHub.",
      brain: "Hormones, network, visual cortex.",
    },
    manifesto: {
      label: "Manifesto",
      quote:
        "LLMs are infrastructure, like electricity. They should run in your home and office, not only in a vendor’s meter.",
      snippet:
        "Most work does not need a frontier model every turn. We are building an open-source agent OS for your hardware with persistent memory, real tools, and optional cloud. See <a href=\"https://github.com/umbecanessa/neural-ledger-system\">NLS research</a> on stateful inference so cost does not explode as history grows.",
      cta: { href: "manifesto/", label: "Read the full manifesto" },
    },
    capabilities: {
      label: "One agent platform",
      title: "Built for work, not just chat",
      lead: "MIT, local-first, no telemetry. An extensible agent platform for memory, channels, and programmatic skills you can add.",
      items: [
        {
          title: "Agentic runtime",
          tags: "Plans · Tools · Sub-agents · Sleep",
          text: "Multi-step loops, verification, and consolidation so work finishes instead of looping forever.",
        },
        {
          title: "Projects & teams",
          tags: "Kanban · Waves · Delegates",
          text: "Assign tasks on a board. The orchestrator spins up teams and tracks real progress.",
        },
        {
          title: "Persistent memory",
          tags: "Cryptex · Episodes · Soul",
          text: "Identity and facts survive sessions. Stop re-explaining your life every morning.",
        },
        {
          title: "Channels",
          tags: "WhatsApp · Telegram · Google · Email",
          text: "Your PA stack in one brain. Connect messaging and workspace in-app.",
        },
        {
          title: "Local inference",
          tags: "Ollama · vLLM · OpenRouter · BYOK",
          text: "Your GPU, your LAN box, or your API keys. Predictable cost, not a sleeping meter.",
        },
        {
          title: "Extensible platform",
          tags: "Native skills · Agent tools · Channels · MCP",
          text: "Bundled skills register tools, webhooks, and config. Add Gmail-scale integrations or niche capabilities in code, then plug them in.",
        },
      ],
    },
    platform: {
      label: "Platform",
      title: "Build new capabilities. We ship the runtime.",
      text: "Babo is designed as an open agent platform, not a closed feature bundle. Contributors and power users add native Python skills, programmatic agent tools, and channel integrations that load into the same loop, memory, and relay stack.",
      stack: [
        "Bundled skills: tools, APIs, and onboarding in nls/skills/",
        "Agent tools: new loop capabilities (bash, plan, team, yours)",
        "Channels: WhatsApp-style surfaces via NestJS webhooks and relay",
        "MCP and ClawHub: external and community packages you can crystallize to native",
      ],
      cta: { href: "extension/", label: "Read the extension guide" },
    },
    quickstart: {
      label: "Quick start",
      hint: "Desktop installer recommended · self-host or fork when you are ready.",
      commands: [
        { label: "Download", value: "github.com/umbecanessa/babo/releases", href: BABO_RELEASES_URL },
        { label: "Clone", value: "git clone https://github.com/umbecanessa/babo.git", copy: true },
      ],
    },
    drift: {
      title: "Built against drift",
      text: "Orchestrator modes, team waves, and coordinator policy tie work to plans and boards, not free-form chat loops that wander and spend.",
    },
    pricing: {
      label: "Pricing",
      title: "Open source first. Cloud optional.",
      lead: "Like Home Assistant, the full agent stack is MIT. Pay only if you want Babo to host relay, channels, and resold models.",
      footnote:
        "BYOK on Babo Cloud: you pay providers directly for inference; the $4.99 platform fee covers relay and channels. Optional pay-as-you-go routing bills at upstream cost with no Babo markup.",
      plans: [
        {
          name: "Self-host",
          price: "$0",
          period: "MIT · forever",
          description: "Your hardware, your keys, full product.",
          features: [
            "Local or LAN inference (Ollama, vLLM, GX10)",
            "BYO API keys with no Babo markup",
            "Self-host Nest or desktop-only",
            "WhatsApp, Google, Telegram via your setup",
          ],
          cta: { href: BABO_DOWNLOAD_URL, label: "Download free" },
          featured: false,
        },
        {
          name: "Babo Cloud",
          price: "$4.99",
          period: "per month",
          badge: "Optional",
          description: "We run relay, inbox, and default integrations.",
          features: [
            "WhatsApp, Telegram & Gmail relay included",
            "Google, Gmail & agent inbox included",
            "Remote relay with no port forwarding",
            "Models: BYOK or pay-at-cost (no markup)",
          ],
          cta: { href: BABO_DOWNLOAD_URL, label: "Get started" },
          note: "31-day refund on your first month",
          featured: true,
        },
      ],
    },
    bottom: {
      downloadTitle: "Run it yourself",
      downloadLead: "Download the desktop app from GitHub Releases, or self-host and fork the repo.",
      contributeTitle: "We need builders",
      contributeLead:
        "Break onboarding, burn tokens, file issues. Early access is rough on purpose.",
      contributePrimary: { href: "https://github.com/umbecanessa/babo/issues/new", label: "Open an issue" },
      contributeDiscord: { href: "https://discord.gg/daCKzkv4z2", label: "Join us on Discord" },
      contributeSecondary: { href: "https://github.com/umbecanessa/babo/blob/main/CONTRIBUTING.md", label: "Contributing guide" },
    },
    nav: { showContribute: true },
    integratesLabel: "Plugs into",
    integrations: [
      { id: "google", name: "Google Workspace" },
      { id: "gmail", name: "Gmail" },
      { id: "google-calendar", name: "Google Calendar" },
      { id: "whatsapp", name: "WhatsApp" },
      { id: "telegram", name: "Telegram" },
      { id: "discord", name: "Discord" },
      { id: "slack", name: "Slack" },
    ],
    marquee: [
      "Local inference",
      "Ollama · vLLM · GX10",
      "Persistent memory",
      "Team waves",
      "Kanban pickup",
      "WhatsApp · Telegram · Discord · Slack",
      "Open source · MIT",
      "Native skills · MCP",
      "Extension guide",
      "NLS research",
    ],
  },

  everyday: {
    id: "everyday",
    meta: {
      title: "Babo — Your personal AI agent. On your computer.",
      description:
        "A real AI agent on your PC. Talk on WhatsApp, connect Gmail, assign tasks. Personal assistant power without the engineer setup.",
    },
    badges: [
      { text: "Personal AI agent", type: "soft" },
      { text: "WhatsApp · Gmail · Chat", type: "soft" },
    ],
    hero: {
      title: "Your personal agent.",
      titleGradient: "On your computer.",
      lead:
        "<strong>Babo</strong> is a real AI agent that runs locally. Talk to it on WhatsApp, connect Gmail, assign tasks, and let it work while you live. It reads, writes, codes when needed, and remembers you. No terminal. No engineering degree.",
      ctaPrimary: { href: BABO_DOWNLOAD_URL, label: "Download free" },
      ctaSecondary: { href: BABO_DISCORD_URL, label: "Join Discord", variant: "discord" },
      metaHtml: '<a href="#product">See how it works</a> · Guided setup · Private on your machine',
      visual: {
        src: "assets/screenshots/tools.png",
        alt: "Babo connected to Gmail, WhatsApp, and messaging apps",
      },
      chips: [
        { label: "WhatsApp", pos: "tl" },
        { label: "On your PC", pos: "br" },
      ],
    },
    trust: [
      { type: "text", value: "Free download" },
      { type: "text", value: "Talk on WhatsApp" },
      { type: "text", value: "Private · local" },
    ],
    sticky: {
      primary: { href: BABO_DOWNLOAD_URL, label: "Download" },
      secondary: { href: BABO_DISCORD_URL, label: "Discord", variant: "discord" },
    },
    punchesLabel: "Real agent power",
    punchesTitle: "Personal assistant · without the complexity",
    punches: [
      {
        step: "What it is",
        title: "An agent on your computer",
        text: "Not a browser tab. Babo stays running on your machine, remembers you, and connects to the apps you already use.",
      },
      {
        step: "How you use it",
        title: "Talk to it anywhere",
        text: "WhatsApp, chat, or email. Assign tasks in plain language. Babo reads context, picks up work, and reports back.",
      },
      {
        step: "What you get",
        title: "A PA that can actually do things",
        text: "Kanban tasks, Gmail, Calendar, and code when needed. Real agent capabilities with a setup wizard, not a command line.",
      },
    ],
    productLead: "The actual app, not mockups.",
    captions: {
      chat: "Talk to your agent. It uses tools and remembers context.",
      projects: "Teams panel and status board to assign and track tasks.",
      memory: "It remembers people, projects, and facts.",
      tools: "Connect Google, WhatsApp, Discord, Slack, and more in clicks.",
      brain: "Optional view of what your agent is doing inside.",
    },
    manifesto: {
      label: "Manifesto",
      quote: "Every family deserves an agent on their own computer, not another subscription that forgets them.",
      snippet:
        "Babo is early access and local-first: one brain for your tasks, email, and messages, with no terminal required. The long-term vision is an agent in every home; today you can install the software.",
      cta: { href: "manifesto/", label: "Read the manifesto" },
    },
    capabilities: {
      label: "Your personal agent",
      title: "One app for your whole life",
      lead: "Talk on WhatsApp · connect Gmail · assign tasks · private on your machine.",
      items: [
        {
          title: "Talk & delegate",
          tags: "Chat · Voice · Tools",
          text: "Ask in plain language; the agent uses tools and remembers what you meant.",
        },
        {
          title: "Task board",
          tags: "Kanban · Pickup · Progress",
          text: "Drop tasks and Babo picks them up instead of losing them in a thread list.",
        },
        {
          title: "Remembers you",
          tags: "People · Projects · Facts",
          text: "Memory that persists across days, not a tab that resets overnight.",
        },
        {
          title: "Your apps",
          tags: "Gmail · Calendar · WhatsApp",
          text: "Connect Google and messaging with clicks, not a hacker checklist.",
        },
        {
          title: "On your computer",
          tags: "Desktop · Private · MIT",
          text: "Data stays local. Optional Babo Cloud only if you want us to host the boring parts.",
        },
        {
          title: "Early access",
          tags: "Wizard · Discord · Open source",
          text: "Rough edges today, improving fast with the community.",
        },
      ],
    },
    quickstart: {
      label: "Quick start",
      hint: "Download the desktop app. The wizard handles Python and setup.",
      commands: [
        { label: "Download", value: "github.com/umbecanessa/babo/releases", href: BABO_RELEASES_URL },
      ],
    },
    drift: null,
    pricing: {
      label: "Pricing",
      title: "Free on your computer. Cloud if you want easy.",
      lead: "Download Babo at no cost. Babo Cloud is optional for hosted Google, email, WhatsApp relay, and models without running a server.",
      footnote:
        "No separate fee to connect Gmail or Google Calendar on Babo Cloud. First month refundable within 31 days. Optional pay-as-you-go model routing bills at upstream cost.",
      plans: [
        {
          name: "Babo Desktop",
          price: "$0",
          period: "on your machine",
          description: "The full app with wizard setup and no terminal.",
          features: [
            "Agent, memory, Kanban, and channels",
            "Your data stays local",
            "Connect Google & messaging yourself",
            "Open source · early access",
          ],
          cta: { href: BABO_DOWNLOAD_URL, label: "Download free" },
          featured: false,
        },
        {
          name: "Babo Cloud",
          price: "$4.99",
          period: "per month",
          badge: "Easiest path",
          description: "We handle relay, email, and hosted integrations.",
          features: [
            "Gmail, Google Calendar & inbox built in",
            "WhatsApp & Telegram relay included",
            "Models via BYOK or pay-at-cost (no markup)",
            "No server or DevOps required",
          ],
          cta: { href: BABO_DOWNLOAD_URL, label: "Start with Babo" },
          note: "31-day refund on your first month",
          featured: true,
        },
      ],
    },
    bottom: {
      downloadTitle: "Get started",
      downloadLead: "Get the desktop installer from GitHub Releases. The wizard does the hard parts.",
      contributeTitle: "Help us polish",
      contributeLead: "Early access means rough edges. Tell us what broke, join the community, or read the quickstart.",
      contributePrimary: { href: "https://github.com/umbecanessa/babo/issues/new", label: "Send feedback" },
      contributeDiscord: { href: "https://discord.gg/daCKzkv4z2", label: "Join us on Discord" },
      contributeSecondary: { href: "getting-started/quickstart/", label: "Quickstart guide" },
    },
    nav: { showContribute: false },
    integratesLabel: "Connects to the apps you already use",
    integrations: [
      { id: "google", name: "Google Workspace" },
      { id: "gmail", name: "Gmail" },
      { id: "google-calendar", name: "Google Calendar" },
      { id: "whatsapp", name: "WhatsApp" },
      { id: "telegram", name: "Telegram" },
      { id: "discord", name: "Discord" },
      { id: "slack", name: "Slack" },
    ],
    marquee: [
      "Beyond ChatGPT",
      "Gmail · Google Calendar",
      "WhatsApp · Telegram · Discord · Slack",
      "Kanban tasks",
      "No terminal",
      "Private · local",
      "Guided setup",
      "Early access",
    ],
  },
};
