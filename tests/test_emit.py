"""Bucket writers (spec §5, §6).

The two buckets use deliberately different write disciplines: the attention
bucket heartbeat-merges one timeline; the fleet bucket posts completed,
overlapping intervals through insert_events(), which has no retry mode of its
own, hence the buffer.
"""
from datetime import datetime, timedelta, timezone

from aw_watcher_herdr.emit import AttentionWriter, FleetWriter
from aw_watcher_herdr.state import Attention, CompletedRun, RunKey

T0 = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, fail_inserts=False):
        self.heartbeats = []
        self.inserted = []
        self.fail_inserts = fail_inserts

    def heartbeat(self, bucket_id, event, pulsetime, queued=False):
        self.heartbeats.append((bucket_id, event, pulsetime, queued))

    def insert_events(self, bucket_id, events):
        if self.fail_inserts:
            raise ConnectionError("aw-server unreachable")
        self.inserted.append((bucket_id, list(events)))


def run(status="working", label="beta app", start=T0, seconds=60, title="task"):
    key = RunKey("w2:p1", label, status, "claude", "/home/dev/beta-app")
    return CompletedRun(key, title, start, start + timedelta(seconds=seconds))


# --- attention --------------------------------------------------------------

def test_attention_heartbeats_with_app_and_title():
    c = FakeClient()
    AttentionWriter(c, "bucket", pulsetime=5.0).write(
        Attention("gamma docs", "w3", "w3:p1", "Rewrite the guide", "claude", "idle"), T0)
    bucket, event, pulsetime, queued = c.heartbeats[0]
    assert bucket == "bucket" and pulsetime == 5.0 and queued is True
    assert event.data["app"] == "gamma docs"
    assert event.data["title"] == "Rewrite the guide"
    assert event.data["agent"] == "claude"
    assert event.data["agent_status"] == "idle"
    assert event.data["workspace_id"] == "w3"
    assert event.data["pane_id"] == "w3:p1"
    assert event.timestamp == T0


def test_attention_uses_the_generic_label_when_there_is_no_title():
    c = FakeClient()
    AttentionWriter(c, "bucket", pulsetime=5.0,
                    generic_terminal_label="terminal").write(
        Attention("alpha-service", "w1", "w1:p1", None, None, "unknown"), T0)
    assert c.heartbeats[0][1].data["title"] == "terminal"


# --- fleet ------------------------------------------------------------------

def test_fleet_inserts_events_with_explicit_durations():
    c = FakeClient()
    FleetWriter(c, "fleet").write([run(seconds=90)])
    bucket, events = c.inserted[0]
    assert bucket == "fleet"
    assert events[0].timestamp == T0
    assert events[0].duration == timedelta(seconds=90)
    assert events[0].data == {
        "app": "beta app", "title": "task", "status": "working",
        "agent": "claude", "cwd": "/home/dev/beta-app", "pane_id": "w2:p1",
    }


def test_fleet_writes_overlapping_runs_in_one_batch():
    # Concurrency is the point: these two events overlap deliberately.
    c = FakeClient()
    FleetWriter(c, "fleet").write([
        run(seconds=600),
        run(start=T0 + timedelta(seconds=120), seconds=600, label="gamma docs"),
    ])
    _, events = c.inserted[0]
    assert len(events) == 2
    assert events[1].timestamp > events[0].timestamp
    assert events[1].timestamp < events[0].timestamp + events[0].duration


def test_fleet_skips_zero_and_negative_durations():
    c = FakeClient()
    FleetWriter(c, "fleet").write([run(seconds=0)])
    assert c.inserted == []


def test_fleet_buffers_and_retries_when_the_server_is_down():
    c = FakeClient(fail_inserts=True)
    w = FleetWriter(c, "fleet")
    w.write([run(seconds=30)])
    assert w.pending_count == 1 and c.inserted == []
    # Server comes back.
    c.fail_inserts = False
    w.write([run(start=T0 + timedelta(seconds=60), seconds=30)])
    assert w.pending_count == 0
    _, events = c.inserted[0]
    assert len(events) == 2  # the buffered one plus the new one


def test_fleet_buffer_is_bounded_and_drops_oldest():
    c = FakeClient(fail_inserts=True)
    w = FleetWriter(c, "fleet", max_pending=3)
    for i in range(6):
        w.write([run(start=T0 + timedelta(seconds=i * 10), seconds=5)])
    assert w.pending_count == 3
    c.fail_inserts = False
    w.flush()
    _, events = c.inserted[0]
    # The three most recent survived.
    assert [e.timestamp for e in events] == [
        T0 + timedelta(seconds=30), T0 + timedelta(seconds=40),
        T0 + timedelta(seconds=50)]


def test_fleet_flush_on_empty_buffer_is_a_noop():
    c = FakeClient()
    FleetWriter(c, "fleet").flush()
    assert c.inserted == []
