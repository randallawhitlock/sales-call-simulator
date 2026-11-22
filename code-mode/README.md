# code-mode

An implementation of Anthropic's "Code execution with MCP" pattern. Instead of loading every MCP
tool definition into context and routing every result through the model, the agent browses tools as
files, writes TypeScript that calls them, and sees only what the code prints.

```
tools → files → code → filter → answer
```

| Part | Where |
|---|---|
| 1. Tools as files | `src/codegen.ts` writes `servers/<server>/<tool>.ts`, one typed wrapper per MCP tool |
| 2. Load on demand | The system prompt lists only server names. The agent uses `list_files`/`read_file` to open what it needs |
| 3. Filter in code | `src/sandbox.ts` runs the agent's code. Output is capped at about 8k chars |
| 4. Loops in code | Polling and retries run inside the script (`skills/wait-until`) |
| 5. Private by default | `src/pii.ts` swaps emails/phones for `[EMAIL_1]`/`[PHONE_1]` in tool results, and swaps them back in tool arguments |
| 6. Skills | The agent saves working code to `skills/<name>/` with a `SKILL.md`. Skills are listed in the prompt and imported as `#skills/<name>/index.ts` |

## Architecture

```
Claude ⇄ src/agent.ts (loop, 4 tools: list_files, read_file, write_file, execute_code)
              │
              └─ src/sandbox.ts ── fork ──► child process (Node --permission)
                     ▲                          agent script imports #servers/... 
                     │ IPC: tool_call / tool_result   └─ src/runtime/client.ts
              src/host.ts (McpHost)
                ├─ MCP connections (stdio / streamable HTTP)
                └─ PII Tokenizer (token map never leaves the host)
```

- The sandbox has no credentials and no MCP connections. It cannot read `mcp.config.json`, write
  outside `workspace/` and `skills/`, or spawn processes.
- The Node permission model **does not block network access**. For untrusted workloads, run the
  whole thing in a container or VM with egress blocked.
- Tokenization happens at the MCP boundary, so code also sees tokens. That means code can't filter
  on email contents. Set `"pii": { "enabled": false }` if you need that.

## Use

Requires Node ≥ 22.18 (runs TypeScript natively, no build step).

```bash
npm install
npm run codegen                     # connect to servers in mcp.config.json, write ./servers
npm test                            # offline: demo servers + mocked Claude
npm run bench                       # context cost: direct vs code mode
export ANTHROPIC_API_KEY=...
npm run agent -- "Find won deals over 49k in the west region and add the first one to the CRM as a Lead"
npm run agent                       # interactive
```

### Your own MCP servers

Edit `mcp.config.json`, then re-run `npm run codegen`. `${VAR}` values are expanded from the
environment, so keep secrets out of the file.

```json
{
  "servers": {
    "github": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"], "env": { "GITHUB_TOKEN": "${GITHUB_TOKEN}" } },
    "linear": { "url": "https://mcp.example.com/mcp", "headers": { "Authorization": "Bearer ${LINEAR_TOKEN}" } }
  },
  "pii": { "enabled": true, "patterns": { "SSN": "\\b\\d{3}-\\d{2}-\\d{4}\\b" } }
}
```

### Settings

| Env var | Default |
|---|---|
| `CODE_MODE_MODEL` | `claude-opus-5-5` |
| `CODE_MODE_EFFORT` | `high` |
| `CODE_MODE_MAX_TURNS` | `40` |
| `CODE_MODE_CONFIG` | `./mcp.config.json` |

Requests use adaptive thinking, prompt caching and server-side refusal fallback (`fallbacks: "default"`).

## Bench (demo servers, ~4 chars/token estimate)

```
Tool definitions (6 tools)
  direct:    448 tokens        code mode: 797 tokens (flat as tools grow)
  ~75 tokens/tool direct -> break-even at ~11 tools; at 1,000 tools direct ≈ 74,667
Data flow (10,000-row sheet)
  direct:    335,860 tokens    code mode: 163 tokens (99.95% saved)
```

With a few tools, the code-mode system prompt costs more than the tool definitions it replaces.
The savings come from large tool catalogs and large intermediate results.
