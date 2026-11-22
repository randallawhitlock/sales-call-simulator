import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import Anthropic from "@anthropic-ai/sdk";
import { Agent } from "../src/agent.ts";
import { generate } from "../src/codegen.ts";
import { McpHost } from "../src/host.ts";

/** Builds an SSE response for one assistant message. */
function sse(blocks: Array<{ type: "text"; text: string } | { type: "tool_use"; name: string; input: unknown }>, stop: string): Response {
  const ev: string[] = [];
  const push = (type: string, data: object) => ev.push(`event: ${type}\ndata: ${JSON.stringify({ type, ...data })}\n\n`);
  push("message_start", {
    message: { id: "msg_1", type: "message", role: "assistant", model: "claude-opus-5-5", content: [], stop_reason: null, stop_sequence: null, usage: { input_tokens: 1, output_tokens: 0 } },
  });
  blocks.forEach((b, index) => {
    if (b.type === "text") {
      push("content_block_start", { index, content_block: { type: "text", text: "" } });
      push("content_block_delta", { index, delta: { type: "text_delta", text: b.text } });
    } else {
      push("content_block_start", { index, content_block: { type: "tool_use", id: `toolu_${index}`, name: b.name, input: {} } });
      push("content_block_delta", { index, delta: { type: "input_json_delta", partial_json: JSON.stringify(b.input) } });
    }
    push("content_block_stop", { index });
  });
  push("message_delta", { delta: { stop_reason: stop, stop_sequence: null }, usage: { output_tokens: 1 } });
  push("message_stop", {});
  return new Response(ev.join(""), { headers: { "content-type": "text/event-stream" } });
}

const host = new McpHost();
before(() => generate(host));
after(() => host.close());

test("agent loop: writes code, runs it in the sandbox, feeds back only stdout", async () => {
  const bodies: any[] = [];
  const script = [
    () =>
      sse(
        [
          { type: "text", text: "Filtering in code." },
          {
            type: "tool_use",
            name: "execute_code",
            input: {
              code: `import { getSheet } from "#servers/sheets/getSheet.ts";
const { rows } = await getSheet({ sheetId: "leads" });
console.log(rows.filter((r: any) => r.amount > 49900).length, rows[0].email);`,
            },
          },
        ],
        "tool_use",
      ),
    () => sse([{ type: "text", text: "Done." }], "end_turn"),
  ];
  const client = new Anthropic({
    apiKey: "test",
    fetch: (async (_url: string, init: RequestInit) => {
      bodies.push(JSON.parse(String(init.body)));
      return script[bodies.length - 1]();
    }) as typeof fetch,
  });

  const agent = new Agent({ client, host });
  const answer = await agent.send("How many rows over 49,900?");
  assert.equal(answer, "Done.");

  const first = bodies[0];
  assert.equal(first.model, "claude-opus-5-5");
  assert.deepEqual(first.thinking, { type: "adaptive" });
  assert.equal(first.fallbacks, "default");
  assert.deepEqual(first.tools.map((t: any) => t.name), ["list_files", "read_file", "write_file", "execute_code"]);
  assert.match(first.system, /servers\/<server>\/<tool>\.ts/);
  assert.match(first.system, /wait-until/);

  const toolResult = bodies[1].messages.at(-1).content[0];
  assert.equal(toolResult.type, "tool_result");
  assert.match(toolResult.content, /exit_code: 0/);
  assert.match(toolResult.content, /\[EMAIL_\d+\]/);
  assert.doesNotMatch(toolResult.content, /@example\.com/);
  assert.ok(JSON.stringify(bodies[1]).length < 20_000, "10,000-row sheet never entered the context");
});
