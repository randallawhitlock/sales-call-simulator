/**
 * Compares context cost of direct tool calling vs code mode for the configured servers.
 *   npm run bench
 * Uses the token counting API when Claude credentials are available, else ~4 chars/token.
 */
import fs from "node:fs";
import path from "node:path";
import Anthropic from "@anthropic-ai/sdk";
import { SERVERS_DIR, isMain } from "./config.ts";
import { McpHost } from "./host.ts";
import { runCode } from "./sandbox.ts";
import { systemPrompt } from "./agent.ts";

async function tokens(client: Anthropic | null, text: string): Promise<{ n: number; exact: boolean }> {
  if (client) {
    try {
      const r = await client.messages.countTokens({ model: "claude-opus-5-5", messages: [{ role: "user", content: text }] });
      return { n: r.input_tokens, exact: true };
    } catch {
      /* fall through to estimate */
    }
  }
  return { n: Math.ceil(text.length / 4), exact: false };
}

if (isMain(import.meta.url)) {
  const host = new McpHost();
  const client = process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN ? new Anthropic() : null;
  try {
    // 1. Definitions: every tool schema up front vs. system prompt + the files a task actually opens.
    const all: unknown[] = [];
    for (const s of Object.keys(host.config.servers)) {
      for (const t of await host.listTools(s)) all.push({ name: `${s}__${t.name}`, description: t.description, input_schema: t.inputSchema });
    }
    const direct = await tokens(client, JSON.stringify(all));
    const opened = ["sheets/getSheet.ts", "crm/updateRecord.ts"].filter((f) => fs.existsSync(path.join(SERVERS_DIR, f)));
    const codeMode = await tokens(client, systemPrompt() + opened.map((f) => fs.readFileSync(path.join(SERVERS_DIR, f), "utf8")).join("\n"));

    // 2. Data flow: a full tool result through the model vs. filtered output from code.
    const sheet = host.config.servers.sheets ? await host.callTool("sheets", "get_sheet", { sheetId: "leads" }) : null;
    const rawResult = sheet ? await tokens(client, JSON.stringify(sheet)) : null;
    const run = sheet
      ? await runCode(
          host,
          `import { getSheet } from "#servers/sheets/getSheet.ts";
const { rows } = await getSheet({ sheetId: "leads" });
const hits = rows.filter((r: any) => r.region === "west" && r.status === "won" && r.amount > 49000);
console.log(hits.length, JSON.stringify(hits.slice(0, 5)));`,
        )
      : null;
    const filtered = run ? await tokens(client, run.stdout) : null;

    const unit = direct.exact ? "tokens" : "tokens (est.)";
    const pct = (a: number, b: number) => `${(100 * (1 - b / a)).toFixed(2)}%`;
    console.log(`Tool definitions (${all.length} tools)`);
    console.log(`  direct:    ${direct.n} ${unit}`);
    console.log(`  code mode: ${codeMode.n} ${unit}  (system prompt + ${opened.length} opened files; fixed as tool count grows)`);
    const perTool = direct.n / Math.max(all.length, 1);
    console.log(`  ~${Math.round(perTool)} ${unit}/tool direct -> break-even at ~${Math.ceil(codeMode.n / perTool)} tools; at 1,000 tools direct ≈ ${Math.round(perTool * 1000)}`);
    if (rawResult && filtered) {
      console.log(`Data flow (10,000-row sheet)`);
      console.log(`  direct:    ${rawResult.n} ${unit}`);
      console.log(`  code mode: ${filtered.n} ${unit}  (saving ${pct(rawResult.n, filtered.n)})`);
    }
  } finally {
    await host.close();
  }
}
