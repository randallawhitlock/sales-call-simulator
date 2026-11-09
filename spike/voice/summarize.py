"""Summarize spike runs from runs/*-caller.json and runs/*-agent.jsonl.

Usage: uv run python summarize.py <room-prefix> [<room-prefix> ...]
Prints latency over every timed turn that isn't right after a forced reconnect, then one line per
forced reconnect (did the session survive, how long, did the customer remember the extension),
then each barge-in and probe.
"""

import json
import re
import statistics
import sys
from pathlib import Path

import numpy as np

RUNS = Path(__file__).resolve().parent / "runs"
RECALL = re.compile(r"4\s*4\s*1\s*7|forty[- ]four[- ]seventeen|four four one seven", re.I)


def load(room: str) -> tuple[list[dict], list[dict]]:
    steps = json.loads((RUNS / f"{room}-caller.json").read_text())
    path = RUNS / f"{room}-agent.jsonl"
    events = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    return steps, events


def p95(xs: list[float]) -> float:
    ordered = sorted(xs)
    return ordered[max(0, int(np.ceil(0.95 * len(ordered))) - 1)]


def assistant_after(events: list[dict], t: float, until: float = 1e18) -> str | None:
    return next(
        (
            e["text"]
            for e in events
            if e["kind"] == "item" and e.get("role") == "assistant" and t < e["t"] < until
        ),
        None,
    )


def main(prefixes: list[str]) -> None:
    rooms = sorted(
        p.name.removesuffix("-caller.json")
        for p in RUNS.glob("*-caller.json")
        if any(p.name.startswith(x) for x in prefixes)
    )
    lats: list[float] = []
    no_reply = 0
    for room in rooms:
        steps, events = load(room)
        after_kill = {s["i"] + 1 for s in steps if s["kind"].startswith("kill")}
        for s in steps:
            if s["i"] in after_kill or s["kind"] not in (
                "turn",
                "probe",
                "barge",
                "kill_idle",
                "kill_speaking",
            ):
                continue
            if s["latency_s"] is None:
                no_reply += 1
            elif s["latency_s"] > 0:
                lats.append(s["latency_s"])
    if lats:
        print(
            f"latency over {len(lats)} turns in {len(rooms)} calls: median "
            f"{statistics.median(lats):.2f}s, p95 {p95(lats):.2f}s, max {max(lats):.2f}s; "
            f"turns with no reply: {no_reply}"
        )

    print("\nforced reconnects:")
    for room in rooms:
        steps, events = load(room)
        for s in steps:
            if not s["kind"].startswith("kill") or "kill_at" not in s["notes"]:
                continue
            k = s["notes"]["kill_at"]
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
            nxt = next((x for x in steps if x["i"] == s["i"] + 1), None)
            reply = assistant_after(events, nxt["said_to"]) if nxt else None
            recalled = bool(reply and RECALL.search(reply)) if s["kind"] == "kill_idle" else None
            status = "DIED" if fatal or not conn else "ok"
            print(
                f"  {room:32s} {s['kind']:13s} {status:4s} back after {conn['t'] - k:.2f}s"
                if conn
                else f"  {room:32s} {s['kind']:13s} {status:4s} never back",
                end="",
            )
            lat = f"{nxt['latency_s']:.2f}s" if nxt and nxt["latency_s"] else "none"
            print(f"  next turn {lat}  recalled={recalled}  {(fatal or {}).get('error', '')[:70]}")

    print("\nbarge-ins and probes:")
    for room in rooms:
        steps, events = load(room)
        for s in steps:
            n = s["notes"]
            if s["kind"] == "barge" and "barge_from" in n:
                b = n["barge_from"]
                heard = next(
                    (
                        e["text"]
                        for e in events
                        if e["kind"] == "item" and e.get("interrupted") and b - 1 < e["t"] < b + 15
                    ),
                    None,
                )
                gen = next(
                    (
                        e["generated"]
                        for e in events
                        if e["kind"] == "model_turn_text"
                        and e.get("interrupted")
                        and b - 1 < e["t"] < b + 15
                    ),
                    None,
                )
                print(
                    f"  {room} barge cutoff={n.get('cutoff_s', float('nan')):.2f}s "
                    f"reply={n.get('reply_latency_s', float('nan')):.2f}s"
                )
                print(f"      heard:     {heard!r}\n      generated: {gen!r}")
            if s["kind"] == "probe":
                nxt_t = next((x["said_from"] for x in steps if x["i"] == s["i"] + 1), 1e18)
                print(f"      probe reply: {assistant_after(events, s['said_to'], nxt_t)!r}")


if __name__ == "__main__":
    main(sys.argv[1:])
