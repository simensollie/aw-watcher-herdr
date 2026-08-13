"""Poll loop: read herdr snapshots, emit attention heartbeats and agent runs.

Like its cmux predecessor the watcher OVER-EMITS the attention bucket: herdr
has no notion of "is my window frontmost", so the focused workspace is reported
regardless. Correctness is restored at query time by intersecting with the
window and AFK watchers (spec §7).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from . import state
from .herdr import HerdrError, HerdrUnavailable

logger = logging.getLogger(__name__)

WARN_AFTER_CONSECUTIVE_ERRORS = 10

_HINT_HERDR_ERROR = (
    "herdr is reachable but is not returning usable snapshots. Its API "
    "protocol may have changed in an update. Check `herdr api snapshot` by hand "
    "and file an issue. (consecutive failures: %s; last error: %s)"
)


def _now() -> datetime:
    """Indirected so tests can drive a deterministic clock."""
    return datetime.now(timezone.utc)


def run(source, attention_writer, fleet_writer, tracker, config,
        stop=None) -> None:
    """Poll until `stop()` returns True (or forever if not given)."""
    stop = stop or (lambda: False)
    gap_threshold = config.gap_factor * config.poll_interval
    last_poll: datetime | None = None
    consecutive = 0
    warned = False

    logger.info("aw-watcher-herdr started (poll=%ss, pulsetime=%ss, fleet=%s)",
                config.poll_interval, config.pulsetime, config.fleet_enabled)

    while not stop():
        time.sleep(config.poll_interval)
        now = _now()

        # A gap far larger than the poll interval means the machine slept.
        # Close open runs at the last good poll rather than recording hours of
        # "working" that never happened (spec §6.3).
        if (last_poll is not None and gap_threshold > 0
                and (now - last_poll).total_seconds() > gap_threshold):
            logger.info("poll gap of %.1fs (sleep/suspend); closing open runs",
                        (now - last_poll).total_seconds())
            fleet_writer.write(tracker.close_all(last_poll))
            last_poll = None

        try:
            snapshot = source.snapshot()
        except HerdrUnavailable:
            # Normal state: herdr simply isn't running. Emit nothing, warn
            # about nothing, and leave an honest gap.
            if tracker.open_count:
                fleet_writer.write(tracker.close_all(last_poll or now))
            last_poll = None
            consecutive = 0
            warned = False
            logger.debug("herdr not running; skipping tick (gap)")
            continue
        except HerdrError as exc:
            consecutive += 1
            if consecutive >= WARN_AFTER_CONSECUTIVE_ERRORS and not warned:
                logger.warning(_HINT_HERDR_ERROR, consecutive, exc)
                warned = True
            # Open runs cannot be trusted once snapshots stop arriving: an
            # agent that stops now looks like it never stopped, max_run_seconds
            # is only enforced inside tracker.update() (never reached here),
            # and close_all ignores the cap, so on shutdown hours later a
            # multi-hour `working` event would be emitted for work that lasted
            # a minute. Close at the last good poll, as the unavailable branch
            # above does.
            if tracker.open_count:
                fleet_writer.write(tracker.close_all(last_poll or now))
            last_poll = None
            continue

        consecutive = 0
        warned = False

        attention = state.extract_attention(snapshot)
        if attention is not None:
            attention_writer.write(attention, now)

        if config.fleet_enabled:
            fleet_writer.write(tracker.update(snapshot, now))

        last_poll = now
