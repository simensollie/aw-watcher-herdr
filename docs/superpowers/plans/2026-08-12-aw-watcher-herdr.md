# aw-watcher-herdr Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an ActivityWatch watcher that records, from herdr's local socket API, both which herdr workspace the user is attending to and what every concurrently-running coding agent is doing.

**Architecture:** A connect-per-request client reads `session.snapshot` from herdr's Unix socket every 2 s. A pure, I/O-free state machine turns consecutive snapshots into (a) a single focused-workspace event and (b) open/close transitions for per-agent runs. Two writers with different disciplines push to aw-server: the attention bucket heartbeat-merges one timeline, the fleet bucket posts completed, deliberately overlapping intervals.

**Tech Stack:** Python 3.10+, `aw-client` (brings `aw-core`), pytest. No `pyobjc`, no macOS Accessibility permission.

**Spec:** `docs/superpowers/specs/2026-08-12-herdr-activitywatch-watcher-design.md`

## Global Constraints

- **Python** `>=3.10`. Use `from __future__ import annotations` in every module so `X | None` annotations work.
- **Runtime dependencies:** `aw-client>=0.5.13` only. Do not add `pyobjc`, `tomli`, or anything else; `aw-core` (which provides `load_config_toml`, `setup_logging`, `Event`) ships with `aw-client`.
- **Client / bucket naming:** client name is `aw-watcher-herdr`. Buckets are `aw-watcher-herdr_<host>` (attention) and `aw-watcher-herdr-agents_<host>` (fleet), each gaining a `-testing` suffix under `--testing`.
- **No real customer data anywhere.** Fixtures, docstrings, README examples and commit messages use synthetic names only (`alpha-service`, `beta app`, `gamma docs`). The predecessor repo had to fix this retroactively in commit `ccb06b4`; do not repeat it.
- **Language:** English throughout — code, comments, docs, commit messages.
- **Timestamps:** all datetimes are timezone-aware UTC (`datetime.now(timezone.utc)`). Never use naive datetimes; aw-server rejects them.
- **herdr protocol facts** (verified against herdr 0.8.0, protocol 19) — encode all three:
  1. `params` is **required** on every request, even when empty (`{}`).
  2. The server answers **one request per connection**, then closes it. Every call opens a fresh connection.
  3. Responses echo the request `id`. Errors arrive as `{"id": "", "error": {"code": ..., "message": ...}}`.

---

## File structure

| File | Responsibility | Action | Task |
|---|---|---|---|
| `pyproject.toml` | packaging, deps, entry point | create | 1 |
| `aw_watcher_herdr/__init__.py` | `__version__` | create | 1 |
| `aw_watcher_herdr/__main__.py` | `Config`, `load_config`, `parse_args`, `main` | create | 1, extend 6 |
| `tests/test_config.py` | config defaults + CLI override precedence | create | 1 |
| `aw_watcher_herdr/herdr.py` | socket client: `HerdrClient`, `HerdrError`, `HerdrUnavailable` | create | 2 |
| `tests/test_herdr.py` | fake-socket-server tests | create | 2 |
| `aw_watcher_herdr/state.py` | pure: `Attention`, `extract_attention` | create | 3 |
| `aw_watcher_herdr/state.py` | pure: `RunKey`, `CompletedRun`, `FleetTracker` | extend | 4 |
| `tests/fixtures/snapshot_basic.json` | 3 workspaces, 3 agents, mixed statuses | create | 3 |
| `tests/fixtures/snapshot_no_focus.json` | nothing focused | create | 3 |
| `tests/test_state_attention.py` | attention extraction | create | 3 |
| `tests/test_state_fleet.py` | run lifecycle table tests | create | 4 |
| `aw_watcher_herdr/emit.py` | `AttentionWriter`, `FleetWriter` | create | 5 |
| `tests/test_emit.py` | writer discipline + retry buffer | create | 5 |
| `aw_watcher_herdr/main.py` | poll loop, statuses, gap handling | create | 6 |
| `tests/test_loop.py` | loop drive tests | create | 6 |
| `scripts/install.sh`, `scripts/uninstall.sh`, `scripts/verify.sh` | deployment | create | 7 |
| `packaging/com.activitywatch.aw-watcher-herdr.plist` | LaunchAgent template | create | 7 |
| `aw-watcher-herdr.toml.example`, `README.md`, `Makefile` | docs | create | 7 |

---

## Task 1: Scaffolding, config, and CLI

**Files:**
- Create: `pyproject.toml`
- Create: `aw_watcher_herdr/__init__.py`
- Create: `aw_watcher_herdr/__main__.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Config` dataclass with fields `socket_path: str | None`, `poll_interval: float`, `pulsetime: float`, `generic_terminal_label: str`, `fleet_enabled: bool`, `fleet_statuses: list[str]`, `max_run_seconds: float`, `gap_factor: float`, `window_app: str`, `window_title: str`. Also `load_config(args) -> Config`, `parse_args(argv=None) -> argparse.Namespace`, and the module constant `CLIENT_NAME = "aw-watcher-herdr"`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "aw-watcher-herdr"
version = "0.1.0"
description = "ActivityWatch watcher recording herdr workspace attention and concurrent agent activity"
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MPL-2.0" }
authors = [{ name = "Simen Sollie", email = "simen@sollie.io" }]
keywords = ["activitywatch", "herdr", "time-tracking", "watcher", "ai-agents"]
classifiers = [
    "Environment :: MacOS X",
    "Operating System :: MacOS :: MacOS X",
    "Programming Language :: Python :: 3",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)",
]
dependencies = [
    # >=0.5.13 for client_hostname and queued heartbeat/create_bucket support.
    # Brings aw-core (Event, load_config_toml, setup_logging) with it.
    "aw-client>=0.5.13",
]

[project.optional-dependencies]
dev = ["pytest"]

[project.urls]
Homepage = "https://github.com/simensollie/aw-watcher-herdr"
Issues = "https://github.com/simensollie/aw-watcher-herdr/issues"

[project.scripts]
aw-watcher-herdr = "aw_watcher_herdr.__main__:main"

[tool.setuptools]
packages = ["aw_watcher_herdr"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create the package marker**

Create `aw_watcher_herdr/__init__.py`:

```python
"""ActivityWatch watcher for herdr: workspace attention + concurrent agent activity."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create the dev venv and install**

```bash
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]"
```
Expected: completes without error. Verify with `.venv/bin/python -c "import aw_client, aw_core; print('ok')"` → prints `ok`.

- [ ] **Step 4: Write the failing config tests**

Create `tests/test_config.py`:

```python
"""Config loading and CLI-over-file override precedence (spec §8)."""

from aw_watcher_herdr import __main__ as cli


def test_defaults_when_no_flags(monkeypatch):
    # Isolate from any real user config file.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {})
    cfg = cli.load_config(cli.parse_args([]))
    assert cfg.poll_interval == 2.0
    assert cfg.pulsetime == 5.0
    assert cfg.generic_terminal_label == "terminal"
    assert cfg.fleet_enabled is True
    assert cfg.fleet_statuses == ["working", "blocked", "done"]
    assert cfg.max_run_seconds == 43200.0
    assert cfg.gap_factor == 3.0
    assert cfg.socket_path is None


def test_file_values_applied(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {
            "poll_interval": 4.0,
            "pulsetime": 9.0,
            "generic_terminal_label": "shell",
            "fleet_statuses": ["working"],
            "max_run_seconds": 600.0,
            "gap_factor": 5.0,
            "fleet_enabled": False,
        }
    })
    cfg = cli.load_config(cli.parse_args([]))
    assert cfg.poll_interval == 4.0
    assert cfg.pulsetime == 9.0
    assert cfg.generic_terminal_label == "shell"
    assert cfg.fleet_statuses == ["working"]
    assert cfg.max_run_seconds == 600.0
    assert cfg.gap_factor == 5.0
    assert cfg.fleet_enabled is False


def test_cli_flags_override_file(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"poll_interval": 4.0, "pulsetime": 9.0,
                             "generic_terminal_label": "shell"}
    })
    cfg = cli.load_config(cli.parse_args(
        ["--poll-interval", "1.5", "--pulsetime", "6",
         "--generic-terminal-label", "tty", "--socket-path", "/run/h.sock"]))
    assert cfg.poll_interval == 1.5
    assert cfg.pulsetime == 6.0
    assert cfg.generic_terminal_label == "tty"
    assert cfg.socket_path == "/run/h.sock"


def test_no_fleet_flag_disables_fleet(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"fleet_enabled": True}
    })
    cfg = cli.load_config(cli.parse_args(["--no-fleet"]))
    assert cfg.fleet_enabled is False


def test_unset_flags_do_not_override_file(monkeypatch):
    # fleet_enabled absent on the CLI must leave the file's False intact.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"fleet_enabled": False}
    })
    cfg = cli.load_config(cli.parse_args([]))
    assert cfg.fleet_enabled is False


def test_poll_interval_zero_is_honored(monkeypatch):
    # `is not None` guard: an explicit 0 must not be dropped by truthiness.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"poll_interval": 2.0}
    })
    cfg = cli.load_config(cli.parse_args(["--poll-interval", "0"]))
    assert cfg.poll_interval == 0.0
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` for `aw_watcher_herdr.__main__` (not written yet).

- [ ] **Step 6: Write `__main__.py`**

Create `aw_watcher_herdr/__main__.py`. (Task 6 extends `main()` to run the loop; for now it only wires config and the `--snapshot` diagnostic.)

```python
"""Entry point: parse args, load config, set up buckets, run the loop."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field

from aw_core.config import load_config_toml

from . import __version__

logger = logging.getLogger(__name__)

CLIENT_NAME = "aw-watcher-herdr"

DEFAULT_GENERIC_LABEL = "terminal"
DEFAULT_FLEET_STATUSES = ["working", "blocked", "done"]

# Default config rendered into the user's toml on first run (aw-core convention).
DEFAULT_CONFIG = f"""
[{CLIENT_NAME}]
poll_interval = 2.0
pulsetime = 5.0
generic_terminal_label = "{DEFAULT_GENERIC_LABEL}"
fleet_enabled = true
fleet_statuses = ["working", "blocked", "done"]
max_run_seconds = 43200.0
gap_factor = 3.0
window_app = "Ghostty"
window_title = "herdr"
# socket_path = "~/.config/herdr/herdr.sock"   # defaults to that path
""".strip()

_FILE_KEYS = (
    "socket_path", "poll_interval", "pulsetime", "generic_terminal_label",
    "fleet_enabled", "fleet_statuses", "max_run_seconds", "gap_factor",
    "window_app", "window_title",
)


@dataclass
class Config:
    socket_path: str | None = None
    poll_interval: float = 2.0
    pulsetime: float = 5.0
    generic_terminal_label: str = DEFAULT_GENERIC_LABEL
    fleet_enabled: bool = True
    fleet_statuses: list[str] = field(
        default_factory=lambda: list(DEFAULT_FLEET_STATUSES))
    max_run_seconds: float = 43200.0
    gap_factor: float = 3.0
    window_app: str = "Ghostty"
    window_title: str = "herdr"


def load_config(args: argparse.Namespace) -> Config:
    """Load config from the aw-core toml, then apply CLI overrides (flags win)."""
    cfg = Config()
    try:
        parsed = load_config_toml(CLIENT_NAME, DEFAULT_CONFIG)
        section = parsed.get(CLIENT_NAME, parsed)
        for key in _FILE_KEYS:
            if key in section:
                setattr(cfg, key, section[key])
    except Exception as exc:  # noqa: BLE001 - config is best-effort, defaults are fine
        logger.warning("could not load config file, using defaults: %s", exc)

    # Flags override the file. Use `is not None` so an explicit 0 is honored and
    # not silently dropped by a truthiness check.
    if args.socket_path is not None:
        cfg.socket_path = args.socket_path
    if args.poll_interval is not None:
        cfg.poll_interval = args.poll_interval
    if args.pulsetime is not None:
        cfg.pulsetime = args.pulsetime
    if args.generic_terminal_label is not None:
        cfg.generic_terminal_label = args.generic_terminal_label
    if args.fleet_enabled is not None:
        cfg.fleet_enabled = args.fleet_enabled
    return cfg


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog=CLIENT_NAME, description=__doc__)
    p.add_argument("--testing", action="store_true",
                   help="use the aw test server (port 5666) and -testing buckets")
    p.add_argument("--verbose", action="store_true", help="debug logging")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--socket-path", dest="socket_path",
                   help="override the herdr socket path")
    p.add_argument("--poll-interval", dest="poll_interval", type=float,
                   help="seconds between snapshots")
    p.add_argument("--pulsetime", dest="pulsetime", type=float,
                   help="heartbeat merge window in seconds (attention bucket)")
    p.add_argument("--generic-terminal-label", dest="generic_terminal_label",
                   help="title stored for panes with no agent")
    # store_const keeps the unset default at None so it doesn't override the file.
    p.add_argument("--no-fleet", dest="fleet_enabled",
                   action="store_const", const=False, default=None,
                   help="do not emit the agent-fleet bucket")
    p.add_argument("--snapshot", action="store_true",
                   help="print herdr's live session snapshot as JSON and exit "
                        "(for capturing test fixtures)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args)
    if args.snapshot:
        print(json.dumps({"note": "implemented in Task 6"}, indent=2))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS (6 passed).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml aw_watcher_herdr/__init__.py aw_watcher_herdr/__main__.py tests/test_config.py
git commit -m "feat: package scaffolding, config loading, and CLI"
```

---

## Task 2: herdr socket client

**Files:**
- Create: `aw_watcher_herdr/herdr.py`
- Test: `tests/test_herdr.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `HerdrError(Exception)`, `HerdrUnavailable(HerdrError)`, `default_socket_path() -> str`, and `HerdrClient(socket_path: str | None = None, timeout: float = 5.0)` with methods `request(method: str, params: dict | None = None) -> dict` (returns the `result` object) and `snapshot() -> dict` (returns `result["snapshot"]`).

- [ ] **Step 1: Write the failing client tests**

Create `tests/test_herdr.py`:

```python
"""Tests for the herdr socket client against a fake server (spec §3).

The fake mimics the three protocol behaviours the real server exhibits:
`params` is required, one request is served per connection, and the connection
is closed afterwards.
"""
import json
import os
import shutil
import socket
import tempfile
import threading
from contextlib import contextmanager

import pytest

from aw_watcher_herdr.herdr import HerdrClient, HerdrError, HerdrUnavailable


@contextmanager
def fake_herdr(responses):
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
                    f.write(json.dumps(resp).encode() + b"\n")
                    f.flush()

    threading.Thread(target=serve, daemon=True).start()
    try:
        yield path, received
    finally:
        srv.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_snapshot_returns_the_snapshot_object():
    resp = {"id": "x", "result": {"snapshot": {"focused_workspace_id": "w1"}}}
    with fake_herdr([resp]) as (path, _):
        assert HerdrClient(path).snapshot() == {"focused_workspace_id": "w1"}


def test_request_always_sends_a_params_field():
    # herdr rejects a request with no `params`, even when it takes none.
    resp = {"id": "x", "result": {"snapshot": {}}}
    with fake_herdr([resp]) as (path, received):
        HerdrClient(path).snapshot()
    sent = json.loads(received[0])
    assert sent["method"] == "session.snapshot"
    assert sent["params"] == {}
    assert sent["id"]


def test_each_call_opens_a_fresh_connection():
    # The real server closes after one response, so two calls must reconnect.
    resp = {"id": "x", "result": {"snapshot": {"n": 1}}}
    with fake_herdr([resp, resp]) as (path, received):
        client = HerdrClient(path)
        client.snapshot()
        client.snapshot()
    assert len(received) == 2


def test_missing_socket_raises_unavailable(tmp_path):
    client = HerdrClient(str(tmp_path / "nope.sock"))
    with pytest.raises(HerdrUnavailable):
        client.snapshot()


def test_server_error_response_raises_herdr_error():
    resp = {"id": "", "error": {"code": "invalid_request", "message": "boom"}}
    with fake_herdr([resp]) as (path, _):
        with pytest.raises(HerdrError) as exc:
            HerdrClient(path).snapshot()
    assert "invalid_request" in str(exc.value)
    assert not isinstance(exc.value, HerdrUnavailable)


def test_closed_without_response_raises_herdr_error():
    with fake_herdr([None]) as (path, _):
        with pytest.raises(HerdrError):
            HerdrClient(path).snapshot()


def test_malformed_response_raises_herdr_error():
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "herdr.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        with conn, conn.makefile("rwb") as f:
            f.readline()
            f.write(b"this is not json\n")
            f.flush()

    threading.Thread(target=serve, daemon=True).start()
    try:
        with pytest.raises(HerdrError):
            HerdrClient(path).snapshot()
    finally:
        srv.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_result_without_snapshot_raises_herdr_error():
    with fake_herdr([{"id": "x", "result": {}}]) as (path, _):
        with pytest.raises(HerdrError):
            HerdrClient(path).snapshot()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_herdr.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aw_watcher_herdr.herdr'`.

- [ ] **Step 3: Write `herdr.py`**

Create `aw_watcher_herdr/herdr.py`:

```python
"""Client for herdr's local JSON-lines socket API (spec §3).

Three protocol behaviours are encoded here, each verified against herdr 0.8.0
(protocol 19):

  * `params` is REQUIRED on every request, even when the method takes none.
    Omitting it gets `invalid_request: missing field 'params'`.
  * The server answers exactly ONE request per connection and then closes it.
    Every call therefore opens a fresh connection — which conveniently makes
    reconnect-after-failure the normal path rather than a special case.
  * Errors come back as {"id": "", "error": {"code": ..., "message": ...}}.

The socket answers from outside a herdr-managed pane, so no HERDR_* environment
and no macOS Accessibility permission are needed.
"""

from __future__ import annotations

import json
import os
import socket

DEFAULT_SOCKET_PATH = "~/.config/herdr/herdr.sock"


class HerdrError(Exception):
    """herdr was reachable but did not return a usable result."""


class HerdrUnavailable(HerdrError):
    """herdr is not running or its socket is not connectable.

    This is a normal state, not a fault: the user may simply have quit herdr.
    Callers treat it as a gap in the timeline, not as an error to warn about.
    """


def default_socket_path() -> str:
    return os.path.expanduser(DEFAULT_SOCKET_PATH)


class HerdrClient:
    """Connect-per-request client for the herdr API socket."""

    def __init__(self, socket_path: str | None = None, timeout: float = 5.0):
        self.socket_path = socket_path or default_socket_path()
        self.timeout = timeout
        self._seq = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        """Send one request, return its `result` object.

        Raises HerdrUnavailable if the socket cannot be connected, HerdrError
        for anything else (server error, truncated or malformed response).
        """
        self._seq += 1
        payload = json.dumps({
            "id": f"aw-watcher-herdr:{self._seq}",
            "method": method,
            "params": params if params is not None else {},
        })

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except OSError as exc:
            # Covers FileNotFoundError, ConnectionRefusedError and timeouts —
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
        try:
            msg = json.loads(line)
        except ValueError as exc:
            raise HerdrError(f"{method}: malformed response: {exc}") from exc

        if "error" in msg:
            err = msg["error"] or {}
            raise HerdrError(
                f"{method}: {err.get('code', 'error')}: {err.get('message', '')}")
        if "result" not in msg:
            raise HerdrError(f"{method}: response contained no result")
        return msg["result"]

    def snapshot(self) -> dict:
        """Return the live session snapshot (spec §3)."""
        result = self.request("session.snapshot")
        snap = result.get("snapshot")
        if not isinstance(snap, dict):
            raise HerdrError("session.snapshot: result contained no snapshot object")
        return snap
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_herdr.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Smoke-test against the real herdr socket**

Run:
```bash
.venv/bin/python -c "
from aw_watcher_herdr.herdr import HerdrClient
s = HerdrClient().snapshot()
print('protocol', s.get('protocol'), '| workspaces', len(s.get('workspaces', [])))
print('focused', s.get('focused_workspace_id'), s.get('focused_pane_id'))
"
```
Expected: prints a protocol number (19 on herdr 0.8.0), a workspace count, and the focused ids. If herdr is not running this raises `HerdrUnavailable`, which is correct behaviour — start herdr and retry.

- [ ] **Step 6: Commit**

```bash
git add aw_watcher_herdr/herdr.py tests/test_herdr.py
git commit -m "feat(herdr): connect-per-request client for the herdr socket API"
```

---

## Task 3: Attention extraction

**Files:**
- Create: `aw_watcher_herdr/state.py`
- Create: `tests/fixtures/snapshot_basic.json`
- Create: `tests/fixtures/snapshot_no_focus.json`
- Test: `tests/test_state_attention.py`

**Interfaces:**
- Consumes: nothing (pure functions over plain dicts).
- Produces: frozen dataclass `Attention(workspace_label: str, workspace_id: str, pane_id: str | None, title: str | None, agent: str | None, agent_status: str | None)` and `extract_attention(snapshot: dict) -> Attention | None`.

- [ ] **Step 1: Create the main fixture**

Create `tests/fixtures/snapshot_basic.json`. This mirrors the real snapshot shape exactly, with synthetic names. Note the deliberate variety: `w1` has a pane with **no** `agent` key and status `unknown`; `w2` has a working agent; `w3` is focused and idle.

```json
{
  "protocol": 19,
  "version": "0.8.0",
  "focused_workspace_id": "w3",
  "focused_tab_id": "w3:t1",
  "focused_pane_id": "w3:p1",
  "workspaces": [
    {"workspace_id": "w1", "label": "alpha-service", "agent_status": "unknown",
     "focused": false, "active_tab_id": "w1:t1", "number": 1, "pane_count": 1, "tab_count": 1},
    {"workspace_id": "w2", "label": "beta app", "agent_status": "working",
     "focused": false, "active_tab_id": "w2:t1", "number": 2, "pane_count": 1, "tab_count": 1},
    {"workspace_id": "w3", "label": "gamma docs", "agent_status": "idle",
     "focused": true, "active_tab_id": "w3:t1", "number": 3, "pane_count": 1, "tab_count": 1}
  ],
  "tabs": [
    {"tab_id": "w1:t1", "workspace_id": "w1", "agent_status": "unknown", "focused": false, "label": "1", "number": 1, "pane_count": 1},
    {"tab_id": "w2:t1", "workspace_id": "w2", "agent_status": "working", "focused": false, "label": "1", "number": 1, "pane_count": 1},
    {"tab_id": "w3:t1", "workspace_id": "w3", "agent_status": "idle", "focused": true, "label": "1", "number": 1, "pane_count": 1}
  ],
  "panes": [
    {"pane_id": "w1:p1", "workspace_id": "w1", "tab_id": "w1:t1",
     "agent_status": "unknown", "cwd": "/home/dev/alpha-service",
     "foreground_cwd": "/home/dev/alpha-service", "focused": false,
     "terminal_id": "term_a1"},
    {"pane_id": "w2:p1", "workspace_id": "w2", "tab_id": "w2:t1",
     "agent": "claude", "agent_status": "working", "cwd": "/home/dev/beta-app",
     "foreground_cwd": "/home/dev/beta-app", "focused": false,
     "terminal_id": "term_b1",
     "terminal_title": "◐ Add retry logic to the import job",
     "terminal_title_stripped": "Add retry logic to the import job"},
    {"pane_id": "w3:p1", "workspace_id": "w3", "tab_id": "w3:t1",
     "agent": "claude", "agent_status": "idle", "cwd": "/home/dev/gamma-docs",
     "foreground_cwd": "/home/dev/gamma-docs", "focused": true,
     "terminal_id": "term_c1",
     "terminal_title": "✳ Rewrite the onboarding guide",
     "terminal_title_stripped": "Rewrite the onboarding guide"}
  ],
  "agents": [
    {"pane_id": "w2:p1", "workspace_id": "w2", "tab_id": "w2:t1",
     "agent": "claude", "agent_status": "working", "cwd": "/home/dev/beta-app",
     "foreground_cwd": "/home/dev/beta-app", "focused": false,
     "terminal_id": "term_b1", "state_change_seq": 11,
     "terminal_title": "◐ Add retry logic to the import job",
     "terminal_title_stripped": "Add retry logic to the import job"},
    {"pane_id": "w3:p1", "workspace_id": "w3", "tab_id": "w3:t1",
     "agent": "claude", "agent_status": "idle", "cwd": "/home/dev/gamma-docs",
     "foreground_cwd": "/home/dev/gamma-docs", "focused": true,
     "terminal_id": "term_c1", "state_change_seq": 12,
     "terminal_title": "✳ Rewrite the onboarding guide",
     "terminal_title_stripped": "Rewrite the onboarding guide"}
  ],
  "layouts": []
}
```

- [ ] **Step 2: Create the no-focus fixture**

Create `tests/fixtures/snapshot_no_focus.json`:

```json
{
  "protocol": 19,
  "version": "0.8.0",
  "focused_workspace_id": null,
  "focused_tab_id": null,
  "focused_pane_id": null,
  "workspaces": [],
  "tabs": [],
  "panes": [],
  "agents": [],
  "layouts": []
}
```

- [ ] **Step 3: Write the failing attention tests**

Create `tests/test_state_attention.py`:

```python
"""Attention extraction from a herdr snapshot (spec §5).

Runs entirely offline against fixture snapshots — no socket, no aw-server.
The fixtures are the contract: a herdr snapshot-shape change breaks these.
"""
import json
from pathlib import Path

from aw_watcher_herdr.state import Attention, extract_attention

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_extracts_focused_workspace_and_agent():
    a = extract_attention(load("snapshot_basic.json"))
    assert a == Attention(
        workspace_label="gamma docs",
        workspace_id="w3",
        pane_id="w3:p1",
        title="Rewrite the onboarding guide",
        agent="claude",
        agent_status="idle",
    )


def test_returns_none_when_nothing_focused():
    assert extract_attention(load("snapshot_no_focus.json")) is None


def test_returns_none_when_focused_workspace_is_absent_from_the_list():
    snap = load("snapshot_basic.json")
    snap["workspaces"] = [w for w in snap["workspaces"] if w["workspace_id"] != "w3"]
    assert extract_attention(snap) is None


def test_pane_without_agent_yields_no_title_or_agent():
    # w1:p1 has no `agent` key and no terminal_title_stripped.
    snap = load("snapshot_basic.json")
    snap["focused_workspace_id"] = "w1"
    snap["focused_pane_id"] = "w1:p1"
    a = extract_attention(snap)
    assert a.workspace_label == "alpha-service"
    assert a.title is None
    assert a.agent is None
    assert a.agent_status == "unknown"


def test_missing_focused_pane_still_yields_the_workspace():
    snap = load("snapshot_basic.json")
    snap["focused_pane_id"] = None
    a = extract_attention(snap)
    assert a.workspace_label == "gamma docs"
    assert a.pane_id is None
    assert a.title is None


def test_empty_label_is_treated_as_missing():
    snap = load("snapshot_basic.json")
    for w in snap["workspaces"]:
        if w["workspace_id"] == "w3":
            w["label"] = ""
    assert extract_attention(snap) is None
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_state_attention.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aw_watcher_herdr.state'`.

- [ ] **Step 5: Write the attention half of `state.py`**

Create `aw_watcher_herdr/state.py`:

```python
"""Pure state machine over herdr session snapshots (spec §5, §6).

Deliberately I/O-free: no socket, no aw-server, no clock of its own (callers
pass `now`). Every rule here is therefore exercised by table-driven tests
against fixture snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Attention:
    """What the user is looking at right now (spec §5)."""

    workspace_label: str
    workspace_id: str
    pane_id: str | None
    title: str | None
    agent: str | None
    agent_status: str | None


def _index(items, key: str) -> dict:
    return {i[key]: i for i in (items or []) if isinstance(i, dict) and key in i}


def workspace_labels(snapshot: dict) -> dict[str, str]:
    """workspace_id -> label, for both attention and fleet lookups."""
    return {
        w["workspace_id"]: w.get("label") or ""
        for w in (snapshot.get("workspaces") or [])
        if isinstance(w, dict) and "workspace_id" in w
    }


def extract_attention(snapshot: dict) -> Attention | None:
    """Focused workspace + pane, or None when nothing is focused.

    Returning None (rather than guessing) is deliberate: the caller emits
    nothing, leaving an honest gap in the timeline.
    """
    ws_id = snapshot.get("focused_workspace_id")
    if not ws_id:
        return None
    label = workspace_labels(snapshot).get(ws_id)
    if not label:
        return None

    pane_id = snapshot.get("focused_pane_id")
    pane = _index(snapshot.get("panes"), "pane_id").get(pane_id) if pane_id else None
    pane = pane or {}

    return Attention(
        workspace_label=label,
        workspace_id=ws_id,
        pane_id=pane_id,
        # herdr already strips the animated spinner glyph, so no title
        # normalisation of our own is needed (spec §5).
        title=pane.get("terminal_title_stripped") or None,
        agent=pane.get("agent"),
        agent_status=pane.get("agent_status"),
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_state_attention.py -v`
Expected: PASS (6 passed).

- [ ] **Step 7: Commit**

```bash
git add aw_watcher_herdr/state.py tests/test_state_attention.py tests/fixtures/
git commit -m "feat(state): pure attention extraction from herdr snapshots"
```

---

## Task 4: Fleet run tracker

**Files:**
- Modify: `aw_watcher_herdr/state.py` (append; do not alter Task 3's code)
- Test: `tests/test_state_fleet.py`

**Interfaces:**
- Consumes: `workspace_labels(snapshot)` from Task 3.
- Produces:
  - `DEFAULT_FLEET_STATUSES: tuple[str, ...] = ("working", "blocked", "done")`
  - frozen dataclass `RunKey(pane_id: str, workspace_label: str, status: str, agent: str, cwd: str)`
  - frozen dataclass `CompletedRun(key: RunKey, title: str, start: datetime, end: datetime)` with property `duration_seconds -> float`
  - `FleetTracker(statuses=DEFAULT_FLEET_STATUSES, max_run_seconds=43200.0)` with `update(snapshot: dict, now: datetime) -> list[CompletedRun]`, `close_all(at: datetime) -> list[CompletedRun]`, and property `open_count -> int`.

- [ ] **Step 1: Write the failing fleet tests**

Create `tests/test_state_fleet.py`:

```python
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
    # snapshot_basic.json has one working and one idle agent.
    t = FleetTracker()
    t.update(load(), T0)
    assert t.open_count == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_state_fleet.py -v`
Expected: FAIL — `ImportError: cannot import name 'FleetTracker' from 'aw_watcher_herdr.state'`.

- [ ] **Step 3: Append the fleet tracker to `state.py`**

Append to `aw_watcher_herdr/state.py` (keep the existing imports; add `datetime` to them so the file's import block reads `from dataclasses import dataclass` and `from datetime import datetime`):

```python
# --- fleet tracking ---------------------------------------------------------

DEFAULT_FLEET_STATUSES = ("working", "blocked", "done")


@dataclass(frozen=True)
class RunKey:
    """Identity of a run. A change to ANY field closes the run and opens a new
    one. The title is deliberately absent: agents rewrite the terminal title as
    they work, and segmenting on it would shred every run (spec §6.1)."""

    pane_id: str
    workspace_label: str
    status: str
    agent: str
    cwd: str


@dataclass(frozen=True)
class CompletedRun:
    """A closed interval, ready to become one ActivityWatch event."""

    key: RunKey
    title: str
    start: datetime
    end: datetime

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()


class FleetTracker:
    """Turns consecutive snapshots into opened/closed agent runs.

    Holds no clock: callers pass `now`, which keeps sleep/suspend handling
    (spec §6.3) in the loop where the real clock lives, and keeps this class
    fully testable.
    """

    def __init__(self, statuses=DEFAULT_FLEET_STATUSES,
                 max_run_seconds: float = 43200.0):
        self._statuses = tuple(statuses)
        self._max_run_seconds = float(max_run_seconds)
        # pane_id -> (key, latest_title, started_at)
        self._open: dict[str, tuple[RunKey, str, datetime]] = {}

    @property
    def open_count(self) -> int:
        return len(self._open)

    def _desired(self, snapshot: dict) -> dict[str, tuple[RunKey, str]]:
        """pane_id -> (key, title) for every agent that should have an open run."""
        labels = workspace_labels(snapshot)
        desired: dict[str, tuple[RunKey, str]] = {}
        for agent in (snapshot.get("agents") or []):
            if not isinstance(agent, dict):
                continue
            pane_id = agent.get("pane_id")
            status = agent.get("agent_status")
            if not pane_id or status not in self._statuses:
                continue
            key = RunKey(
                pane_id=pane_id,
                workspace_label=labels.get(agent.get("workspace_id"), ""),
                status=status,
                agent=agent.get("agent") or "",
                cwd=agent.get("cwd") or "",
            )
            desired[pane_id] = (key, agent.get("terminal_title_stripped") or "")
        return desired

    def update(self, snapshot: dict, now: datetime) -> list[CompletedRun]:
        """Reconcile open runs against a snapshot; return the runs that closed."""
        desired = self._desired(snapshot)
        closed: list[CompletedRun] = []

        for pane_id, (key, title, start) in list(self._open.items()):
            incoming = desired.get(pane_id)
            expired = (now - start).total_seconds() >= self._max_run_seconds
            if incoming is None or incoming[0] != key or expired:
                closed.append(CompletedRun(key, title, start, now))
                del self._open[pane_id]
            else:
                # Same run continues; keep the most recent non-empty title.
                self._open[pane_id] = (key, incoming[1] or title, start)

        # Anything still desired but not open starts now. This also reopens the
        # runs just closed by a key change or the cap, so tracking continues.
        for pane_id, (key, title) in desired.items():
            if pane_id not in self._open:
                self._open[pane_id] = (key, title, now)

        return closed

    def close_all(self, at: datetime) -> list[CompletedRun]:
        """Close every open run at `at`. Used on shutdown and on a sleep gap."""
        closed = [CompletedRun(key, title, start, at)
                  for (key, title, start) in self._open.values()]
        self._open.clear()
        return closed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_state_fleet.py -v`
Expected: PASS (16 passed).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (config + herdr + attention + fleet).

- [ ] **Step 6: Commit**

```bash
git add aw_watcher_herdr/state.py tests/test_state_fleet.py
git commit -m "feat(state): agent run tracker with title-churn, rename, and cap rules"
```

---

## Task 5: ActivityWatch writers

**Files:**
- Create: `aw_watcher_herdr/emit.py`
- Test: `tests/test_emit.py`

**Interfaces:**
- Consumes: `Attention` and `CompletedRun` from Tasks 3-4.
- Produces:
  - `AttentionWriter(client, bucket_id: str, pulsetime: float, generic_terminal_label: str = "terminal")` with `write(attention: Attention, now: datetime) -> None`
  - `FleetWriter(client, bucket_id: str, max_pending: int = 10000)` with `write(runs: list[CompletedRun]) -> None`, `flush() -> None`, and property `pending_count -> int`

- [ ] **Step 1: Write the failing writer tests**

Create `tests/test_emit.py`:

```python
"""Bucket writers (spec §5, §6).

The two buckets use deliberately different write disciplines: the attention
bucket heartbeat-merges one timeline; the fleet bucket posts completed,
overlapping intervals through insert_events(), which has no retry mode of its
own — hence the buffer.
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_emit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aw_watcher_herdr.emit'`.

- [ ] **Step 3: Write `emit.py`**

Create `aw_watcher_herdr/emit.py`:

```python
"""ActivityWatch bucket writers (spec §5, §6).

Two buckets, two deliberately different disciplines:

  * Attention is a single timeline, so it uses heartbeat() with a pulsetime and
    lets aw-server merge consecutive identical events.
  * The fleet bucket records concurrent agents, so its events OVERLAP by design
    and cannot be heartbeated — heartbeat only merges against the bucket's last
    event, and interleaved agents would never match, fragmenting the timeline.
    It posts completed events with explicit durations instead.

aw-client's insert_events() has no queued/retry mode (unlike heartbeat), so
FleetWriter keeps its own bounded buffer and retries on the next flush.
"""

from __future__ import annotations

import logging
from datetime import datetime

from aw_core.models import Event

from .state import Attention, CompletedRun

logger = logging.getLogger(__name__)

DEFAULT_MAX_PENDING = 10_000


class AttentionWriter:
    """Heartbeats the focused workspace/pane into a `currentwindow` bucket."""

    def __init__(self, client, bucket_id: str, pulsetime: float,
                 generic_terminal_label: str = "terminal"):
        self._client = client
        self._bucket = bucket_id
        self._pulsetime = pulsetime
        self._generic = generic_terminal_label

    def write(self, attention: Attention, now: datetime) -> None:
        data = {
            # app/title rather than custom keys: aw merges heartbeats on these
            # and its categorization rules match them (spec §5).
            "app": attention.workspace_label,
            "title": attention.title or self._generic,
            "agent": attention.agent,
            "agent_status": attention.agent_status,
            "workspace_id": attention.workspace_id,
            "pane_id": attention.pane_id,
        }
        self._client.heartbeat(self._bucket, Event(timestamp=now, data=data),
                               pulsetime=self._pulsetime, queued=True)


class FleetWriter:
    """Posts completed, overlapping agent-run intervals."""

    def __init__(self, client, bucket_id: str,
                 max_pending: int = DEFAULT_MAX_PENDING):
        self._client = client
        self._bucket = bucket_id
        self._max_pending = max_pending
        self._pending: list[Event] = []

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def write(self, runs) -> None:
        for run in runs:
            if run.duration_seconds <= 0:
                continue
            self._pending.append(Event(
                timestamp=run.start,
                duration=run.end - run.start,
                data={
                    "app": run.key.workspace_label,
                    "title": run.title or "",
                    "status": run.key.status,
                    "agent": run.key.agent,
                    "cwd": run.key.cwd,
                    "pane_id": run.key.pane_id,
                },
            ))
        self.flush()

    def flush(self) -> None:
        """Try to post everything buffered; keep it buffered on failure."""
        if not self._pending:
            return
        try:
            self._client.insert_events(self._bucket, list(self._pending))
        except Exception as exc:  # noqa: BLE001 - any transport failure retries
            if len(self._pending) > self._max_pending:
                dropped = len(self._pending) - self._max_pending
                self._pending = self._pending[-self._max_pending:]
                logger.warning(
                    "fleet buffer full; dropped %s oldest event(s)", dropped)
            logger.debug("fleet flush failed, %s event(s) pending: %s",
                         len(self._pending), exc)
            return
        self._pending = []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_emit.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add aw_watcher_herdr/emit.py tests/test_emit.py
git commit -m "feat(emit): heartbeat attention writer + buffered overlapping fleet writer"
```

---

## Task 6: Poll loop and entry point

**Files:**
- Create: `aw_watcher_herdr/main.py`
- Modify: `aw_watcher_herdr/__main__.py` (replace the `main()` body and the `--snapshot` stub from Task 1)
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: `HerdrClient`, `HerdrError`, `HerdrUnavailable` (Task 2); `extract_attention`, `FleetTracker` (Tasks 3-4); `AttentionWriter`, `FleetWriter` (Task 5); `Config`, `CLIENT_NAME` (Task 1).
- Produces: status constants `OK`, `NO_SOURCE`, `HERDR_ERROR`; `WARN_AFTER_CONSECUTIVE_ERRORS = 10`; and `run(herdr_client, attention_writer, fleet_writer, tracker, config, stop=None) -> None`.

- [ ] **Step 1: Write the failing loop tests**

Create `tests/test_loop.py`:

```python
"""Poll-loop behaviour: gap handling, error escalation, and shutdown
(spec §6.3). The loop is driven with a fake herdr client and fake writers, so
no socket and no aw-server are involved."""
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


class FakeHerdr:
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

    herdr = FakeHerdr(script)
    aw = FakeAttentionWriter()
    fleet = FakeFleetWriter()
    tracker = FleetTracker(statuses=config.fleet_statuses,
                           max_run_seconds=config.max_run_seconds)
    state = {"n": 0}

    def stop():
        state["n"] += 1
        return state["n"] > ticks

    loop.run(herdr, aw, fleet, tracker, config, stop=stop)
    return herdr, aw, fleet, tracker


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
    # clock_step of 60s with poll_interval 2s and gap_factor 3 => every tick
    # after the first looks like a suspend gap.
    _, _, fleet, _ = drive(monkeypatch, [snap("working")], ticks=3,
                           clock_step=60.0)
    assert len(fleet.runs) >= 1
    # Each closed run is bounded by the last good poll, never the full gap.
    assert all(r.duration_seconds <= 60 for r in fleet.runs)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aw_watcher_herdr.main'`.

- [ ] **Step 3: Write `main.py`**

Create `aw_watcher_herdr/main.py`:

```python
"""Poll loop: read herdr snapshots, emit attention heartbeats and agent runs.

Like its cmux predecessor the watcher OVER-EMITS the attention bucket: herdr
has no notion of "is my window frontmost", so the focused workspace is reported
regardless. Correctness is restored at query time by intersecting with the
window and AFK watchers (spec §7).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from . import state
from .herdr import HerdrError, HerdrUnavailable

logger = logging.getLogger(__name__)

# Poll outcomes.
OK = "ok"
NO_SOURCE = "no_source"      # herdr not running — a legitimate gap
HERDR_ERROR = "herdr_error"  # herdr answered, but not usably

WARN_AFTER_CONSECUTIVE_ERRORS = 10

_HINT_HERDR_ERROR = (
    "herdr's socket is reachable but is not returning usable snapshots. Its API "
    "protocol may have changed in an update. Check `herdr api snapshot` by hand "
    "and file an issue. (consecutive failures: %s; last error: %s)"
)


def _now() -> datetime:
    """Indirected so tests can drive a deterministic clock."""
    return datetime.now(timezone.utc)


def run(herdr_client, attention_writer, fleet_writer, tracker, config,
        stop=None) -> None:
    """Poll until `stop()` returns True (or forever if not given)."""
    stop = stop or (lambda: False)
    gap_threshold = config.gap_factor * config.poll_interval
    last_poll: datetime | None = None
    consecutive = 0
    warned = False

    logger.info("aw-watcher-herdr started (poll=%ss, pulsetime=%ss, fleet=%s)",
                config.poll_interval, config.pulsetime, config.fleet_enabled)

    while not stop():
        time.sleep(config.poll_interval)
        now = _now()

        # A gap far larger than the poll interval means the machine slept.
        # Close open runs at the last good poll rather than recording hours of
        # "working" that never happened (spec §6.3).
        if (last_poll is not None and gap_threshold > 0
                and (now - last_poll).total_seconds() > gap_threshold):
            logger.info("poll gap of %.1fs (sleep/suspend); closing open runs",
                        (now - last_poll).total_seconds())
            fleet_writer.write(tracker.close_all(last_poll))
            last_poll = None

        try:
            snapshot = herdr_client.snapshot()
        except HerdrUnavailable:
            # Normal state: herdr simply isn't running. Emit nothing, warn
            # about nothing, and leave an honest gap.
            if tracker.open_count:
                fleet_writer.write(tracker.close_all(last_poll or now))
            last_poll = None
            consecutive = 0
            warned = False
            logger.debug("herdr not running; skipping tick (gap)")
            continue
        except HerdrError as exc:
            consecutive += 1
            if consecutive >= WARN_AFTER_CONSECUTIVE_ERRORS and not warned:
                logger.warning(_HINT_HERDR_ERROR, consecutive, exc)
                warned = True
            continue

        consecutive = 0
        warned = False

        attention = state.extract_attention(snapshot)
        if attention is not None:
            attention_writer.write(attention, now)

        if config.fleet_enabled:
            fleet_writer.write(tracker.update(snapshot, now))

        last_poll = now
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_loop.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Wire up `__main__.py`**

In `aw_watcher_herdr/__main__.py`, replace the import block and the `main()` function written in Task 1.

Replace the imports at the top with:

```python
from __future__ import annotations

import argparse
import json
import logging
import signal
import socket as socketlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aw_client import ActivityWatchClient
from aw_core.config import load_config_toml
from aw_core.log import setup_logging

from . import __version__
from . import main as loop
from .emit import AttentionWriter, FleetWriter
from .herdr import HerdrClient, HerdrUnavailable
from .state import FleetTracker
```

Replace the whole `main()` function with:

```python
def run_snapshot(config: Config) -> int:
    """Print herdr's live snapshot as JSON (for capturing test fixtures).

    Remember to replace real workspace names with synthetic ones before
    committing anything derived from this.
    """
    try:
        snap = HerdrClient(config.socket_path).snapshot()
    except HerdrUnavailable as exc:
        print(f"herdr is not running: {exc}")
        return 1
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args)

    # One-shot diagnostic mode prints to stdout and exits; handle it before
    # setup_logging so it doesn't spin up a rotating log file.
    if args.snapshot:
        return run_snapshot(config)

    setup_logging(CLIENT_NAME, testing=args.testing, verbose=args.verbose,
                  log_stderr=True, log_file=True)

    client = ActivityWatchClient(CLIENT_NAME, testing=args.testing)
    hostname = client.client_hostname or socketlib.gethostname()
    suffix = "-testing" if args.testing else ""
    attention_bucket = f"{CLIENT_NAME}_{hostname}{suffix}"
    fleet_bucket = f"{CLIENT_NAME}-agents_{hostname}{suffix}"

    # currentwindow reuses aw's window-activity views and categorization (§5).
    client.create_bucket(attention_bucket, event_type="currentwindow", queued=True)
    if config.fleet_enabled:
        client.create_bucket(fleet_bucket, event_type="app.agent.activity",
                             queued=True)

    herdr_client = HerdrClient(config.socket_path)
    tracker = FleetTracker(statuses=config.fleet_statuses,
                           max_run_seconds=config.max_run_seconds)
    attention_writer = AttentionWriter(client, attention_bucket, config.pulsetime,
                                       config.generic_terminal_label)
    fleet_writer = FleetWriter(client, fleet_bucket)

    # SIGTERM (launchd stop) must close open runs, or their intervals are lost.
    stopping = {"flag": False}

    def _stop(_signum, _frame):
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, _stop)

    with client:
        try:
            loop.run(herdr_client, attention_writer, fleet_writer, tracker,
                     config, stop=lambda: stopping["flag"])
        except KeyboardInterrupt:
            logger.info("interrupted; shutting down")
        finally:
            if config.fleet_enabled:
                fleet_writer.write(tracker.close_all(datetime.now(timezone.utc)))
                fleet_writer.flush()
    return 0
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (all five test modules).

- [ ] **Step 7: Verify `--snapshot` against live herdr**

Run: `.venv/bin/python -m aw_watcher_herdr --snapshot | head -5`
Expected: prints JSON beginning with `{` and containing `"protocol"`.

- [ ] **Step 8: Run the watcher live against the aw test server**

```bash
/Applications/ActivityWatch.app/Contents/MacOS/aw-server --testing >/tmp/aw-test.log 2>&1 &
sleep 3
.venv/bin/python -m aw_watcher_herdr --testing --verbose --poll-interval 1 &
WATCHER=$!
sleep 10
kill $WATCHER
curl -s "http://localhost:5666/api/0/buckets/" | .venv/bin/python -m json.tool | grep aw-watcher-herdr
```
Expected: both `aw-watcher-herdr_<host>-testing` and `aw-watcher-herdr-agents_<host>-testing` appear. Stop the test server afterwards: `pkill -f "aw-server --testing"`.

- [ ] **Step 9: Commit**

```bash
git add aw_watcher_herdr/main.py aw_watcher_herdr/__main__.py tests/test_loop.py
git commit -m "feat: poll loop with sleep-gap handling, plus entry-point wiring"
```

---

## Task 7: Packaging, install scripts, and documentation

**Files:**
- Create: `scripts/install.sh`, `scripts/uninstall.sh`, `scripts/verify.sh`
- Create: `packaging/com.activitywatch.aw-watcher-herdr.plist`
- Create: `aw-watcher-herdr.toml.example`, `Makefile`
- Modify: `README.md` (replace the Task-0 stub)

**Interfaces:**
- Consumes: the `aw-watcher-herdr` console script from Task 1's `pyproject.toml`.
- Produces: no Python interfaces.

- [ ] **Step 1: Write the installer**

Create `scripts/install.sh` (then `chmod +x scripts/install.sh`). Far simpler than the cmux predecessor: no `.app` bundle, no codesign, no Accessibility prompt, no manual System Settings step.

```bash
#!/usr/bin/env bash
#
# One-command install for aw-watcher-herdr as a macOS launchd LaunchAgent.
#
# What it does:
#   1. Creates a self-contained venv at ~/.local/share/aw-watcher-herdr/venv
#      and installs aw-watcher-herdr into it (no pipx / global state needed).
#   2. Installs + loads a LaunchAgent (auto-starts at login, survives
#      ActivityWatch updates, writes nothing into the AW app).
#
# Unlike aw-watcher-cmux this needs NO macOS Accessibility permission: herdr's
# socket API answers from any process owned by the user.
#
# Re-running is safe (idempotent): it reinstalls and reloads.
set -euo pipefail

if [ "$(uname)" != "Darwin" ]; then
  echo "This installer targets macOS launchd. On other platforms, run" >&2
  echo "aw-watcher-herdr under your own supervisor." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$HOME/.local/share/aw-watcher-herdr"
VENV="$APP_DIR/venv"
LABEL="com.activitywatch.aw-watcher-herdr"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/activitywatch"
EXEC="$VENV/bin/aw-watcher-herdr"

echo "==> Creating venv at $VENV"
mkdir -p "$APP_DIR" "$LOG_DIR" "$HOME/Library/LaunchAgents"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
echo "==> Installing aw-watcher-herdr from $REPO_ROOT"
"$VENV/bin/pip" install --quiet "$REPO_ROOT"

echo "==> Verifying the install"
"$EXEC" --version

echo "==> Writing LaunchAgent $PLIST"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$EXEC</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG_DIR/aw-watcher-herdr.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/aw-watcher-herdr.log</string>
</dict></plist>
PLIST

echo "==> Loading the LaunchAgent"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

cat <<EOF

Installed and started. No permission prompts needed.

Check it is reading herdr:

    $EXEC --snapshot | head -3

Logs: $LOG_DIR/aw-watcher-herdr.log
Restart: launchctl kickstart -k gui/$(id -u)/$LABEL
Uninstall: scripts/uninstall.sh
EOF
```

- [ ] **Step 2: Write the uninstaller**

Create `scripts/uninstall.sh` (then `chmod +x scripts/uninstall.sh`):

```bash
#!/usr/bin/env bash
#
# Remove the aw-watcher-herdr LaunchAgent and its venv.
# Recorded ActivityWatch data is NOT touched.
set -euo pipefail

LABEL="com.activitywatch.aw-watcher-herdr"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP_DIR="$HOME/.local/share/aw-watcher-herdr"

echo "==> Stopping the LaunchAgent"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

echo "==> Removing $PLIST"
rm -f "$PLIST"

echo "==> Removing $APP_DIR"
rm -rf "$APP_DIR"

cat <<EOF

Uninstalled. Your recorded data is untouched; the buckets
aw-watcher-herdr_<host> and aw-watcher-herdr-agents_<host> remain in
ActivityWatch. Delete them from the aw web UI if you want them gone.
EOF
```

- [ ] **Step 3: Write the end-to-end verify script**

Create `scripts/verify.sh` (then `chmod +x scripts/verify.sh`):

```bash
#!/usr/bin/env bash
#
# End-to-end verification against an ISOLATED test server (aw-server --testing
# on port 5666). Never touches your real ActivityWatch data on port 5600.
#
# Requires herdr to be running, with at least one workspace open.
set -euo pipefail

PORT=5666
HOST="$(hostname)"
ATTENTION="aw-watcher-herdr_${HOST}-testing"
FLEET="aw-watcher-herdr-agents_${HOST}-testing"
DURATION="${DURATION:-8}"
PY="${PY:-python3}"

AWS="${AW_SERVER:-}"
if [ -z "$AWS" ]; then
  if [ -x "/Applications/ActivityWatch.app/Contents/MacOS/aw-server" ]; then
    AWS="/Applications/ActivityWatch.app/Contents/MacOS/aw-server"
  elif command -v aw-server >/dev/null 2>&1; then
    AWS="$(command -v aw-server)"
  else
    echo "ERROR: aw-server not found. Install ActivityWatch or set AW_SERVER=/path" >&2
    exit 1
  fi
fi
echo "aw-server : $AWS"
echo "python    : $($PY --version 2>&1)"
echo "buckets   : $ATTENTION, $FLEET"

STARTED_SERVER=0
WATCHER_PID=""
cleanup() {
  [ -n "$WATCHER_PID" ] && kill "$WATCHER_PID" 2>/dev/null || true
  if [ "$STARTED_SERVER" = "1" ]; then
    pkill -f "aw-server --testing" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if curl -s -m 1 "http://localhost:${PORT}/api/0/info" >/dev/null 2>&1; then
  echo "test server already running on :${PORT}"
else
  echo "starting aw-server --testing on :${PORT} ..."
  "$AWS" --testing >/tmp/aw-herdr-verify-server.log 2>&1 &
  STARTED_SERVER=1
  for _ in $(seq 1 20); do
    curl -s -m 1 "http://localhost:${PORT}/api/0/info" >/dev/null 2>&1 && break
    sleep 0.5
  done
  curl -s -m 1 "http://localhost:${PORT}/api/0/info" >/dev/null 2>&1 \
    || { echo "ERROR: test server did not come up"; exit 1; }
fi

echo "running watcher for ${DURATION}s (poll=1s) ..."
"$PY" -m aw_watcher_herdr --testing --verbose --poll-interval 1 \
  >/tmp/aw-herdr-verify-watcher.log 2>&1 &
WATCHER_PID=$!
sleep "$DURATION"
kill -TERM "$WATCHER_PID" 2>/dev/null || true
wait "$WATCHER_PID" 2>/dev/null || true
WATCHER_PID=""

if grep -q "herdr not running" /tmp/aw-herdr-verify-watcher.log; then
  echo
  echo "FAIL: herdr is not running. Start herdr and re-run."
  exit 2
fi

count() {
  curl -s "http://localhost:${PORT}/api/0/buckets/$1/events?limit=100" \
    | "$PY" -c 'import sys,json; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0
}

A_COUNT=$(count "$ATTENTION")
F_COUNT=$(count "$FLEET")

echo
echo "attention events: ${A_COUNT:-0}"
echo "fleet events    : ${F_COUNT:-0}  (0 is OK if no agent was working)"
if [ "${A_COUNT:-0}" -gt 0 ]; then
  echo "PASS. Sample attention event:"
  curl -s "http://localhost:${PORT}/api/0/buckets/${ATTENTION}/events?limit=1" \
    | "$PY" -m json.tool
else
  echo "FAIL: no attention events. See /tmp/aw-herdr-verify-watcher.log"
  exit 2
fi
```

- [ ] **Step 4: Write the plist template and config example**

Create `packaging/com.activitywatch.aw-watcher-herdr.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!--
  launchd service for aw-watcher-herdr (spec §10).

  Reads herdr's local socket API, so it needs NO Accessibility permission —
  just the user's own GUI login session. scripts/install.sh generates this file
  with real paths filled in; this copy is the hand-editable template.

  Install:
    1. Replace CHANGE_ME below with your home directory.
    2. cp this file to ~/Library/LaunchAgents/
    3. launchctl bootstrap gui/$(id -u) \
         ~/Library/LaunchAgents/com.activitywatch.aw-watcher-herdr.plist
-->
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.activitywatch.aw-watcher-herdr</string>

    <key>ProgramArguments</key>
    <array>
      <string>/Users/CHANGE_ME/.local/share/aw-watcher-herdr/venv/bin/aw-watcher-herdr</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <!--
      Restart only on a crash (non-zero exit), not on a clean exit. With a bare
      <true/> a misconfigured path would respawn in a tight loop; ThrottleInterval
      caps the respawn rate as a backstop.
    -->
    <key>KeepAlive</key>
    <dict>
      <key>SuccessfulExit</key>
      <false/>
    </dict>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>/Users/CHANGE_ME/Library/Logs/activitywatch/aw-watcher-herdr.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/CHANGE_ME/Library/Logs/activitywatch/aw-watcher-herdr.log</string>

    <key>ProcessType</key>
    <string>Background</string>
  </dict>
</plist>
```

Create `aw-watcher-herdr.toml.example`:

```toml
# Copy to:
#   ~/Library/Application Support/activitywatch/aw-watcher-herdr/aw-watcher-herdr.toml
# CLI flags override these values.

[aw-watcher-herdr]
# herdr's API socket. Defaults to ~/.config/herdr/herdr.sock.
# socket_path = "~/.config/herdr/herdr.sock"

poll_interval = 2.0            # seconds between snapshots
pulsetime = 5.0                # heartbeat merge window (attention bucket)
generic_terminal_label = "terminal"   # title for panes with no agent

# --- agent fleet bucket ---
fleet_enabled = true
# Which herdr agent states become intervals. `idle` is the resting state and
# would dwarf everything else; `unknown` does not prove completion.
fleet_statuses = ["working", "blocked", "done"]
max_run_seconds = 43200.0      # hard cap on one run (12h), guards a wedged watcher
gap_factor = 3.0               # gap > gap_factor x poll_interval => machine slept

# --- used only by the README query recipes ---
window_app = "Ghostty"
window_title = "herdr"
```

Create `Makefile`:

```makefile
.PHONY: install test verify clean

install:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -e ".[dev]"

test:
	.venv/bin/pytest -q

verify:
	PY=.venv/bin/python scripts/verify.sh

clean:
	rm -rf .venv build dist *.egg-info .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
```

- [ ] **Step 5: Write the README**

Replace `README.md` entirely:

````markdown
# aw-watcher-herdr

An [ActivityWatch](https://activitywatch.net/) watcher for
[herdr](https://herdr.dev), the terminal workspace manager for AI coding agents.

It records two things:

- **Attention** — which herdr workspace and agent session you are looking at.
- **Agent fleet** — what every agent is doing *concurrently*, on which project,
  including how long agents sit blocked waiting on you and how long finished
  work goes unnoticed.

It reads herdr's local socket API, so it needs **no macOS Accessibility
permission** and runs fine as a detached launchd agent.

## How it works

```
   poll (2s)   ┌────────────────────────────────┐   heartbeat   ┌───────────┐
   ┌────────►  │      aw-watcher-herdr          │──────────────►│ aw-server │
   │           │  herdr.py  session.snapshot    │   intervals   │   :5600   │
 herdr.sock    │  state.py  diff → transitions  │──────────────►└───────────┘
   ◄───────────┤  emit.py   two bucket writers  │
               └────────────────────────────────┘
```

## Buckets

### `aw-watcher-herdr_<host>` — attention

Type `currentwindow`, one event at a time, heartbeat-merged.

| Field | Example | Notes |
|---|---|---|
| `app` | `beta app` | Focused workspace label (the project dimension) |
| `title` | `Add retry logic to the import job` | Focused pane's agent task, or `terminal` |
| `agent` | `claude` | Agent kind, or null |
| `agent_status` | `working` | herdr's own classification |
| `workspace_id` / `pane_id` | `w2` / `w2:p1` | Opaque ids, diagnostic |

Events are keyed on the workspace **label**, not the working directory: two
workspaces can share one `cwd` while representing different contexts, and
merging them would be unrecoverable. The cost is that renaming a workspace
splits its timeline; fix that with a categorization rule if it bites.

### `aw-watcher-herdr-agents_<host>` — agent fleet

Type `app.agent.activity`. One event per agent per status-run, with real start
and duration. **These events overlap by design** — that is what concurrency
looks like.

| Field | Example |
|---|---|
| `app` | `beta app` |
| `title` | `Add retry logic to the import job` |
| `status` | `working` / `blocked` / `done` |
| `agent` | `claude` |
| `cwd` | `/home/dev/beta-app` |
| `pane_id` | `w2:p1` |

`idle` and `unknown` are not recorded: `idle` is the resting state and would
dwarf everything else, and `unknown` does not prove completion.

`done` is recorded as a real duration on purpose. herdr clears it the moment you
focus the tab, so its length is exactly **how long finished work sat unnoticed**.

## Install

```bash
git clone https://github.com/simensollie/aw-watcher-herdr
cd aw-watcher-herdr
./scripts/install.sh
```

This creates a self-contained venv at `~/.local/share/aw-watcher-herdr` and
installs a launchd LaunchAgent that starts at login and survives ActivityWatch
updates. There are no permission prompts and no manual System Settings step.

Uninstall any time with `./scripts/uninstall.sh`.

### Check it is working

```bash
~/.local/share/aw-watcher-herdr/venv/bin/aw-watcher-herdr --snapshot | head -3
```

## Queries

### Active herdr time

The watcher over-emits: herdr has no "am I frontmost" concept, so the focused
workspace is reported whether or not you are looking at it. Recover real
attention by intersecting with the window and AFK watchers — the same separation
of concerns ActivityWatch already uses for AFK.

```python
afk      = flood(query_bucket(find_bucket("aw-watcher-afk_")))
window   = flood(query_bucket(find_bucket("aw-watcher-window_")))
herdr    = flood(query_bucket(find_bucket("aw-watcher-herdr_")))
not_afk  = filter_keyvals(afk, "status", ["not-afk"])
in_herdr = filter_keyvals(window, "app", ["Ghostty"])
events   = filter_period_intersect(herdr, in_herdr)
events   = filter_period_intersect(events, not_afk)
RETURN   = merge_events_by_keys(events, ["app", "title"])
```

### Agent-hours per project

Deliberately **not** gated on AFK or frontmost — agent work happening while you
are away is the whole point.

```python
agents = query_bucket(find_bucket("aw-watcher-herdr-agents_"))
RETURN = merge_events_by_keys(filter_keyvals(agents, "status", ["working"]), ["app"])
```

Swap `working` for `blocked` to see how long agents waited on you, or `done` to
see how long completed work sat unnoticed.

> **Never apply `flood()` to the fleet bucket.** `flood` closes gaps by
> stretching events within a single timeline; applied to deliberately
> overlapping events it corrupts them.

> **Known limitation:** ActivityWatch's stock Activity view assumes one
> non-overlapping timeline per bucket, so the fleet bucket renders stacked and
> its "top apps" totals exceed wall-clock time. That is inherent to recording
> concurrency. Read it through the queries above.

## Configuration

Config lives at
`~/Library/Application Support/activitywatch/aw-watcher-herdr/aw-watcher-herdr.toml`.
See [`aw-watcher-herdr.toml.example`](aw-watcher-herdr.toml.example). CLI flags
override the file.

| Key | Default | Meaning |
|---|---|---|
| `socket_path` | `~/.config/herdr/herdr.sock` | herdr API socket |
| `poll_interval` | `2.0` | Seconds between snapshots |
| `pulsetime` | `5.0` | Heartbeat merge window (attention bucket) |
| `generic_terminal_label` | `terminal` | Title for panes with no agent |
| `fleet_enabled` | `true` | Emit the agent-fleet bucket at all |
| `fleet_statuses` | `["working", "blocked", "done"]` | Statuses that open a run |
| `max_run_seconds` | `43200` | Hard cap on a single run |
| `gap_factor` | `3.0` | Multiple of `poll_interval` treated as a sleep gap |

Flags: `--testing`, `--verbose`, `--socket-path`, `--poll-interval`,
`--pulsetime`, `--generic-terminal-label`, `--no-fleet`, `--snapshot`.

## Development

```bash
make install      # editable venv install + dev deps
make test         # unit tests; no herdr or aw-server needed
make verify       # end-to-end against aw-server --testing on :5666
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Permanent gap in the timeline | herdr is not running. That is recorded as an honest gap, not an error. |
| No buckets created | aw-server is not reachable. Attention heartbeats are queued and flush on reconnect; fleet events are buffered in memory (up to 10 000) and retried. |
| Fleet totals exceed 24 h in a day | Expected. Agents run concurrently; see the known limitation above. |
| Timeline splits after renaming a workspace | Expected. `app` is the workspace label; add a categorization rule to merge the two names. |

## Related

[aw-watcher-cmux](https://github.com/simensollie/aw-watcher-cmux) does the
equivalent job for cmux and is maintained separately.

## License

[MPL-2.0](LICENSE), matching the ActivityWatch ecosystem.
````

- [ ] **Step 6: Make the scripts executable and run the full suite**

```bash
chmod +x scripts/install.sh scripts/uninstall.sh scripts/verify.sh
.venv/bin/pytest -q
```
Expected: PASS.

- [ ] **Step 7: Run the end-to-end verification**

Run: `PY=.venv/bin/python DURATION=8 scripts/verify.sh`
Expected: `PASS.` with a non-zero attention event count. A fleet count of 0 is acceptable if no agent happened to be working during the window; to exercise it, start an agent in herdr and re-run.

- [ ] **Step 8: Mark the spec implemented and commit**

In `docs/superpowers/specs/2026-08-12-herdr-activitywatch-watcher-design.md`, change `**Status:** Approved (brainstorm)` to `**Status:** Implemented`.

```bash
git add scripts/ packaging/ Makefile README.md aw-watcher-herdr.toml.example docs/
git commit -m "feat: launchd installer, verify script, and full documentation"
```

- [ ] **Step 9: Install for real and confirm it records**

```bash
./scripts/install.sh
sleep 20
curl -s "http://localhost:5600/api/0/buckets/" | python3 -c "
import json,sys
print([k for k in json.load(sys.stdin) if 'herdr' in k])
"
```
Expected: both herdr buckets are listed. Check the log at
`~/Library/Logs/activitywatch/aw-watcher-herdr.log` if not.

---

## Self-review notes

**Spec coverage.** §1-2 problem/outcomes → no task (context). §3 data source →
Task 2. §4 architecture → Tasks 1-6; §4.1 poll-not-subscribe → Task 6 loop;
§4.2 identity key → Task 3 `workspace_labels` + Task 4 `RunKey`, tested by
`test_workspace_rename_closes_and_reopens_the_run`. §5 attention bucket → Tasks
3 and 5. §6 fleet bucket → Tasks 4 and 5; §6.1 lifecycle → Task 4 tests; §6.2
`done` as duration → `test_done_is_tracked_as_a_real_run`; §6.3 failure modes →
Task 4 cap test, Task 6 gap and unavailable tests, Task 6 Step 5 SIGTERM
handler. §7 query recipes → Task 7 README. §8 config → Task 1. §9 testing →
every task, fixtures in Task 3. §10 deployment → Task 7. §11 out of scope → no
tasks, correct. §12 open questions → no tasks, correct (deferred).

**Deviation from the spec worth noting.** Spec §4 lists dependencies as
"`aw-client` and `tomli`". `tomli` is not needed — `aw_core.config.load_config_toml`
handles TOML parsing and ships with `aw-client`. The Global Constraints section
above is authoritative: `aw-client>=0.5.13` only.

**Type consistency.** `Attention` is constructed only in `state.extract_attention`
and consumed in `emit.AttentionWriter.write` and `test_state_attention.py`, with
the same six fields throughout. `RunKey` field order
`(pane_id, workspace_label, status, agent, cwd)` is identical in Task 4's
definition, its positional construction in `test_state_fleet.py`, and its
attribute access in `emit.FleetWriter.write`. `CompletedRun.duration_seconds` is
a property (no parentheses) at every use site. `FleetTracker.__init__` takes
`statuses` and `max_run_seconds`, matching both `__main__.py` and `test_loop.py`.
`loop.run` takes `(herdr_client, attention_writer, fleet_writer, tracker, config,
stop=None)` in both `test_loop.py` and `__main__.py`.

**Deliberate test-seam note.** `main._now()` exists solely so `test_loop.py` can
drive a deterministic clock; production code calls it with no arguments and it
returns `datetime.now(timezone.utc)`.
