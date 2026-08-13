"""Poll-loop behaviour: gap handling, error escalation, and shutdown
(spec §6.3). The loop is driven with a fake snapshot source and fake writers,
so no socket and no aw-server are involved."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from aw_watcher_herdr import main as loop
from aw_watcher_herdr.herdr import HerdrError, HerdrUnavailable
from aw_watcher_herdr.state import FleetTracker

T0 = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)


@dataclass
class FakeConfig:
    poll_interval: float = 2.0
    pulsetime: float = 5.0
    generic_terminal_label: str = "terminal"
    fleet_enabled: bool = True
    fleet_statuses: list = field(
        default_factory=lambda: ["working", "blocked", "done"])
    max_run_seconds: float = 43200.0
    gap_factor: float = 3.0


class FakeSource:
    """Yields a scripted sequence; entries may be dicts or exceptions."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def snapshot(self):
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


class FakeAttentionWriter:
    def __init__(self):
        self.writes = []

    def write(self, attention, now):
        self.writes.append((attention, now))


class FakeFleetWriter:
    def __init__(self):
        self.runs = []
        self.flushes = 0

    def write(self, runs):
        self.runs.extend(runs)

    def flush(self):
        self.flushes += 1


def snap(status="working", ws_label="beta app"):
    return {
        "focused_workspace_id": "w2",
        "focused_pane_id": "w2:p1",
        "workspaces": [{"workspace_id": "w2", "label": ws_label}],
        "panes": [{"pane_id": "w2:p1", "agent": "claude",
                   "agent_status": status,
                   "terminal_title_stripped": "task"}],
        "agents": [{"pane_id": "w2:p1", "workspace_id": "w2", "agent": "claude",
                    "agent_status": status, "cwd": "/home/dev/beta-app",
                    "terminal_title_stripped": "task"}],
    }


def drive(monkeypatch, script, ticks, clock_step=2.0, config=None):
    """Run the loop for `ticks` iterations with a deterministic clock."""
    config = config or FakeConfig()
    times = [T0 + timedelta(seconds=clock_step * i) for i in range(ticks + 2)]
    it = iter(times)
    monkeypatch.setattr(loop.time, "sleep", lambda _s: None)
    monkeypatch.setattr(loop, "_now", lambda: next(it))

    source = FakeSource(script)
    aw = FakeAttentionWriter()
    fleet = FakeFleetWriter()
    tracker = FleetTracker(statuses=config.fleet_statuses,
                           max_run_seconds=config.max_run_seconds)
    state = {"n": 0}

    def stop():
        state["n"] += 1
        return state["n"] > ticks

    loop.run(source, aw, fleet, tracker, config, stop=stop)
    return source, aw, fleet, tracker


def test_heartbeats_attention_each_tick(monkeypatch):
    _, aw, _, _ = drive(monkeypatch, [snap()], ticks=3)
    assert len(aw.writes) == 3
    assert aw.writes[0][0].workspace_label == "beta app"


def test_opens_a_fleet_run_and_emits_on_close(monkeypatch):
    _, _, fleet, tracker = drive(
        monkeypatch, [snap("working"), snap("working"), snap("idle")], ticks=3)
    assert len(fleet.runs) == 1
    assert fleet.runs[0].key.status == "working"
    assert tracker.open_count == 0


def test_fleet_disabled_emits_nothing(monkeypatch):
    cfg = FakeConfig(fleet_enabled=False)
    _, aw, fleet, tracker = drive(monkeypatch, [snap()], ticks=3, config=cfg)
    assert fleet.runs == [] and tracker.open_count == 0
    assert len(aw.writes) == 3


def test_herdr_unavailable_closes_runs_and_emits_nothing_new(monkeypatch):
    script = [snap("working"), HerdrUnavailable("herdr gone"),
              HerdrUnavailable("herdr gone")]
    _, aw, fleet, tracker = drive(monkeypatch, script, ticks=3)
    # One tick of attention (the first), then gaps.
    assert len(aw.writes) == 1
    # The open run was closed when herdr went away.
    assert len(fleet.runs) == 1
    assert tracker.open_count == 0


def test_herdr_error_warns_once_after_the_threshold(monkeypatch):
    warnings = []
    monkeypatch.setattr(loop.logger, "warning",
                        lambda msg, *a: warnings.append(msg % a if a else msg))
    n = loop.WARN_AFTER_CONSECUTIVE_ERRORS + 5
    drive(monkeypatch, [HerdrError("protocol boom")], ticks=n)
    assert len(warnings) == 1


def test_herdr_unavailable_does_not_warn(monkeypatch):
    warnings = []
    monkeypatch.setattr(loop.logger, "warning",
                        lambda msg, *a: warnings.append(msg % a if a else msg))
    drive(monkeypatch, [HerdrUnavailable("not running")],
          ticks=loop.WARN_AFTER_CONSECUTIVE_ERRORS + 5)
    assert warnings == []


def test_sleep_gap_closes_open_runs_at_the_last_good_poll(monkeypatch):
    # Two good polls at the normal 2s step, then a 60s jump that looks like a
    # sleep/suspend. Every tick after the first would look like a gap at a
    # 60s clock step (config.gap_factor * poll_interval == 6s), which is why
    # the naive version of this test (uniform clock_step=60.0) could not
    # distinguish "close at the last good poll" from the bug "close at now":
    # both satisfy duration_seconds <= 60 on every tick. Driving two normal
    # ticks first and asserting an exact duration does distinguish them.
    times = iter([T0, T0 + timedelta(seconds=2), T0 + timedelta(seconds=62)])
    monkeypatch.setattr(loop.time, "sleep", lambda _s: None)
    monkeypatch.setattr(loop, "_now", lambda: next(times))

    config = FakeConfig()
    source = FakeSource([snap("working")])
    aw = FakeAttentionWriter()
    fleet = FakeFleetWriter()
    tracker = FleetTracker(statuses=config.fleet_statuses,
                           max_run_seconds=config.max_run_seconds)
    state = {"n": 0}

    def stop():
        state["n"] += 1
        return state["n"] > 3

    loop.run(source, aw, fleet, tracker, config, stop=stop)

    assert len(fleet.runs) == 1
    run = fleet.runs[0]
    assert run.duration_seconds == 2.0
    assert run.end == T0 + timedelta(seconds=2)
