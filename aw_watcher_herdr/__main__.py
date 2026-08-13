"""Entry point: parse args, load config, set up buckets, run the loop."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket as socketlib
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aw_client import ActivityWatchClient
from aw_core.config import load_config_toml
from aw_core.dirs import get_data_dir
from aw_core.log import setup_logging

from . import __version__
from . import main as loop
from .emit import AttentionWriter, FleetWriter
from .herdr import DEFAULT_HERDR_BINARY, HerdrUnavailable, resolve_source
from .lock import AlreadyRunning, single_instance
from .state import DEFAULT_FLEET_STATUSES, FleetTracker

logger = logging.getLogger(__name__)

CLIENT_NAME = "aw-watcher-herdr"

DEFAULT_GENERIC_LABEL = "terminal"

# Default config rendered into the user's toml on first run (aw-core convention).
# Every live key here is merged over the dataclass defaults by load_config_toml,
# so a key whose default is platform-specific (window_app) must stay commented
# out or it would force one platform's value onto all of them.
DEFAULT_CONFIG = f"""
[{CLIENT_NAME}]
source = "auto"                # auto | socket | cli
herdr_binary = "{DEFAULT_HERDR_BINARY}"
poll_interval = 2.0
pulsetime = 5.0
generic_terminal_label = "{DEFAULT_GENERIC_LABEL}"
fleet_enabled = true
fleet_statuses = {json.dumps(list(DEFAULT_FLEET_STATUSES))}
max_run_seconds = 43200.0
gap_factor = 3.0
# Terminal app names for the gating query. Run --detect-terminal to find yours.
# window_app = ["Ghostty"]
# window_title = "herdr"
# socket_path = "~/.config/herdr/herdr.sock"   # defaults to that path
""".strip()

_FILE_KEYS = (
    "source", "socket_path", "herdr_binary", "poll_interval", "pulsetime",
    "generic_terminal_label", "fleet_enabled", "fleet_statuses",
    "max_run_seconds", "gap_factor", "window_app", "window_title",
)

SOURCE_CHOICES = ("auto", "socket", "cli")


def default_window_apps() -> list[str]:
    """Terminal app names aw-watcher-window is likely to report.

    Only macOS gets a default. On Linux the value is the WM_CLASS and on
    Windows the executable name, neither of which is worth guessing when
    --detect-terminal can read the real value from the user's own data
    (spec §7.1). An empty list drops the filter rather than matching nothing.

    This is one of the three code sites spec §4.3 permits to read
    sys.platform (the others are herdr.py and lock.py). Spec §7.1 defines
    the default itself as per-platform, and the lookup here is static: it
    touches no platform-specific API and holds no resource, so it carries
    none of the portability risk the rule exists to contain.
    """
    return ["Ghostty"] if sys.platform == "darwin" else []


@dataclass
class Config:
    source: str = "auto"
    socket_path: str | None = None
    herdr_binary: str = DEFAULT_HERDR_BINARY
    poll_interval: float = 2.0
    pulsetime: float = 5.0
    generic_terminal_label: str = DEFAULT_GENERIC_LABEL
    fleet_enabled: bool = True
    fleet_statuses: list[str] = field(
        default_factory=lambda: list(DEFAULT_FLEET_STATUSES))
    max_run_seconds: float = 43200.0
    gap_factor: float = 3.0
    window_app: list[str] = field(default_factory=default_window_apps)
    window_title: str | None = None


def load_config(args: argparse.Namespace) -> Config:
    """Load config from the aw-core toml, then apply CLI overrides (flags win)."""
    cfg = Config()
    try:
        parsed = load_config_toml(CLIENT_NAME, DEFAULT_CONFIG)
        section = parsed.get(CLIENT_NAME, parsed)
        for key in _FILE_KEYS:
            if key in section:
                setattr(cfg, key, section[key])
    except Exception as exc:  # noqa: BLE001 - config is best-effort, defaults are fine
        logger.warning("could not load config file, using defaults: %s", exc)

    # window_app was a single string before the 2026-08-13 amendment. Accept
    # that shape so an old config file degrades gracefully instead of crashing.
    if isinstance(cfg.window_app, str):
        cfg.window_app = [cfg.window_app]

    # Flags override the file. Use `is not None` so an explicit 0 is honored and
    # not silently dropped by a truthiness check.
    if args.source is not None:
        cfg.source = args.source
    if args.socket_path is not None:
        cfg.socket_path = args.socket_path
    if args.herdr_binary is not None:
        cfg.herdr_binary = args.herdr_binary
    if args.poll_interval is not None:
        cfg.poll_interval = args.poll_interval
    if args.pulsetime is not None:
        cfg.pulsetime = args.pulsetime
    if args.generic_terminal_label is not None:
        cfg.generic_terminal_label = args.generic_terminal_label
    if args.fleet_enabled is not None:
        cfg.fleet_enabled = args.fleet_enabled
    return cfg


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog=CLIENT_NAME, description=__doc__)
    p.add_argument("--testing", action="store_true",
                   help="use the aw test server (port 5666) and -testing buckets")
    p.add_argument("--verbose", action="store_true", help="debug logging")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--source", choices=SOURCE_CHOICES,
                   help="how to reach herdr (default auto: socket on POSIX, "
                        "cli on Windows)")
    p.add_argument("--socket-path", dest="socket_path",
                   help="override the herdr socket path (socket source)")
    p.add_argument("--herdr-binary", dest="herdr_binary",
                   help="herdr executable to invoke (cli source)")
    p.add_argument("--poll-interval", dest="poll_interval", type=float,
                   help="seconds between snapshots")
    p.add_argument("--pulsetime", dest="pulsetime", type=float,
                   help="heartbeat merge window in seconds (attention bucket)")
    p.add_argument("--generic-terminal-label", dest="generic_terminal_label",
                   help="title stored for panes with no agent")
    # store_const keeps the unset default at None so it doesn't override the file.
    p.add_argument("--no-fleet", dest="fleet_enabled",
                   action="store_const", const=False, default=None,
                   help="do not emit the agent-fleet bucket")
    p.add_argument("--snapshot", action="store_true",
                   help="print herdr's live session snapshot as JSON and exit "
                        "(for capturing test fixtures)")
    return p.parse_args(argv)


def lock_path(testing: bool = False) -> str:
    """Where the single-instance lock lives (spec §10.3).

    A --testing run uses a distinct file so it never contends with an
    installed production watcher: without this, `--testing` would fail with
    AlreadyRunning the moment the real LaunchAgent is installed. The two
    instances also target different aw-server ports (5666 vs the default),
    so they cannot double-count even while both hold their own lock.
    """
    name = "watcher-testing.lock" if testing else "watcher.lock"
    return os.path.join(get_data_dir(CLIENT_NAME), name)


def run_snapshot(config: Config) -> int:
    """Print herdr's live snapshot as JSON (for capturing test fixtures).

    Remember to replace real workspace names with synthetic ones before
    committing anything derived from this.
    """
    try:
        snap = resolve_source(config).snapshot()
    except HerdrUnavailable as exc:
        print(f"herdr is not running: {exc}")
        return 1
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args)

    # One-shot diagnostic mode prints to stdout and exits; handle it before
    # setup_logging so it doesn't spin up a rotating log file.
    if args.snapshot:
        return run_snapshot(config)

    setup_logging(CLIENT_NAME, testing=args.testing, verbose=args.verbose,
                  log_stderr=True, log_file=True)

    try:
        lock = single_instance(lock_path(args.testing))
        lock.__enter__()
    except AlreadyRunning as exc:
        # Both launchd and aw-qt can start this watcher; two copies would
        # silently double the fleet bucket (spec §10.3).
        logger.error("%s", exc)
        print(f"aw-watcher-herdr: {exc}", file=sys.stderr)
        return 1

    try:
        client = ActivityWatchClient(CLIENT_NAME, testing=args.testing)
        hostname = client.client_hostname or socketlib.gethostname()
        suffix = "-testing" if args.testing else ""
        attention_bucket = f"{CLIENT_NAME}_{hostname}{suffix}"
        fleet_bucket = f"{CLIENT_NAME}-agents_{hostname}{suffix}"

        # currentwindow reuses aw's window-activity views and categorization (§5).
        client.create_bucket(attention_bucket, event_type="currentwindow",
                             queued=True)
        if config.fleet_enabled:
            client.create_bucket(fleet_bucket, event_type="app.agent.activity",
                                 queued=True)

        source = resolve_source(config)
        logger.info("reading herdr via %s", type(source).__name__)
        tracker = FleetTracker(statuses=config.fleet_statuses,
                               max_run_seconds=config.max_run_seconds)
        attention_writer = AttentionWriter(client, attention_bucket,
                                           config.pulsetime,
                                           config.generic_terminal_label)
        fleet_writer = FleetWriter(client, fleet_bucket)

        # SIGTERM (launchd or aw-qt stop) must close open runs, or their
        # intervals are lost.
        stopping = {"flag": False}

        def _stop(_signum, _frame):
            stopping["flag"] = True

        signal.signal(signal.SIGTERM, _stop)

        with client:
            try:
                loop.run(source, attention_writer, fleet_writer, tracker,
                         config, stop=lambda: stopping["flag"])
            except KeyboardInterrupt:
                logger.info("interrupted; shutting down")
            finally:
                if config.fleet_enabled:
                    fleet_writer.write(
                        tracker.close_all(datetime.now(timezone.utc)))
                    fleet_writer.flush()
    finally:
        lock.__exit__(None, None, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
