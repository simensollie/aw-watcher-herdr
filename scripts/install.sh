#!/usr/bin/env bash
#
# One-command install for aw-watcher-herdr as a macOS launchd LaunchAgent
# (spec §10.2, the default route on macOS).
#
# What it does:
#   1. Creates a self-contained venv at ~/.local/share/aw-watcher-herdr/venv
#      and installs aw-watcher-herdr into it (no pipx / global state needed).
#   2. Symlinks the entry point into ~/.local/bin, so the aw-qt tray route
#      (§10.1) becomes available if ActivityWatch is later upgraded to 0.14.x.
#   3. Installs + loads a LaunchAgent (auto-starts at login, survives
#      ActivityWatch updates, writes nothing into the AW app).
#
# Unlike aw-watcher-cmux this needs NO macOS Accessibility permission: herdr's
# API answers from any process owned by the user.
#
# Re-running is safe (idempotent): it reinstalls and reloads.
set -euo pipefail

if [ "$(uname)" != "Darwin" ]; then
  echo "This installer targets macOS launchd." >&2
  echo "On Linux and Windows, install with pipx and let aw-qt manage it:" >&2
  echo "    pipx install ." >&2
  echo "  then add \"aw-watcher-herdr\" to autostart_modules in aw-qt.toml." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$HOME/.local/share/aw-watcher-herdr"
VENV="$APP_DIR/venv"
BIN_DIR="$HOME/.local/bin"
LABEL="com.activitywatch.aw-watcher-herdr"
PLIST_TEMPLATE="$REPO_ROOT/packaging/$LABEL.plist"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/activitywatch"
EXEC="$VENV/bin/aw-watcher-herdr"
PYTHON="${PYTHON:-python3}"

# An x86_64 Python on an arm64 Mac runs the watcher under Rosetta for no
# reason. Set ALLOW_ARCH_MISMATCH=1 if that is deliberate.
HOST_ARCH="$(uname -m)"
PY_ARCH="$("$PYTHON" -c 'import platform; print(platform.machine())')"
if [ "$HOST_ARCH" != "$PY_ARCH" ] && [ "${ALLOW_ARCH_MISMATCH:-0}" != "1" ]; then
  echo "ERROR: $PYTHON is $PY_ARCH but this Mac is $HOST_ARCH." >&2
  echo "Install a native Python (e.g. brew install python) or re-run with" >&2
  echo "    PYTHON=/opt/homebrew/bin/python3 ./scripts/install.sh" >&2
  echo "To proceed anyway: ALLOW_ARCH_MISMATCH=1 ./scripts/install.sh" >&2
  exit 1
fi

AW_QT_TOML="$HOME/Library/Application Support/activitywatch/aw-qt/aw-qt.toml"
if [ -f "$AW_QT_TOML" ] && grep -q '^[^#]*aw-watcher-herdr' "$AW_QT_TOML"; then
  echo "WARNING: aw-watcher-herdr appears in autostart_modules in" >&2
  echo "  $AW_QT_TOML" >&2
  echo "  Two supervisors would both start it. The watcher's single-instance" >&2
  echo "  lock stops the duplicate, but pick one route to avoid noisy logs." >&2
fi

echo "==> Creating venv at $VENV ($PY_ARCH)"
mkdir -p "$APP_DIR" "$LOG_DIR" "$BIN_DIR" "$HOME/Library/LaunchAgents"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
echo "==> Installing aw-watcher-herdr from $REPO_ROOT"
"$VENV/bin/pip" install --quiet "$REPO_ROOT"

echo "==> Verifying the install"
"$EXEC" --version

echo "==> Linking $BIN_DIR/aw-watcher-herdr"
ln -sf "$EXEC" "$BIN_DIR/aw-watcher-herdr"

echo "==> Writing LaunchAgent $PLIST"
# The plist template in packaging/ is the single source of truth (its
# hand-editable copy documents the manual install steps); this substitutes
# the real paths into a copy rather than duplicating the XML in a heredoc.
# The template's explanatory comment is stripped from the generated copy
# since it describes a manual install that no longer applies once installed.
sed -e '/<!--/,/-->/d' -e "s#/Users/CHANGE_ME#$HOME#g" "$PLIST_TEMPLATE" > "$PLIST"
plutil -lint "$PLIST"

echo "==> Loading the LaunchAgent"
# Delegated so the unload race is retried and the result verified; a bare
# bootout-then-bootstrap pair silently left the agent unloaded.
"$REPO_ROOT/scripts/reload-agent.sh" "$LABEL" "$PLIST"

cat <<EOF

Installed and started. No permission prompts needed.

Check it is reading herdr:

    $EXEC --snapshot | head -3

Find your terminal's app name for the query recipes:

    $EXEC --detect-terminal

Logs: $LOG_DIR/aw-watcher-herdr.log
Restart: launchctl kickstart -k gui/$(id -u)/$LABEL
Uninstall: scripts/uninstall.sh
EOF
