"""Offline checks for the caller, control and worker scripts. Needs the spike's dependencies
installed (the scripts import LiveKit and google-genai), but no server, no key and no network.

Run: uv run python -m unittest test_scripts
"""

import asyncio
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from google.genai import types

import agent
import caller
import direct
import live_settings
import summarize
from test_summarize import ev, say, step


class Report(unittest.TestCase):
    def report(self, steps: list[dict], events: list[dict]) -> str:
        with tempfile.TemporaryDirectory() as d, mock.patch.object(caller, "RUNS", Path(d)):
            (Path(d) / "room-caller.json").write_text(json.dumps(steps))
            (Path(d) / "room-agent.jsonl").write_text("\n".join(json.dumps(e) for e in events))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                caller.report("room")
        return out.getvalue()

    def test_a_dead_call_still_reports(self):
        """Run B's shape: the session died at the drop, so the barge got no answer to cut off
        (no barge_from) and the rep's lines got interim transcripts but no final one."""
        steps = [
            step(0, "kill_idle", 100.0, 103.0, 1.2, kill_at=110.0),
            step(1, "turn", 112.0, 116.0, None),
            step(2, "barge", 125.0, 127.0, None),
            step(3, "probe", 140.0, 142.0, None),
        ]
        events = [
            ev(90.0, "start", model="m", resumption=True),
            ev(104.0, "user_interim", text="Just so you"),
            say(108.0, "assistant", "Thank you, Marcus."),
        ]
        out = self.report(steps, events)
        self.assertIn("barge [2]: talking at barge=None cutoff=None reply=None", out)
        self.assertIn("reconnected after never", out)
        self.assertIn("first interim median +1.00s (n=1), final median none (n=0)", out)

    def test_replies_are_credited_only_inside_their_own_line(self):
        steps = [
            step(0, "kill_idle", 100.0, 103.0, 1.2, kill_at=110.0),
            step(1, "turn", 112.0, 116.0, None),
            step(2, "probe", 125.0, 127.0, None),
            step(3, "turn", 140.0, 142.0, 1.0),
        ]
        events = [
            ev(90.0, "start", model="m", resumption=True),
            ev(111.0, "model_connected", connect_s=0.6, handle="h"),
            say(144.0, "assistant", "Alder Family Pharmacy."),
        ]
        out = self.report(steps, events)
        self.assertIn("next turn latency None, reply: None", out)
        self.assertIn("probe [2]: 'line 2'\n    reply: None", out)

    def test_a_line_spoken_over_the_customer_is_named_in_the_report(self):
        steps = [
            step(0, "turn", 100.0, 103.0, 1.2),
            step(1, "turn", 112.0, 116.0, 0.2, not_idle_at_start=True),
        ]
        out = self.report(steps, [ev(90.0, "start", model="m", resumption=True)])
        self.assertIn("over 1 turns", out)
        self.assertIn("the wait for quiet timed out", out)
        self.assertIn("steps [1]", out)

    def test_a_barge_that_cut_nothing_reports_nothing_cut(self):
        steps = [
            step(0, "barge", 100.0, 102.0, 1.0, barge_from=104.0, barge_line="Sorry."),
            step(1, "barge", 110.0, 112.0, 1.0, barge_from=114.0, barge_line="Sorry."),
        ]
        events = [
            ev(90.0, "start", model="m", resumption=True),
            ev(114.5, "item", role="assistant", text="We bake", interrupted=True),
            ev(114.6, "model_turn_text", generated="We bake cookies.", interrupted=True),
        ]
        out = self.report(steps, events)
        first, second = out.split("barge [1]")[0], out.split("barge [1]")[1]
        self.assertIn("heard (LiveKit, interrupted item): None", first)
        self.assertIn("generated (Gemini transcript):     None", first)
        self.assertIn("'We bake cookies.'", second.split("\ntranscript")[0])


def ear_with(frames: list[tuple[float, float]]) -> caller.Ear:
    ear = caller.Ear()
    for t, rms in frames:
        ear.add(t, rms)
    return ear


class EarLookups(unittest.TestCase):
    """The bisected lookups against the plain scans they replaced."""

    def setUp(self):
        rng = np.random.default_rng(3)
        # 60 s of 10 ms frames: bursts of speech with quiet between them.
        self.frames = [
            (1000.0 + i * 0.01, 4000.0 if (i // 150) % 3 == 1 and rng.random() > 0.1 else 2.0)
            for i in range(6000)
        ]
        self.ear = ear_with(self.frames)

    def test_first_loud_after_matches_a_scan(self):
        for t0 in (0.0, 1000.0, 1001.5, 1002.0, 1017.305, 1059.99, 2000.0):
            scan = next((t for t, r in self.frames if t > t0 and r > caller.LOUD), None)
            self.assertEqual(self.ear.first_loud_after(t0), scan)

    def test_silence_start_after_matches_a_scan(self):
        def scan(t0: float, gap: float) -> float | None:
            last = None
            for t, rms in self.frames:
                if t <= t0:
                    continue
                if rms > caller.LOUD:
                    last = t
                elif last is not None and t - last >= gap:
                    return last
            return None

        for t0 in (0.0, 1001.6, 1002.2, 1003.1, 1030.0, 1058.0, 2000.0):
            self.assertEqual(self.ear.silence_start_after(t0, 0.5), scan(t0, 0.5))
        self.assertIsNotNone(self.ear.silence_start_after(1001.6, 0.5))

    def test_last_loud(self):
        self.assertEqual(caller.Ear().last_loud(), 0.0)
        self.assertEqual(self.ear.last_loud(), max(t for t, r in self.frames if r > caller.LOUD))

    def test_quiet_frames_are_not_loud(self):
        ear = ear_with([(1.0, caller.LOUD), (2.0, caller.LOUD + 1)])
        self.assertEqual(ear.first_loud_after(0.0), 2.0)


class FakeRoom:
    def __init__(self, state: str | None) -> None:
        attrs = {"lk.agent.state": state} if state else {}
        self.remote_participants = {"agent": SimpleNamespace(attributes=attrs)}


class Waits(unittest.TestCase):
    def test_an_agent_that_never_gets_ready_fails_with_a_clear_error(self):
        ear = caller.Ear()
        ear.room = FakeRoom(None)
        with self.assertRaises(TimeoutError) as raised:
            asyncio.run(ear.wait_ready(timeout=0.05))
        self.assertIn("didn't reach listening or speaking", str(raised.exception))
        self.assertIn("initializing", str(raised.exception))

    def test_a_ready_agent_returns_at_once(self):
        ear = caller.Ear()
        ear.room = FakeRoom("listening")
        asyncio.run(ear.wait_ready(timeout=0.05))

    def test_wait_idle_says_whether_she_went_quiet(self):
        ear = caller.Ear()
        ear.room = FakeRoom("speaking")
        self.assertFalse(asyncio.run(ear.wait_idle(quiet=0.0, timeout=0.1)))
        ear.room = FakeRoom("listening")
        self.assertTrue(asyncio.run(ear.wait_idle(quiet=0.0, timeout=0.1)))


class Synth(unittest.TestCase):
    def test_a_clip_is_trimmed_to_its_voiced_samples_at_either_rate(self):
        pcm = np.array([0, 150, -3, 900, -40, -1200, 199, 0], dtype=np.int16)
        for rate in (caller.SR, direct.SR_IN):
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "clip.wav"
                with wave.open(str(path), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(rate)
                    w.writeframes(pcm.tobytes())
                with mock.patch.object(caller, "synth", lambda text, cache, r, p=path: p):
                    clip = caller.voiced_clip("hello", Path(d), rate)
                    self.assertEqual(clip.tolist(), [900, -40, -1200])
                    if rate == direct.SR_IN:
                        self.assertEqual(direct.tts16("hello"), clip.tobytes())

    def test_a_clip_is_renamed_into_place_and_made_once(self):
        seen: list[Path] = []

        def fake_say(cmd: list[str], check: bool) -> None:
            target = Path(cmd[cmd.index("-o") + 1])
            seen.append(target)
            target.write_bytes(b"RIFF")

        with tempfile.TemporaryDirectory() as d, mock.patch.object(subprocess, "run", fake_say):
            path = caller.synth("hello", Path(d) / "tts", 48_000)
            self.assertEqual(caller.synth("hello", Path(d) / "tts", 48_000), path)
            self.assertEqual(len(seen), 1)
            self.assertNotEqual(seen[0], path)
            self.assertEqual([p.name for p in path.parent.iterdir()], [path.name])
            self.assertEqual(path.read_bytes(), b"RIFF")

    def test_a_failed_synthesis_leaves_nothing_in_the_cache(self):
        def fake_say(cmd: list[str], check: bool) -> None:
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"RI")
            raise subprocess.CalledProcessError(1, cmd)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(subprocess, "run", fake_say):
            with self.assertRaises(subprocess.CalledProcessError):
                caller.synth("hello", Path(d), 48_000)
            self.assertEqual(list(Path(d).iterdir()), [])


class KillWait(unittest.TestCase):
    def test_default_pause_outlasts_the_slowest_recorded_reconnect(self):
        self.assertGreater(caller.KILL_WAIT_S, 2.52)


class Credit(unittest.TestCase):
    def reply(self, line: int, first: float, done_at: float) -> direct.Reply:
        return direct.Reply(line, first, first + 0.1, done_at)

    def test_reply_after_the_line_is_credited(self):
        self.assertEqual(direct.credit(self.reply(3, 11.0, 14.0), 3, 10.0), "reply")

    def test_reply_that_began_early_but_finished_after_the_line_is_still_its_reply(self):
        self.assertEqual(direct.credit(self.reply(3, 9.8, 14.0), 3, 10.0), "reply")

    def test_reply_that_finished_while_the_line_was_still_going_is_set_aside(self):
        self.assertEqual(direct.credit(self.reply(3, 7.0, 8.0), 3, None), "mid_line")
        self.assertEqual(direct.credit(self.reply(3, 7.0, 8.0), 3, 10.0), "mid_line")

    def test_late_reply_to_an_earlier_line_is_not_credited_to_this_one(self):
        self.assertEqual(direct.credit(self.reply(2, 5.0, 14.0), 3, 10.0), "stale")


class ControlLines(unittest.TestCase):
    def test_the_control_speaks_only_lines_that_are_plain_turns_for_the_worker(self):
        picked = direct.control_lines(caller.SCRIPT, 99)
        kinds = [caller.SCRIPT[i][0] for i, _ in picked]
        self.assertEqual(set(kinds), {"turn", "kill_idle", "kill_speaking"})
        texts = [line for _, line in picked]
        self.assertIn("Just so you have it, my extension is four four one seven.", texts)
        for line in texts:
            self.assertNotIn("what my extension was", line)
            self.assertNotIn("You cut out", line)
        for i, line in picked:
            self.assertEqual(caller.SCRIPT[i][1], line)
            self.assertFalse(caller.SCRIPT[i - 1][0].startswith("kill") if i else False)

    def test_every_control_line_is_one_the_worker_figure_counts(self):
        """Give every step a latency equal to its index: the steps the control picks are a
        subset of the ones summarize.turn_latencies keeps."""
        steps = [step(i, s[0], 0.0, 0.0, float(i + 1)) for i, s in enumerate(caller.SCRIPT)]
        counted = {int(x) - 1 for x in summarize.turn_latencies(steps)[0]}
        picked = {i for i, _ in direct.control_lines(caller.SCRIPT, 99)}
        self.assertLessEqual(picked, counted)
        self.assertEqual(len(direct.control_lines(caller.SCRIPT, 5)), 5)


class SharedSettings(unittest.TestCase):
    def config(self, silence_ms: int | None) -> types.LiveConnectConfig:
        with mock.patch.object(live_settings, "customer_prompt", lambda: "prompt"):
            return live_settings.connect_config(silence_ms)

    def test_the_control_config_is_built_from_the_workers_pieces(self):
        conf = self.config(500)
        self.assertEqual(conf.realtime_input_config, live_settings.end_of_speech(500))
        self.assertEqual(conf.context_window_compression, live_settings.context_compression())
        self.assertEqual(conf.temperature, live_settings.TEMPERATURE)
        self.assertEqual(
            conf.realtime_input_config.automatic_activity_detection.silence_duration_ms, 500
        )

    def test_no_window_leaves_googles_end_of_speech_defaults(self):
        self.assertIsNone(live_settings.end_of_speech(None))
        self.assertIsNone(self.config(None).realtime_input_config)
        self.assertIsNone(direct.silence_arg("default"))
        self.assertEqual(direct.silence_arg("700"), 700)

    def test_the_voice_follows_the_workers_variable(self):
        def voice_of(conf: types.LiveConnectConfig) -> str:
            return conf.speech_config.voice_config.prebuilt_voice_config.voice_name

        with mock.patch.dict("os.environ", {"SPIKE_VOICE": "Kore"}):
            self.assertEqual(voice_of(self.config(500)), "Kore")
        with mock.patch.dict("os.environ", clear=True):
            self.assertEqual(voice_of(self.config(500)), live_settings.DEFAULT_VOICE)

    def test_the_worker_uses_the_same_builders(self):
        self.assertIs(agent.customer_prompt, live_settings.customer_prompt)
        self.assertIs(agent.end_of_speech, live_settings.end_of_speech)
        self.assertIs(agent.context_compression, live_settings.context_compression)


class Stats(unittest.TestCase):
    def test_stats_uses_the_shared_p95_over_positive_latencies(self):
        xs = [float(x) for x in range(1, 21)] + [-0.5, 0.0]
        self.assertIn(f"p95 {summarize.p95(xs[:20]):.2f}s", direct.stats(xs))
        self.assertTrue(direct.stats(xs).startswith("20 turns"))
        self.assertEqual(direct.stats([-1.0]), "no turns")


def said(text: str, **flags: bool) -> types.LiveServerContent:
    return types.LiveServerContent(output_transcription=types.Transcription(text=text), **flags)


class Instrument(unittest.TestCase):
    def instrumented(self) -> tuple[SimpleNamespace, list[tuple[str, dict]]]:
        logged: list[tuple[str, dict]] = []
        rt = SimpleNamespace(
            _report_connection_acquired=lambda dt: None,
            _handle_server_content=lambda sc: None,
            _emit_error=lambda error, recoverable: None,
            session_resumption_handle="h",
        )
        agent._instrument(rt, lambda kind, **fields: logged.append((kind, fields)))
        return rt, logged

    def turns(self, logged: list[tuple[str, dict]]) -> list[tuple]:
        return [
            (f["generated"], f["interrupted"], f.get("late"))
            for k, f in logged
            if k == "model_turn_text"
        ]

    def test_text_after_the_interruption_is_logged_as_the_tail_of_the_cut_turn(self):
        rt, logged = self.instrumented()
        rt._handle_server_content(said("They live in"))
        rt._handle_server_content(types.LiveServerContent(interrupted=True))
        rt._handle_server_content(said(" Ohio, so"))
        rt._handle_server_content(types.LiveServerContent(turn_complete=True))
        rt._handle_server_content(said("Alder Family Pharmacy.", turn_complete=True))
        self.assertEqual(
            self.turns(logged),
            [
                ("They live in", True, None),
                ("They live in Ohio, so", True, " Ohio, so"),
                ("Alder Family Pharmacy.", False, None),
            ],
        )
        heard, gen = summarize.cut_turn(
            [{"t": 100.0 + i, "kind": k, **f} for i, (k, f) in enumerate(logged)], 99.5
        )
        self.assertEqual(gen, "They live in Ohio, so")

    def test_an_interruption_with_no_late_text_logs_the_turn_once(self):
        rt, logged = self.instrumented()
        rt._handle_server_content(said("They live in", interrupted=True))
        rt._handle_server_content(types.LiveServerContent(turn_complete=True))
        rt._handle_server_content(said("Yes.", interrupted=True, turn_complete=True))
        rt._handle_server_content(said("Alder Family Pharmacy.", turn_complete=True))
        self.assertEqual(
            self.turns(logged),
            [
                ("They live in", True, None),
                ("Yes.", True, None),
                ("Alder Family Pharmacy.", False, None),
            ],
        )

    def test_a_reconnect_closes_a_cut_turn(self):
        rt, logged = self.instrumented()
        rt._handle_server_content(said("They live in", interrupted=True))
        rt._report_connection_acquired(1.2)
        rt._handle_server_content(said("I was just saying.", turn_complete=True))
        self.assertEqual(
            self.turns(logged),
            [("They live in", True, None), ("I was just saying.", False, None)],
        )

    def test_text_from_a_turn_the_socket_dropped_is_not_prepended_to_the_next(self):
        rt, logged = self.instrumented()

        rt._handle_server_content(said("It was quite a frightening time, which"))
        rt._report_connection_acquired(1.2)
        rt._handle_server_content(said("I was just saying.", turn_complete=True))
        rt._report_connection_acquired(0.9)

        self.assertEqual(
            [(k, f.get("generated")) for k, f in logged if k != "model_connected"],
            [
                ("model_turn_dropped", "It was quite a frightening time, which"),
                ("model_turn_text", "I was just saying."),
            ],
        )


if __name__ == "__main__":
    unittest.main()
