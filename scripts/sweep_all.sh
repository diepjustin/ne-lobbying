#!/bin/bash
# Run the LB sweep to completion, then the LR sweep.
#
# Sequential on purpose. Two concurrent sweeps double the request rate against
# a server that already returns 429 at one request per two seconds, so running
# them together makes both slower AND rougher on the source. Waiting costs
# nothing: the checkpoint means neither sweep loses work.
set -u
cd "$(dirname "$0")/.."
PY=./venv/bin/python

# If an LB sweep is already running, wait for it rather than starting a second.
if [ -f data/sweep.pid ] && ps -p "$(cat data/sweep.pid)" > /dev/null 2>&1; then
  echo "waiting for running LB sweep (pid $(cat data/sweep.pid))..."
  while ps -p "$(cat data/sweep.pid)" > /dev/null 2>&1; do sleep 30; done
  echo "LB sweep finished at $(date)"
else
  echo "no LB sweep running; starting one at $(date)"
  $PY scripts/lobby.py --all --prefixes LB --delay 2.0
fi

echo "=== starting LR sweep at $(date) ==="
$PY scripts/lobby.py --all --prefixes LR --delay 2.0
echo "=== all sweeps done at $(date) ==="
