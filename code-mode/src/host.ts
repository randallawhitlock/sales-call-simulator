/**
 * McpHost: owns MCP connections and the PII token map. Lives outside the sandbox.
 * Sandbox code reaches it only through callTool() over IPC.
 */
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import type { Tool } from "@modelcontextprotocol/sdk/types.js";
import { type Config, ROOT, isHttp, loadConfig } from "./config.ts";
import { Tokenizer } from "./pii.ts";

export class McpHost {
  readonly config: Config;
  readonly tokenizer: Tokenizer;
  private clients = new Map<string, Client>();

  constructor(config: Config = loadConfig()) {
    this.config = config;
    this.tokenizer = new Tokenizer(config.pii);
  }

  async client(server: string): Promise<Client> {
    const existing = this.clients.get(server);
    if (existing) return existing;
    const cfg = this.config.servers[server];
    if (!cfg) throw new Error(`Unknown MCP server "${server}". Configured: ${Object.keys(this.config.servers).join(", ")}`);
    const client = new Client({ name: "code-mode", version: "0.1.0" });
    const transport = isHttp(cfg)
      ? new StreamableHTTPClientTransport(new URL(cfg.url), { requestInit: { headers: cfg.headers } })
      : new StdioClientTransport({
          command: cfg.command,
          args: cfg.args,
          cwd: cfg.cwd ?? ROOT,
          env: { ...(process.env as Record<string, string>), ...cfg.env },
          stderr: "ignore",
        });
    await client.connect(transport);
    this.clients.set(server, client);
    return client;
  }

  async listTools(server: string): Promise<Tool[]> {
    const client = await this.client(server);
    const tools: Tool[] = [];
    let cursor: string | undefined;
    do {
      const page = await client.listTools(cursor ? { cursor } : {});
      tools.push(...page.tools);
      cursor = page.nextCursor;
    } while (cursor);
    return tools;
  }

  /**
   * Calls a tool. Args are detokenized (real PII goes to the server);
   * the result is unwrapped to plain data and tokenized before it is returned.
   */
  async callTool(server: string, tool: string, args: Record<string, unknown> = {}): Promise<unknown> {
    const client = await this.client(server);
    const res = await client.callTool({ name: tool, arguments: this.tokenizer.detokenize(args) });
    const data = unwrap(res);
    if (res.isError) {
      const msg = typeof data === "string" ? data : JSON.stringify(data);
      throw new Error(this.tokenizer.tokenizeString(`${server}.${tool} failed: ${msg}`));
    }
    return this.tokenizer.tokenize(data);
  }

  async close(): Promise<void> {
    await Promise.allSettled([...this.clients.values()].map((c) => c.close()));
    this.clients.clear();
  }
}

/** MCP results -> plain JS: structuredContent, else parsed JSON text, else text, else raw content. */
function unwrap(res: Awaited<ReturnType<Client["callTool"]>>): unknown {
  if (res.structuredContent !== undefined) return res.structuredContent;
  const content = (res.content ?? []) as Array<{ type: string; text?: string }>;
  const texts = content.filter((c) => c.type === "text").map((c) => c.text ?? "");
  if (texts.length === content.length && texts.length > 0) {
    const joined = texts.join("\n");
    try {
      return JSON.parse(joined);
    } catch {
      return joined;
    }
  }
  return content;
}
