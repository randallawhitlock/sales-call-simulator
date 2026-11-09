#!/usr/bin/env bash
# Repeat the one-kill reconnect script in fresh rooms, three configs side by side per round.
# Needs the worker from agent.py running (it reads each room's config from the room name).
#   ./reconnect_rounds.sh <rounds> <tag>
set -euo pipefail
cd "$(dirname "$0")"
rounds="${1:-5}"
tag="${2:-rc}"
configs=(
  "m38-vad500-res1-fr1"   # plugin default: first retry after 0.1 s, resume with the handle
  "m38-vad500-res1-fr15"  # first retry after 1.5 s, resume with the handle
  "m38-vad500-res0-fr1"   # no handle: fresh session, LiveKit replays its chat history
)
for r in $(seq 1 "$rounds"); do
  for c in "${configs[@]}"; do
    SPIKE_SCRIPT=reconnect uv run python -W ignore caller.py "$tag-$c-r$r" > "runs/$tag-$c-r$r-caller.log" 2>&1 &
    sleep 2
  done
  wait
  echo "round $r done"
done
