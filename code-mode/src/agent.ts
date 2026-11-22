/**
 * Code-mode agent: Claude explores ./servers, writes TypeScript that calls MCP
 * tools, and runs it in the sandbox. Only what the code prints reaches the model.
 *
 *   npm run agent -- "Find won deals over $49k in the west and add them to the CRM as Leads"
 *   npm run agent            # interactive
 */
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline/promises";
import Anthropic from "@anthropic-ai/sdk";
import { z } from "zod";
import { ROOT, SERVERS_DIR, SKILLS_DIR, WORKSPACE_DIR, isMain } from "./config.ts";
import { McpHost } from "./host.ts";
import { runCode } from "./sandbox.ts";
import { listServers, listSkills } from "./skills.ts";

const MODEL = process.env.CODE_MODE_MODEL ?? "claude-opus-5-5";
const EFFORT = (process.env.CODE_MODE_EFFORT ?? "high") as "low" | "medium" | "high" | "xhigh" | "max";
const MAX_TURNS = Number(process.env.CODE_MODE_MAX_TURNS ?? 40);

export function systemPrompt(): string {
  const servers = listServers();
  const skills = listSkills();
  return `You operate in a code-execution sandbox. You do not call MCP tools directly: you write TypeScript that calls them, and run it with execute_code.

<tools_as_files>
Every MCP tool is a typed async function in servers/<server>/<tool>.ts.
Servers: ${servers.length ? servers.join(", ") : "(none; run npm run codegen)"}.
Discover with list_files("servers/<server>") and read_file only the tool files you need. Never read every file.
Import in code with the #servers alias, e.g.:
  import { getSheet } from "#servers/sheets/getSheet.ts";
</tools_as_files>

<execute_code>
- Code runs as an ES module (Node, TypeScript type-stripping: no enums or namespaces). Top-level await works.
- Working directory is workspace/. Allowed: read servers/, skills/, workspace/; write workspace/ and skills/. No network except through tool functions, and no child processes.
- Only stdout/stderr come back to you, truncated at ~8k chars.
</execute_code>

<data_handling>
- Filter, join, aggregate and transform inside code. Print only what you need to reason about: counts, small samples (at most 5 rows), summaries, IDs.
- Never print full documents, large arrays or raw API responses. Pass large data between tools as variables, or save it to workspace/ files for later steps.
- Use loops, retries and polling in code (e.g. a while loop with a delay until a condition holds), not repeated execute_code calls. Batch related operations into one script.
</data_handling>

<privacy>
Values like [EMAIL_1] or [PHONE_2] are tokenized personal data. Pass them through to tools unchanged; the harness restores the real values on the way to the destination. Do not try to guess or reconstruct them.
</privacy>

<skills>
Reusable code lives in skills/<name>/ (SKILL.md + index.ts). Import with "#skills/<name>/index.ts". Check for a matching skill before writing new code.
Available: ${skills.length ? "\n" + skills.map((s) => `- ${s.dir}: ${s.description}`).join("\n") : "(none yet)"}
When you write code that works and is likely to be reused, save it with write_file as skills/<name>/index.ts plus skills/<name>/SKILL.md, which starts with frontmatter (name, description) and then documents inputs, outputs and a usage example.
</skills>

When the task is done, answer concisely with the result.`;
}

// ---- client-side tools -------------------------------------------------------

const ListFilesInput = z.object({ path: z.string().default(".") });
const ReadFileInput = z.object({ path: z.string() });
const WriteFileInput = z.object({ path: z.string(), content: z.string() });
const ExecuteCodeInput = z.object({
  code: z.string(),
  timeout_seconds: z.number().int().min(1).max(600).optional(),
});

const TOOLS: Anthropic.Beta.BetaTool[] = [
  {
    name: "list_files",
    description: "List a directory under servers/, skills/ or workspace/ (relative to the project root).",
    input_schema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
  },
  {
    name: "read_file",
    description: "Read a text file under servers/, skills/ or workspace/.",
    input_schema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
  },
  {
    name: "write_file",
    description: "Write a text file under skills/ or workspace/. Creates parent directories.",
    eager_input_streaming: true,
    input_schema: {
      type: "object",
      properties: { path: { type: "string" }, content: { type: "string" } },
      required: ["path", "content"],
    },
  },
  {
    name: "execute_code",
    description:
      "Run a TypeScript ES module in the sandbox and return its exit code, stdout and stderr. Import tools from #servers/... and skills from #skills/....",
    eager_input_streaming: true,
    input_schema: {
      type: "object",
      properties: {
        code: { type: "string", description: "TypeScript source" },
        timeout_seconds: { type: "integer", description: "Default 120, max 600" },
      },
      required: ["code"],
    },
  },
];

const READ_ROOTS = [SERVERS_DIR, SKILLS_DIR, WORKSPACE_DIR];
const WRITE_ROOTS = [SKILLS_DIR, WORKSPACE_DIR];

/** Resolves a model-supplied path and rejects anything outside the allowed roots. */
export function confine(p: string, roots: string[]): string {
  const target = path.resolve(ROOT, p);
  const real = fs.existsSync(target) ? fs.realpathSync(target) : target;
  for (const root of roots) {
    const rel = path.relative(root, real);
    if (rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel))) return real;
  }
  throw new Error(`Path not allowed: ${p}. Allowed: ${roots.map((r) => path.relative(ROOT, r) + "/").join(", ")}`);
}

async function runTool(host: McpHost, name: string, input: unknown): Promise<string> {
  switch (name) {
    case "list_files": {
      const { path: p } = ListFilesInput.parse(input);
      if (p === "." || p === "" || p === "./") return "servers/\nskills/\nworkspace/";
      const dir = confine(p, READ_ROOTS);
      return fs
        .readdirSync(dir, { withFileTypes: true })
        .filter((d) => !d.name.startsWith("."))
        .map((d) => d.name + (d.isDirectory() ? "/" : ""))
        .join("\n");
    }
    case "read_file": {
      const file = confine(ReadFileInput.parse(input).path, READ_ROOTS);
      const text = fs.readFileSync(file, "utf8");
      return host.tokenizer.tokenizeString(text.length > 20_000 ? text.slice(0, 20_000) + "\n...[truncated]" : text);
    }
    case "write_file": {
      const { path: p, content } = WriteFileInput.parse(input);
      const file = confine(p, WRITE_ROOTS);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, content);
      return `Wrote ${path.relative(ROOT, file)} (${content.length} chars)`;
    }
    case "execute_code": {
      const { code, timeout_seconds } = ExecuteCodeInput.parse(input);
      const r = await runCode(host, code, { timeoutMs: (timeout_seconds ?? 120) * 1000 });
      const parts = [`exit_code: ${r.timedOut ? "timeout" : r.exitCode}`, `tool_calls: ${r.toolCalls}`, `duration_ms: ${r.durationMs}`];
      if (r.stdout) parts.push(`stdout:\n${r.stdout}`);
      if (r.stderr) parts.push(`stderr:\n${r.stderr}`);
      return parts.join("\n");
    }
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

// ---- agent loop --------------------------------------------------------------

export interface AgentOptions {
  client?: Anthropic;
  host?: McpHost;
  onText?: (delta: string) => void;
  onTool?: (name: string, input: unknown) => void;
}

export class Agent {
  private client: Anthropic;
  private host: McpHost;
  private messages: Anthropic.Beta.BetaMessageParam[] = [];
  private system = systemPrompt();
  private opts: AgentOptions;

  constructor(opts: AgentOptions = {}) {
    this.client = opts.client ?? new Anthropic();
    this.host = opts.host ?? new McpHost();
    this.opts = opts;
  }

  async send(userText: string): Promise<string> {
    const start = this.messages.length;
    this.messages.push({ role: "user", content: userText });
    let jsonRetries = 0;

    for (let turn = 0; turn < MAX_TURNS; turn++) {
      const stream = this.client.beta.messages.stream({
        model: MODEL,
        max_tokens: 64000,
        betas: ["server-side-fallback-2026-07-01"],
        fallbacks: "default",
        thinking: { type: "adaptive" },
        output_config: { effort: EFFORT },
        cache_control: { type: "ephemeral" },
        system: this.system,
        tools: TOOLS,
        messages: this.messages,
      });
      if (this.opts.onText) stream.on("text", this.opts.onText);

      let message: Anthropic.Beta.BetaMessage;
      try {
        message = await stream.finalMessage();
        jsonRetries = 0;
      } catch (err) {
        // Unparseable eager-streamed tool input or a dropped connection: re-issue the turn.
        if (err instanceof Anthropic.APIError || jsonRetries++ >= 2) throw err;
        continue;
      }

      const text = message.content
        .filter((b): b is Anthropic.Beta.BetaTextBlock => b.type === "text")
        .map((b) => b.text)
        .join("");

      if (message.stop_reason === "refusal") {
        this.messages.length = start; // drop the refused exchange so the conversation stays valid
        return `[refused${message.stop_details?.category ? `: ${message.stop_details.category}` : ""}] ${text}`;
      }
      this.messages.push({ role: "assistant", content: message.content });
      if (message.stop_reason === "pause_turn") continue;

      const toolUses = message.content.filter((b): b is Anthropic.Beta.BetaToolUseBlock => b.type === "tool_use");
      if (toolUses.length === 0) return text;
      if (message.stop_reason === "max_tokens") throw new Error("Tool input truncated at max_tokens");

      const results = await Promise.all(
        toolUses.map(async (t): Promise<Anthropic.Beta.BetaToolResultBlockParam> => {
          this.opts.onTool?.(t.name, t.input);
          try {
            return { type: "tool_result", tool_use_id: t.id, content: await runTool(this.host, t.name, t.input) };
          } catch (err) {
            const msg = err instanceof z.ZodError ? `INVALID_INPUT: ${err.message}` : (err as Error).message;
            return { type: "tool_result", tool_use_id: t.id, is_error: true, content: msg };
          }
        }),
      );
      this.messages.push({ role: "user", content: results });
    }
    return `[stopped after ${MAX_TURNS} turns]`;
  }

  async close(): Promise<void> {
    await this.host.close();
  }
}

if (isMain(import.meta.url)) {
  const agent = new Agent({
    onText: (d) => process.stdout.write(d),
    onTool: (name, input) => {
      const i = input as { code?: string; path?: string };
      process.stderr.write(`\n\x1b[2m▶ ${name}${i.path ? ` ${i.path}` : ""}${i.code ? `\n${i.code}` : ""}\x1b[0m\n`);
    },
  });
  try {
    const task = process.argv.slice(2).join(" ").trim();
    if (task) {
      await agent.send(task);
      process.stdout.write("\n");
    } else {
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
      for (;;) {
        const q = (await rl.question("\n> ")).trim();
        if (!q || q === "exit") break;
        await agent.send(q);
        process.stdout.write("\n");
      }
      rl.close();
    }
  } finally {
    await agent.close();
  }
}
