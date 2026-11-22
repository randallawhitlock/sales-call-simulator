/**
 * Offline demo MCP server (stdio). Run one "service" per process:
 *   node examples/demo-server.ts sheets|crm|chat
 * Fake data only.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const service = process.argv[2] ?? "sheets";
const server = new McpServer({ name: `demo-${service}`, version: "0.1.0" });
const json = (v: unknown) => ({ content: [{ type: "text" as const, text: JSON.stringify(v) }] });

if (service === "sheets") {
  const regions = ["north", "south", "east", "west"];
  const statuses = ["open", "won", "lost", "pending"];
  const rows = Array.from({ length: 10_000 }, (_, i) => ({
    id: `row-${i + 1}`,
    name: `Customer ${i + 1}`,
    email: `customer${i + 1}@example.com`,
    phone: `555-${String(100 + (i % 900)).padStart(3, "0")}-${String(1000 + i).slice(-4)}`,
    region: regions[i % 4],
    status: statuses[(i * 7) % 4],
    amount: ((i * 7919) % 50_000) + 100,
  }));

  server.registerTool(
    "get_sheet",
    {
      description: "Return every row of a spreadsheet. Rows: id, name, email, phone, region, status, amount.",
      inputSchema: { sheetId: z.string().describe("Spreadsheet id, e.g. 'leads'") },
    },
    async ({ sheetId }) => json({ sheetId, rows }),
  );
  server.registerTool(
    "list_sheets",
    { description: "List available spreadsheets.", inputSchema: {} },
    async () => json([{ sheetId: "leads", rows: rows.length }]),
  );
}

if (service === "crm") {
  const records = new Map<string, Record<string, unknown>>();
  server.registerTool(
    "update_record",
    {
      description: "Create or update a CRM record.",
      inputSchema: {
        objectType: z.enum(["Lead", "Contact", "Opportunity"]),
        recordId: z.string(),
        data: z.record(z.string(), z.any()).describe("Fields to set"),
      },
    },
    async ({ objectType, recordId, data }) => {
      const key = `${objectType}:${recordId}`;
      records.set(key, { ...(records.get(key) ?? {}), ...data });
      return json({ ok: true, key });
    },
  );
  server.registerTool(
    "get_record",
    { description: "Fetch a CRM record.", inputSchema: { objectType: z.string(), recordId: z.string() } },
    async ({ objectType, recordId }) => json(records.get(`${objectType}:${recordId}`) ?? null),
  );
}

if (service === "chat") {
  let polls = 0;
  server.registerTool(
    "get_channel_history",
    {
      description: "Get recent messages from a chat channel.",
      inputSchema: { channel: z.string(), limit: z.number().int().optional() },
    },
    async ({ channel }) => {
      polls++;
      const messages = [{ user: "ci-bot", text: "deployment started", channel }];
      if (polls >= 3) messages.push({ user: "ci-bot", text: "deployment complete", channel });
      return json({ messages });
    },
  );
  server.registerTool(
    "post_message",
    { description: "Post a message to a channel.", inputSchema: { channel: z.string(), text: z.string() } },
    async ({ channel, text }) => json({ ok: true, channel, text }),
  );
}

await server.connect(new StdioServerTransport());
