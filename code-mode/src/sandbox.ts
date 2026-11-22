/**
 * Runs agent-written TypeScript in a separate Node process with the Node
 * permission model enabled:
 *   - read:  servers/, skills/, workspace/, src/runtime/, package.json
 *   - write: workspace/, skills/
 *   - no child processes, workers, native addons or WASI
 * Tool calls go to the host over IPC. Output is truncated before it is
 * returned, so a stray console.log(bigArray) cannot flood the model's context.
 *
 * The permission model does not restrict network access. For untrusted
 * workloads, also run this inside a container or VM with egress blocked.
 */
import { fork } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { ROOT, RUNTIME_DIR, SERVERS_DIR, SKILLS_DIR, WORKSPACE_DIR } from "./config.ts";
import type { McpHost } from "./host.ts";

export interface RunResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
  toolCalls: number;
  durationMs: number;
}

export interface RunOptions {
  timeoutMs?: number;
  maxOutputChars?: number;
}

const RUNS_DIR = path.join(WORKSPACE_DIR, ".runs");
let runCounter = 0;

export async function runCode(host: McpHost, code: string, opts: RunOptions = {}): Promise<RunResult> {
  const timeoutMs = opts.timeoutMs ?? 120_000;
  const maxChars = opts.maxOutputChars ?? 8_000;
  for (const d of [RUNS_DIR, SKILLS_DIR, SERVERS_DIR]) fs.mkdirSync(d, { recursive: true });

  const file = path.join(RUNS_DIR, `run-${process.pid}-${Date.now()}-${++runCounter}.ts`);
  fs.writeFileSync(file, code);

  const started = Date.now();
  const child = fork(path.join(RUNTIME_DIR, "bootstrap.ts"), [file], {
    cwd: WORKSPACE_DIR,
    stdio: ["ignore", "pipe", "pipe", "ipc"],
    env: { PATH: process.env.PATH ?? "", NODE_NO_WARNINGS: "1" },
    execArgv: [
      "--permission",
      `--allow-fs-read=${SERVERS_DIR}`,
      `--allow-fs-read=${SKILLS_DIR}`,
      `--allow-fs-read=${WORKSPACE_DIR}`,
      `--allow-fs-read=${RUNTIME_DIR}`,
      `--allow-fs-read=${path.join(ROOT, "package.json")}`,
      `--allow-fs-write=${WORKSPACE_DIR}`,
      `--allow-fs-write=${SKILLS_DIR}`,
      "--disable-warning=ExperimentalWarning",
    ],
  });

  let stdout = "";
  let stderr = "";
  let toolCalls = 0;
  let timedOut = false;
  const cap = maxChars * 4; // keep a bounded buffer; truncate precisely at the end
  child.stdout!.on("data", (b: Buffer) => {
    if (stdout.length < cap) stdout += b.toString();
  });
  child.stderr!.on("data", (b: Buffer) => {
    if (stderr.length < cap) stderr += b.toString();
  });

  child.on("message", async (msg: { type: string; id: number; server: string; tool: string; input: Record<string, unknown> }) => {
    if (msg?.type !== "tool_call") return;
    toolCalls++;
    try {
      const value = await host.callTool(msg.server, msg.tool, msg.input ?? {});
      if (child.connected) child.send({ type: "tool_result", id: msg.id, ok: true, value });
    } catch (err) {
      if (child.connected) child.send({ type: "tool_result", id: msg.id, ok: false, error: (err as Error).message });
    }
  });

  const timer = setTimeout(() => {
    timedOut = true;
    child.kill("SIGKILL");
  }, timeoutMs);

  const exitCode = await new Promise<number | null>((resolve) => child.on("close", (code) => resolve(code)));
  clearTimeout(timer);
  fs.rmSync(file, { force: true });

  return {
    exitCode,
    stdout: truncate(host.tokenizer.tokenizeString(stdout), maxChars),
    stderr: truncate(host.tokenizer.tokenizeString(stderr.replaceAll(file, "<script>")), Math.floor(maxChars / 2)),
    timedOut,
    toolCalls,
    durationMs: Date.now() - started,
  };
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  const head = Math.floor(max * 0.7);
  const tail = max - head;
  return `${s.slice(0, head)}\n... [${s.length - max} chars truncated — filter or summarize in code instead of printing] ...\n${s.slice(-tail)}`;
}
