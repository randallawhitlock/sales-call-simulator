"""Offline checks for summarize.py's analysis helpers. No LiveKit, no Gemini, no saved runs.

Run: uv run python -m unittest test_summarize
"""

import unittest

import summarize as S

KILL_AT = 110.0


def step(i: int, kind: str, said_from: float, said_to: float, latency: float | None, **notes):
    return {
        "i": i,
        "kind": kind,
        "line": f"line {i}",
        "said_from": said_from,
        "said_to": said_to,
        "latency_s": latency,
        "notes": notes,
    }


def ev(t: float, kind: str, **fields):
    return {"t": t, "kind": kind, **fields}


def say(t: float, role: str, text: str):
    return ev(t, "item", role=role, text=text, interrupted=False)


def reconnect_run(*after_kill: dict, follow_latency: float | None = 1.4, heard="4417"):
    """A short call: the rep gives the extension (step 0), the model is dropped at KILL_AT, the rep
    asks for it back (step 1), then asks one more question (step 2)."""
    steps = [
        step(0, "kill_idle", 100.0, 103.0, 1.2, kill_at=KILL_AT),
        step(1, "turn", 112.0, 116.0, follow_latency),
        step(2, "turn", 125.0, 127.0, 1.1),
    ]
    events = [
        ev(90.0, "start", resumption=True),
        ev(91.0, "model_connected", connect_s=0.5),
        say(108.0, "user", f"Just so you have it. My extension is {heard}"),
        say(108.0, "assistant", "Thank you, Marcus."),
        ev(KILL_AT + 0.01, "model_error", error="1006 abnormal closure", recoverable=True),
        *after_kill,
    ]
    return steps, sorted(events, key=lambda e: e["t"])


class SpokenNumbers(unittest.TestCase):
    def test_spellings_of_the_extension_match(self):
        for text in [
            "Your extension was 4417.",
            "It's 44 17.",
            "four four one seven",
            "You said forty-four seventeen.",
            "You told me your extension number was fortyfour seventeen.",
            "Forty four, seventeen.",
            "4-4-1-7",
        ]:
            with self.subTest(text=text):
                self.assertTrue(S.has_number(text))

    def test_partial_or_missing_numbers_do_not_match(self):
        for text in [
            "My extension is 44",
            "You said your extension is forty-four, Marcus.",
            "My extension is",
            "four hundred seventeen",
            "",
            None,
        ]:
            with self.subTest(text=text):
                self.assertFalse(S.has_number(text))


class Bounds(unittest.TestCase):
    def test_items_between_is_open_at_start_closed_at_end(self):
        events = [
            say(10.0, "assistant", "a"),
            say(15.0, "assistant", "b"),
            say(20.1, "assistant", "c"),
        ]
        self.assertEqual(S.items_between(events, "assistant", 10.0, 20.0), ["b"])
        self.assertEqual(S.items_between(events, "user", 0.0, 99.0), [])

    def test_last_step_window_ends_at_the_last_event(self):
        steps = [step(0, "turn", 1.0, 2.0, 1.0)]
        events = [ev(0.5, "start"), say(4.0, "assistant", "hi"), ev(9.0, "close")]
        self.assertEqual(S.window_end(steps, events, 0), 9.0)
        self.assertEqual(S.reply_to(steps, events, steps[0]), ("hi", 1))

    def test_a_reply_to_the_next_line_is_not_credited_to_this_one(self):
        steps = [step(0, "turn", 1.0, 2.0, None), step(1, "turn", 10.0, 12.0, 1.0)]
        events = [say(13.5, "assistant", "Your extension was 4417.")]
        self.assertEqual(S.reply_to(steps, events, steps[0]), (None, 0))
        self.assertEqual(S.reply_to(steps, events, steps[1]), ("Your extension was 4417.", 1))


class Reconnects(unittest.TestCase):
    def test_healthy_reconnect_passes_every_check(self):
        steps, events = reconnect_run(
            ev(111.2, "model_connected", connect_s=0.6),
            say(119.0, "assistant", "You said your extension was 4417."),
            say(129.0, "assistant", "Alder Family Pharmacy."),
        )
        r = S.classify_reconnect(steps, events, steps[0])
        self.assertTrue(r["connected"])
        self.assertAlmostEqual(r["back_s"], 1.2)
        self.assertIsNone(r["fatal"])
        self.assertTrue(r["replied"])
        self.assertTrue(r["attributed"])
        self.assertTrue(r["heard"])
        self.assertTrue(r["recalled"])
        self.assertEqual((r["later_ok"], r["later_n"]), (1, 1))

    def test_fatal_setup_error_means_nothing_after_counts(self):
        steps, events = reconnect_run(
            ev(110.2, "model_error", error="1008 already connected", recoverable=False),
            follow_latency=None,
        )
        steps[2]["latency_s"] = None
        r = S.classify_reconnect(steps, events, steps[0])
        self.assertFalse(r["connected"])
        self.assertIn("1008", r["fatal"])
        self.assertFalse(r["replied"])
        self.assertFalse(r["attributed"])
        self.assertFalse(r["recalled"])
        self.assertEqual((r["later_ok"], r["later_n"]), (0, 1))

    def test_connection_alone_is_not_recovery(self):
        steps, events = reconnect_run(ev(111.2, "model_connected"), follow_latency=None)
        steps[2]["latency_s"] = None
        r = S.classify_reconnect(steps, events, steps[0])
        self.assertTrue(r["connected"])
        self.assertFalse(r["replied"])
        self.assertFalse(r["attributed"])
        self.assertEqual(r["later_ok"], 0)

    def test_connection_before_the_drop_does_not_count(self):
        steps, events = reconnect_run(follow_latency=None)
        r = S.classify_reconnect(steps, events, steps[0])
        self.assertFalse(r["connected"])

    def test_a_recoverable_error_is_not_fatal(self):
        steps, events = reconnect_run(ev(111.2, "model_connected"))
        self.assertIsNone(S.classify_reconnect(steps, events, steps[0])["fatal"])

    def test_late_reply_is_attributed_but_not_in_time(self):
        steps, events = reconnect_run(
            ev(111.2, "model_connected"),
            say(123.0, "assistant", "You said 4417."),
            follow_latency=S.REPLY_DEADLINE_S + 1,
        )
        r = S.classify_reconnect(steps, events, steps[0])
        self.assertFalse(r["replied"])
        self.assertTrue(r["attributed"])
        self.assertTrue(r["recalled"])

    def test_two_replies_in_one_window_are_ambiguous(self):
        steps, events = reconnect_run(
            ev(111.2, "model_connected"),
            say(118.0, "assistant", "Hello? Are you there?"),
            say(121.0, "assistant", "You said 4417."),
        )
        r = S.classify_reconnect(steps, events, steps[0])
        self.assertFalse(r["attributed"])
        self.assertEqual(r["n_replies"], 2)
        self.assertFalse(r["recalled"])

    def test_digits_clipped_before_the_drop(self):
        steps, events = reconnect_run(
            ev(111.2, "model_connected"),
            say(119.0, "assistant", "You said your extension is forty-four."),
            heard="44",
        )
        r = S.classify_reconnect(steps, events, steps[0])
        self.assertFalse(r["heard"])
        self.assertFalse(r["recalled"])

    def test_reconnect_setup_from_the_agent_log(self):
        self.assertTrue(S.reconnect_setup([ev(0, "start", resumption=True)]).startswith("plugin"))
        self.assertTrue(S.reconnect_setup([ev(0, "start", resumption=False)]).startswith("fresh"))
        self.assertIn(
            "1.5 s",
            S.reconnect_setup([ev(0, "start", resumption=True), ev(0, "retry", first_retry_s=1.5)]),
        )

    def test_reconnect_setup_is_labelled_by_the_actual_retry_delay(self):
        def setup(resumption: bool, first: float) -> str:
            return S.reconnect_setup(
                [ev(0, "start", resumption=resumption), ev(0, "retry", first_retry_s=first)]
            )

        self.assertEqual(setup(True, 0.1), "plugin default: resume, first retry 0.1 s")
        self.assertEqual(setup(True, 0.5), "resume with handle, first retry 0.5 s")
        self.assertNotEqual(setup(False, 0.1), setup(False, 1.5))
        self.assertIn("1.5 s", setup(False, 1.5))

    def test_a_reply_that_began_before_the_rep_finished_is_not_in_time(self):
        for latency in (0.0, -0.4):
            with self.subTest(latency=latency):
                steps, events = reconnect_run(
                    ev(111.2, "model_connected"),
                    say(119.0, "assistant", "You said 4417."),
                    say(129.0, "assistant", "Alder Family Pharmacy."),
                    follow_latency=latency,
                )
                r = S.classify_reconnect(steps, events, steps[0])
                self.assertFalse(r["replied"])
                self.assertTrue(r["attributed"])
                self.assertFalse(S.answered(steps, events, steps[1]))
                self.assertTrue(S.answered(steps, events, steps[2]))

    def test_follow_up_spoken_over_the_reconnect_is_flagged(self):
        after = (say(119.0, "assistant", "You said 4417."),)
        steps, events = reconnect_run(ev(111.2, "model_connected"), *after)
        self.assertFalse(S.classify_reconnect(steps, events, steps[0])["overlapped"])
        steps, events = reconnect_run(ev(112.5, "model_connected"), *after)
        self.assertTrue(S.classify_reconnect(steps, events, steps[0])["overlapped"])
        steps, events = reconnect_run(follow_latency=None)
        self.assertFalse(S.classify_reconnect(steps, events, steps[0])["overlapped"])


class Latencies(unittest.TestCase):
    def test_early_missing_and_after_drop_turns_stay_out_of_the_latencies(self):
        steps = [
            step(0, "turn", 1.0, 2.0, 1.2),
            step(1, "turn", 5.0, 6.0, -0.1),
            step(2, "barge", 9.0, 10.0, None),
            step(3, "kill_idle", 13.0, 14.0, 1.5),
            step(4, "turn", 17.0, 18.0, 4.0),
            step(5, "probe", 21.0, 22.0, 1.1),
        ]
        self.assertEqual(S.turn_latencies(steps), ([1.2, 1.5, 1.1], 1, 1))


class CutTurns(unittest.TestCase):
    def test_heard_and_generated_come_from_the_turn_the_barge_cut(self):
        events = [
            ev(40.0, "model_turn_text", generated="An earlier cut turn.", interrupted=True),
            ev(99.5, "item", role="assistant", text="They live in", interrupted=True),
            ev(100.2, "item", role="user", text="Sorry to jump in.", interrupted=True),
            ev(100.4, "model_turn_text", generated="They live in Ohio.", interrupted=True),
            ev(104.0, "model_turn_text", generated="Alder Family Pharmacy.", interrupted=False),
        ]
        self.assertEqual(S.cut_turn(events, 100.0), ("They live in", "They live in Ohio."))
        self.assertEqual(S.cut_turn(events, 300.0), (None, None))


class Probes(unittest.TestCase):
    heard = "I have three wonderful grandkids. They live in"
    generated = "I have three wonderful grandkids. They live in Ohio, so I don't see them often."

    def test_detail_only_in_the_unheard_part_is_reported(self):
        reply = "I was just saying they live in Ohio."
        self.assertEqual(S.unheard_words(self.heard, self.generated, reply, known=""), ["ohio"])

    def test_words_she_could_get_elsewhere_do_not_count(self):
        reply = "I was just saying they live in Ohio."
        self.assertEqual(S.unheard_words(self.heard, self.generated, reply, known="Ohio"), [])
        self.assertEqual(
            S.unheard_words(self.heard, self.generated, "My three grandkids.", known=""), []
        )


class Percentile(unittest.TestCase):
    def test_nearest_rank(self):
        self.assertEqual(S.p95([float(x) for x in range(1, 21)]), 19.0)
        self.assertEqual(S.p95([2.0]), 2.0)


if __name__ == "__main__":
    unittest.main()
