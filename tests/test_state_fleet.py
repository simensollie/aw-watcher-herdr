"""Agent-run lifecycle rules (spec §6.1, §6.3).

A "run" is one continuous interval of one pane holding one status. These tests
pin the rules that decide when a run opens, survives, or closes.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aw_watcher_herdr.state import CompletedRun, FleetTracker, RunKey

FIX = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)


def load(name="snapshot_basic.json"):
    return json.loads((FIX / name).read_text())


def snap(*agents, workspaces=None):
    """Build a minimal snapshot from (pane_id, ws_id, status, title) tuples."""
    workspaces = workspaces or {"w1": "alpha-service", "w2": "beta app",
                                "w3": "gamma docs"}
    return {
        "workspaces": [{"workspace_id": k, "label": v} for k, v in workspaces.items()],
        "agents": [
            {"pane_id": p, "workspace_id": w, "agent": "claude",
             "agent_status": s, "cwd": f"/home/dev/{w}",
             "terminal_title_stripped": t}
            for (p, w, s, t) in agents
        ],
    }


def at(seconds):
    return T0 + timedelta(seconds=seconds)


# --- opening and closing ----------------------------------------------------

def test_working_agent_opens_a_run_and_emits_nothing_yet():
    t = FleetTracker()
    closed = t.update(snap(("w2:p1", "w2", "working", "task one")), T0)
    assert closed == []
    assert t.open_count == 1


def test_status_change_closes_the_run_and_opens_a_new_one():
    t = FleetTracker()
    t.update(snap(("w2:p1", "w2", "working", "task one")), T0)
    closed = t.update(snap(("w2:p1", "w2", "blocked", "task one")), at(60))
    assert len(closed) == 1
    assert closed[0].key.status == "working"
    assert closed[0].duration_seconds == 60
    assert t.open_count == 1


def test_pane_disappearing_closes_the_run():
    t = FleetTracker()
    t.update(snap(("w2:p1", "w2", "working", "task one")), T0)
    closed = t.update(snap(), at(30))
    assert len(closed) == 1 and closed[0].duration_seconds == 30
    assert t.open_count == 0


def test_idle_and_unknown_open_nothing():
    t = FleetTracker()
    closed = t.update(snap(("w1:p1", "w1", "unknown", ""),
                           ("w3:p1", "w3", "idle", "resting")), T0)
    assert closed == [] and t.open_count == 0


def test_done_is_tracked_as_a_real_run():
    # `done` measures how long finished work sat unnoticed (spec §6.2).
    t = FleetTracker()
    t.update(snap(("w2:p1", "w2", "done", "finished task")), T0)
    closed = t.update(snap(("w2:p1", "w2", "idle", "finished task")), at(3600))
    assert len(closed) == 1
    assert closed[0].key.status == "done"
    assert closed[0].duration_seconds == 3600


# --- identity rules ---------------------------------------------------------

def test_title_change_does_not_segment_a_run():
    # Agents rewrite the terminal title constantly; segmenting there would
    # shred every run into fragments (spec §6.1).
    t = FleetTracker()
    t.update(snap(("w2:p1", "w2", "working", "first title")), T0)
    closed = t.update(snap(("w2:p1", "w2", "working", "second title")), at(30))
    assert closed == []
    assert t.open_count == 1


def test_last_title_wins_when_the_run_closes():
    t = FleetTracker()
    t.update(snap(("w2:p1", "w2", "working", "first title")), T0)
    t.update(snap(("w2:p1", "w2", "working", "second title")), at(30))
    closed = t.update(snap(), at(60))
    assert closed[0].title == "second title"


def test_run_titles_are_glyph_stripped():
    # The same cleaning as the attention bucket, so one task reads identically
    # in both buckets (spec §5.1).
    t = FleetTracker()
    t.update(snap(("w2:p1", "w2", "working", "◐ Add retry logic")), T0)
    closed = t.update(snap(), at(30))
    assert closed[0].title == "Add retry logic"


def test_workspace_rename_closes_and_reopens_the_run():
    # `app` is the workspace label and must not change mid-interval (spec §6.1).
    t = FleetTracker()
    t.update(snap(("w2:p1", "w2", "working", "task")), T0)
    closed = t.update(
        snap(("w2:p1", "w2", "working", "task"),
             workspaces={"w2": "beta app renamed"}), at(45))
    assert len(closed) == 1
    assert closed[0].key.workspace_label == "beta app"
    assert t.open_count == 1


def test_pane_moved_to_another_workspace_closes_and_reopens():
    t = FleetTracker()
    t.update(snap(("w2:p1", "w2", "working", "task")), T0)
    closed = t.update(snap(("w2:p1", "w3", "working", "task")), at(45))
    assert len(closed) == 1 and closed[0].key.workspace_label == "beta app"
    assert t.open_count == 1


# --- concurrency ------------------------------------------------------------

def test_four_concurrent_panes_track_independently():
    t = FleetTracker()
    t.update(snap(("w1:p1", "w1", "working", "a"),
                  ("w2:p1", "w2", "working", "b"),
                  ("w3:p1", "w3", "blocked", "c"),
                  ("w3:p2", "w3", "done", "d")), T0)
    assert t.open_count == 4
    # Close only one of them.
    closed = t.update(snap(("w1:p1", "w1", "working", "a"),
                           ("w3:p1", "w3", "blocked", "c"),
                           ("w3:p2", "w3", "done", "d")), at(120))
    assert len(closed) == 1 and closed[0].key.pane_id == "w2:p1"
    assert t.open_count == 3


def test_close_all_closes_every_open_run_at_the_given_time():
    t = FleetTracker()
    t.update(snap(("w1:p1", "w1", "working", "a"),
                  ("w2:p1", "w2", "working", "b")), T0)
    closed = t.close_all(at(90))
    assert len(closed) == 2
    assert all(c.duration_seconds == 90 for c in closed)
    assert t.open_count == 0


# --- caps -------------------------------------------------------------------

def test_run_longer_than_the_cap_is_closed_and_reopened():
    t = FleetTracker(max_run_seconds=100.0)
    t.update(snap(("w2:p1", "w2", "working", "long task")), T0)
    closed = t.update(snap(("w2:p1", "w2", "working", "long task")), at(150))
    assert len(closed) == 1 and closed[0].duration_seconds == 150
    assert t.open_count == 1  # reopened, so tracking continues


def test_custom_status_set_is_respected():
    t = FleetTracker(statuses=("blocked",))
    t.update(snap(("w2:p1", "w2", "working", "x"),
                  ("w3:p1", "w3", "blocked", "y")), T0)
    assert t.open_count == 1


def test_run_key_is_hashable_and_comparable():
    k1 = RunKey("w1:p1", "alpha-service", "working", "claude", "/home/dev/w1")
    k2 = RunKey("w1:p1", "alpha-service", "working", "claude", "/home/dev/w1")
    assert k1 == k2 and len({k1, k2}) == 1


def test_completed_run_duration_is_seconds():
    k = RunKey("w1:p1", "alpha-service", "working", "claude", "/home/dev/w1")
    assert CompletedRun(k, "t", T0, at(12.5)).duration_seconds == 12.5


def test_real_fixture_opens_only_the_working_agent():
    # snapshot_basic.json has two agents: w2:p1 working and w3:p1 idle.
    t = FleetTracker()
    t.update(load(), T0)
    assert t.open_count == 1
    # Pin the identity too, so a future fixture edit fails here loudly instead
    # of quietly leaving the count right for the wrong reason.
    assert [c.key.pane_id for c in t.close_all(at(30))] == ["w2:p1"]
