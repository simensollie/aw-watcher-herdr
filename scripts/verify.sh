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
DURATION="${DURATION:-8}"
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
WATCHER_PID=""
cleanup() {
  [ -n "$WATCHER_PID" ] && kill "$WATCHER_PID" 2>/dev/null || true
  if [ "$STARTED_SERVER" = "1" ]; then
    pkill -f "aw-server --testing" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if curl -s -m 1 "http://localhost:${PORT}/api/0/info" >/dev/null 2>&1; then
  echo "test server already running on :${PORT}"
else
  echo "starting aw-server --testing on :${PORT} ..."
  "$AWS" --testing >/tmp/aw-herdr-verify-server.log 2>&1 &
  STARTED_SERVER=1
  for _ in $(seq 1 20); do
    curl -s -m 1 "http://localhost:${PORT}/api/0/info" >/dev/null 2>&1 && break
    sleep 0.5
  done
  curl -s -m 1 "http://localhost:${PORT}/api/0/info" >/dev/null 2>&1 \
    || { echo "ERROR: test server did not come up"; exit 1; }
fi

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

count() {
  curl -s "http://localhost:${PORT}/api/0/buckets/$1/events?limit=100" \
    | "$PY" -c 'import sys,json; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0
}

A_COUNT=$(count "$ATTENTION")
F_COUNT=$(count "$FLEET")

echo
echo "attention events: ${A_COUNT:-0}"
echo "fleet events    : ${F_COUNT:-0}  (0 is OK if no agent was working)"
if [ "${A_COUNT:-0}" -gt 0 ]; then
  echo "PASS. Sample attention event:"
  curl -s "http://localhost:${PORT}/api/0/buckets/${ATTENTION}/events?limit=1" \
    | "$PY" -m json.tool
else
  echo "FAIL: no attention events. See /tmp/aw-herdr-verify-watcher.log"
  exit 2
fi
