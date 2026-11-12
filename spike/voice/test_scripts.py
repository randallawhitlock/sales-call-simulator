"""Offline checks for the caller, control and worker scripts. Needs the spike's dependencies
installed (the scripts import LiveKit and google-genai), but no server, no key and no network.

Run: uv run python -m unittest test_scripts
"""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from google.genai import types

import agent
import caller
import direct
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


class Synth(unittest.TestCase):
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


class Instrument(unittest.TestCase):
    def test_text_from_a_turn_the_socket_dropped_is_not_prepended_to_the_next(self):
        logged: list[tuple[str, dict]] = []
        rt = SimpleNamespace(
            _report_connection_acquired=lambda dt: None,
            _handle_server_content=lambda sc: None,
            _emit_error=lambda error, recoverable: None,
            session_resumption_handle="h",
        )
        agent._instrument(rt, lambda kind, **fields: logged.append((kind, fields)))

        def said(text: str, **flags: bool) -> types.LiveServerContent:
            return types.LiveServerContent(
                output_transcription=types.Transcription(text=text), **flags
            )

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
