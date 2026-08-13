"""Advisory single-instance lock (spec §10.3).

The watcher can be started by launchd or by aw-qt, and both running at once
would double every event in the fleet bucket. Because overlapping events are
correct and expected there (spec §6), nothing downstream could detect the
duplication — so it is prevented here instead.

Together with herdr.py and __main__.default_window_apps(), this is one of the
three code sites spec §4.3 permits to branch on the platform.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys

logger = logging.getLogger(__name__)


class AlreadyRunning(RuntimeError):
    """Another aw-watcher-herdr process already holds the lock."""


def _write_pid(handle) -> None:
    """Record the current pid in a handle that already holds the lock.

    Called only after the lock is confirmed held, so a losing acquisition
    never reaches this and never disturbs the winner's pid.
    """
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()


@contextlib.contextmanager
def single_instance(path: str):
    """Hold an exclusive advisory lock on `path` for the duration of the block.

    Raises AlreadyRunning if another process holds it. On Windows this is a
    no-op: only the aw-qt install route exists there, so two supervisors
    cannot both start the watcher.

    The file is opened without truncating (append mode) so that a losing
    acquisition, which never gets past the lock attempt below, cannot erase
    the winning instance's already-recorded pid.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    if sys.platform.startswith("win"):
        handle = open(path, "a+")
        try:
            _write_pid(handle)
            yield handle
        finally:
            handle.close()
        return

    import fcntl

    handle = open(path, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise AlreadyRunning(
            f"another aw-watcher-herdr is already running (lock: {path})"
        ) from exc

    try:
        _write_pid(handle)
        yield handle
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
