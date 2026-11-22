/**
 * Sandbox-side MCP client. Runs inside the sandboxed child process and forwards
 * every tool call to the host over IPC. No credentials, connections or PII map
 * exist on this side.
 */

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void };
type Reply = { type: "tool_result"; id: number; ok: boolean; value?: unknown; error?: string };

const pending = new Map<number, Pending>();
let nextId = 1;
let listening = false;

function listen() {
  if (listening) return;
  listening = true;
  process.on("message", (msg: Reply) => {
    if (msg?.type !== "tool_result") return;
    const p = pending.get(msg.id);
    if (!p) return;
    pending.delete(msg.id);
    if (pending.size === 0) process.channel?.unref();
    msg.ok ? p.resolve(msg.value) : p.reject(new Error(msg.error));
  });
}

export async function callMCPTool<T = any>(server: string, tool: string, input: unknown = {}): Promise<T> {
  if (!process.send) throw new Error("callMCPTool must run inside the code-mode sandbox");
  listen();
  const id = nextId++;
  process.channel?.ref();
  return new Promise<T>((resolve, reject) => {
    pending.set(id, { resolve: resolve as (v: unknown) => void, reject });
    process.send!({ type: "tool_call", id, server, tool, input });
  });
}
