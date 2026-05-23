# Babo tool definitions

Each `.json` file in this directory defines a tool the Babo agent can use.

**Adding a tool = adding a JSON file.** No Python code needed for common patterns.

## Tool Catalog (50 tools)

### File System (5)
| Tool | Description | Executor |
|------|-------------|----------|
| `file_read` | Read file contents | file_read |
| `file_write` | Create/overwrite files | file_write |
| `file_edit` | Find-and-replace edits | file_edit |
| `file_search` | Regex search across files | shell |
| `file_tree` | Directory tree listing | shell |

### Code & IDE (5)
| Tool | Description | Executor |
|------|-------------|----------|
| `terminal` | Run shell commands | shell |
| `git` | Full Git operations | shell |
| `test_runner` | Run test suites (pytest, jest) | shell |
| `code_analyze` | Extract symbols, TODOs, stats | shell |
| `diff_patch` | Generate/apply unified diffs | shell |

### Knowledge & Research (5)
| Tool | Description | Executor |
|------|-------------|----------|
| `web_search` | Search the web (DuckDuckGo) | http |
| `wikipedia` | Search/read Wikipedia articles | http |
| `arxiv_search` | Search academic papers on arXiv | http |
| `youtube_transcript` | Extract video transcripts | shell |
| `rss_reader` | Parse RSS/Atom feeds | shell |

### Communication (7)
| Tool | Description | Executor |
|------|-------------|----------|
| `email` | Send emails via SMTP | python |
| `slack` | Send/read Slack messages | http |
| `discord` | Send Discord messages | http |
| `telegram` | Telegram Bot API | http |
| `whatsapp` | WhatsApp Business API | http |
| `sms` | Send SMS via Twilio | http |
| `notification` | Desktop notifications | shell |

### System & Desktop (8)
| Tool | Description | Executor |
|------|-------------|----------|
| `system_info` | OS, CPU, RAM, disk info | shell |
| `clipboard` | Read/write clipboard | shell |
| `screenshot` | Capture screenshots | python |
| `app_launcher` | Open URLs/files/apps | python |
| `process_manager` | List/find/kill processes | shell |
| `env_manager` | Read env vars and .env files | shell |
| `keychain` | Secure credential storage | shell |
| `docker` | Manage containers & images | shell |

### Data & Analytics (6)
| Tool | Description | Executor |
|------|-------------|----------|
| `api_client` | Generic HTTP API calls | http |
| `spreadsheet` | Read/write CSV/Excel | python |
| `local_db` | Query local SQLite databases | shell |
| `sql_query` | SQL queries (SQLite/PG/MySQL) | shell |
| `json_transform` | Parse, query, transform JSON | shell |
| `chart_generate` | Create charts (matplotlib) | python |

### Creative & Media (4)
| Tool | Description | Executor |
|------|-------------|----------|
| `image_generate` | Text-to-image generation | shell |
| `audio_transcribe` | Speech-to-text (Whisper) | shell |
| `tts` | Text-to-speech | shell |
| `pdf_tools` | PDF text extraction | shell |

### Productivity & Utility (8)
| Tool | Description | Executor |
|------|-------------|----------|
| `browser` | Automated web browsing | python |
| `calendar` | Calendar events (Google/Outlook) | python |
| `calculator` | Safe math evaluation | shell |
| `translate` | Language translation | http |
| `regex_tool` | Regex match/replace | shell |
| `hash_encode` | Hash, base64, JWT decode | shell |
| `compress` | Zip/tar archive management | shell |
| `http_server` | Local HTTP file server | shell |

### Agent-Internal (2)
| Tool | Description | Executor |
|------|-------------|----------|
| `note_memory` | Persistent scratch notes | python |
| `cron_scheduler` | Scheduled recurring tasks | python |

## Executor Types

| Type | What it does | Requires Python? |
|------|-------------|-----------------|
| `http` | Make an HTTP request | No |
| `shell` | Run a shell command | No |
| `file_read` | Read a file | No |
| `file_write` | Write a file | No |
| `python` | Run a Python function | Yes (handler class) |
| `composite` | Chain multiple executors | No |

## Template Variables

Use `{{variable}}` in executor configs. Variables come from:
- `{{args.param}}` -- from the tool's input_schema
- `{{env.VAR_NAME}}` -- from environment variables

## Biological Metadata

Each tool declares NLS biological integration fields:
- `category` -- `sense | think | act | communicate | create`
- `hormone_affinity` -- which hormone this tool stimulates
- `base_effort` -- 0.0-1.0, how "costly" the tool feels to use
- `learning_yield` -- `low | medium | high`, how much the agent learns
- `risk_level` -- `read | write | execute | admin`
- `permissions` -- required security permissions

## Tool Manuals (Onboarding)

Every tool includes a `manual` section — structured documentation that the
agent studies through the NLS education pipeline when a new tool is enabled:

```json
"manual": {
  "overview": "When and why to use this tool (1-2 sentences)",
  "examples": [
    { "description": "What this example does", "input": { "param": "value" }, "output": "Expected result" }
  ],
  "tips": ["Best practice 1", "Best practice 2"],
  "edge_cases": ["Warning about failure mode"],
  "related_tools": ["other_tool_1", "other_tool_2"]
}
```

### Onboarding pipeline

When an agent gains access to a new tool (from the Tools UI or admin API), the runtime typically:

1. **Study** — Manual sections are fed as teaching turns via `process_message()`
2. **Sleep** — A consolidation cycle writes durable facts into memory (Cryptex / DomainDB)
3. **Recall** — A short quiz verifies the agent learned the tool's purpose and parameters
4. **Extra sleep** — If recall is below threshold, another consolidation cycle may run

Product onboarding is triggered through the server admin/skills routes and the desktop UI — not a separate weight-training CLI.

## Adding a New Tool

1. Create a JSON file in this directory
2. Define the manifest metadata (name, description, category, etc.)
3. Add a `manual` section with overview, examples, tips, and edge cases
4. Define the `executor` section with the appropriate type
5. Run `nls tool-onboard <agent> -t <tool_name>` to teach the agent
6. Restart the agent -- the tool is auto-discovered

No Python code needed for `http`, `shell`, `file_read`, or `file_write` executors.

## Example

```json
{
  "name": "weather",
  "description": "Get current weather for a city",
  "category": "sense",
  "hormone_affinity": "norepinephrine",
  "base_effort": 0.2,
  "risk_level": "read",
  "permissions": ["network.outbound"],
  "input_schema": {
    "type": "object",
    "properties": {
      "city": { "type": "string", "description": "City name" }
    },
    "required": ["city"]
  },
  "executor": {
    "type": "http",
    "method": "GET",
    "url": "https://wttr.in/{{args.city}}?format=j1"
  }
}
```
