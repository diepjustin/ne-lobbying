#!/bin/bash
# Collect everything this project can, in order: bill positions first, then the
# expense reports.
#
# Strictly sequential. The Legislature's server returns 429 at one request per
# two seconds already; two scrapers at once makes both slower and is rougher on
# a small government host for no gain. Waiting costs nothing, because every
# stage checkpoints and resumes.
#
# Order matters for more than politeness. `expenses.py --entities` scopes itself
# to the lobbyist and principal IDs found in data/bill_positions.csv, so running
# it after the position sweep means it covers every entity the full sweep
# discovered rather than only those known when it started.
#
# Each scraper reports how it ended through its exit status, and the chain
# acts on it rather than on whether a process is still alive -- the previous
# version treated a vanished pid as "finished", so a sweep that died on a
# timeout after one legislature looked complete and six were never started.
#   0    the requested set finished
#   2    stopped cleanly on a 429 or a dropped connection; checkpoint saved
#   130  interrupted by hand -- the chain stops too
#
# To stop the chain, kill the running scraper (not this script): it saves its
# checkpoint, exits 130, and the chain ends.
set -u
cd "$(dirname "$0")/.."
PY=./venv/bin/python

MAX_ATTEMPTS=12
RETRY_WAIT=300   # seconds between resumes after a clean stop

# Rerun a scraper until it exits 0. A 2 is the server or the network setting
# a boundary: wait, then resume from the checkpoint. Anything else is either
# a deliberate interrupt or a bug, and neither is a reason to keep asking.
run_until_complete() {
  local attempt status
  for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    "$@"
    status=$?
    case $status in
      0) return 0 ;;
      2) echo "... stopped before completion (attempt $attempt of $MAX_ATTEMPTS) at $(date); resuming in $((RETRY_WAIT / 60)) min" ;;
      *) echo "!!! exit status $status at $(date); not retrying" >&2; return "$status" ;;
    esac
    sleep "$RETRY_WAIT"
  done
  echo "!!! still incomplete after $MAX_ATTEMPTS attempts at $(date)" >&2
  return 2
}

# Stop the whole chain on an interrupt; carry on past anything else so the
# later stages still run against whatever the earlier ones managed. But a
# non-zero, non-interrupt exit means a stage did NOT finish -- record it so
# the closing banner cannot claim a clean sweep when one stage actually
# crashed (a crashed position sweep once fed silently into the expense
# stages, and the final "all collection finished" line was taken at face
# value as proof every legislature had been swept).
FAILURES=()
stop_if_interrupted() {
  if [ "$1" -eq 130 ]; then
    echo "=== chain interrupted at $(date) ==="
    exit 130
  fi
  if [ "$1" -ne 0 ]; then
    echo "!!! stage exited $1 -- continuing with whatever data exists, but this run is INCOMPLETE" >&2
    FAILURES+=("$1")
  fi
}

# --- 1. bill positions ------------------------------------------------------
echo "=== position sweep starting at $(date) ==="
run_until_complete $PY scripts/lobby.py --all --prefixes LB LR --delay 2.0
stop_if_interrupted $?

# Refresh the landing page so its coverage figure reflects what was collected,
# rather than sitting on the number it had when the sweep began.
$PY scripts/check_data.py && $PY scripts/build_site.py || true

# --- 2. statewide expense totals -------------------------------------------
# Two requests per year. Cheap enough to redo every run, and it keeps the
# published series current. The file is rewritten, not appended, so rerunning
# never doubles it.
echo "=== statewide expense totals at $(date) ==="
run_until_complete $PY scripts/expenses.py --aggregate --from-year 2015 --delay 2.5
stop_if_interrupted $?

# --- 3. per-entity expense reports -----------------------------------------
# The long one. The report aggregates whatever it is asked for, so a per-entity
# figure costs one request per entity per year. Scoped to entities that appear
# elsewhere in the project; the full picker would be 113,076 requests, near 78
# hours.
echo "=== per-entity expense sweep at $(date) ==="
run_until_complete $PY scripts/expenses.py --entities --from-year 2015 --delay 2.0
stop_if_interrupted $?

$PY scripts/check_data.py && $PY scripts/build_site.py || true
if [ ${#FAILURES[@]} -eq 0 ]; then
  echo "=== all collection finished cleanly at $(date) ==="
else
  echo "=== collection finished at $(date) WITH FAILURES (exit codes: ${FAILURES[*]}) -- data is INCOMPLETE, do not describe this run as complete ==="
fi
