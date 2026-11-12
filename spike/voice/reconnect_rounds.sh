#!/usr/bin/env bash
# Repeat the one-kill reconnect script in fresh rooms, three configs side by side per round.
# Needs the worker from agent.py running (it reads each room's config from the room name).
#   ./reconnect_rounds.sh <rounds> <tag>
set -euo pipefail
cd "$(dirname "$0")"
rounds="${1:-5}"
tag="${2:-rc}"
mkdir -p runs
configs=(
  "m38-vad500-res1-fr1"   # plugin default: first retry after 0.1 s, resume with the handle
  "m38-vad500-res1-fr15"  # first retry after 1.5 s, resume with the handle
  "m38-vad500-res0-fr1"   # no handle: fresh session, LiveKit replays its chat history
)
for r in $(seq 1 "$rounds"); do
  pids=()
  for c in "${configs[@]}"; do
    SPIKE_SCRIPT=reconnect uv run python -W ignore caller.py "$tag-$c-r$r" > "runs/$tag-$c-r$r-caller.log" 2>&1 &
    pids+=("$!")
    sleep 2
  done
  # A bare `wait` returns 0 whatever the callers did. Wait for each, let the others finish, then
  # stop: a round with a failed caller isn't a like-for-like round.
  failed=0
  for i in "${!pids[@]}"; do
    if ! wait "${pids[$i]}"; then
      echo "round $r: caller for ${configs[$i]} failed, see runs/$tag-${configs[$i]}-r$r-caller.log" >&2
      failed=1
    fi
  done
  if [ "$failed" -ne 0 ]; then
    exit 1
  fi
  echo "round $r done"
done
