import assert from "node:assert/strict";
import { test } from "node:test";
import { schemaToTs } from "../src/schema-to-ts.ts";
import { camel } from "../src/codegen.ts";

test("converts object schemas", () => {
  const ts = schemaToTs({
    type: "object",
    properties: { id: { type: "string" }, tags: { type: "array", items: { type: "string" } }, "x-y": { enum: ["a", "b"] } },
    required: ["id"],
  });
  assert.match(ts, /id: string;/);
  assert.match(ts, /tags\?: string\[\];/);
  assert.match(ts, /"x-y"\?: "a" \| "b";/);
});

test("names", () => {
  assert.equal(camel("get_channel_history"), "getChannelHistory");
  assert.equal(camel("2fa-check"), "_2faCheck");
});
