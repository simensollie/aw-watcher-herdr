"""Single-instance lock (spec §10.3).

Two supervisors can start this watcher — launchd and aw-qt — and two copies
running would silently double every fleet event. The fleet bucket cannot detect
that, because overlapping events are expected and correct there.

flock locks belong to the open file description, so a second open() of the same
path is denied even inside one process. That makes this testable without
spawning a subprocess.
"""
import os
import sys

import pytest

from aw_watcher_herdr.lock import AlreadyRunning, single_instance


@pytest.mark.skipif(sys.platform.startswith("win"),
                    reason="the lock is a no-op on Windows")
def test_second_acquisition_is_refused(tmp_path):
    path = str(tmp_path / "watcher.lock")
    with single_instance(path):
        with pytest.raises(AlreadyRunning):
            with single_instance(path):
                pass


@pytest.mark.skipif(sys.platform.startswith("win"),
                    reason="the lock is a no-op on Windows")
def test_lock_is_released_on_exit(tmp_path):
    path = str(tmp_path / "watcher.lock")
    with single_instance(path):
        pass
    # Re-acquiring must now succeed.
    with single_instance(path):
        pass


@pytest.mark.skipif(sys.platform.startswith("win"),
                    reason="the lock is a no-op on Windows")
def test_lock_is_released_after_an_exception(tmp_path):
    path = str(tmp_path / "watcher.lock")
    with pytest.raises(ValueError):
        with single_instance(path):
            raise ValueError("boom")
    with single_instance(path):
        pass


def test_lock_records_the_pid(tmp_path):
    path = str(tmp_path / "watcher.lock")
    with single_instance(path):
        with open(path) as f:
            assert f.read().strip() == str(os.getpid())


def test_missing_parent_directory_is_created(tmp_path):
    path = str(tmp_path / "nested" / "dir" / "watcher.lock")
    with single_instance(path):
        pass


@pytest.mark.skipif(sys.platform.startswith("win"),
                    reason="the lock is a no-op on Windows")
def test_pid_survives_a_refused_second_acquisition(tmp_path):
    # open(path, "w") would truncate the file the instant a second process
    # tries and fails to acquire it, erasing the winner's pid. Guard against
    # that regression directly.
    path = str(tmp_path / "watcher.lock")
    with single_instance(path):
        with pytest.raises(AlreadyRunning):
            with single_instance(path):
                pass
        with open(path) as f:
            assert f.read().strip() == str(os.getpid())
