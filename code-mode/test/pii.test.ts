import assert from "node:assert/strict";
import { test } from "node:test";
import { Tokenizer } from "../src/pii.ts";

test("tokenizes and restores emails and phones", () => {
  const t = new Tokenizer();
  const data = { a: "mail jane@acme.com or call 555-123-4567", b: ["jane@acme.com", "+1 (415) 555-0100"], n: 42 };
  const tok = t.tokenize(data);
  assert.equal(tok.a, "mail [EMAIL_1] or call [PHONE_1]");
  assert.deepEqual(tok.b, ["[EMAIL_1]", "[PHONE_2]"]);
  assert.equal(tok.n, 42);
  assert.deepEqual(t.detokenize(tok), data);
});

test("leaves unknown tokens and non-PII numbers alone", () => {
  const t = new Tokenizer();
  assert.equal(t.detokenizeString("[EMAIL_9]"), "[EMAIL_9]");
  assert.equal(t.tokenizeString("order 12345, 2026-11-22, $49,277"), "order 12345, 2026-11-22, $49,277");
});

test("custom patterns and disabled mode", () => {
  const t = new Tokenizer({ patterns: { SSN: "\\b\\d{3}-\\d{2}-\\d{4}\\b" } });
  assert.equal(t.tokenizeString("ssn 123-45-6789"), "ssn [SSN_1]");
  assert.equal(new Tokenizer({ enabled: false }).tokenizeString("a@b.co"), "a@b.co");
});
