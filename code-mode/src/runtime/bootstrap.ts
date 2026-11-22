/** Sandbox entry point: imports the agent's script, reports uncaught errors, exits. */
import { pathToFileURL } from "node:url";

process.channel?.unref();
const script = process.argv[2];
try {
  await import(pathToFileURL(script).href);
} catch (err) {
  const e = err as Error;
  console.error(e?.stack ?? String(err));
  process.exitCode = 1;
}
