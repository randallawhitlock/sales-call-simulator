# M4 voice spike: LiveKit worker on Gemini Live

Run on November 9, 2026, for `docs/PROPOSAL.md` §11. Code, run instructions and raw reports are
in [`spike/voice/`](../spike/voice/README.md). It's throwaway and isn't wired into the app.

## Decision

**Proceed with the server-hosted LiveKit worker and `gemini-3.8-live` as the leading candidate
for M4.** This is a provisional call from a short local spike with a bot caller, not acceptance.
The voice path isn't done until it passes the [M4 acceptance gate](#m4-acceptance-gate).

The spike ran Case A end to end through a local LiveKit server and a Python agent. Over the 15
plain turns one call got through before its forced drop killed the session, latency passed the
p95 target and missed the median target by 0.07 s. Barge-in cut the customer off within 0.6 s in
18 of 18 tries. With a fresh Gemini session and LiveKit replaying its
history, 4 of 4 forced drops reconnected, answered the next line within 2 s, recalled a number
given before the drop and answered the line after that. With the plugin's default retry, only 3
of 8 drops survived.

Nothing in the spike pointed to browser-direct. The worker runs had a higher median than the
direct control (1.27 s over run B's 15 plain turns against 1.00 s at the same end-of-speech
setting), but the two measure different endpoints, so that gap doesn't isolate LiveKit's overhead (see
[Latency](#latency-target-median--12-s-p95--25-s)). The serious failure, a fast reconnect that
kills the session, comes from how the client reconnects, not where it runs: Google rejects a
resume that arrives before it has noticed the old socket died. A browser client would need the
same fix. The worker lets us write it once, on the server, next to the evidence log and the
heard-only notes.

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

I can't talk into a microphone, so a bot played the rep. That made runs repeatable (17 calls
kept and 2 discarded, 154 rep lines, 18 barge-ins, 18 forced disconnects), but it flatters the
system: synthesized speech has clean pauses and no "um", there's no echo, and everything ran on
one machine. The numbers cover the model, LiveKit and the worker. They don't cover a real network
or a browser's playout buffer.

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
| B | Worker, one call, plain turns before it went silent | 500 ms silence, high sensitivity | 15 | **1.27 s** | **1.77 s** | 1.77 s |
| Rounds | Worker, three calls at once | 500 ms, high | 86 | 1.46 s | 2.74 s | 5.71 s |
| Control | Straight to Gemini, no LiveKit | 500 ms, high | 18 | 1.00 s | 1.57 s | 1.57 s |

p95 passes for a single call with either setting. The median misses by 0.07 s with the 500 ms
window and by 0.26 s with Google's default. Three calls at once on the free tier pushed p95 past
the target. Repeat that on the paid tier before planning for a class running together.

Run B's figures cover less than run A's. Its session died at the forced drop on step 14 (the
plugin-default failure described under [Forced reconnect](#forced-reconnect)) and the customer
never spoke again: no later line got a reply (the report counts 8 timed turns with none), none
of its three barge-ins had an answer to cut off, its probe went unanswered and its second drop
wasn't run. So the 1.27 s median and 1.77 s p95 come from
the 15 plain question-and-answer turns before the call went silent, with no barge-in, probe or
post-reconnect turn among them. Read them as the 500 ms setting on plain turns, not as a whole
call.

Run A came from an earlier version of the caller script: three barge-ins at steps 17 to 19, no
probe step, 22 turns in its latency figure. The committed `caller.py` has a different step
layout and won't reproduce that report line for line.

The control sent the same audio straight to Gemini over one WebSocket, roughly the shape of the
browser-direct path minus the browser. Its median was 1.00 s against 1.27 s for worker run B's
15 plain turns at the same setting. That 0.27 s gap is not a measure of LiveKit's overhead, because the two scripts
time different things:

| | Control (`direct.py` as run) | Worker (`caller.py`) |
|---|---|---|
| Clock starts | Estimated end of the line (send start plus clip length), not observed | Last voiced sample handed to the caller's WebRTC track |
| Clock stops | First audio chunk received, loud or not | First frame above the loudness threshold, after real-time playout through the worker and LiveKit |
| Sample | 18 plain turns, one session | 15 plain turns, the part of one call before its forced drop ended the session |

They also ran at different times on a shared free tier. What the control does show is that the
model and its end-of-speech detection took about a second on their own before any audio came
back, so moving the client wouldn't get this setup far under the 1.2 s median target.
`direct.py` now records the real send time of the last chunk and a "first audible" time on the
caller's threshold, but it hasn't been rerun yet. A like-for-like comparison, with control and
worker calls alternating in the same window, is part of the
[acceptance gate](#m4-acceptance-gate). (The plugin reports no model-only time to first
token for this model, `ttft=-1`, so there's no split from inside the worker either.)

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

All 18 interruptions stopped her mid-sentence, with no tail and no resumed sentence, and she
answered the new question.

**Heard-only text works locally, but Gemini keeps the rest.** LiveKit adds the interrupted
turn to its history trimmed to what actually played (`interrupted=True`). It estimates the cut by
pacing the transcript against audio playout. Gemini's own transcript of the same turn is the whole
sentence. Gemini streams audio faster than real time, and Google's docs say the session keeps "the
information already sent to the client." For example:

- Heard: "I have three wonderful grandkids. They live in"
- Generated: "I have three wonderful grandkids. They live in Ohio, so I don't get to see them as
  often as I'd like, but they keep me very busy."

Right after each of the 15 interruptions in the reconnect rounds, the bot asked, "What was the
last thing you told me about your grandkids?" `summarize.py` lists the content words in each
answer that appear only in the part Gemini generated after the cut, and nowhere in what was
heard, the rep's lines or her prompt:

- In 1 of 15 the answer held a detail that could only have come from the unheard part. For the
  example above, she said "I was just saying they live in Ohio."
- In 12 more the answer reused words from the unheard part, but they're the model's stock
  phrases. "A few towns over" turns up in 7 of the 15 cut-off replies and "keeps me busy" in 4,
  so she may have said the same thing again rather than remembered it.
- In 2 she reused nothing. One went back to her doctor, and one said "Northside", which is in
  her prompt.

So the spike shows one clear case and a pattern consistent with it, not a rate. The mechanism
matches Google's description of the session, and the plugin can't change it: Gemini Live has no
truncate or delete (`message_truncation=False`, and the plugin logs "Gemini Live does not support
removing messages"). Measuring it properly needs a fixture where the unheard part holds something
that exists nowhere else. For example, ask her to make up a four-digit number, cut her off after
two digits, then compare her answer with Gemini's own transcript of the cut turn.

### Forced reconnect

The worker aborted the Gemini WebSocket's TCP connection mid-call. That's the fast-detect case:
the worker knows at once and Google doesn't. Then the bot asked "what was my extension?" about a
number it had given just before the drop.

A reconnected socket isn't a recovered call, so `summarize.py` checks each drop separately:
did Gemini connect again, did the caller hear a reply to the next line within 5 s, did exactly
one customer turn land between that line ending and the next line starting (so a late answer
can't be credited to the wrong question), did she recall the number, and did every line after
that get an answer.

| Reconnect setup | Drops | Reconnected (back after) | Answered the next line within 5 s | Recalled the extension | Answered every later line |
|---|---|---|---|---|---|
| Plugin default: retry after 0.1 s, resume with Gemini's handle | 8 | **3** (1.18 to 1.32 s) | 3 (1.45 to 1.79 s) | 2 of 2 asked | 3 of 8 |
| Retry after 1.5 s, resume with the handle | 6 | **6** (2.14 to 2.52 s) | 5 (1.06 to 1.84 s; the sixth took 5.03 s) | 4 of 4 whose number reached Gemini (2 lost the digits to clipping before the drop) | 6 of 6 |
| Fresh session, LiveKit replays its history | 4 | **4** (0.64 to 0.74 s) | 4 (1.25 to 1.93 s) | 4 of 4 | 4 of 4 |

Every reply the caller heard passed the attribution check. "Later lines" is one line in the
short reconnect script and two to eight in the full script, so it shows the session kept
working for a few turns, not for a whole call. In this data every drop that reconnected also
answered and kept going. The checks are split so M4's tests can catch one that doesn't.
`results/reconnects.txt` has the per-drop rows.

The three rows aren't like for like on the "answered the next line" column. The caller as run
waited a fixed 2.0 s after the drop and then spoke the follow-up line. The other two setups,
when they reconnected at all, were back by then (0.64 to 1.32 s). The 1.5 s-retry setup took 2.14 to 2.52 s, so by the script's
timing the rep began the follow-up before Gemini was connected again in all six of its drops.
The reports in `results/` don't record when each line started, so that overlap is read from the
script, not measured. That row's reply times, the 5.03 s miss included, were measured with the
follow-up overlapping the reconnect and may say more about the overlap than about the setup.
`caller.py` now waits 4.0 s by default (`SPIKE_KILL_WAIT_S`) and `summarize.py` flags any drop
whose follow-up started before the reconnect finished. Neither has been rerun.

What happened in the failures: the plugin retries 0.1 s after a drop and resumes with Gemini's
session handle. Google answers `1008 BidiGenerateContent session is already connected to an
existing client`, because it hasn't noticed the old socket is dead yet. The plugin treats any error
during connection setup as fatal, so the whole agent session closes and the customer goes silent
for the rest of the call. Turning off the plugin's `session_resumption` option doesn't help: the
plugin always asks Gemini for handles and resumes with the latest one. The fresh-session row
dropped the handle before reconnecting, which is what a fallback in M4 would do.

When it recovered, it recovered well. When the drop hit while she was talking (run A), her turn
ends mid-sentence in the transcript ("...ever since, which"). The report didn't capture how long
her audio kept playing after the drop (`audio after kill=None`). Asked "what were you saying?",
she picked the thought back up.

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
   with director notes. Check it with the made-up-number fixture above, not with grandkid
   questions.
3. **Tune end-of-speech with real speech, number-heavy lines included.** 500 ms bought 0.2 s on the
   median (over run B's 15 plain turns) and clipped spoken digits twice in 125 lines. Try 700 ms with real people and take the
   lowest window that never clips a Medicare number or a date of birth.
4. **Pin the API version and the key variable** as above, and use the paid tier for anything a
   person talks into.

## M4 acceptance gate

The spike picks a direction. It doesn't accept one, and a short run with a bot caller isn't a
substitute for testing the real thing. M4's voice path is done when one setup passes all of
this on the paid tier, with a person on a real microphone in a browser:

- **A 60-minute call** that goes through every GoAway (about one per 10 minutes) plus at least two
  forced drops. After each transition the customer answers the next line within 5 s, stays in
  character, and recalls something said before it.
- **Interruptions throughout**, each logged with what was heard and what was generated, and the
  made-up-number fixture showing she doesn't refer back to unheard words.
- **A complete evidence log**, ordered by audio time across every transition, with no turn missing
  or duplicated.
- **Latency on target** (median ≤ 1.2 s, p95 ≤ 2.5 s) with an end-of-speech setting that clips no
  number-heavy line, and the direct control run alternately in the same window with matching
  endpoints, so any worker overhead is measured rather than inferred.
- **Concurrency**: the same checks with as many calls at once as a class would run.

If the worker fails this in a way browser-direct wouldn't, the decision gets revisited.

## Not tested

- A human on a real microphone, echo, a browser's audio path and a real network
- GoAway at the 10-minute mark, and 45 to 60 minute stability with context compression (it's
  configured in the spike but never hit its limit)
- Tools executed in the worker against the engine
- The paid tier
- Silent network drops, where neither side notices for a while; only the fast-detect case was
  forced
- How often the customer refers back to unheard words, with a fixture that rules out guessing
- Worker against direct latency with matching endpoints
