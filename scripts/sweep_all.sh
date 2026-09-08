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
set -u
cd "$(dirname "$0")/.."
PY=./venv/bin/python

# --- 1. bill positions ------------------------------------------------------
if [ -f data/sweep.pid ] && ps -p "$(cat data/sweep.pid)" > /dev/null 2>&1; then
  echo "waiting for the running position sweep (pid $(cat data/sweep.pid))..."
  while ps -p "$(cat data/sweep.pid)" > /dev/null 2>&1; do sleep 30; done
  echo "position sweep finished at $(date)"
else
  echo "=== position sweep starting at $(date) ==="
  $PY scripts/lobby.py --all --prefixes LB LR --delay 2.0
fi

# Refresh the landing page so its coverage figure reflects what was collected,
# rather than sitting on the number it had when the sweep began.
$PY scripts/build_site.py || true

# --- 2. statewide expense totals -------------------------------------------
# Two requests per year. Cheap enough to redo every run, and it keeps the
# published series current.
echo "=== statewide expense totals at $(date) ==="
$PY scripts/expenses.py --aggregate --from-year 2015 --delay 2.5

# --- 3. per-entity expense reports -----------------------------------------
# The long one. The report aggregates whatever it is asked for, so a per-entity
# figure costs one request per entity per year. Scoped to entities that appear
# elsewhere in the project; the full picker would be 113,076 requests, near 78
# hours.
echo "=== per-entity expense sweep at $(date) ==="
$PY scripts/expenses.py --entities --from-year 2015 --delay 2.0

$PY scripts/build_site.py || true
echo "=== all collection finished at $(date) ==="
