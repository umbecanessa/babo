# Glossary

| Term | Meaning |
|------|---------|
| **Agent** | One persistent AI identity with its own memory, config, and workspace |
| **AgentRuntime** | Python class (`nls/runtime/agent_runtime.py`) orchestrating chat, tools, and memory for one agent |
| **Agentic loop** | Multi-turn LLM + tool cycle until task completion (`nls/agentic/loop.py`) |
| **AKU** | Atomic Knowledge Unit — structured learning artifact from bridge extraction |
| **ANS** | Autonomic Nervous System — signal buffer and sleep triggers (`nls/brain/autonomic.py`) |
| **BYO inference** | User supplies OpenAI-compatible API URL + model |
| **Channel** | External messaging surface (web, WhatsApp, Telegram, email) |
| **ChannelRelayClient** | Outbound WS from desktop Python → NestJS (`nls/runtime/channels.py`) |
| **ClawHub** | Community skill marketplace proxied by NestJS |
| **Cryptex** | Layered long-term memory rings (`nls/brain/working_memory.py`) |
| **Consciousness scheduler** | Chooses which agents run autonomous inner loops |
| **Consolidation sleep** | LLM summarization into durable memory (not weight training) |
| **Delegate / sub-agent** | Child agentic run with scoped context (`nls/agentic/orchestrator.py`) |
| **Desktop hub** | User machine running Electron + Python runtime |
| **DMN** | Default Mode Network — idle replay/exploration (`nls/brain/dmn.py`) |
| **DomainDB** | Searchable fact store per agent |
| **Genesis** | Template used to create a new agent's initial config |
| **Inner loop** | Autonomous heartbeat when agent is CONSCIOUS (`nls/engine/inner_loop.py`) |
| **MCP** | Model Context Protocol — external tool servers |
| **Native skill** | Python package under `nls/skills/` with `register(app, ctx)` |
| **AgentSkill** | Instruction skill with `SKILL.md` frontmatter |
| **Crystallized skill** | Instruction skill converted to native Python module under `nls/skills/` |
| **Relay mode** | Web chat routed NestJS → desktop via relay WebSocket |
| **runtimeAgentId** | Python-side agent id (directory name under `data/agents/`) |
| **DB agent id** | UUID in Postgres — mapped to `runtimeAgentId` |
| **Signal** | Typed learning/event (LEARN, REFLECT, BOND, …) feeding hormones |
| **Skill** | Bundled or installed capability (tools + routers + bridges) |
| **Soul** | Identity package — values, axioms (`nls/identity/soul.py`) |
| **Thalamus** | Routing / domain classification (`nls/engine/` + calibrator) |
| **WM** | Working memory — salience-weighted short-term slots |
| **Workspace** | Agent files under `data/agents/{id}/workspace/` |
