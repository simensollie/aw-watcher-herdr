"""Query rendering and terminal detection (spec §7, §7.1).

These two commands turn window_app and window_title from documentation-only
config keys into working features: one reads the user's real terminal name out
of their own window bucket, the other renders a pasteable query from it.
"""
from datetime import timedelta

from aw_watcher_herdr.query import (
    render_attention_query, render_fleet_query, top_window_apps,
    window_bucket_id,
)


class FakeEvent:
    def __init__(self, app, seconds):
        self.data = {"app": app} if app is not None else {}
        self.duration = timedelta(seconds=seconds)


class FakeClient:
    def __init__(self, buckets=None, events=None):
        self._buckets = buckets if buckets is not None else {}
        self._events = events or []
        self.asked = []

    def get_buckets(self):
        return self._buckets

    def get_events(self, bucket_id, start=None, end=None, limit=-1):
        self.asked.append((bucket_id, start, end, limit))
        return list(self._events)


# --- attention query rendering ----------------------------------------------

def test_renders_a_single_terminal_app():
    q = render_attention_query(["Ghostty"])
    assert '"app", ["Ghostty"]' in q
    assert "aw-watcher-herdr_" in q
    assert "not-afk" in q
    assert q.rstrip().endswith('merge_events_by_keys(events, ["app", "title"]);')


def test_renders_several_terminal_apps():
    q = render_attention_query(["Ghostty", "Alacritty"])
    assert '"app", ["Ghostty", "Alacritty"]' in q


def test_omits_the_window_filter_when_no_apps_are_configured():
    # An empty list means "not configured yet" and must not match nothing.
    q = render_attention_query([])
    assert "aw-watcher-window_" not in q
    assert "not-afk" in q  # AFK gating still applies


def test_title_narrowing_is_only_emitted_when_set():
    assert "filter_keyvals_regex" not in render_attention_query(["Ghostty"])
    q = render_attention_query(["Ghostty"], window_title="herdr")
    assert 'filter_keyvals_regex(in_term, "title", "herdr");' in q


def test_every_statement_is_terminated():
    for line in render_attention_query(["Ghostty"], "herdr").splitlines():
        assert line.endswith(";"), line


# --- fleet query rendering --------------------------------------------------

def test_fleet_query_defaults_to_working():
    q = render_fleet_query()
    assert '"status", ["working"]' in q
    # flood() would corrupt deliberately overlapping events (spec §7).
    assert "flood" not in q


def test_fleet_query_accepts_another_status():
    assert '"status", ["done"]' in render_fleet_query("done")


# --- terminal detection -----------------------------------------------------

def test_window_bucket_is_found_by_prefix():
    client = FakeClient(buckets={"aw-watcher-afk_host": {},
                                 "aw-watcher-window_host": {}})
    assert window_bucket_id(client) == "aw-watcher-window_host"


def test_window_bucket_missing_returns_none():
    assert window_bucket_id(FakeClient(buckets={"aw-watcher-afk_host": {}})) is None


def test_top_window_apps_sums_durations_and_sorts_descending():
    client = FakeClient(events=[
        FakeEvent("Ghostty", 100), FakeEvent("Safari", 300),
        FakeEvent("Ghostty", 250), FakeEvent(None, 999),
    ])
    assert top_window_apps(client, "bucket") == [("Ghostty", 350.0),
                                                 ("Safari", 300.0)]


def test_top_window_apps_respects_the_limit():
    client = FakeClient(events=[FakeEvent(f"app{i}", i) for i in range(1, 20)])
    assert len(top_window_apps(client, "bucket", limit=3)) == 3


def test_top_window_apps_requests_the_asked_for_window():
    client = FakeClient(events=[])
    top_window_apps(client, "bucket", hours=6)
    bucket_id, start, end, _ = client.asked[0]
    assert bucket_id == "bucket"
    assert abs((end - start).total_seconds() - 6 * 3600) < 1


def test_top_window_apps_on_an_empty_bucket_returns_empty():
    assert top_window_apps(FakeClient(events=[]), "bucket") == []
