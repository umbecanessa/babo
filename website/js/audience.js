/** Landing copy per audience — ?audience=innovator|everyday (default: innovator) */
const BABO_DOWNLOAD_URL = "download/";
const BABO_RELEASES_URL = BABO_DOWNLOAD_URL;

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
        "An <strong>extensible agent platform</strong> on your hardware — Ollama, vLLM, GX10. Native skills, channels, and tools plug in; persistent memory and orchestration so agents <strong>drift less</strong> and <strong>burn fewer tokens</strong>.",
      ctaPrimary: { href: "https://github.com/umbecanessa/babo", label: "Star on GitHub" },
      ctaSecondary: { href: BABO_DOWNLOAD_URL, label: "Download" },
      meta: "MIT · Local-first · BYOK or Babo Cloud for the boring parts",
    },
    punchesLabel: "Why builders switch",
    punchesTitle: "Agent stack, not chat UI",
    punches: [
      {
        title: "Local inference, predictable cost",
        text: "90% of work doesn’t need frontier pricing. Your box, your LAN GPU, your bill — not a meter that runs while you sleep.",
      },
      {
        title: "Persistent — less drift, less replay",
        text: "Plans, Kanban, memory, sleep. The agent remembers the job instead of re-processing your life story every turn.",
      },
      {
        title: "Extend it — plug-in capabilities",
        text: "Ship native NLS skills, agent tools, and new channels in Python. MCP and ClawHub install community packages; crystallize the ones you rely on. A platform, not a frozen app.",
      },
    ],
    productLead: "Real early-access UI — chat, board, integrations, brain.",
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
        "LLMs are infrastructure — like electricity. They should run in your home and office, not only in a vendor’s meter.",
      snippet:
        "Most work does not need a frontier model every turn. We are building an open-source agent OS for your hardware — persistent memory, real tools, optional cloud — with <a href=\"https://github.com/umbecanessa/neural-ledger-system\">NLS research</a> on stateful inference so cost does not explode as history grows.",
      cta: { href: "manifesto/", label: "Read the full manifesto" },
    },
    capabilities: {
      label: "One agent platform",
      title: "Built for work, not just chat",
      lead: "MIT · local-first · no telemetry — an extensible agent platform: memory, channels, and programmatic skills you can add.",
      items: [
        {
          title: "Agentic runtime",
          tags: "Plans · Tools · Sub-agents · Sleep",
          text: "Multi-step loops, verification, and consolidation — work finishes instead of looping forever.",
        },
        {
          title: "Projects & teams",
          tags: "Kanban · Waves · Delegates",
          text: "Assign tasks on a board; orchestrator spins up teams and tracks real progress.",
        },
        {
          title: "Persistent memory",
          tags: "Cryptex · Episodes · Soul",
          text: "Identity and facts survive sessions — stop re-explaining your life every morning.",
        },
        {
          title: "Channels",
          tags: "WhatsApp · Telegram · Google · Email",
          text: "Your PA stack in one brain — connect messaging and workspace in-app.",
        },
        {
          title: "Local inference",
          tags: "Ollama · vLLM · OpenRouter · BYOK",
          text: "Your GPU, your LAN box, or your API keys — predictable cost, not a sleeping meter.",
        },
        {
          title: "Extensible platform",
          tags: "Native skills · Agent tools · Channels · MCP",
          text: "Bundled skills register tools, webhooks, and config — add Gmail-scale integrations or niche capabilities in code, then plug them in.",
        },
      ],
    },
    platform: {
      label: "Platform",
      title: "Build new capabilities — we ship the runtime",
      text: "Babo is designed as an open agent platform, not a closed feature bundle. Contributors and power users add native Python skills, programmatic agent tools, and channel integrations that load into the same loop, memory, and relay stack.",
      stack: [
        "Bundled skills — tools + APIs + onboarding in nls/skills/",
        "Agent tools — new loop capabilities (bash, plan, team, yours)",
        "Channels — WhatsApp-style surfaces via NestJS webhooks + relay",
        "MCP & ClawHub — external and community packages, crystallize to native",
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
      text: "Orchestrator modes, team waves, coordinator policy — work tied to plans and boards, not free-form chat loops that wander and spend.",
    },
    pricing: {
      label: "Pricing",
      title: "Open source first. Cloud optional.",
      lead: "Like Home Assistant — the full agent stack is MIT. Pay only if you want Babo to host relay, channels, and resold models.",
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
            "BYO API keys — no Babo markup",
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
            "Remote relay — no port forwarding",
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
      downloadLead: "Download the desktop app from GitHub Releases — or self-host and fork the repo.",
      contributeTitle: "We need builders",
      contributeLead:
        "Break onboarding, burn tokens, file issues — early access is rough on purpose.",
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
      title: "Babo — Beyond ChatGPT. One agent for your life.",
      description:
        "Personal AI on your computer: tasks, Gmail, Google, WhatsApp. Guided setup, no terminal. Early access.",
    },
    badges: [
      { text: "Early access", type: "soft" },
      { text: "No terminal required", type: "soft" },
    ],
    hero: {
      title: "Assign it work.",
      titleGradient: "Go live your life.",
      lead:
        "You already pay for AI subscriptions. Babo is the next step — <strong>one agent</strong> on your computer that remembers you, connects to <strong>Gmail, Google, and messages</strong>, and picks up tasks from a board. Setup walks you through it. No command line. Ever.",
      ctaPrimary: { href: BABO_DOWNLOAD_URL, label: "Download Babo" },
      ctaSecondary: { href: "#product", label: "See it work" },
      meta: "Private · On your machine · Guided connect for email & channels",
    },
    punchesLabel: "More than a chat window",
    punchesTitle: "What ChatGPT doesn’t do",
    punches: [
      {
        title: "Drop tasks — it picks them up",
        text: "A Kanban board linked to real work. Not another thread you have to re-explain tomorrow morning.",
      },
      {
        title: "Your PA channels, one brain",
        text: "Gmail, Calendar, WhatsApp, Telegram — connect in-app. Answer and coordinate from one place with guardrails built in.",
      },
      {
        title: "Lives on your computer",
        text: "Your data stays local. You’re not paste-sharing life into a browser tab that forgets you overnight.",
      },
    ],
    productLead: "The actual app — not mockups.",
    captions: {
      chat: "Talk to your agent. It uses tools and remembers context.",
      projects: "Teams panel and status board — assign and track tasks.",
      memory: "It remembers people, projects, and facts.",
      tools: "Connect Google, WhatsApp, Discord, Slack, and more in clicks.",
      brain: "Optional — see what your agent is doing inside.",
    },
    manifesto: {
      label: "Manifesto",
      quote: "Every family deserves an agent on their own computer — not another subscription that forgets them.",
      snippet:
        "Babo is early access and local-first: one brain for your tasks, email, and messages — no terminal required. The long-term vision is an agent in every home; today you can install the software.",
      cta: { href: "manifesto/", label: "Read the manifesto" },
    },
    capabilities: {
      label: "More than ChatGPT",
      title: "One app for your week",
      lead: "Private on your machine · guided setup · no terminal for everyday use.",
      items: [
        {
          title: "Talk & delegate",
          tags: "Chat · Voice · Tools",
          text: "Ask in plain language; the agent uses tools and remembers what you meant.",
        },
        {
          title: "Task board",
          tags: "Kanban · Pickup · Progress",
          text: "Drop tasks — it picks them up instead of losing them in a thread list.",
        },
        {
          title: "Remembers you",
          tags: "People · Projects · Facts",
          text: "Memory that persists across days — not a tab that resets overnight.",
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
          text: "Rough edges today — improving fast with the community.",
        },
      ],
    },
    quickstart: {
      label: "Quick start",
      hint: "Download the desktop app — the wizard handles Python and setup.",
      commands: [
        { label: "Download", value: "github.com/umbecanessa/babo/releases", href: BABO_RELEASES_URL },
      ],
    },
    drift: null,
    pricing: {
      label: "Pricing",
      title: "Free on your computer. Cloud if you want easy.",
      lead: "Download Babo at no cost. Babo Cloud is optional — hosted Google, email, WhatsApp relay, and models without running a server.",
      footnote:
        "No separate fee to connect Gmail or Google Calendar on Babo Cloud. First month refundable within 31 days. Optional pay-as-you-go model routing bills at upstream cost.",
      plans: [
        {
          name: "Babo Desktop",
          price: "$0",
          period: "on your machine",
          description: "The full app — wizard setup, no terminal.",
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
      downloadLead: "Get the desktop installer from GitHub Releases — the wizard does the hard parts.",
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
