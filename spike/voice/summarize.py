"""Summarize spike runs from runs/*-caller.json and runs/*-agent.jsonl.

Usage: uv run python summarize.py <room-prefix> [<room-prefix> ...]
Prints latency over every timed turn that isn't right after a forced reconnect, then each forced
reconnect broken into separate checks, a tally per reconnect setup, then each barge-in and probe.

Reconnect checks, kept apart on purpose (a reconnect can pass one and fail the next):
  connected   the plugin reported a new Gemini connection after the drop
  replied     the caller heard a reply to the follow-up line within REPLY_DEADLINE_S, starting
              after that line ended (a reply that began before the rep finished doesn't count)
  attributed  exactly one customer reply landed between that line ending and the next line starting
  heard       Gemini's transcript of the rep's line before the drop held the number (kill_idle only)
  recalled    the attributed reply held the number (kill_idle only)
  later       every line after the follow-up got an audible, attributed reply
A drop is also flagged when the rep started the follow-up line before the reconnect finished: that
reply time isn't comparable with one from a setup that was already back.
"""

import json
import re
import statistics
import sys
from pathlib import Path

import numpy as np

RUNS = Path(__file__).resolve().parent / "runs"
EXTENSION = "4417"
REPLY_DEADLINE_S = 5.0  # longer than this on a phone call is dead air
PLUGIN_FIRST_RETRY_S = 0.1  # the Google plugin's delay before its first reconnect attempt

# Split strings read better here than ruff's one-word-per-line list literals.
ONES = "zero one two three four five six seven eight nine".split()  # noqa: SIM905
TEENS = "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()  # noqa: SIM905, E501
TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()  # noqa: SIM905
_TENS_RE = "|".join(TENS)
_ONES_RE = "|".join(ONES[1:])

STOP = frozenset(
    """that this with they them their there have just really about what when were been from
    your you're i'm it's i've don't like over here very much more some only also into then than
    because would could should which while said saying told last thing back right well yes
    marcus sorry""".split()  # noqa: SIM905
)


def spoken_digits(text: str) -> str:
    """Turn spoken numbers into digit runs: "forty-four seventeen", "fortyfour seventeen",
    "four four one seven" and "4-4-1-7" all become "4417"."""
    s = text.lower()
    s = re.sub(
        rf"\b({_TENS_RE})[\s-]?({_ONES_RE})\b",
        lambda m: str((TENS.index(m[1]) + 2) * 10 + ONES.index(m[2])),
        s,
    )
    s = re.sub(rf"\b({_TENS_RE})\b", lambda m: str((TENS.index(m[1]) + 2) * 10), s)
    s = re.sub(rf"\b({'|'.join(TEENS)})\b", lambda m: str(TEENS.index(m[1]) + 10), s)
    s = re.sub(rf"\b({'|'.join(ONES)})\b", lambda m: str(ONES.index(m[1])), s)
    return re.sub(r"(?<=\d)[\s,.\-]+(?=\d)", "", s)


def has_number(text: str | None, number: str = EXTENSION) -> bool:
    return bool(text) and number in spoken_digits(text)


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z']+", text.lower())
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words if len(w) >= 4} - STOP


def unheard_words(heard: str, generated: str, reply: str, known: str) -> list[str]:
    """Content words in the reply that Gemini generated after the cut-off point and that appear
    nowhere in what was heard, the rep's lines or the customer prompt. A non-empty result means
    the reply reused something the rep never heard and the model wasn't told elsewhere."""
    unheard = generated[len(heard) :] if generated.startswith(heard) else generated
    exclusive = content_words(unheard) - content_words(heard) - content_words(known)
    return sorted(content_words(reply) & exclusive)


def load(room: str) -> tuple[list[dict], list[dict]]:
    steps = json.loads((RUNS / f"{room}-caller.json").read_text())
    path = RUNS / f"{room}-agent.jsonl"
    events = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    return steps, events


def p95(xs: list[float]) -> float:
    """Nearest-rank p95. With ~20 samples this is the second-worst turn, so read it loosely."""
    ordered = sorted(xs)
    return ordered[max(0, int(np.ceil(0.95 * len(ordered))) - 1)]


def items_between(events: list[dict], role: str, start: float, end: float) -> list[str]:
    """Conversation items of one role added in (start, end]. Both bounds are required: an
    unbounded search picks up a reply to a later line and credits it to this one."""
    return [
        e["text"] or ""
        for e in events
        if e["kind"] == "item" and e.get("role") == role and start < e["t"] <= end
    ]


def window_end(steps: list[dict], events: list[dict], i: int) -> float:
    """When the line after step i started, or the end of the agent log for the last step."""
    nxt = next((x for x in steps if x["i"] == i + 1), None)
    if nxt:
        return nxt["said_from"]
    return max((e["t"] for e in events), default=0.0)


def reply_to(steps: list[dict], events: list[dict], step: dict) -> tuple[str | None, int]:
    """The customer reply attributed to a rep line, and how many candidates there were."""
    replies = items_between(
        events, "assistant", step["said_to"], window_end(steps, events, step["i"])
    )
    return (replies[0] if len(replies) == 1 else None), len(replies)


TIMED = ("turn", "probe", "barge", "kill_idle", "kill_speaking")


def turn_latencies(steps: list[dict]) -> tuple[list[float], int, int]:
    """Latencies of timed turns that aren't right after a forced drop, plus how many replies
    started before the rep finished (latency <= 0) and how many lines got no reply."""
    after_kill = {s["i"] + 1 for s in steps if s["kind"].startswith("kill")}
    normal = [s["latency_s"] for s in steps if s["kind"] in TIMED and s["i"] not in after_kill]
    heard = [x for x in normal if x is not None]
    return [x for x in heard if x > 0], sum(x <= 0 for x in heard), len(normal) - len(heard)


def cut_turn(events: list[dict], barge_from: float) -> tuple[str | None, str | None]:
    """The customer turn a barge-in cut off: what LiveKit says was heard, and what Gemini's own
    transcript says it generated. Either is None when the agent logged no such turn."""

    def near(e: dict) -> bool:
        return bool(e.get("interrupted")) and barge_from - 1 < e["t"] < barge_from + 15

    heard = next(
        (
            e["text"]
            for e in events
            if e["kind"] == "item" and e.get("role") == "assistant" and near(e)
        ),
        None,
    )
    gen = next((e["generated"] for e in events if e["kind"] == "model_turn_text" and near(e)), None)
    return heard, gen


def answered(steps: list[dict], events: list[dict], step: dict) -> bool:
    """Heard a reply, and the agent logged at least one customer item for this line (a barge-in
    line logs two: the interrupted turn and the answer)."""
    _, n = reply_to(steps, events, step)
    return step["latency_s"] is not None and step["latency_s"] > 0 and n > 0


def reconnect_setup(events: list[dict]) -> str:
    start = next((e for e in events if e["kind"] == "start"), {})
    retry = next((e for e in events if e["kind"] == "retry"), {})
    first = float(retry.get("first_retry_s", PLUGIN_FIRST_RETRY_S))
    if not start.get("resumption", True):
        return f"fresh session, LiveKit replays history, first retry {first:g} s"
    if first == PLUGIN_FIRST_RETRY_S:
        return f"plugin default: resume, first retry {first:g} s"
    return f"resume with handle, first retry {first:g} s"


def classify_reconnect(steps: list[dict], events: list[dict], kill: dict) -> dict:
    k = kill["notes"]["kill_at"]
    conn = next((e for e in events if e["kind"] == "model_connected" and e["t"] > k), None)
    fatal = next(
        (
            e
            for e in events
            if e["kind"] == "model_error"
            and e["t"] > k
            and str(e.get("recoverable")) in ("False", "false")
        ),
        None,
    )
    follow = next((x for x in steps if x["i"] == kill["i"] + 1), None)
    reply, n_replies = reply_to(steps, events, follow) if follow else (None, 0)
    lat = follow["latency_s"] if follow else None
    is_idle = kill["kind"] == "kill_idle"
    heard = " ".join(items_between(events, "user", kill["said_from"], k)) if is_idle else None
    later = [x for x in steps if follow and x["i"] > follow["i"]]
    return {
        "kind": kill["kind"],
        "connected": conn is not None,
        "back_s": conn["t"] - k if conn else None,
        "fatal": (fatal or {}).get("error"),
        "reply_s": lat,
        "replied": lat is not None and 0 < lat <= REPLY_DEADLINE_S,
        "overlapped": bool(conn and follow and follow["said_from"] < conn["t"]),
        "attributed": reply is not None,
        "n_replies": n_replies,
        "reply": reply,
        "heard": has_number(heard) if is_idle else None,
        "recalled": has_number(reply) if is_idle else None,
        "later_ok": sum(answered(steps, events, x) for x in later),
        "later_n": len(later),
    }


def _yn(v: bool | None) -> str:
    return "-" if v is None else ("yes" if v else "NO")


def _range(xs: list[float]) -> str:
    return f"{min(xs):.2f}-{max(xs):.2f}s" if xs else "-"


def main(prefixes: list[str]) -> None:
    rooms = sorted(
        p.name.removesuffix("-caller.json")
        for p in RUNS.glob("*-caller.json")
        if any(p.name.startswith(x) for x in prefixes)
    )
    lats: list[float] = []
    no_reply = 0
    for room in rooms:
        room_lats, _, room_missing = turn_latencies(load(room)[0])
        lats += room_lats
        no_reply += room_missing
    if lats:
        print(
            f"latency over {len(lats)} turns in {len(rooms)} calls: median "
            f"{statistics.median(lats):.2f}s, p95 {p95(lats):.2f}s, max {max(lats):.2f}s; "
            f"turns with no reply: {no_reply}"
        )

    print(f"\nforced reconnects (reply deadline {REPLY_DEADLINE_S:g} s):")
    print(
        f"  {'room':32s} {'kind':13s} {'connected':>14s} {'replied':>13s} {'attrib':>6s} "
        f"{'heard':>5s} {'recall':>6s} {'later':>5s}"
    )
    by_setup: dict[str, list[dict]] = {}
    for room in rooms:
        steps, events = load(room)
        for s in steps:
            if not s["kind"].startswith("kill") or "kill_at" not in s["notes"]:
                continue
            r = classify_reconnect(steps, events, s)
            by_setup.setdefault(reconnect_setup(events), []).append(r)
            back = f"yes {r['back_s']:.2f}s" if r["connected"] else "NO"
            rep = f"{_yn(r['replied'])} {r['reply_s']:.2f}s" if r["reply_s"] else "NO"
            attrib = _yn(r["attributed"]) + ("?" if r["n_replies"] > 1 else "")
            print(
                f"  {room:32s} {r['kind']:13s} {back:>14s} {rep:>13s} {attrib:>6s} "
                f"{_yn(r['heard']):>5s} {_yn(r['recalled']):>6s} "
                f"{r['later_ok']}/{r['later_n']:<3d}"
            )
            if r["fatal"]:
                print(f"      fatal: {r['fatal'][:90]}")
            if r["overlapped"]:
                print("      follow-up line started before the reconnect finished")
            if r["reply"] and r["kind"] == "kill_idle":
                print(f"      reply: {r['reply'][:100]!r}")

    print("\nreconnects by setup:")
    for setup, rs in sorted(by_setup.items()):
        idle = [r for r in rs if r["kind"] == "kill_idle"]
        heard = [r for r in idle if r["heard"] and r["connected"]]
        print(f"  {setup}: {len(rs)} drops")
        print(
            f"    connected {sum(r['connected'] for r in rs)}/{len(rs)} "
            f"(back {_range([r['back_s'] for r in rs if r['back_s'] is not None])}); "
            f"replied in time {sum(r['replied'] for r in rs)}/{len(rs)} "
            f"({_range([r['reply_s'] for r in rs if r['reply_s']])}); "
            f"attributed {sum(r['attributed'] for r in rs)}/{len(rs)}; "
            f"later lines all answered "
            f"{sum(r['later_n'] > 0 and r['later_ok'] == r['later_n'] for r in rs)}/{len(rs)}; "
            f"follow-up spoken over the reconnect {sum(r['overlapped'] for r in rs)}/{len(rs)}"
        )
        print(
            f"    number in the rep transcript before the drop "
            f"{sum(bool(r['heard']) for r in idle)}/{len(idle)}; recalled "
            f"{sum(bool(r['recalled']) for r in heard)}/{len(heard)} of those that also reconnected"
        )

    print("\nbarge-ins and probes:")
    known_base = _customer_prompt()
    for room in rooms:
        steps, events = load(room)
        for s in steps:
            n = s["notes"]
            if s["kind"] != "barge" or "barge_from" not in n:
                continue
            heard, gen = cut_turn(events, n["barge_from"])
            print(
                f"  {room} barge cutoff={n.get('cutoff_s', float('nan')):.2f}s "
                f"reply={n.get('reply_latency_s', float('nan')):.2f}s"
            )
            print(f"      heard:     {heard!r}\n      generated: {gen!r}")
            probe = next((x for x in steps if x["i"] == s["i"] + 1 and x["kind"] == "probe"), None)
            if probe is None:
                continue
            reply, _ = reply_to(steps, events, probe)
            print(f"      probe reply: {reply!r}")
            if heard and gen and reply:
                known = " ".join([known_base, s["line"], n.get("barge_line", ""), probe["line"]])
                print(f"      unheard words reused: {unheard_words(heard, gen, reply, known)}")


def _customer_prompt() -> str:
    """The customer prompt, so words she could have taken from it don't count as unheard."""
    from agent import customer_prompt

    return customer_prompt()


if __name__ == "__main__":
    main(sys.argv[1:])
