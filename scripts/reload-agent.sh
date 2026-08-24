#!/usr/bin/env bash
#
# Reload a LaunchAgent, tolerating the unload race and verifying the result.
#
#   reload-agent.sh <label> <plist-path>
#
# `launchctl bootout` returns before the job is fully gone, so a bootstrap
# issued straight after it can fail with "Bootstrap failed: 5: Input/output
# error". Run back to back with no retry, that leaves the agent UNLOADED while
# the installer goes on to print "Installed and started": the watcher records
# nothing and nothing says so, the same silent-failure shape that emit.py's
# flush escalation exists to prevent.
#
# A bootstrap exiting 0 is not proof either, so the job is asserted present
# afterwards. Exiting non-zero here aborts the caller under `set -e` before it
# can claim success.
#
# LAUNCHCTL, ATTEMPTS and RETRY_SLEEP are overridable so the retry and the
# post-check can be tested against a stub (tests/test_scripts.py).
set -euo pipefail

LABEL="${1:?usage: reload-agent.sh <label> <plist-path>}"
PLIST="${2:?usage: reload-agent.sh <label> <plist-path>}"

DOMAIN="gui/$(id -u)"
LAUNCHCTL="${LAUNCHCTL:-launchctl}"
ATTEMPTS="${ATTEMPTS:-10}"
RETRY_SLEEP="${RETRY_SLEEP:-1}"

# Absent is the desired state, so a failure here is not interesting.
"$LAUNCHCTL" bootout "$DOMAIN/$LABEL" 2>/dev/null || true

error=""
loaded=0
for attempt in $(seq 1 "$ATTEMPTS"); do
  if error="$("$LAUNCHCTL" bootstrap "$DOMAIN" "$PLIST" 2>&1)"; then
    loaded=1
    break
  fi
  sleep "$RETRY_SLEEP"
done

if [ "$loaded" -ne 1 ]; then
  echo "ERROR: launchctl bootstrap of $LABEL failed $ATTEMPTS times." >&2
  echo "  last error: $error" >&2
  exit 1
fi

if ! "$LAUNCHCTL" print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  echo "ERROR: $LABEL is not loaded after a bootstrap that reported success." >&2
  echo "  Check the plist paths and $HOME/Library/Logs/activitywatch/." >&2
  exit 1
fi

echo "==> $LABEL loaded"
