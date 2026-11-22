import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const SERVERS_DIR = path.join(ROOT, "servers");
export const SKILLS_DIR = path.join(ROOT, "skills");
export const WORKSPACE_DIR = path.join(ROOT, "workspace");
export const RUNTIME_DIR = path.join(ROOT, "src", "runtime");

export type StdioServer = { command: string; args?: string[]; env?: Record<string, string>; cwd?: string };
export type HttpServer = { url: string; headers?: Record<string, string> };
export type ServerConfig = StdioServer | HttpServer;

export interface PiiConfig {
  enabled: boolean;
  /** Extra patterns: token label -> regex source, e.g. { "SSN": "\\b\\d{3}-\\d{2}-\\d{4}\\b" } */
  patterns?: Record<string, string>;
}

export interface Config {
  servers: Record<string, ServerConfig>;
  pii: PiiConfig;
}

/** Expands ${VAR} from the host environment so secrets stay out of the config file. */
function expandEnv(value: unknown): unknown {
  if (typeof value === "string") return value.replace(/\$\{(\w+)\}/g, (_, k: string) => process.env[k] ?? "");
  if (Array.isArray(value)) return value.map(expandEnv);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, expandEnv(v)]));
  }
  return value;
}

export function loadConfig(file = process.env.CODE_MODE_CONFIG ?? path.join(ROOT, "mcp.config.json")): Config {
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const cfg = expandEnv(raw) as Partial<Config>;
  return { servers: cfg.servers ?? {}, pii: { enabled: true, ...cfg.pii } };
}

export function isHttp(s: ServerConfig): s is HttpServer {
  return "url" in s;
}

/** True when the module at `url` is the process entry point. */
export function isMain(url: string): boolean {
  return !!process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(url);
}
