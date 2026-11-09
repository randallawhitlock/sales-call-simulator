# M4 voice spike: LiveKit worker on Gemini Live

Run on November 9, 2026, for `docs/PROPOSAL.md` §11. Code, run instructions and raw reports are
in [`spike/voice/`](../spike/voice/README.md). It's throwaway and isn't wired into the app.

## Decision

**Build M4 on the server-hosted LiveKit worker, with `gemini-3.8-live`.** The spike ran Case A end
to end through a local LiveKit server and a Python agent. Latency passed the p95 target and
missed the median target by 0.07 s. Barge-in cut the customer off within half a second every
time. A forced model disconnect recovered without losing the conversation in every call where
the reconnect was set up properly.

Nothing failed that browser-direct would have avoided. The worker path cost about 0.25 s over
talking to Gemini directly, and the model accounts for the rest. The serious failure (a fast
reconnect that kills the session) is a race on Google's side that a browser client would hit too.
The worker lets us fix it once, on the server.

M4 has four things to fix. See [What M4 must do](#what-m4-must-do).

## Setup

| | |
|---|---|
| Model | `gemini-3.8-live` (Gemini API, not Vertex), voice `Aoede`, temperature 0.8 |
| API tier | Free (AI Studio key, no billing) |
| LiveKit | `livekit-server` 1.13.11 `--dev`, local, bound to 127.0.0.1 |
| Agent | `livekit-agents` 1.8.11, `livekit-plugins-google` 1.8.11, `google-genai` 3.0.0, `livekit` (rtc) 1.2.0, Python 3.13.12 |
| Machine | Apple silicon Mac, macOS 26.6.2 |
| Customer | `scenario-101` (Loretta Banks), mood `worried`, prompt from `build_customer_prompt` (`customer-v1`); `prompt_problems` clean |
| Caller | A bot rep: macOS `say` (Samantha, 48 kHz) on a real-time-paced mic track, loudness detection on the customer's track |

I can't talk into a microphone, so a bot played the rep. That made runs repeatable (19 calls,
about 150 rep lines, 18 barge-ins, 18 forced disconnects), but it flatters the system: synthesized
speech has clean pauses and no "um", there's no echo, and everything ran on one machine. The
numbers cover the model, LiveKit and the worker. They don't cover a real network or a browser's
playout buffer.

## Model choice

[S30] said LiveKit flags one newer Gemini Live model as incompatible. The page no longer marks
any model that way. It lists per-model limits instead:

- `gemini-3.8-live` on the Gemini API rejects `thinking_level` (the plugin raises an error). The
  docs point to `gemini-3.8-live-extended-thinking` if you need it.
- Gemini 3.1 (`gemini-3.1-flash-live-preview`) doesn't support affective dialog, proactive audio
  or asynchronous function calls.
- The separate-TTS mode only works with non-native-audio models.

The plugin's default is `gemini-2.5-flash-native-audio-preview-12-2025`. A text-in, audio-out probe
(three tries each, straight to the API) ruled it out, and the audio control below settled 3.8
against 3.1:

| Model | Text to first audio | Audio control: median / p95 |
|---|---|---|
| `gemini-3.8-live` | 0.51, 0.61, 3.27 s | 1.00 / 1.57 s |
| `gemini-3.1-flash-live-preview` | 0.78, 0.66, 0.63 s | 1.48 / 2.19 s |
| `gemini-2.5-flash-native-audio-preview-12-2025` | 2.42, 2.30, 4.81 s | not run |

**Pin `SIM_LIVE_MODEL=gemini-3.8-live`.** It's the GA id (no preview suffix), it's on LiveKit's
supported list for both the Gemini API and Vertex, and it was the fastest on real audio.

## Results

### Latency (target: median ≤ 1.2 s, p95 ≤ 2.5 s)

This is the time from the rep's last voiced sample to the first audible frame of the customer's
reply, measured at the caller.

| Run | Path | Gemini end-of-speech setting | Turns | Median | p95 | Max |
|---|---|---|---|---|---|---|
| A | Worker, one call | Google default | 22 | 1.46 s | 2.11 s | 3.77 s |
| B | Worker, one call | 500 ms silence, high sensitivity | 15 | **1.27 s** | **1.77 s** | 1.77 s |
| Rounds | Worker, three calls at once | 500 ms, high | 86 | 1.46 s | 2.74 s | 5.71 s |
| Control | Straight to Gemini, no LiveKit | 500 ms, high | 18 | 1.00 s | 1.57 s | 1.57 s |

p95 passes for a single call with either setting. The median misses by 0.07 s with the 500 ms
window and by 0.26 s with Google's default. Three calls at once on the free tier pushed p95 past
the target. Repeat that on the paid tier before planning for a class running together.

The control sent the same audio straight to Gemini over one WebSocket. It's the shape of the
browser-direct path, minus the browser. So the model plus end-of-speech detection takes about
1.0 s, and the worker, both LiveKit hops and audio playout add about 0.25 s on localhost.
Browser-direct could win back some of that 0.25 s. It can't touch the 1.0 s. (The plugin reports
no model-only time to first token for this model, `ttft=-1`, so this control is the only split.)

**The 500 ms window clips numbers.** In 125 rep lines at 500 ms, Gemini lost the end of the line
twice, both times the spaced-out "my extension is four four one seven". The transcripts read
"My extension is" and "My extension is 44", and the customer only knew what the transcript held.
At Google's default it lost nothing in 25 lines. Reps read numbers all day (Medicare numbers,
dates of birth, phone numbers), so this matters more than 0.2 s. No reply ever started before the
rep finished on the worker path. But the bot never pauses mid-sentence the way people do.

### Barge-in

The bot let the customer talk for 2.5 s, then spoke over her.

| | Run A (3) | Reconnect rounds (15) |
|---|---|---|
| Rep starts talking until her audio stops | 0.14 to 0.60 s | median 0.45 s (0.19 to 0.55) |
| Rep stops talking until her reply | 1.66 to 2.37 s | median 1.36 s (1.14 to 1.55) |

Every interruption stopped her mid-sentence, with no tail and no resumed sentence, and she
answered the new question.

**Heard-only text works locally, but Gemini remembers the rest.** LiveKit adds the interrupted
turn to its history trimmed to what actually played (`interrupted=True`). It estimates the cut by
pacing the transcript against audio playout. Gemini's own transcript of the same turn is the whole
sentence. Gemini streams audio faster than real time, and Google's docs say the session keeps "the
information already sent to the client." For example:

- Heard: "I have three wonderful grandkids. They live in"
- Generated: "I have three wonderful grandkids. They live in Ohio, so I don't get to see them as
  often as I'd like, but they keep me very busy."

Right after each interruption the bot asked, "What was the last thing you told me about your
grandkids?" In 15 of 15 calls she answered with details from the part the rep never heard. For
the example above, she said "I was just saying they live in Ohio." The plugin can't fix this.
Gemini Live has no truncate or delete (`message_truncation=False`, and the plugin logs "Gemini
Live does not support removing messages").

### Forced reconnect

The worker aborted the Gemini WebSocket's TCP connection mid-call. That's the fast-detect case:
the worker knows at once and Google doesn't. Then the bot asked "what was my extension?" about a
number it had given just before the drop.

| Reconnect setup | Drops | Survived | Model back after | Next turn | Remembered the extension |
|---|---|---|---|---|---|
| Plugin default: retry after 0.1 s, resume with Gemini's handle | 8 | **3** | 1.18 to 1.32 s | 1.45 to 1.79 s | 2 of 2 asked |
| Retry after 1.5 s, resume with the handle | 6 | **6** | 2.14 to 2.52 s | 1.06 to 5.03 s | 4 of 4 that heard the number (2 lost the digits to clipping before the drop) |
| Fresh session, LiveKit replays its history | 4 | **4** | 0.64 to 0.74 s | 1.25 to 1.93 s | 4 of 4 |

What happened in the failures: the plugin retries 0.1 s after a drop and resumes with Gemini's
session handle. Google answers `1008 BidiGenerateContent session is already connected to an
existing client`, because it hasn't noticed the old socket is dead yet. The plugin treats any error
during connection setup as fatal, so the whole agent session closes and the customer goes silent
for the rest of the call. Turning off the plugin's `session_resumption` option doesn't help: the
plugin always asks Gemini for handles and resumes with the latest one. The fresh-session row
dropped the handle before reconnecting, which is what a fallback in M4 would do.

When it recovered, it recovered well. When the drop hit while she was talking (run A), the worker
kept playing the 4.45 s of audio it already had, and then she stopped mid-word ("...ever since,
which"). Asked "what were you saying?", she picked the thought back up.

## Other things the spike turned up

- **API version.** `google-genai` 3.x defaults to API `v1`, where the Live endpoint returns HTTP
  404 at the WebSocket handshake. Pass `api_version="v1beta"` (the plugin takes it) or set
  `GOOGLE_GENAI_API_VERSION=v1beta`. The backend pins `google-genai>=3.0.0`, so this applies to
  M4.
- **Model list.** `models.list` returns no Live models for this key, though they all work. Don't
  validate `SIM_LIVE_MODEL` against it.
- **Key variable.** The Google plugin reads `GOOGLE_API_KEY`, not `GEMINI_API_KEY`, which is the
  one the README lists.
- **Rep transcript timing.** Gemini's transcript of the rep streams. The first piece reached the
  worker a median 0.88 s after the rep stopped talking. The plugin only marks it final when the
  customer's reply finishes, a median 6.9 s later.
- **Event order.** An interrupted customer turn lands in the history before the rep line that came
  before it, because the rep's final transcript arrives late. Order the evidence log by audio
  time, not by event arrival.
- **Mishearing.** Gemini once transcribed "Part A and Part B" as "Part D and Part B", from clean
  synthesized speech. Rule checks on rep wording shouldn't trust this transcript alone.
- **GoAway.** The plugin handles Gemini's GoAway (sent before the roughly 10-minute connection
  limit) by restarting at once. A TODO in its source says that isn't seamless yet. I didn't test
  it. Expect it to behave like the forced reconnect.
- **Free tier.** Latency on the free tier was fine for one call. Google may use free-tier prompts
  and audio to improve its products. That's fine for a bot reading synthetic lines and not fine
  for real learners. Paid Live audio is $0.005 a minute in and $0.018 a minute out.

## What M4 must do

1. **Make reconnects survive.** On an unexpected drop, start a fresh Gemini session and let
   LiveKit replay its history. That was 4 of 4, the fastest at about 0.7 s, and it replays only
   what the rep heard. If M4 keeps resuming with handles, wait at least 1.5 s before the first
   retry (6 of 6) and treat `1008 ... already connected` as retryable. Either way, the plugin's
   `_main_task` treats setup errors as fatal, so this needs a small wrapper or an upstream fix.
2. **Tell the model what wasn't heard.** After an interruption, Gemini believes it said the whole
   sentence. Send a short note with what was actually heard (from LiveKit's interrupted item) so
   the customer doesn't refer back to words the rep never got. The earlier prototype did this
   with director notes.
3. **Tune end-of-speech with real speech, number-heavy lines included.** 500 ms bought 0.2 s on the
   median and clipped spoken digits twice in 125 lines. Try 700 ms with real people and take the
   lowest window that never clips a Medicare number or a date of birth.
4. **Pin the API version and the key variable** as above, and use the paid tier for anything a
   person talks into.

## Not tested

- A human on a real microphone, echo, a browser's audio path and a real network
- GoAway at the 10-minute mark, and 45 to 60 minute stability with context compression (it's
  configured in the spike but never hit its limit)
- Tools executed in the worker against the engine
- The paid tier
- Silent network drops, where neither side notices for a while; only the fast-detect case was
  forced
