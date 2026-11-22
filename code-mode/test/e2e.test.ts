import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { McpHost } from "../src/host.ts";
import { generate } from "../src/codegen.ts";
import { runCode } from "../src/sandbox.ts";
import { confine } from "../src/agent.ts";
import { ROOT, SKILLS_DIR, WORKSPACE_DIR } from "../src/config.ts";

const host = new McpHost();
before(async () => {
  await generate(host);
});
after(() => host.close());

test("filters 10,000 rows in the sandbox; model sees tokens, CRM gets real values", async () => {
  const r = await runCode(
    host,
    `import { getSheet } from "#servers/sheets/getSheet.ts";
import { updateRecord } from "#servers/crm/updateRecord.ts";
const { rows } = await getSheet({ sheetId: "leads" });
const hits = rows.filter((r: any) => r.region === "west" && r.status === "won" && r.amount > 49000);
await updateRecord({ objectType: "Lead", recordId: hits[0].id, data: { email: hits[0].email } });
console.log(rows.length, hits.length, hits[0].id, hits[0].email);`,
  );
  assert.equal(r.exitCode, 0, r.stderr);
  const [total, count, id, email] = r.stdout.trim().split(" ");
  assert.equal(total, "10000");
  assert.ok(Number(count) > 0 && Number(count) < 100);
  assert.match(email, /^\[EMAIL_\d+\]$/);
  const raw = await (await host.client("crm")).callTool({ name: "get_record", arguments: { objectType: "Lead", recordId: id } });
  assert.match(JSON.stringify(raw.content), /customer\d+@example\.com/);
});

test("polling loop runs in code with no model round trips", async () => {
  const r = await runCode(
    host,
    `import { waitUntil } from "#skills/wait-until/index.ts";
import { getChannelHistory } from "#servers/chat/getChannelHistory.ts";
let polls = 0;
const m = await waitUntil(async () => { polls++; const { messages } = await getChannelHistory({ channel: "d" });
  return messages.find((x: any) => x.text === "deployment complete"); }, { intervalMs: 10 });
console.log(polls, m.text);`,
  );
  assert.equal(r.exitCode, 0, r.stderr);
  assert.match(r.stdout, /deployment complete/);
});

test("sandbox blocks config reads, writes outside workspace, and child processes", async () => {
  const r = await runCode(
    host,
    `import fs from "node:fs";
const tryIt = (f: () => unknown) => { try { f(); return "ALLOWED"; } catch (e: any) { return e.code; } };
console.log(tryIt(() => fs.readFileSync(${JSON.stringify(ROOT + "/mcp.config.json")})));
console.log(tryIt(() => fs.writeFileSync(${JSON.stringify(ROOT + "/x.txt")}, "x")));
console.log(tryIt(() => process.binding("spawn_sync")));
const cp = await import("node:child_process");
console.log(tryIt(() => cp.execSync("id")));
fs.writeFileSync("ok.txt", "ok"); console.log(fs.readFileSync("ok.txt", "utf8"));`,
  );
  assert.equal(r.exitCode, 0, r.stderr);
  const lines = r.stdout.trim().split("\n");
  assert.equal(lines[0], "ERR_ACCESS_DENIED");
  assert.equal(lines[1], "ERR_ACCESS_DENIED");
  assert.equal(lines[3], "ERR_ACCESS_DENIED");
  assert.equal(lines[4], "ok");
});

test("output is truncated and timeouts are enforced", async () => {
  const big = await runCode(host, `console.log("x".repeat(100000));`, { maxOutputChars: 1000 });
  assert.ok(big.stdout.length < 1300);
  assert.match(big.stdout, /truncated/);
  const slow = await runCode(host, `await new Promise(r => setTimeout(r, 10000));`, { timeoutMs: 300 });
  assert.equal(slow.timedOut, true);
});

test("tool errors surface as exceptions in code", async () => {
  const r = await runCode(
    host,
    `import { updateRecord } from "#servers/crm/updateRecord.ts";
try { await updateRecord({ objectType: "Nope" as any, recordId: "1", data: {} }); } catch (e: any) { console.log("caught"); }`,
  );
  assert.match(r.stdout, /caught/);
});

test("agent file tools are confined", () => {
  assert.ok(confine("skills/x/SKILL.md", [SKILLS_DIR]).startsWith(SKILLS_DIR));
  assert.ok(confine("workspace/a.json", [WORKSPACE_DIR]).startsWith(WORKSPACE_DIR));
  assert.throws(() => confine("mcp.config.json", [SKILLS_DIR, WORKSPACE_DIR]));
  assert.throws(() => confine("skills/../src/agent.ts", [SKILLS_DIR]));
});
