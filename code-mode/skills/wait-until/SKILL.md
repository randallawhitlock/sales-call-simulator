---
name: wait-until
description: Poll an async check in code until it returns a truthy value or times out (e.g. wait for "deployment complete" in a channel).
---
# wait-until

`waitUntil(check, { intervalMs = 2000, timeoutMs = 300000 })` calls `check()` until it returns a
truthy value, then returns that value. Throws on timeout. No model round trip per check.

```ts
import { waitUntil } from "#skills/wait-until/index.ts";
import { getChannelHistory } from "#servers/chat/getChannelHistory.ts";

const msg = await waitUntil(async () => {
  const { messages } = await getChannelHistory({ channel: "deploys" });
  return messages.find((m: any) => m.text.includes("deployment complete"));
}, { intervalMs: 5000 });
console.log("done:", msg.text);
```
