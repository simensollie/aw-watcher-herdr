"""Entry-point contracts: bucket names, lock path, and the shutdown flush.

Three things main() owns are binding and were previously untested, so a rename
or a dropped flush would have shipped with a green suite:

* the two bucket ids and their `-testing` suffix (global constraint),
* lock_path()'s -testing discrimination, which is what keeps a `--testing` run
  from colliding with an installed LaunchAgent (preflight ruling 3),
* the finally block that closes open runs and flushes them on SIGTERM
  (spec 6.3), which otherwise only scripts/verify.sh exercises and that needs a
  live herdr plus an aw-server.

Everything I/O-bound is replaced with a fake, so no aw-server, no herdr, no
socket and no real data directory are involved.
"""
import pytest

from aw_watcher_herdr import __main__ as cli
from aw_watcher_herdr.lock import AlreadyRunning

HOST = "test-host"


class FakeClient:
    """Stands in for ActivityWatchClient: records every bucket created."""

    instances: list["FakeClient"] = []

    def __init__(self, client_name, testing=False):
        self.client_name = client_name
        self.testing = testing
        self.client_hostname = HOST
        self.buckets: list[tuple[str, str]] = []
        FakeClient.instances.append(self)

    def create_bucket(self, bucket_id, event_type, queued=False):
        self.buckets.append((bucket_id, event_type))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeTracker:
    def __init__(self, statuses=None, max_run_seconds=None):
        self.statuses = statuses
        self.max_run_seconds = max_run_seconds
        self.closed_at = []
        self.open_count = 0

    def close_all(self, at):
        self.closed_at.append(at)
        return ["run"]

    def update(self, snapshot, now):
        return []


class FakeFleetWriter:
    def __init__(self, client, bucket_id, *a, **k):
        self.bucket_id = bucket_id
        self.written = []
        self.flushes = 0

    def write(self, runs):
        self.written.append(list(runs))

    def flush(self):
        self.flushes += 1


class FakeAttentionWriter:
    def __init__(self, client, bucket_id, pulsetime, generic_label):
        self.bucket_id = bucket_id

    def write(self, attention, now):
        pass


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Patch out every side effect main() has, and expose the fakes."""
    FakeClient.instances = []
    trackers: list[FakeTracker] = []
    fleet_writers: list[FakeFleetWriter] = []
    calls = {"run": 0}

    def make_tracker(*a, **k):
        t = FakeTracker(*a, **k)
        trackers.append(t)
        return t

    def make_fleet_writer(*a, **k):
        w = FakeFleetWriter(*a, **k)
        fleet_writers.append(w)
        return w

    def fake_run(source, attention_writer, fleet_writer, tracker, config,
                 stop=None):
        calls["run"] += 1

    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {})
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(cli, "get_data_dir", lambda name: str(tmp_path / name))
    monkeypatch.setattr(cli, "ActivityWatchClient", FakeClient)
    monkeypatch.setattr(cli, "resolve_source", lambda config: object())
    monkeypatch.setattr(cli, "FleetTracker", make_tracker)
    monkeypatch.setattr(cli, "FleetWriter", make_fleet_writer)
    monkeypatch.setattr(cli, "AttentionWriter", FakeAttentionWriter)
    monkeypatch.setattr(cli.loop, "run", fake_run)

    return {"trackers": trackers, "fleet_writers": fleet_writers,
            "calls": calls, "clients": FakeClient.instances}


# --- bucket naming ----------------------------------------------------------

def test_bucket_ids_and_types(wired):
    assert cli.main([]) == 0
    client = wired["clients"][0]
    assert client.buckets == [
        (f"aw-watcher-herdr_{HOST}", "currentwindow"),
        (f"aw-watcher-herdr-agents_{HOST}", "app.agent.activity"),
    ]


def test_testing_suffixes_both_buckets(wired):
    assert cli.main(["--testing"]) == 0
    client = wired["clients"][0]
    assert client.testing is True
    assert [b for b, _ in client.buckets] == [
        f"aw-watcher-herdr_{HOST}-testing",
        f"aw-watcher-herdr-agents_{HOST}-testing",
    ]


def test_no_fleet_creates_only_the_attention_bucket(wired):
    assert cli.main(["--no-fleet"]) == 0
    client = wired["clients"][0]
    assert [b for b, _ in client.buckets] == [f"aw-watcher-herdr_{HOST}"]


def test_writers_target_the_created_buckets(wired):
    assert cli.main([]) == 0
    assert wired["fleet_writers"][0].bucket_id == f"aw-watcher-herdr-agents_{HOST}"


# --- lock path --------------------------------------------------------------

def test_lock_path_distinguishes_testing(monkeypatch, tmp_path):
    # Without this, --testing fails with AlreadyRunning the moment the real
    # LaunchAgent is installed (preflight ruling 3).
    import os

    monkeypatch.setattr(cli, "get_data_dir", lambda name: str(tmp_path / name))
    prod = cli.lock_path(False)
    test = cli.lock_path(True)
    assert prod != test
    assert os.path.basename(prod) == "watcher.lock"
    assert os.path.basename(test) == "watcher-testing.lock"


def test_main_uses_the_testing_lock_under_testing(wired, monkeypatch):
    seen = []
    real = cli.single_instance
    monkeypatch.setattr(cli, "single_instance",
                        lambda path: seen.append(path) or real(path))
    assert cli.main(["--testing"]) == 0
    assert seen == [cli.lock_path(True)]


def test_already_running_exits_one_and_says_so(wired, monkeypatch, capsys):
    def refuse(path):
        raise AlreadyRunning(f"another aw-watcher-herdr is already running "
                             f"(lock: {path})")

    monkeypatch.setattr(cli, "single_instance", refuse)
    assert cli.main([]) == 1
    err = capsys.readouterr().err
    assert "another aw-watcher-herdr is already running" in err
    # Nothing may be created once the lock is refused.
    assert wired["clients"] == []
    assert wired["calls"]["run"] == 0


# --- shutdown ---------------------------------------------------------------

def test_shutdown_closes_open_runs_and_flushes(wired):
    assert cli.main([]) == 0
    tracker = wired["trackers"][0]
    writer = wired["fleet_writers"][0]
    assert len(tracker.closed_at) == 1
    # aw-server rejects naive datetimes, so the closing timestamp must be aware.
    assert tracker.closed_at[0].tzinfo is not None
    assert writer.written == [["run"]]
    assert writer.flushes == 1


def test_shutdown_flushes_even_when_the_loop_raises(wired, monkeypatch):
    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.loop, "run", boom)
    assert cli.main([]) == 0
    assert wired["fleet_writers"][0].flushes == 1


def test_sigterm_handler_sets_the_stop_flag(wired, monkeypatch):
    import signal

    captured = {}

    def fake_signal(signum, handler):
        captured[signum] = handler

    monkeypatch.setattr(cli.signal, "signal", fake_signal)

    def fake_run(source, attention_writer, fleet_writer, tracker, config,
                 stop=None):
        assert stop() is False
        captured[signal.SIGTERM](signal.SIGTERM, None)
        assert stop() is True

    monkeypatch.setattr(cli.loop, "run", fake_run)
    assert cli.main([]) == 0
    assert signal.SIGTERM in captured


def test_no_fleet_skips_the_shutdown_flush(wired):
    assert cli.main(["--no-fleet"]) == 0
    assert wired["trackers"][0].closed_at == []
    assert wired["fleet_writers"][0].flushes == 0
