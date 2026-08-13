"""Snapshot sources for herdr's local API (spec §3, §4.3).

Protocol behaviours encoded here, each verified against herdr 0.8.0
(protocol 19):

  * `params` is REQUIRED on every socket request, even when the method takes
    none. Omitting it gets `invalid_request: missing field 'params'`.
  * The server answers exactly ONE request per connection and then closes it.
    Every socket call therefore opens a fresh connection, which conveniently
    makes reconnect-after-failure the normal path rather than a special case.
  * Errors come back as {"id": "", "error": {"code": ..., "message": ...}}.
  * `herdr api snapshot` returns the identical envelope, and exits 1 with
    code `server_not_running` when nothing is listening.

The API answers from outside a herdr-managed pane, so no HERDR_* environment
and no macOS Accessibility permission are needed.

Two transports exist because herdr uses a Unix domain socket on macOS and Linux
but a named pipe on Windows, where CPython exposes no socket.AF_UNIX. The CLI
wrapper is the route herdr's own documentation recommends for plugins there.
This module holds the only platform branch in the data path. Spec §4.3 permits
two further sites: lock.py, and __main__.default_window_apps() as the third,
which picks a display default rather than a transport.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from typing import Protocol

DEFAULT_SOCKET_PATH = "~/.config/herdr/herdr.sock"
DEFAULT_HERDR_BINARY = "herdr"
SNAPSHOT_METHOD = "session.snapshot"

# herdr's error code for "nothing is listening". Anything else is a real fault.
UNAVAILABLE_CODES = frozenset({"server_not_running"})


class HerdrError(Exception):
    """herdr was reachable but did not return a usable result."""


class HerdrUnavailable(HerdrError):
    """herdr is not running or its API is not connectable.

    This is a normal state, not a fault: the user may simply have quit herdr.
    Callers treat it as a gap in the timeline, not as an error to warn about.
    """


class SnapshotSource(Protocol):
    """Anything that can hand back a herdr session snapshot."""

    def snapshot(self) -> dict:
        ...


def default_socket_path() -> str:
    return os.path.expanduser(DEFAULT_SOCKET_PATH)


def _parse_envelope(raw, method: str) -> dict:
    """Turn a raw response into its `result` object, or raise.

    Shared by both transports: herdr's CLI emits byte-for-byte the same
    envelope as the socket, so the error taxonomy must not diverge between them.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    raw = (raw or "").strip()
    if not raw:
        raise HerdrError(f"{method}: herdr returned no response")
    try:
        msg = json.loads(raw)
    except ValueError as exc:
        raise HerdrError(f"{method}: malformed response: {exc}") from exc
    if not isinstance(msg, dict):
        raise HerdrError(f"{method}: response was not an object")

    if "error" in msg:
        err = msg["error"] or {}
        code = err.get("code", "error")
        message = err.get("message", "")
        if code in UNAVAILABLE_CODES:
            raise HerdrUnavailable(f"{method}: {message or code}")
        raise HerdrError(f"{method}: {code}: {message}")
    if "result" not in msg:
        raise HerdrError(f"{method}: response contained no result")
    return msg["result"]


def _snapshot_from_result(result: dict) -> dict:
    snap = result.get("snapshot") if isinstance(result, dict) else None
    if not isinstance(snap, dict):
        raise HerdrError(f"{SNAPSHOT_METHOD}: result contained no snapshot object")
    return snap


class UnixSocketSource:
    """Connect-per-request client for the herdr API socket (macOS, Linux)."""

    def __init__(self, socket_path: str | None = None, timeout: float = 5.0):
        # expanduser applies to a SUPPLIED path too, not just the default: the
        # value documented in the example config and in DEFAULT_CONFIG is
        # "~/.config/herdr/herdr.sock", and unexpanded it can never connect.
        # The resulting FileNotFoundError becomes HerdrUnavailable, which the
        # loop treats as "herdr is not running" and logs at debug only, so the
        # watcher would record nothing forever while looking healthy.
        self.socket_path = os.path.expanduser(socket_path or DEFAULT_SOCKET_PATH)
        self.timeout = timeout
        self._seq = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        """Send one request, return its `result` object."""
        self._seq += 1
        payload = json.dumps({
            "id": f"aw-watcher-herdr:{self._seq}",
            "method": method,
            "params": params if params is not None else {},
        })

        # AF_UNIX is referenced inside the method, never at module level: it
        # does not exist on Windows and would break the import there.
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except OSError as exc:
            # Covers FileNotFoundError, ConnectionRefusedError and timeouts,
            # all of which mean "herdr isn't there", not "herdr is broken".
            sock.close()
            raise HerdrUnavailable(
                f"cannot connect to {self.socket_path}: {exc}") from exc

        try:
            with sock.makefile("rwb") as f:
                f.write(payload.encode() + b"\n")
                f.flush()
                line = f.readline()
        except OSError as exc:
            raise HerdrError(f"{method}: socket error: {exc}") from exc
        finally:
            sock.close()

        if not line:
            raise HerdrError(
                f"{method}: herdr closed the connection without responding")
        return _parse_envelope(line, method)

    def snapshot(self) -> dict:
        """Return the live session snapshot (spec §3)."""
        return _snapshot_from_result(self.request(SNAPSHOT_METHOD))


class CliSource:
    """Reads snapshots by invoking `herdr api snapshot` (Windows, and anywhere).

    Costs roughly 5-10 ms per call against roughly 0.1 ms for the socket, so it
    is not the default where a socket exists. It is the only option on Windows,
    where herdr uses a named pipe whose path is undocumented.
    """

    def __init__(self, binary: str = DEFAULT_HERDR_BINARY, timeout: float = 10.0):
        self.binary = binary or DEFAULT_HERDR_BINARY
        self.timeout = timeout

    def snapshot(self) -> dict:
        try:
            proc = subprocess.run(
                [self.binary, "api", "snapshot"],
                capture_output=True, text=True, timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise HerdrUnavailable(
                f"herdr binary {self.binary!r} not found: {exc}") from exc
        except OSError as exc:
            raise HerdrUnavailable(
                f"cannot run {self.binary!r}: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise HerdrUnavailable(
                f"{self.binary} api snapshot timed out after {self.timeout}s"
            ) from exc

        stdout = (proc.stdout or "").strip()
        if not stdout:
            stderr = (proc.stderr or "").strip()
            raise HerdrError(
                f"{SNAPSHOT_METHOD}: {self.binary} exited {proc.returncode} "
                f"with no output: {stderr}")
        # A non-zero exit with a well-formed error envelope is classified by
        # its code, not its exit status: server_not_running exits 1.
        return _snapshot_from_result(_parse_envelope(stdout, SNAPSHOT_METHOD))


def resolve_source(config) -> SnapshotSource:
    """Build the snapshot source the config asks for (spec §4.3)."""
    choice = (getattr(config, "source", "auto") or "auto").lower()
    if choice == "auto":
        choice = "cli" if sys.platform.startswith("win") else "socket"
    if choice == "socket":
        return UnixSocketSource(getattr(config, "socket_path", None))
    if choice == "cli":
        return CliSource(getattr(config, "herdr_binary", DEFAULT_HERDR_BINARY))
    raise ValueError(
        f"unknown source {choice!r}; expected one of auto, socket, cli")
