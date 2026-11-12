# M4 voice spike (throwaway)

Code for the half-day spike in `docs/PROPOSAL.md` §11. Results and the decision are in
[`docs/voice-spike.md`](../../docs/voice-spike.md). Nothing here is wired into the backend app,
and none of it should be. M4 starts over with real code.

- `agent.py` is a LiveKit Agents worker that plays Case A's customer (Loretta Banks,
  `scenario-101`, mood `worried`) on Gemini Live. The system prompt comes from
  `backend/app/actor/prompt.py` and goes through `prompt_problems` like the app's prompts do.
  Events are appended to `runs/<room>-agent.jsonl`.
- `live_settings.py` holds the Gemini Live settings the worker and the control share: the
  prompt, voice, temperature, end-of-speech setting and context compression.
- `caller.py` is a bot rep. It joins the room, speaks scripted lines synthesized with macOS `say`
  through a mic track paced in real time, and timestamps every frame of the customer's audio. It
  runs about 20 timed turns, three barge-ins, a probe of what the customer remembers after being cut
  off, and two forced reconnects (one while she's listening, one while she's talking), then
  prints a report. `SPIKE_SCRIPT=reconnect` runs a short version with one barge-in and one drop.
  After a drop while she's listening it waits `SPIKE_KILL_WAIT_S` (default 4.0) before the
  follow-up line, so the line is spoken after the reconnect in every setup. It gives up with an
  error if the agent isn't listening or speaking within 60 s of joining. If the customer is
  still talking when its 45 s wait for quiet runs out, it speaks the next line anyway, marks the
  step (`not_idle_at_start`), and the reports name the step and leave it out of the latency
  figure.
- `reconnect_rounds.sh` repeats the short script in fresh rooms, three reconnect setups side by
  side. The worker reads each room's setup from tokens in the room name (see `room_overrides`).
- `summarize.py` rolls up any set of runs: latency, every forced reconnect, every barge-in and
  probe. Each reconnect is scored on separate checks (reconnected, answered the next line in
  time, reply attributed to that line, number recalled, later lines answered) and only on what
  happened before the next forced drop. Each probe answer lists the words it shares only with
  the part of the cut-off turn the rep never heard, not counting anything in her prompt or said
  and heard earlier in the call.
- `test_summarize.py` checks those helpers on synthetic events. No LiveKit, Gemini or saved runs.
- `test_scripts.py` checks the offline parts of the other scripts (the caller's report and clip
  cache, its audio lookups and timeouts, the control's line selection and reply attribution, the
  shared settings, the worker's transcript buffer). It imports them, so it needs the
  dependencies installed, but no server, key or network.
- `direct.py` is the control: rep lines sent straight to Gemini Live with no LiveKit, on the
  same settings as the worker (`live_settings.py`). It speaks only the lines that are plain
  turns on the worker path too: `turn` lines and the line of each forced-drop step, without the
  follow-up that comes right after a drop and without the barge-in and probe steps. Each row
  shows its script step. It times to the first audio chunk and to the first audible window.
  Those endpoints still differ from `caller.py`'s, so don't read the gap between them as LiveKit
  overhead.
- `results/` holds the text reports the write-up quotes. They came from earlier versions of
  these scripts, and the committed ones won't reproduce them line for line.
  `run-a-default-vad.txt` used an older caller script (barge-ins at steps 17 to 19, no probe
  step), and every reconnect in them was run with a fixed 2.0 s pause before the follow-up line.
  `direct-control.txt` came from an earlier `direct.py` that estimated when the line ended,
  stopped at the first chunk and credited each finished reply to the current line unchecked.
  It also took the first 18 `turn` lines, so it asked two follow-ups to drops that never
  happened and skipped the extension line, and it built its own Live config with no context
  compression (`docs/voice-spike.md` has the details).
  `reconnects.txt` is the reconnect section over every kept run; `summarize.py` now also names
  the retry delay for fresh-session setups and flags follow-ups spoken over the reconnect. Raw logs, audio and frame dumps
  go to `runs/`, which is git-ignored, and invalid runs were moved to `runs/discarded/`.

## Run it

You need macOS (for `say`), [uv](https://docs.astral.sh/uv/), and the LiveKit server:

```bash
brew install livekit livekit-cli
```

Three terminals, all from `spike/voice/`.

1. Local LiveKit server (dev keys `devkey` / `secret`):

   ```bash
   livekit-server --dev --bind 127.0.0.1
   ```

2. The worker. `SIM_LIVE_MODEL` is required and never defaulted. Set `GEMINI_API_KEY` in your
   shell first; the Google plugin reads `GOOGLE_API_KEY`, so the line below copies it across.

   ```bash
   export LIVEKIT_URL=ws://127.0.0.1:7880 LIVEKIT_API_KEY=devkey LIVEKIT_API_SECRET=secret
   export GOOGLE_API_KEY="$GEMINI_API_KEY" SIM_LIVE_MODEL=gemini-3.8-live
   uv run python agent.py start
   ```

   Options: `SPIKE_RESUMPTION=0` drops Gemini's resumption handle on a forced drop, so the
   reconnect is a fresh session filled from LiveKit's chat history. `SPIKE_SILENCE_MS=500` sets
   Gemini's end-of-speech silence window (and high end-of-speech sensitivity); unset keeps
   Google's defaults. `SPIKE_FIRST_RETRY_S=1.5` delays the first reconnect attempt (the plugin
   uses 0.1). `SPIKE_VOICE` picks the Gemini voice (default `Aoede`). Room-name tokens override
   all of these per call: `m38`/`m31`, `res0`/`res1`, `vad500`, `fr15`.

3. The bot caller. Use a new room name every run: a room that's still open keeps its old agent
   job, and that job writes to the old log.

   ```bash
   export LIVEKIT_URL=ws://127.0.0.1:7880 LIVEKIT_API_KEY=devkey LIVEKIT_API_SECRET=secret
   uv run python caller.py my-run-1
   uv run python caller.py my-run-1 --report   # reprint the report from saved logs
   ./reconnect_rounds.sh 4 rc                  # 4 rounds x 3 reconnect setups
   uv run python summarize.py rc-              # roll up every room starting with rc-
   ```

The tests need nothing running:

```bash
uv run python -m unittest test_summarize test_scripts
```

The control needs no LiveKit, just the key and model:

```bash
SIM_LIVE_MODEL=gemini-3.8-live uv run python direct.py 18 500
SIM_LIVE_MODEL=gemini-3.8-live uv run python direct.py 18 default   # Google's end-of-speech defaults
```

## Talk to it yourself

With the server and worker running, mint a token and open LiveKit's hosted agents playground
(or the `lk` CLI's room join). Chrome lets an https page reach `ws://localhost`.

```bash
lk token create --api-key devkey --api-secret secret --join --room talk-1 --identity rep --valid-for 1h
```

In <https://agents-playground.livekit.io>, choose manual connection, enter `ws://localhost:7880`
and the token. Use a headset: there's no echo cancellation on the bot path, and the playground
relies on the browser's.

## What the numbers mean

"End of turn to first audio" runs from the last voiced sample of the rep's line entering the
WebRTC track to the first loud frame of the customer's reply arriving at the caller. It includes
Gemini's end-of-speech detection, the model, the worker and both LiveKit hops on localhost. It
doesn't include a browser jitter buffer, speaker output or a real network. The rep is synthesized
speech with a clean noise floor, which is kinder to voice activity detection than a real mic.

## Known limitations of the transcript log

The worker logs Gemini's own transcript of each customer turn (`model_turn_text`) by wrapping
the plugin's handler for server messages. Two cases it can't get right from there:

- **An interruption after the turn already ended.** Gemini sends `turn_complete` and the audio
  is still playing when the rep barges in. The plugin clears the `interrupted` flag on that
  message before the wrapper sees it, so the turn stays logged as `interrupted=False` and
  `caller.py` and `summarize.py` show `generated: None` for that barge-in (with what was heard,
  if LiveKit logged its trimmed item as interrupted). Nothing was run to see how often this
  happens.
- **Message order around an interruption.** Text that arrives after the `interrupted` message
  and before the next `turn_complete` is logged as the tail of the cut turn (a second
  `model_turn_text` with `late` set, which `summarize.py` prefers). That follows the plugin,
  which keeps one generation open until `turn_complete`. It's checked only against synthetic
  messages. If Gemini ever starts the next reply without sending `turn_complete` for the cut
  turn, that reply's text would be logged as the tail of the cut one. Check the order on a live
  run before relying on `late`.
