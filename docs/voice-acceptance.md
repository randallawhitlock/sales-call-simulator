# Voice acceptance run

This is the M4 acceptance call: a person on a real microphone, about an hour, on the paid
Gemini tier. It covers the parts of the [acceptance gate](voice-spike.md#m4-acceptance-gate)
that need a human. Concurrency, the direct-vs-worker latency control and the made-up-number
fixture run with a bot caller and aren't part of this session.

Budget about 75 minutes, including setup and reading the report. At the default price settings
the model costs roughly a dollar an hour (the smoke call was $0.03 for 113 s).

## Before you start

- A paid-tier `GEMINI_API_KEY`. The free tier's limits end long calls early.
- Chrome, with the laptop's own speakers and mic. Speakers are the harder case: the mic hears
  her voice, so echo cancellation has to work. Close other tabs that use the mic.
- Four terminals, each with the voice settings from the README (`SIM_LIVE_MODEL`,
  `GEMINI_API_KEY`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`):

```bash
livekit-server --dev
```

```bash
cd backend && uv run uvicorn app.main:app
```

```bash
cd backend && SIM_VOICE_DEBUG_DROPS=1 uv run python -m app.voice.worker dev
```

```bash
cd frontend && npm run dev
```

`SIM_VOICE_DEBUG_DROPS=1` lets the drop command below cut the worker's Gemini connection. Leave
it off for anything but test runs.

## The call

Open http://localhost:5173, pick the must-keep specialist case, choose a voice call and join.
The session id is the `session` value in the address bar; copy it, you'll need it for drops
and the report.

Work the case the way you would on a real call, and stretch it to an hour. Along the way:

- **Interrupt her** at least ten times, spread through the call. Do a few of them while she's
  reading out a number or a list, so part of what she generated never plays.
- **Go quiet** for research a few times: a minute or two of looking things up without talking.
  She should check in after about 25 s, then less often.
- **Put her on hold** once for a minute or so, then come back and pick up where you were.
- **Reload the tab** once mid-call and rejoin.
- **Force two drops**, one around minute 15 and one around minute 40, from a fifth terminal
  with the same settings:

```bash
cd backend && uv run python -m app.voice.drop <session id>
```

  If it says nothing was dropped, she was between connections. Run it again a few seconds later.

- **GoAways** happen on their own, about every 10 minutes. You won't notice them; the report
  finds them.
- **After each drop, and every 10 minutes or so**, ask her something that only works if she
  remembers the call: her cardiologist's name, a drug she mentioned, which pharmacy she uses,
  something she worried about earlier. The report shows what she said after each transition,
  and you'll judge whether she stayed in character and remembered.
- **Read out some numbers**: a phone number, a plan id, a dollar amount. Note the time if she
  answers as if she missed digits. That points to the end-of-speech setting
  (`SIM_VOICE_SILENCE_MS`, default 700) cutting you off.

Jot down anything odd with the call time: a reply that took forever, a line she repeated, a
moment she lost the thread. The report can't hear tone, and your notes are what the recording
gets checked against.

End the call with the End call button, then wait for the debrief to show the call stats.

## The report

```bash
cd backend && uv run python -m app.voice.gate <session id>
```

Each part of the gate gets a status:

| Status | Meaning |
|---|---|
| PASS | Met, from the logs alone |
| FAIL | Missed, with the line or number that missed it |
| REVIEW | Needs a person: listen to the moment in the recording or read the lines it shows |
| NOT_COVERED | One call can't show it (concurrency, the direct control, the fixture) |

Times are from the start of the call. The recording is at `backend/recordings/<session id>.ogg`
and starts when the customer joins, so it can run a second or two behind the report. If her
session was rebuilt (a restart or rejoin in the Transitions section), the audio carries on in
`<session id>-2.ogg` and so on, and each part's clock starts at that restart or rejoin.

For each REVIEW item:

- **Transitions**: read the lines before and after. She should answer in character, and the
  answer to your memory question should match what was said before.
- **Interruptions**: the report lists what she generated and what you heard. Later in the call
  she shouldn't refer to anything that only appears in the generated text.
- **Evidence log**: places where one side spoke twice in a row are listed with times. Most are
  pauses, nudges or interruptions. Check any you don't recognize against the recording for a
  lost line.

Save the report next to the run's notes (`--json` gives a machine-readable copy) and add the
results to [voice-spike.md](voice-spike.md) when the run is done.
