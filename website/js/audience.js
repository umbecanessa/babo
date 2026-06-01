/** Landing copy per audience — ?audience=innovator|everyday (default: innovator) */
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
        "Run the brain on your hardware — Ollama, vLLM, GX10. Persistent memory and real orchestration so agents <strong>drift less</strong> and <strong>burn fewer tokens</strong>. Babo handles the plumbing: WhatsApp, relay, guided onboarding.",
      ctaPrimary: { href: "https://github.com/umbecanessa/babo", label: "Star on GitHub" },
      ctaSecondary: { href: "#download", label: "Install" },
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
        title: "You run the model. We can host the wiring.",
        text: "BYO weights + optional Babo Cloud / relay for channels and hosted inference. OpenClaw-class depth without solo DevOps.",
      },
    ],
    productLead: "Real early-access UI — chat, board, integrations, brain.",
    captions: {
      chat: "Agentic chat, workbench, live state.",
      projects: "Waves, Kanban, sub-agent teams.",
      memory: "Memory rings and durable facts.",
      tools: "Google, WhatsApp, MCP, ClawHub.",
      brain: "Hormones, network, visual cortex.",
    },
    manifesto: {
      quote:
        "Intelligence sold like water — metered tokens forever — is the future we’re refusing.",
      body:
        "Babo is the bet on a box under your desk: open source, local brain, optional cloud only where it saves you time. <a href=\"https://github.com/umbecanessa/neural-ledger-system\">NLS research</a> points at stateful inference so cost stays flat as history grows.",
      showNlsLink: true,
    },
    drift: {
      title: "Built against drift",
      text: "Orchestrator modes, team waves, coordinator policy — work tied to plans and boards, not free-form chat loops that wander and spend.",
    },
    bottom: {
      downloadTitle: "Run it yourself",
      downloadLead: "Desktop wizard, self-host, or fork the repo.",
      contributeTitle: "We need builders",
      contributeLead:
        "Break onboarding, burn tokens, file issues — early access is rough on purpose.",
      contributePrimary: { href: "https://github.com/umbecanessa/babo/issues/new", label: "Open an issue" },
      contributeSecondary: { href: "https://github.com/umbecanessa/babo/blob/main/CONTRIBUTING.md", label: "Contributing guide" },
    },
    nav: { showContribute: true },
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
      ctaPrimary: { href: "#download", label: "Download Babo" },
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
      projects: "Assign tasks. Watch progress on a board.",
      memory: "It remembers people, projects, and facts.",
      tools: "Connect Google, WhatsApp, and more in clicks.",
      brain: "Optional — see what your agent is doing inside.",
    },
    manifesto: {
      quote: "You shouldn’t need a hacker setup to get an agent that runs your week.",
      body:
        "Babo brings the power people get from terminal-heavy agents — with a desktop wizard and guardrails for people who’d rather not live in a shell. Open source, early access, getting smoother every week.",
      showNlsLink: false,
    },
    drift: null,
    bottom: {
      downloadTitle: "Get started",
      downloadLead: "Download the desktop app — the wizard does the hard parts.",
      contributeTitle: "Help us polish",
      contributeLead: "Early access means rough edges. Tell us what broke or confused you.",
      contributePrimary: { href: "https://github.com/umbecanessa/babo/issues/new", label: "Send feedback" },
      contributeSecondary: { href: "getting-started/quickstart/", label: "Quickstart guide" },
    },
    nav: { showContribute: false },
  },
};
