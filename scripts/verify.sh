#!/usr/bin/env bash
#
# End-to-end verification against an ISOLATED test server (aw-server --testing
# on port 5666). Never touches your real ActivityWatch data on port 5600.
#
# Requires herdr to be running, with at least one workspace open.
set -euo pipefail

PORT=5666
HOST="$(hostname)"
ATTENTION="aw-watcher-herdr_${HOST}-testing"
FLEET="aw-watcher-herdr-agents_${HOST}-testing"
# aw-client pre-merges queued heartbeats in memory until their accumulated
# duration reaches commit_interval, which is 5 s under the `client-testing`
# config this script runs with. At --poll-interval 1 the first commit therefore
# lands around the 7th tick, and only then does the dispatch thread POST it. The
# old 8 s budget left a few hundred milliseconds of margin, so a slower machine
# reported FAIL for a perfectly working watcher.
DURATION="${DURATION:-15}"
PY="${PY:-python3}"
SOURCE="${SOURCE:-auto}"

AWS="${AW_SERVER:-}"
if [ -z "$AWS" ]; then
  if [ -x "/Applications/ActivityWatch.app/Contents/MacOS/aw-server" ]; then
    AWS="/Applications/ActivityWatch.app/Contents/MacOS/aw-server"
  elif command -v aw-server >/dev/null 2>&1; then
    AWS="$(command -v aw-server)"
  else
    echo "ERROR: aw-server not found. Install ActivityWatch or set AW_SERVER=/path" >&2
    exit 1
  fi
fi
echo "aw-server : $AWS"
echo "python    : $($PY --version 2>&1)"
echo "source    : $SOURCE"
echo "buckets   : $ATTENTION, $FLEET"

STARTED_SERVER=0
SERVER_PID=""
WATCHER_PID=""
cleanup() {
  [ -n "$WATCHER_PID" ] && kill "$WATCHER_PID" 2>/dev/null || true
  # By PID, so only the server this script started is stopped. An unscoped
  # `pkill -f "aw-server --testing"` would also kill a test server the user is
  # running for something else.
  if [ "$STARTED_SERVER" = "1" ] && [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if curl -s -m 1 "http://localhost:${PORT}/api/0/info" >/dev/null 2>&1; then
  echo "test server already running on :${PORT}"
else
  echo "starting aw-server --testing on :${PORT} ..."
  "$AWS" --testing >/tmp/aw-herdr-verify-server.log 2>&1 &
  SERVER_PID=$!
  STARTED_SERVER=1
  for _ in $(seq 1 20); do
    curl -s -m 1 "http://localhost:${PORT}/api/0/info" >/dev/null 2>&1 && break
    sleep 0.5
  done
  curl -s -m 1 "http://localhost:${PORT}/api/0/info" >/dev/null 2>&1 \
    || { echo "ERROR: test server did not come up"; exit 1; }
fi

# aw-server --testing keeps its database across restarts, so events seeded by an
# earlier run would keep the gate green forever. Everything below is scoped to
# events that start at or after this instant, i.e. to THIS run only.
START="$("$PY" -c 'from datetime import datetime, timezone
print(datetime.now(timezone.utc).isoformat())')"
echo "run start : $START"

echo "running watcher for ${DURATION}s (poll=1s, source=${SOURCE}) ..."
"$PY" -m aw_watcher_herdr --testing --verbose --poll-interval 1 \
  --source "$SOURCE" >/tmp/aw-herdr-verify-watcher.log 2>&1 &
WATCHER_PID=$!
sleep "$DURATION"
kill -TERM "$WATCHER_PID" 2>/dev/null || true
wait "$WATCHER_PID" 2>/dev/null || true
WATCHER_PID=""

if grep -q "already running" /tmp/aw-herdr-verify-watcher.log; then
  echo
  echo "FAIL: another aw-watcher-herdr holds the lock. Stop it and re-run."
  exit 2
fi
if grep -q "herdr not running" /tmp/aw-herdr-verify-watcher.log; then
  echo
  echo "FAIL: herdr is not running. Start herdr and re-run."
  exit 2
fi

# Events whose own timestamp is at or after START, so leftovers from an earlier
# session on the same persistent testing database cannot satisfy the gate. The
# server's own `start` filter also keeps events that merely overlap the
# boundary, hence the second check on the timestamp itself.
new_count() {
  curl -s -G "http://localhost:${PORT}/api/0/buckets/$1/events" \
    --data-urlencode "start=${START}" --data-urlencode "limit=-1" \
    | "$PY" -c '
import json, sys
from datetime import datetime

def parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

start = parse(sys.argv[1])
try:
    events = json.load(sys.stdin)
except Exception:
    events = None
if not isinstance(events, list):   # a missing bucket answers with an object
    print(0)
    raise SystemExit(0)
print(sum(1 for e in events if parse(e["timestamp"]) >= start))
' "${START}" 2>/dev/null || echo 0
}

total_count() {
  curl -s -G "http://localhost:${PORT}/api/0/buckets/$1/events" \
    --data-urlencode "limit=-1" \
    | "$PY" -c '
import json, sys
try:
    events = json.load(sys.stdin)
except Exception:
    events = None
print(len(events) if isinstance(events, list) else 0)
' 2>/dev/null || echo 0
}

A_NEW=$(new_count "$ATTENTION")
F_NEW=$(new_count "$FLEET")
A_TOTAL=$(total_count "$ATTENTION")
F_TOTAL=$(total_count "$FLEET")

echo
echo "attention events: ${A_NEW:-0} new this run (bucket holds ${A_TOTAL:-0})"
echo "fleet events    : ${F_NEW:-0} new this run (bucket holds ${F_TOTAL:-0})"
echo "                  (0 fleet events is OK if no agent was working)"
if [ "${A_NEW:-0}" -gt 0 ]; then
  echo "PASS. Sample attention event from this run:"
  curl -s -G "http://localhost:${PORT}/api/0/buckets/${ATTENTION}/events" \
    --data-urlencode "start=${START}" --data-urlencode "limit=1" \
    | "$PY" -m json.tool
else
  echo "FAIL: this run wrote no attention events. Any ${A_TOTAL:-0} events in the"
  echo "      bucket are left over from earlier runs and do not count."
  echo "      See /tmp/aw-herdr-verify-watcher.log"
  exit 2
fi
