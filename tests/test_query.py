"""Query rendering and terminal detection (spec §7, §7.1).

These two commands turn window_app and window_title from documentation-only
config keys into working features: one reads the user's real terminal name out
of their own window bucket, the other renders a pasteable query from it.
"""
import re
from datetime import timedelta

from aw_watcher_herdr import __main__ as cli
from aw_watcher_herdr.query import (
    local_hostname, render_attention_query, render_fleet_query,
    top_window_apps, window_bucket_id,
)

HOST = "synthetic-host"


class FakeEvent:
    def __init__(self, app, seconds):
        self.data = {"app": app} if app is not None else {}
        self.duration = timedelta(seconds=seconds)


class FakeClient:
    def __init__(self, buckets=None, events=None, hostname=HOST):
        self._buckets = buckets if buckets is not None else {}
        self._events = events or []
        self.client_hostname = hostname
        self.asked = []

    def get_buckets(self):
        return self._buckets

    def get_events(self, bucket_id, start=None, end=None, limit=-1):
        self.asked.append((bucket_id, start, end, limit))
        return list(self._events)


# --- attention query rendering ----------------------------------------------

def _find_bucket_calls(query: str) -> list[str]:
    """Every find_bucket(...) argument list appearing in a rendered query."""
    return re.findall(r"find_bucket\(([^)]*)\)", query)


def test_renders_a_single_terminal_app():
    q = render_attention_query(["Ghostty"], hostname=HOST)
    assert '"app", ["Ghostty"]' in q
    assert "aw-watcher-herdr_" in q
    assert "not-afk" in q
    assert q.rstrip().endswith('merge_events_by_keys(events, ["app", "title"]);')


def test_renders_several_terminal_apps():
    q = render_attention_query(["Ghostty", "Alacritty"], hostname=HOST)
    assert '"app", ["Ghostty", "Alacritty"]' in q


def test_omits_the_window_filter_when_no_apps_are_configured():
    # An empty list means "not configured yet" and must not match nothing.
    q = render_attention_query([], hostname=HOST)
    assert "aw-watcher-window_" not in q
    assert "not-afk" in q  # AFK gating still applies


def test_title_narrowing_is_only_emitted_when_set():
    assert "filter_keyvals_regex" not in render_attention_query(
        ["Ghostty"], hostname=HOST)
    q = render_attention_query(["Ghostty"], window_title="herdr", hostname=HOST)
    assert 'filter_keyvals_regex(in_term, "title", "herdr");' in q


def test_every_statement_is_terminated():
    for line in render_attention_query(["Ghostty"], "herdr", HOST).splitlines():
        assert line.endswith(";"), line


# --- hostname-qualified bucket lookup (integration fix) ---------------------
#
# find_bucket("prefix") returns the FIRST bucket whose id contains the prefix
# and ignores hostname unless one is passed. A machine that has been renamed
# keeps one bucket per old hostname, so the unqualified form resolves to a dead
# bucket and every filter_period_intersect gate silently yields zero hours.

def test_attention_query_qualifies_every_find_bucket_with_the_hostname():
    q = render_attention_query(["Ghostty"], window_title="herdr", hostname=HOST)
    calls = _find_bucket_calls(q)
    assert len(calls) == 3  # afk, herdr, window
    for call in calls:
        assert call.endswith(f'"{HOST}"'), call


def test_attention_query_without_window_apps_still_qualifies_its_buckets():
    calls = _find_bucket_calls(render_attention_query([], hostname=HOST))
    assert len(calls) == 2  # afk, herdr
    for call in calls:
        assert call.endswith(f'"{HOST}"'), call


def test_fleet_query_qualifies_its_find_bucket_with_the_hostname():
    calls = _find_bucket_calls(render_fleet_query(hostname=HOST))
    assert calls == [f'"aw-watcher-herdr-agents_", "{HOST}"']


def test_rendering_falls_back_to_this_machines_hostname(monkeypatch):
    # No caller can render an unqualified (silently wrong) query by omission.
    monkeypatch.setattr("socket.gethostname", lambda: "fallback-host")
    for q in (render_attention_query(["Ghostty"]), render_fleet_query()):
        calls = _find_bucket_calls(q)
        assert calls
        for call in calls:
            assert call.endswith('"fallback-host"'), call


def test_local_hostname_prefers_the_clients_own_hostname(monkeypatch):
    monkeypatch.setattr("socket.gethostname", lambda: "fallback-host")
    assert local_hostname(FakeClient(hostname="client-host")) == "client-host"
    assert local_hostname(FakeClient(hostname=None)) == "fallback-host"
    assert local_hostname() == "fallback-host"


# --- terminal detection -----------------------------------------------------

def test_window_bucket_is_found_by_prefix():
    client = FakeClient(buckets={"aw-watcher-afk_host": {"hostname": HOST},
                                 "aw-watcher-window_host": {"hostname": HOST}})
    assert window_bucket_id(client) == "aw-watcher-window_host"


def test_window_bucket_missing_returns_none():
    client = FakeClient(buckets={"aw-watcher-afk_host": {"hostname": HOST}})
    assert window_bucket_id(client) is None


def test_window_bucket_prefers_this_machines_hostname_over_listing_order():
    # A renamed machine keeps a bucket per old hostname; only the current one
    # is still written to, and the server may list a stale one first.
    client = FakeClient(buckets={
        "aw-watcher-window_old-host.lan": {
            "hostname": "old-host.lan", "last_updated": "2026-05-27T21:56:41+00:00"},
        "aw-watcher-window_other-host": {
            "hostname": "other-host", "last_updated": "2026-06-04T09:23:13+00:00"},
        f"aw-watcher-window_{HOST}": {
            "hostname": HOST, "last_updated": "2026-08-13T11:21:58+00:00"},
    })
    assert window_bucket_id(client) == f"aw-watcher-window_{HOST}"


def test_window_bucket_falls_back_to_the_most_recently_updated_match():
    # No bucket carries this hostname (renamed since, or a remote-recorded
    # bucket). The freshest one beats whichever is listed first.
    client = FakeClient(buckets={
        "aw-watcher-window_old-host.lan": {
            "hostname": "old-host.lan", "last_updated": "2026-05-27T21:56:41+00:00"},
        "aw-watcher-window_other-host": {
            "hostname": "other-host", "last_updated": "2026-06-04T09:23:13+00:00"},
    })
    assert window_bucket_id(client) == "aw-watcher-window_other-host"


def test_window_bucket_tolerates_missing_and_unparsable_last_updated():
    client = FakeClient(buckets={
        "aw-watcher-window_a-host": {"hostname": "a-host", "last_updated": None},
        "aw-watcher-window_b-host": {"hostname": "b-host", "last_updated": "nonsense"},
        "aw-watcher-window_c-host": {
            "hostname": "c-host", "last_updated": "2026-06-04T09:23:13+00:00"},
    })
    assert window_bucket_id(client) == "aw-watcher-window_c-host"


# --- fleet query rendering --------------------------------------------------

def test_fleet_query_defaults_to_working():
    q = render_fleet_query(hostname=HOST)
    assert '"status", ["working"]' in q
    # flood() would corrupt deliberately overlapping events (spec §7).
    assert "flood" not in q


def test_fleet_query_accepts_another_status():
    assert '"status", ["done"]' in render_fleet_query("done", hostname=HOST)


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


# --- run_detect_terminal's client identity (fix round 1) -------------------

def test_detect_terminal_uses_a_distinct_client_identity_from_the_daemon(
        monkeypatch):
    # ActivityWatchClient takes an OS-level single-instance file lock keyed by
    # client name, independent of this package's own lock.py. The daemon
    # holds that lock (under CLIENT_NAME) for its whole lifetime, so
    # --detect-terminal must use a different name or a second process making
    # the same call is killed by SystemExit(-1) before any HTTP request.
    captured = {}

    class FakeAWClient:
        def __init__(self, client_name, testing=False):
            captured["client_name"] = client_name
            captured["testing"] = testing

        def get_buckets(self):
            return {}

    monkeypatch.setattr(cli, "ActivityWatchClient", FakeAWClient)
    cli.run_detect_terminal(cli.Config(), testing=True)

    assert captured["client_name"] == f"{cli.CLIENT_NAME}-detect"
    assert captured["client_name"] != cli.CLIENT_NAME
    assert captured["testing"] is True


def test_detect_terminal_reads_the_local_bucket_not_a_stale_one(
        monkeypatch, capsys):
    # The stale bucket is listed first and has no events. Reading it made
    # --detect-terminal exit 1 with "no events in the last 24 hours", which
    # blocks setup because window_app gates the whole attention query.
    monkeypatch.setattr("socket.gethostname", lambda: HOST)
    events = {f"aw-watcher-window_{HOST}": [FakeEvent("Ghostty", 3600)]}

    class FakeAWClient:
        client_hostname = HOST

        def __init__(self, client_name, testing=False):
            pass

        def get_buckets(self):
            return {
                "aw-watcher-window_old-host.lan": {
                    "hostname": "old-host.lan",
                    "last_updated": "2026-05-27T21:56:41+00:00"},
                f"aw-watcher-window_{HOST}": {
                    "hostname": HOST,
                    "last_updated": "2026-08-13T11:21:58+00:00"},
            }

        def get_events(self, bucket_id, start=None, end=None, limit=-1):
            return list(events.get(bucket_id, []))

    monkeypatch.setattr(cli, "ActivityWatchClient", FakeAWClient)
    assert cli.run_detect_terminal(cli.Config(), testing=True) == 0
    out = capsys.readouterr().out
    assert f"aw-watcher-window_{HOST}" in out
    assert "old-host.lan" not in out
    assert 'window_app = ["Ghostty"]' in out
