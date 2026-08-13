#!/usr/bin/env bash
#
# Remove the aw-watcher-herdr LaunchAgent, its venv, and its ~/.local/bin link.
# Recorded ActivityWatch data is NOT touched.
set -euo pipefail

LABEL="com.activitywatch.aw-watcher-herdr"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP_DIR="$HOME/.local/share/aw-watcher-herdr"
LINK="$HOME/.local/bin/aw-watcher-herdr"

echo "==> Stopping the LaunchAgent"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

echo "==> Removing $PLIST"
rm -f "$PLIST"

# Only remove the link if it points into the venv we installed.
if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$APP_DIR/venv/bin/aw-watcher-herdr" ]; then
  echo "==> Removing $LINK"
  rm -f "$LINK"
fi

echo "==> Removing $APP_DIR"
rm -rf "$APP_DIR"

cat <<EOF

Uninstalled. Your recorded data is untouched; the buckets
aw-watcher-herdr_<host> and aw-watcher-herdr-agents_<host> remain in
ActivityWatch. Delete them from the aw web UI if you want them gone.

If you also listed aw-watcher-herdr in aw-qt's autostart_modules, remove it
from aw-qt.toml.
EOF
