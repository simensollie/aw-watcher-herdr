"""Tests for both herdr snapshot sources (spec §3, §4.3).

The socket fake mimics the three protocol behaviours the real server exhibits:
`params` is required, one request is served per connection, and the connection
is closed afterwards. The CLI fake is a real executable script on a temporary
PATH, so subprocess handling is exercised for real rather than mocked away.
"""
import json
import os
import shutil
import socket
import stat
import sys
import tempfile
import threading
from contextlib import contextmanager

import pytest

from aw_watcher_herdr.herdr import (
    CliSource, HerdrError, HerdrUnavailable, UnixSocketSource, resolve_source,
)


@contextmanager
def fake_herdr_socket(responses):
    """Serve one response per connection. A `None` entry means: accept the
    request, then close without answering."""
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "herdr.sock")
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
                received.append(f.readline())
                if resp is not None:
                    payload = resp if isinstance(resp, bytes) else \
                        json.dumps(resp).encode()
                    f.write(payload + b"\n")
                    f.flush()

    threading.Thread(target=serve, daemon=True).start()
    try:
        yield path, received
    finally:
        srv.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


@contextmanager
def fake_herdr_cli(body, exit_code=0):
    """Write an executable `herdr` shell script into a temp dir and return it."""
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "herdr")
    with open(path, "w") as f:
        f.write("#!/bin/sh\n")
        f.write(f"cat <<'EOF'\n{body}\nEOF\n")
        f.write(f"exit {exit_code}\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP
             | stat.S_IXOTH)
    try:
        yield path
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


GOOD = {"id": "x", "result": {"snapshot": {"focused_workspace_id": "w1"}}}
NOT_RUNNING = {"id": "", "error": {"code": "server_not_running",
                                   "message": "no herdr server is running"}}
OTHER_ERROR = {"id": "", "error": {"code": "invalid_request",
                                   "message": "missing field 'params'"}}


# --- socket source ----------------------------------------------------------

def test_socket_snapshot_returns_the_snapshot_object():
    with fake_herdr_socket([GOOD]) as (path, _):
        assert UnixSocketSource(path).snapshot() == {"focused_workspace_id": "w1"}


def test_socket_request_always_sends_a_params_field():
    # herdr rejects a request with no `params`, even when it takes none.
    with fake_herdr_socket([GOOD]) as (path, received):
        UnixSocketSource(path).snapshot()
    sent = json.loads(received[0])
    assert sent["method"] == "session.snapshot"
    assert sent["params"] == {}
    assert sent["id"]


def test_socket_each_call_opens_a_fresh_connection():
    # The real server closes after one response, so two calls must reconnect.
    with fake_herdr_socket([GOOD, GOOD]) as (path, received):
        source = UnixSocketSource(path)
        source.snapshot()
        source.snapshot()
    assert len(received) == 2


def test_socket_missing_path_raises_unavailable(tmp_path):
    source = UnixSocketSource(str(tmp_path / "nope.sock"))
    with pytest.raises(HerdrUnavailable):
        source.snapshot()


def test_socket_closed_without_response_raises_herdr_error():
    with fake_herdr_socket([None]) as (path, _):
        with pytest.raises(HerdrError):
            UnixSocketSource(path).snapshot()


# --- CLI source -------------------------------------------------------------

def test_cli_snapshot_returns_the_snapshot_object():
    with fake_herdr_cli(json.dumps(GOOD)) as binary:
        assert CliSource(binary).snapshot() == {"focused_workspace_id": "w1"}


def test_cli_server_not_running_raises_unavailable():
    # Verified against the real binary: exit 1 plus this error envelope.
    with fake_herdr_cli(json.dumps(NOT_RUNNING), exit_code=1) as binary:
        with pytest.raises(HerdrUnavailable):
            CliSource(binary).snapshot()


def test_cli_missing_binary_raises_unavailable(tmp_path):
    with pytest.raises(HerdrUnavailable):
        CliSource(str(tmp_path / "no-such-herdr")).snapshot()


def test_cli_timeout_raises_unavailable():
    with fake_herdr_cli("ignored") as binary:
        with open(binary, "w") as f:
            f.write("#!/bin/sh\nsleep 5\n")
        with pytest.raises(HerdrUnavailable):
            CliSource(binary, timeout=0.3).snapshot()


def test_cli_non_json_stdout_raises_herdr_error():
    with fake_herdr_cli("this is not json") as binary:
        with pytest.raises(HerdrError) as exc:
            CliSource(binary).snapshot()
    assert not isinstance(exc.value, HerdrUnavailable)


def test_cli_failure_with_no_output_raises_herdr_error():
    with fake_herdr_cli("") as binary:
        with open(binary, "w") as f:
            f.write("#!/bin/sh\necho boom >&2\nexit 3\n")
        with pytest.raises(HerdrError) as exc:
            CliSource(binary).snapshot()
    assert "boom" in str(exc.value)


# --- shared envelope handling -----------------------------------------------

@pytest.mark.parametrize("envelope,expected", [
    (OTHER_ERROR, HerdrError),
    (NOT_RUNNING, HerdrUnavailable),
    ({"id": "x", "result": {}}, HerdrError),          # result without snapshot
    ({"id": "x"}, HerdrError),                        # neither result nor error
])
def test_both_sources_agree_on_envelope_handling(envelope, expected):
    with fake_herdr_socket([envelope]) as (path, _):
        with pytest.raises(expected):
            UnixSocketSource(path).snapshot()
    with fake_herdr_cli(json.dumps(envelope)) as binary:
        with pytest.raises(expected):
            CliSource(binary).snapshot()


def test_invalid_request_error_is_not_unavailable():
    # A protocol error must escalate (it warns after N), unlike a missing server.
    with fake_herdr_socket([OTHER_ERROR]) as (path, _):
        with pytest.raises(HerdrError) as exc:
            UnixSocketSource(path).snapshot()
    assert not isinstance(exc.value, HerdrUnavailable)
    assert "invalid_request" in str(exc.value)


# --- source resolution ------------------------------------------------------

class FakeConfig:
    def __init__(self, source="auto"):
        self.source = source
        self.socket_path = "/tmp/herdr.sock"
        self.herdr_binary = "herdr"


def test_auto_picks_the_socket_on_posix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert isinstance(resolve_source(FakeConfig()), UnixSocketSource)


def test_auto_picks_the_cli_on_windows(monkeypatch):
    # herdr uses a named pipe on Windows and CPython has no AF_UNIX there.
    monkeypatch.setattr(sys, "platform", "win32")
    assert isinstance(resolve_source(FakeConfig()), CliSource)


def test_explicit_source_overrides_the_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert isinstance(resolve_source(FakeConfig("cli")), CliSource)


def test_unknown_source_raises():
    with pytest.raises(ValueError):
        resolve_source(FakeConfig("carrier-pigeon"))
