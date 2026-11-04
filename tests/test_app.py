"""The native-app source: adapting herdr-app + herdr-core into the TUI
snapshot shape, and the two line-protocol transports behind it.

The adapter is pure and table-tested against synthetic fixtures captured in
the shape herdr-app 0.1.12 and herdr-core 0.1.17 produce. The socket tests
use fake servers that answer one line per connection, which is what both
real programs do.
"""
import json
import os
import shutil
import socket
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from aw_watcher_herdr import app as app_module
from aw_watcher_herdr.app import (
    AppSource, adapt_snapshot, default_app_socket_path,
    default_core_socket_path,
)
from aw_watcher_herdr.herdr import HerdrError, HerdrUnavailable
from aw_watcher_herdr.state import FleetTracker, extract_attention

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def parts():
    return (load("app_status.json"), load("app_ls.json"),
            load("core_terminals.json")["terminals"])


@pytest.fixture
def snap(parts):
    return adapt_snapshot(*parts)


def agent(snap, pane_id):
    return next(a for a in snap["agents"] if a["pane_id"] == pane_id)


# --- adaptation --------------------------------------------------------------

def test_focus_comes_from_the_app_status(snap):
    assert snap["source"] == "app"
    assert snap["focused_workspace_id"] == "sp-beta"
    assert snap["focused_tab_id"] == "tb-beta-import"
    assert snap["focused_pane_id"] == "pn-beta-import-1"


def test_spaces_and_tabs_become_workspaces_and_tabs(snap):
    assert [(w["workspace_id"], w["label"]) for w in snap["workspaces"]] == [
        ("sp-alpha", "alpha-service"), ("sp-beta", "beta app"),
        ("box/sp-gamma", "gamma docs")]
    assert [(t["tab_id"], t["workspace_id"], t["label"]) for t in snap["tabs"]] == [
        ("tb-alpha-1", "sp-alpha", "1"),
        ("tb-beta-import", "sp-beta", "import"),
        ("tb-beta-review", "sp-beta", "review"),
        ("box/tb-gamma-1", "box/sp-gamma", "guide")]


def test_a_pane_merges_the_apps_view_with_the_cores_facts(snap):
    pane = agent(snap, "pn-beta-import-1")
    assert pane["tab_id"] == "tb-beta-import"
    assert pane["workspace_id"] == "sp-beta"
    assert pane["terminal_id"] == "t2"
    assert pane["agent"] == "claude-code"
    assert pane["agent_status"] == "working"
    assert pane["terminal_title_stripped"] == "Add retry logic to the import job"
    # The foreground program's folder, not the shell's: that is where the
    # agent works.
    assert pane["cwd"] == "/home/dev/beta-app"
    assert pane["status_since_ms"] == 1793600500000
    assert pane["focused"] is True


def test_the_existing_attention_extractor_reads_the_adapted_snapshot(snap):
    attention = extract_attention(snap)
    assert attention.workspace_label == "beta app"
    assert attention.tab_label == "import"
    assert attention.title == "Add retry logic to the import job"
    assert attention.agent == "claude-code"
    assert attention.agent_status == "working"


def test_a_plain_shell_is_a_pane_but_not_an_agent(snap):
    shell = next(p for p in snap["panes"] if p["pane_id"] == "pn-alpha-1")
    assert shell["agent"] is None
    assert shell["agent_status"] is None
    assert shell["cwd"] == "/home/dev/alpha-service/src"
    assert all(a["pane_id"] != "pn-alpha-1" for a in snap["agents"])


def test_done_is_the_apps_word_not_the_cores(snap):
    # The core still says `done` for t3 (its turn is over); the app says
    # `idle` because the person has looked at the tab. The fleet bucket's
    # `done` means "unnoticed", so the app's reading wins.
    assert agent(snap, "pn-beta-review-1")["agent_status"] == "idle"


def test_sub_agents_are_placed_in_their_ancestors_tab(snap):
    # t4 was started by t2 (the import pane) and t5 by t4: both land in the
    # import tab, keyed by their terminal address since they have no pane.
    helper = agent(snap, "t4")
    assert helper["tab_id"] == "tb-beta-import"
    assert helper["workspace_id"] == "sp-beta"
    assert helper["agent_status"] == "working"
    assert helper["parent"] == "t2"
    assert helper["terminal_title_stripped"] == "Run the import test suite"
    grandchild = agent(snap, "t5")
    assert grandchild["tab_id"] == "tb-beta-import"


def test_a_sub_agents_done_is_translated_to_idle(snap):
    # Nobody "notices" a sub-agent, so there is no unnoticed duration to
    # measure, and the raw core value would stay `done` until the next turn.
    assert agent(snap, "t5")["agent_status"] == "idle"


def test_a_parked_terminal_is_placed_by_its_own_label(snap):
    parked = agent(snap, "t6")
    assert parked["tab_id"] == "tb-beta-review"
    assert parked["agent_status"] == "blocked"


def test_a_terminal_the_app_does_not_know_is_left_out(snap):
    # t7 has neither labels nor a parent in the app's world: another
    # program's terminal, so it is not this person's herdr work.
    assert all(a["pane_id"] != "t7" for a in snap["agents"])


def test_a_remote_machines_pane_keeps_its_prefixed_ids(snap):
    remote = agent(snap, "box/pn-gamma-1")
    assert remote["workspace_id"] == "box/sp-gamma"
    assert remote["machine"] == "box"
    assert remote["agent"] == "codex"
    assert remote["agent_status"] == "blocked"
    assert remote["cwd"] == "/srv/gamma-docs"


def test_a_pane_without_a_core_entry_still_records_the_apps_view(parts):
    status, listing, terminals = parts
    snap = adapt_snapshot(status, listing, [t for t in terminals
                                            if t["address"] != "t2"])
    pane = agent(snap, "pn-beta-import-1")
    assert pane["agent_status"] == "working"
    assert pane["terminal_title_stripped"] is None
    assert pane["cwd"] is None


def test_the_fleet_tracker_opens_runs_from_the_adapted_snapshot(snap):
    from datetime import datetime, timezone
    tracker = FleetTracker()
    tracker.update(snap, datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc))
    # working t2, working t4, blocked t6, blocked box/t1; idle ones stay shut.
    assert tracker.open_count == 4


@pytest.mark.parametrize("listing", [
    {}, {"spaces": None}, {"spaces": "nope"}, [], None,
])
def test_an_unrecognised_ls_shape_is_an_error_not_an_empty_snapshot(listing):
    with pytest.raises(HerdrError):
        adapt_snapshot({"space": "x"}, listing, [])


def test_an_unrecognised_status_shape_is_an_error():
    with pytest.raises(HerdrError):
        adapt_snapshot(None, {"spaces": []}, [])


def test_nothing_focused_yields_no_focus_ids():
    snap = adapt_snapshot({"space": None, "tab": None, "focused_pane": None},
                          {"spaces": []}, [])
    assert snap["focused_workspace_id"] is None
    assert extract_attention(snap) is None


def test_a_parent_cycle_cannot_hang_the_adapter():
    terminals = [
        {"address": "ta", "parent": "tb", "labels": {},
         "status": {"app": "pi", "state": "working"}},
        {"address": "tb", "parent": "ta", "labels": {},
         "status": {"app": "pi", "state": "working"}},
    ]
    snap = adapt_snapshot({"space": None}, {"spaces": []}, terminals)
    assert snap["agents"] == []


# --- default paths -----------------------------------------------------------

def test_app_socket_path_follows_herdr_app_home(monkeypatch):
    monkeypatch.delenv("HERDR_APP_HOME", raising=False)
    monkeypatch.setattr(app_module.os.path, "expanduser",
                        lambda p: p.replace("~", "/home/dev", 1))
    assert default_app_socket_path() == "/home/dev/.config/herdr-app/herdr-app.sock"
    monkeypatch.setenv("HERDR_APP_HOME", "/tmp/other-home")
    assert default_app_socket_path() == "/tmp/other-home/herdr-app.sock"


def test_core_socket_path_prefers_hc_socket(monkeypatch):
    monkeypatch.setenv("HC_SOCKET", "/tmp/custom.sock")
    assert default_core_socket_path() == "/tmp/custom.sock"


def test_core_socket_path_uses_the_runtime_dir_and_session(monkeypatch):
    monkeypatch.delenv("HC_SOCKET", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.delenv("HC_SESSION", raising=False)
    assert default_core_socket_path() == "/run/user/1000/herdr-core/default.sock"
    monkeypatch.setenv("HC_SESSION", " smoke ")
    assert default_core_socket_path() == "/run/user/1000/herdr-core/smoke.sock"


def test_core_socket_path_falls_back_to_the_state_dir(monkeypatch):
    # macOS: no XDG_RUNTIME_DIR and no /run/user, so the core puts the socket
    # beside its state.
    monkeypatch.delenv("HC_SOCKET", raising=False)
    monkeypatch.delenv("HC_SESSION", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(app_module.os.path, "isdir", lambda p: False)
    monkeypatch.setattr(app_module.os.path, "expanduser",
                        lambda p: p.replace("~", "/Users/dev", 1))
    assert default_core_socket_path() == \
        "/Users/dev/.local/state/herdr-core/default/default.sock"


# --- transports --------------------------------------------------------------

@contextmanager
def fake_line_server(name, responses):
    """Answer one JSON line per connection; a `None` response closes without
    answering. Records what each connection sent."""
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, name)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(8)
    received = []

    def serve():
        for resp in responses:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn, conn.makefile("rwb") as f:
                received.append(json.loads(f.readline()))
                if resp is not None:
                    f.write(json.dumps(resp).encode() + b"\n")
                    f.flush()

    threading.Thread(target=serve, daemon=True).start()
    try:
        yield path, received
    finally:
        srv.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_snapshot_asks_status_then_ls_then_terminal_list(parts):
    status, listing, terminals = parts
    with fake_line_server("app.sock", [status, listing]) as (app_path, app_seen), \
            fake_line_server("core.sock", [
                {"jsonrpc": "2.0", "id": "x",
                 "result": {"terminals": terminals, "hidden_finished": 0,
                            "machines": []}}]) as (core_path, core_seen):
        snap = AppSource(app_path, core_path).snapshot()
    assert app_seen == [{"status": True}, {"ls": True}]
    assert core_seen[0]["method"] == "terminal.list"
    assert core_seen[0]["jsonrpc"] == "2.0"
    assert core_seen[0]["params"] == {"all": False}
    assert snap["focused_workspace_id"] == "sp-beta"
    # t2, t3 and box/t1 are panes; t4, t5 and t6 are placed orphans; t7 is out.
    assert len(snap["agents"]) == 6


def test_a_missing_app_socket_is_unavailable(tmp_path):
    source = AppSource(str(tmp_path / "nope.sock"), str(tmp_path / "core.sock"))
    with pytest.raises(HerdrUnavailable):
        source.snapshot()


def test_a_missing_core_socket_is_unavailable(parts, tmp_path):
    status, listing, _ = parts
    with fake_line_server("app.sock", [status, listing]) as (app_path, _):
        with pytest.raises(HerdrUnavailable):
            AppSource(app_path, str(tmp_path / "core.sock")).snapshot()


def test_an_app_error_line_is_a_herdr_error():
    with fake_line_server("app.sock", [
            {"error": "expected { do }, { ls }, { commands } or { status }"}]) \
            as (app_path, _):
        with pytest.raises(HerdrError) as exc:
            AppSource(app_path, "/nonexistent").snapshot()
    assert "expected" in str(exc.value)


def test_a_core_error_envelope_is_a_herdr_error(parts):
    status, listing, _ = parts
    with fake_line_server("app.sock", [status, listing]) as (app_path, _), \
            fake_line_server("core.sock", [
                {"jsonrpc": "2.0", "id": "x",
                 "error": {"code": -32601, "message": 'unknown method "x"',
                           "data": {"kind": "unknown_method"}}}]) as (core_path, _):
        with pytest.raises(HerdrError) as exc:
            AppSource(app_path, core_path).snapshot()
    assert "unknown_method" in str(exc.value)


def test_a_core_result_without_terminals_is_a_herdr_error(parts):
    status, listing, _ = parts
    with fake_line_server("app.sock", [status, listing]) as (app_path, _), \
            fake_line_server("core.sock", [
                {"jsonrpc": "2.0", "id": "x", "result": {}}]) as (core_path, _):
        with pytest.raises(HerdrError):
            AppSource(app_path, core_path).snapshot()


def test_a_closed_connection_without_an_answer_is_a_herdr_error():
    with fake_line_server("app.sock", [None]) as (app_path, _):
        with pytest.raises(HerdrError):
            AppSource(app_path, "/nonexistent").snapshot()


def test_socket_paths_are_tilde_expanded(monkeypatch):
    monkeypatch.setattr(app_module.os.path, "expanduser",
                        lambda p: p.replace("~", "/home/dev", 1))
    source = AppSource("~/a.sock", "~/b.sock")
    assert source.app_socket_path == "/home/dev/a.sock"
    assert source.core_socket_path == "/home/dev/b.sock"
