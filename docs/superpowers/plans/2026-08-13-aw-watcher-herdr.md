# aw-watcher-herdr Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Supersedes** `2026-08-12-aw-watcher-herdr.md`, which was written against the
spec before its 2026-08-13 portability amendment.

**Goal:** Build an ActivityWatch watcher that records, from herdr's local API,
both which herdr workspace the user is attending to and what every concurrently
running coding agent is doing.

**Architecture:** A snapshot source reads `session.snapshot` from herdr every 2 s,
over a Unix socket on POSIX or the `herdr api snapshot` CLI on Windows. A pure,
I/O-free state machine turns consecutive snapshots into (a) a single
focused-workspace event and (b) open/close transitions for per-agent runs. Two
writers with different disciplines push to aw-server: the attention bucket
heartbeat-merges one timeline, the fleet bucket posts completed, deliberately
overlapping intervals.

**Tech Stack:** Python 3.10+, `aw-client` (brings `aw-core`), pytest. No
`pyobjc`, no macOS Accessibility permission.

**Spec:** `docs/superpowers/specs/2026-08-12-herdr-activitywatch-watcher-design.md`
(amended 2026-08-13)

## Global Constraints

- **Python** `>=3.10`. Use `from __future__ import annotations` in every module so `X | None` annotations work.
- **Runtime dependencies:** `aw-client>=0.5.13` only. Do not add `pyobjc`, `tomli`, or anything else; `aw-core` (which provides `load_config_toml`, `setup_logging`, `Event`) ships with `aw-client`.
- **Client / bucket naming:** client name is `aw-watcher-herdr`. Buckets are `aw-watcher-herdr_<host>` (attention) and `aw-watcher-herdr-agents_<host>` (fleet), each gaining a `-testing` suffix under `--testing`.
- **No real customer data anywhere.** Fixtures, docstrings, README examples and commit messages use synthetic names only (`alpha-service`, `beta app`, `gamma docs`, `beta client-one`). The predecessor repo had to fix this retroactively in commit `ccb06b4`; do not repeat it.
- **Language:** English throughout — code, comments, docs, commit messages.
- **Timestamps:** all datetimes are timezone-aware UTC (`datetime.now(timezone.utc)`). Never use naive datetimes; aw-server rejects them.
- **Platform rules** (spec §4.3):
  - Nothing outside `herdr.py`, `lock.py`, `scripts/` and `__main__.default_window_apps()` may branch on the operating system. Those four sites are exhaustive and normative (spec §4.3 tabulates them); `default_window_apps()` is there because spec §7.1 defines a per-platform default for `window_app`, and it is a static value lookup that touches no platform-specific API. Adding a fifth site is a spec change, not an implementation detail.
  - `socket.AF_UNIX` must only ever be referenced *inside* a method body. It does not exist on Windows, so a module-level reference would break the import there.
  - macOS is the only platform verified end to end. Do not write docs or docstrings implying Linux or Windows have been tested.
- **herdr protocol facts** (verified against herdr 0.8.0, protocol 19) — encode all five:
  1. `params` is **required** on every socket request, even when empty (`{}`).
  2. The server answers **one request per connection**, then closes it. Every socket call opens a fresh connection.
  3. Responses echo the request `id`. Errors arrive as `{"id": "", "error": {"code": ..., "message": ...}}`.
  4. `herdr api snapshot` returns the **identical envelope** and exits 1 with `error.code == "server_not_running"` when no server is up.
  5. `terminal_title_stripped` removes `✳` (U+2733) but **not** `◐` (U+25D0). The watcher strips leading symbol glyphs itself (spec §5.1).

---

## File structure

| File | Responsibility | Action | Task |
|---|---|---|---|
| `pyproject.toml` | packaging, deps, entry point | create | 1 |
| `aw_watcher_herdr/__init__.py` | `__version__` | create | 1 |
| `aw_watcher_herdr/__main__.py` | `Config`, `load_config`, `parse_args`, `main` | create 1, extend 6, extend 7 |
| `tests/test_config.py` | config defaults + CLI override precedence | create | 1 |
| `aw_watcher_herdr/herdr.py` | snapshot sources: envelope, socket, CLI, resolution | create | 2 |
| `tests/test_herdr.py` | both sources against fakes | create | 2 |
| `aw_watcher_herdr/state.py` | pure: `clean_title`, `Attention`, `extract_attention` | create | 3 |
| `aw_watcher_herdr/state.py` | pure: `RunKey`, `CompletedRun`, `FleetTracker` | extend | 4 |
| `tests/fixtures/snapshot_basic.json` | 3 workspaces, 3 agents, mixed statuses | create | 3 |
| `tests/fixtures/snapshot_no_focus.json` | nothing focused | create | 3 |
| `tests/test_state_attention.py` | attention extraction + glyph strip | create | 3 |
| `tests/test_state_fleet.py` | run lifecycle table tests | create | 4 |
| `aw_watcher_herdr/emit.py` | `AttentionWriter`, `FleetWriter` | create | 5 |
| `tests/test_emit.py` | writer discipline + retry buffer | create | 5 |
| `aw_watcher_herdr/lock.py` | single-instance lock | create | 6 |
| `aw_watcher_herdr/main.py` | poll loop, gap handling | create | 6 |
| `tests/test_lock.py` | lock acquisition and release | create | 6 |
| `tests/test_loop.py` | loop drive tests | create | 6 |
| `aw_watcher_herdr/query.py` | terminal detection + query rendering | create | 7 |
| `tests/test_query.py` | rendering and aggregation | create | 7 |
| `scripts/install.sh`, `scripts/uninstall.sh`, `scripts/verify.sh` | deployment | create | 8 |
| `packaging/com.activitywatch.aw-watcher-herdr.plist` | LaunchAgent template | create | 8 |
| `aw-watcher-herdr.toml.example`, `README.md`, `Makefile` | docs | create | 8 |

`herdr.py` holds both transports rather than splitting them: its single
responsibility is "everything that talks to herdr", the envelope parsing is
shared between them, and separating them would put a two-line protocol class in
its own file.

---

## Task 1: Scaffolding, config, and CLI

**Files:**
- Create: `pyproject.toml`
- Create: `aw_watcher_herdr/__init__.py`
- Create: `aw_watcher_herdr/__main__.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Config` dataclass with fields `source: str`, `socket_path: str | None`, `herdr_binary: str`, `poll_interval: float`, `pulsetime: float`, `generic_terminal_label: str`, `fleet_enabled: bool`, `fleet_statuses: list[str]`, `max_run_seconds: float`, `gap_factor: float`, `window_app: list[str]`, `window_title: str | None`. Also `load_config(args) -> Config`, `parse_args(argv=None) -> argparse.Namespace`, `default_window_apps() -> list[str]`, and the module constant `CLIENT_NAME = "aw-watcher-herdr"`.

- [x] **Step 1: Create `pyproject.toml`**

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
    "Operating System :: MacOS :: MacOS X",
    "Operating System :: POSIX :: Linux",
    "Operating System :: Microsoft :: Windows",
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

The three OS classifiers reflect that the code supports all three (spec §4.3).
Only macOS is verified; §11 of the spec says so and the README must too.

- [x] **Step 2: Create the package marker**

Create `aw_watcher_herdr/__init__.py`:

```python
"""ActivityWatch watcher for herdr: workspace attention + concurrent agent activity."""

__version__ = "0.1.0"
```

- [x] **Step 3: Create the dev venv and install**

```bash
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]"
```
Expected: completes without error. Verify with `.venv/bin/python -c "import aw_client, aw_core; print('ok')"` → prints `ok`.

- [x] **Step 4: Write the failing config tests**

Create `tests/test_config.py`:

```python
"""Config loading and CLI-over-file override precedence (spec §8)."""

import pytest

from aw_watcher_herdr import __main__ as cli


def test_defaults_when_no_flags(monkeypatch):
    # Isolate from any real user config file.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {})
    cfg = cli.load_config(cli.parse_args([]))
    assert cfg.source == "auto"
    assert cfg.herdr_binary == "herdr"
    assert cfg.poll_interval == 2.0
    assert cfg.pulsetime == 5.0
    assert cfg.generic_terminal_label == "terminal"
    assert cfg.fleet_enabled is True
    assert cfg.fleet_statuses == ["working", "blocked", "done"]
    assert cfg.max_run_seconds == 43200.0
    assert cfg.gap_factor == 3.0
    assert cfg.socket_path is None
    assert cfg.window_title is None


def test_window_apps_default_is_platform_specific(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    assert cli.default_window_apps() == ["Ghostty"]
    monkeypatch.setattr(cli.sys, "platform", "linux")
    assert cli.default_window_apps() == []


def test_file_values_applied(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {
            "source": "cli",
            "herdr_binary": "/opt/herdr/bin/herdr",
            "poll_interval": 4.0,
            "pulsetime": 9.0,
            "generic_terminal_label": "shell",
            "fleet_statuses": ["working"],
            "max_run_seconds": 600.0,
            "gap_factor": 5.0,
            "fleet_enabled": False,
            "window_app": ["Alacritty", "Ghostty"],
            "window_title": "herdr",
        }
    })
    cfg = cli.load_config(cli.parse_args([]))
    assert cfg.source == "cli"
    assert cfg.herdr_binary == "/opt/herdr/bin/herdr"
    assert cfg.poll_interval == 4.0
    assert cfg.pulsetime == 9.0
    assert cfg.generic_terminal_label == "shell"
    assert cfg.fleet_statuses == ["working"]
    assert cfg.max_run_seconds == 600.0
    assert cfg.gap_factor == 5.0
    assert cfg.fleet_enabled is False
    assert cfg.window_app == ["Alacritty", "Ghostty"]
    assert cfg.window_title == "herdr"


def test_window_app_string_in_file_is_wrapped_in_a_list(monkeypatch):
    # Tolerate the pre-amendment single-string form rather than crashing on it.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"window_app": "Ghostty"}
    })
    assert cli.load_config(cli.parse_args([])).window_app == ["Ghostty"]


def test_cli_flags_override_file(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"poll_interval": 4.0, "pulsetime": 9.0,
                             "generic_terminal_label": "shell",
                             "source": "socket", "herdr_binary": "herdr"}
    })
    cfg = cli.load_config(cli.parse_args(
        ["--poll-interval", "1.5", "--pulsetime", "6",
         "--generic-terminal-label", "tty", "--socket-path", "/run/h.sock",
         "--source", "cli", "--herdr-binary", "/usr/local/bin/herdr"]))
    assert cfg.poll_interval == 1.5
    assert cfg.pulsetime == 6.0
    assert cfg.generic_terminal_label == "tty"
    assert cfg.socket_path == "/run/h.sock"
    assert cfg.source == "cli"
    assert cfg.herdr_binary == "/usr/local/bin/herdr"


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


def test_source_flag_rejects_unknown_values():
    # argparse choices=() turns a typo into a usage error, not a runtime crash
    # ten minutes into a poll loop.
    with pytest.raises(SystemExit):
        cli.parse_args(["--source", "carrier-pigeon"])
```

- [x] **Step 5: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` for `aw_watcher_herdr.__main__` (not written yet).

- [x] **Step 6: Write `__main__.py`**

Create `aw_watcher_herdr/__main__.py`. (Tasks 6 and 7 extend `main()`; for now it only wires config.)

```python
"""Entry point: parse args, load config, set up buckets, run the loop."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field

from aw_core.config import load_config_toml

from . import __version__

logger = logging.getLogger(__name__)

CLIENT_NAME = "aw-watcher-herdr"

DEFAULT_GENERIC_LABEL = "terminal"
DEFAULT_FLEET_STATUSES = ["working", "blocked", "done"]
DEFAULT_HERDR_BINARY = "herdr"

# Default config rendered into the user's toml on first run (aw-core convention).
DEFAULT_CONFIG = f"""
[{CLIENT_NAME}]
source = "auto"                # auto | socket | cli
herdr_binary = "{DEFAULT_HERDR_BINARY}"
poll_interval = 2.0
pulsetime = 5.0
generic_terminal_label = "{DEFAULT_GENERIC_LABEL}"
fleet_enabled = true
fleet_statuses = ["working", "blocked", "done"]
max_run_seconds = 43200.0
gap_factor = 3.0
# Terminal app names for the gating query. Run --detect-terminal to find yours.
window_app = ["Ghostty"]
# window_title = "herdr"
# socket_path = "~/.config/herdr/herdr.sock"   # defaults to that path
""".strip()

_FILE_KEYS = (
    "source", "socket_path", "herdr_binary", "poll_interval", "pulsetime",
    "generic_terminal_label", "fleet_enabled", "fleet_statuses",
    "max_run_seconds", "gap_factor", "window_app", "window_title",
)

SOURCE_CHOICES = ("auto", "socket", "cli")


def default_window_apps() -> list[str]:
    """Terminal app names aw-watcher-window is likely to report.

    Only macOS gets a default. On Linux the value is the WM_CLASS and on
    Windows the executable name, neither of which is worth guessing when
    --detect-terminal can read the real value from the user's own data
    (spec §7.1). An empty list drops the filter rather than matching nothing.

    This is one of the three code sites spec §4.3 permits to read
    sys.platform (the others are herdr.py and lock.py). Spec §7.1 defines
    the default itself as per-platform, and the lookup here is static: it
    touches no platform-specific API and holds no resource, so it carries
    none of the portability risk the rule exists to contain.
    """
    return ["Ghostty"] if sys.platform == "darwin" else []


@dataclass
class Config:
    source: str = "auto"
    socket_path: str | None = None
    herdr_binary: str = DEFAULT_HERDR_BINARY
    poll_interval: float = 2.0
    pulsetime: float = 5.0
    generic_terminal_label: str = DEFAULT_GENERIC_LABEL
    fleet_enabled: bool = True
    fleet_statuses: list[str] = field(
        default_factory=lambda: list(DEFAULT_FLEET_STATUSES))
    max_run_seconds: float = 43200.0
    gap_factor: float = 3.0
    window_app: list[str] = field(default_factory=default_window_apps)
    window_title: str | None = None


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

    # window_app was a single string before the 2026-08-13 amendment. Accept
    # that shape so an old config file degrades gracefully instead of crashing.
    if isinstance(cfg.window_app, str):
        cfg.window_app = [cfg.window_app]

    # Flags override the file. Use `is not None` so an explicit 0 is honored and
    # not silently dropped by a truthiness check.
    if args.source is not None:
        cfg.source = args.source
    if args.socket_path is not None:
        cfg.socket_path = args.socket_path
    if args.herdr_binary is not None:
        cfg.herdr_binary = args.herdr_binary
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
    p.add_argument("--source", choices=SOURCE_CHOICES,
                   help="how to reach herdr (default auto: socket on POSIX, "
                        "cli on Windows)")
    p.add_argument("--socket-path", dest="socket_path",
                   help="override the herdr socket path (socket source)")
    p.add_argument("--herdr-binary", dest="herdr_binary",
                   help="herdr executable to invoke (cli source)")
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
    load_config(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS (9 passed).

- [x] **Step 8: Commit**

```bash
git add pyproject.toml aw_watcher_herdr/__init__.py aw_watcher_herdr/__main__.py tests/test_config.py
git commit -m "feat: package scaffolding, config loading, and CLI"
```

---

## Task 2: herdr snapshot sources

**Files:**
- Create: `aw_watcher_herdr/herdr.py`
- Test: `tests/test_herdr.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `HerdrError(Exception)`, `HerdrUnavailable(HerdrError)`
  - `default_socket_path() -> str`
  - `SnapshotSource` protocol with `snapshot() -> dict`
  - `UnixSocketSource(socket_path: str | None = None, timeout: float = 5.0)` with `request(method, params=None) -> dict` and `snapshot() -> dict`
  - `CliSource(binary: str = "herdr", timeout: float = 10.0)` with `snapshot() -> dict`
  - `resolve_source(config) -> SnapshotSource`

- [x] **Step 1: Write the failing source tests**

Create `tests/test_herdr.py`:

```python
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
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_herdr.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aw_watcher_herdr.herdr'`.

- [x] **Step 3: Write `herdr.py`**

Create `aw_watcher_herdr/herdr.py`:

```python
"""Snapshot sources for herdr's local API (spec §3, §4.3).

Protocol behaviours encoded here, each verified against herdr 0.8.0
(protocol 19):

  * `params` is REQUIRED on every socket request, even when the method takes
    none. Omitting it gets `invalid_request: missing field 'params'`.
  * The server answers exactly ONE request per connection and then closes it.
    Every socket call therefore opens a fresh connection — which conveniently
    makes reconnect-after-failure the normal path rather than a special case.
  * Errors come back as {"id": "", "error": {"code": ..., "message": ...}}.
  * `herdr api snapshot` returns the identical envelope, and exits 1 with
    code `server_not_running` when nothing is listening.

The API answers from outside a herdr-managed pane, so no HERDR_* environment
and no macOS Accessibility permission are needed.

Two transports exist because herdr uses a Unix domain socket on macOS and Linux
but a named pipe on Windows, where CPython exposes no socket.AF_UNIX. The CLI
wrapper is the route herdr's own documentation recommends for plugins there.
This module is the only place in the package that branches on the platform for
transport purposes. Spec §4.3 permits exactly two others: lock.py and
__main__.default_window_apps().
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
from typing import Protocol

logger = logging.getLogger(__name__)

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
        self.socket_path = socket_path or default_socket_path()
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
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_herdr.py -v`
Expected: PASS (20 passed — 5 socket, 6 CLI, 5 shared envelope, 4 resolution).

- [x] **Step 5: Smoke-test both sources against live herdr**

```bash
.venv/bin/python -c "
from aw_watcher_herdr.herdr import UnixSocketSource, CliSource
for src in (UnixSocketSource(), CliSource()):
    s = src.snapshot()
    print(type(src).__name__, 'protocol', s.get('protocol'),
          '| workspaces', len(s.get('workspaces', [])))
"
```
Expected: two lines, each printing a protocol number (19 on herdr 0.8.0) and the same workspace count. If herdr is not running both raise `HerdrUnavailable`, which is correct behaviour — start herdr and retry.

- [x] **Step 6: Commit**

```bash
git add aw_watcher_herdr/herdr.py tests/test_herdr.py
git commit -m "feat(herdr): socket and CLI snapshot sources behind one interface"
```

---

## Task 3: Attention extraction and title cleaning

**Files:**
- Create: `aw_watcher_herdr/state.py`
- Create: `tests/fixtures/snapshot_basic.json`
- Create: `tests/fixtures/snapshot_no_focus.json`
- Test: `tests/test_state_attention.py`

**Interfaces:**
- Consumes: nothing (pure functions over plain dicts).
- Produces: `clean_title(title: str | None) -> str | None`, `workspace_labels(snapshot: dict) -> dict[str, str]`, frozen dataclass `Attention(workspace_label: str, workspace_id: str, pane_id: str | None, title: str | None, agent: str | None, agent_status: str | None)`, and `extract_attention(snapshot: dict) -> Attention | None`.

- [ ] **Step 1: Create the main fixture**

Create `tests/fixtures/snapshot_basic.json`. This mirrors the real snapshot shape exactly, with synthetic names. Note the deliberate variety: `w1` has a pane with **no** `agent` key and status `unknown`; `w2` has a working agent whose title keeps its `◐` glyph (herdr does not strip that one); `w3` is focused and idle with a `✳` title that herdr already stripped.

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
     "terminal_title_stripped": "◐ Add retry logic to the import job"},
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
     "terminal_title_stripped": "◐ Add retry logic to the import job"},
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
"""Attention extraction and title cleaning (spec §5, §5.1).

Runs entirely offline against fixture snapshots — no socket, no aw-server.
The fixtures are the contract: a herdr snapshot-shape change breaks these.
"""
import json
from pathlib import Path

import pytest

from aw_watcher_herdr.state import Attention, clean_title, extract_attention

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


# --- title cleaning ---------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    # herdr leaves this glyph in place, so we remove it (measured on 0.8.0).
    ("◐ Brainstorm the architecture", "Brainstorm the architecture"),
    # herdr already stripped this one; cleaning must be idempotent.
    ("Rewrite the onboarding guide", "Rewrite the onboarding guide"),
    ("✳ Fetch the latest meetings", "Fetch the latest meetings"),
    ("⏺⏺  Multiple glyphs", "Multiple glyphs"),
    ("  leading whitespace only", "leading whitespace only"),
    # Not symbols: a path and a bracketed prefix must survive untouched.
    ("~/dev/alpha-service", "~/dev/alpha-service"),
    ("[dev] run the suite", "[dev] run the suite"),
    ("(2) pending review", "(2) pending review"),
    # Nothing left after cleaning falls back to None, so the caller can use
    # the generic terminal label instead of an empty title.
    ("◐", None),
    ("", None),
    (None, None),
])
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


# --- attention extraction ---------------------------------------------------

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


def test_focused_pane_title_is_glyph_stripped():
    snap = load("snapshot_basic.json")
    snap["focused_workspace_id"] = "w2"
    snap["focused_pane_id"] = "w2:p1"
    assert extract_attention(snap).title == "Add retry logic to the import job"


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

import unicodedata
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


def clean_title(title: str | None) -> str | None:
    """Strip leading status glyphs from a terminal title (spec §5.1).

    herdr's `terminal_title_stripped` removes some agent glyphs but not all:
    measured on 0.8.0 it strips U+2733 but leaves U+25D0. Without this, one
    task yields two distinct titles either side of a working-to-idle
    transition and fragments the attention timeline.

    Only Unicode category "So" (Symbol, other) is stripped, which covers the
    spinner glyphs and emoji while leaving `~`, `[`, `(` and `/` intact — a
    plain-shell title like `~/dev/alpha-service` must survive unharmed.
    """
    if not title:
        return None
    index = 0
    for char in title:
        if char.isspace() or unicodedata.category(char) == "So":
            index += 1
        else:
            break
    return title[index:].strip() or None


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
        title=clean_title(pane.get("terminal_title_stripped")),
        agent=pane.get("agent"),
        agent_status=pane.get("agent_status"),
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_state_attention.py -v`
Expected: PASS (18 passed).

- [ ] **Step 7: Commit**

```bash
git add aw_watcher_herdr/state.py tests/test_state_attention.py tests/fixtures/
git commit -m "feat(state): attention extraction with leading-glyph title cleaning"
```

---

## Task 4: Fleet run tracker

**Files:**
- Modify: `aw_watcher_herdr/state.py` (append; do not alter Task 3's code)
- Test: `tests/test_state_fleet.py`

**Interfaces:**
- Consumes: `workspace_labels(snapshot)` and `clean_title(title)` from Task 3.
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
    # snapshot_basic.json has one working and one idle agent.
    t = FleetTracker()
    t.update(load(), T0)
    assert t.open_count == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_state_fleet.py -v`
Expected: FAIL — `ImportError: cannot import name 'FleetTracker' from 'aw_watcher_herdr.state'`.

- [ ] **Step 3: Append the fleet tracker to `state.py`**

First add `datetime` to the import block at the top of `aw_watcher_herdr/state.py`, so it reads:

```python
import unicodedata
from dataclasses import dataclass
from datetime import datetime
```

Then append to the end of the file:

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
            # Same cleaning as the attention bucket (spec §5.1), so one task
            # reads identically in both.
            desired[pane_id] = (
                key, clean_title(agent.get("terminal_title_stripped")) or "")
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
Expected: PASS (17 passed).

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

## Task 6: Single-instance lock, poll loop, and entry point

**Files:**
- Create: `aw_watcher_herdr/lock.py`
- Create: `aw_watcher_herdr/main.py`
- Modify: `aw_watcher_herdr/__main__.py` (replace the import block and `main()` from Task 1)
- Test: `tests/test_lock.py`
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: `resolve_source`, `HerdrError`, `HerdrUnavailable` (Task 2); `extract_attention`, `FleetTracker` (Tasks 3-4); `AttentionWriter`, `FleetWriter` (Task 5); `Config`, `CLIENT_NAME` (Task 1).
- Produces:
  - `lock.AlreadyRunning(RuntimeError)` and `lock.single_instance(path: str)` context manager
  - `main.OK`, `main.NO_SOURCE`, `main.HERDR_ERROR`, `main.WARN_AFTER_CONSECUTIVE_ERRORS = 10`
  - `main.run(source, attention_writer, fleet_writer, tracker, config, stop=None) -> None`
  - `__main__.lock_path() -> str`

- [ ] **Step 1: Confirm the aw-core directory helper exists**

```bash
.venv/bin/python -c "from aw_core.dirs import get_data_dir; print(get_data_dir('aw-watcher-herdr'))"
```
Expected: prints a path under the ActivityWatch data directory. If this raises `ImportError`, use `aw_core.dirs.get_cache_dir` instead and adjust `lock_path()` in Step 7 accordingly — the lock only needs a stable, user-writable directory.

- [ ] **Step 2: Write the failing lock tests**

Create `tests/test_lock.py`:

```python
"""Single-instance lock (spec §10.3).

Two supervisors can start this watcher — launchd and aw-qt — and two copies
running would silently double every fleet event. The fleet bucket cannot detect
that, because overlapping events are expected and correct there.

flock locks belong to the open file description, so a second open() of the same
path is denied even inside one process. That makes this testable without
spawning a subprocess.
"""
import sys

import pytest

from aw_watcher_herdr.lock import AlreadyRunning, single_instance


@pytest.mark.skipif(sys.platform.startswith("win"),
                    reason="the lock is a no-op on Windows")
def test_second_acquisition_is_refused(tmp_path):
    path = str(tmp_path / "watcher.lock")
    with single_instance(path):
        with pytest.raises(AlreadyRunning):
            with single_instance(path):
                pass


@pytest.mark.skipif(sys.platform.startswith("win"),
                    reason="the lock is a no-op on Windows")
def test_lock_is_released_on_exit(tmp_path):
    path = str(tmp_path / "watcher.lock")
    with single_instance(path):
        pass
    # Re-acquiring must now succeed.
    with single_instance(path):
        pass


@pytest.mark.skipif(sys.platform.startswith("win"),
                    reason="the lock is a no-op on Windows")
def test_lock_is_released_after_an_exception(tmp_path):
    path = str(tmp_path / "watcher.lock")
    with pytest.raises(ValueError):
        with single_instance(path):
            raise ValueError("boom")
    with single_instance(path):
        pass


def test_lock_records_the_pid(tmp_path):
    import os
    path = str(tmp_path / "watcher.lock")
    with single_instance(path):
        with open(path) as f:
            assert f.read().strip() == str(os.getpid())


def test_missing_parent_directory_is_created(tmp_path):
    path = str(tmp_path / "nested" / "dir" / "watcher.lock")
    with single_instance(path):
        pass
```

- [ ] **Step 3: Run the lock tests to verify they fail**

Run: `.venv/bin/pytest tests/test_lock.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aw_watcher_herdr.lock'`.

- [ ] **Step 4: Write `lock.py`**

Create `aw_watcher_herdr/lock.py`:

```python
"""Advisory single-instance lock (spec §10.3).

The watcher can be started by launchd or by aw-qt, and both running at once
would double every event in the fleet bucket. Because overlapping events are
correct and expected there (spec §6), nothing downstream could detect the
duplication — so it is prevented here instead.

Together with herdr.py and __main__.default_window_apps(), this is one of the
three code sites spec §4.3 permits to branch on the platform.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys

logger = logging.getLogger(__name__)


class AlreadyRunning(RuntimeError):
    """Another aw-watcher-herdr process already holds the lock."""


@contextlib.contextmanager
def single_instance(path: str):
    """Hold an exclusive advisory lock on `path` for the duration of the block.

    Raises AlreadyRunning if another process holds it. On Windows this is a
    no-op: only the aw-qt install route exists there, so two supervisors
    cannot both start the watcher.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    if sys.platform.startswith("win"):
        with open(path, "w") as handle:
            handle.write(str(os.getpid()))
            handle.flush()
            yield handle
        return

    import fcntl

    handle = open(path, "w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise AlreadyRunning(
            f"another aw-watcher-herdr is already running (lock: {path})"
        ) from exc

    try:
        handle.write(str(os.getpid()))
        handle.flush()
        yield handle
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
```

- [ ] **Step 5: Run the lock tests to verify they pass**

Run: `.venv/bin/pytest tests/test_lock.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Write the failing loop tests**

Create `tests/test_loop.py`:

```python
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
    # clock_step of 60s with poll_interval 2s and gap_factor 3 => every tick
    # after the first looks like a suspend gap.
    _, _, fleet, _ = drive(monkeypatch, [snap("working")], ticks=3,
                           clock_step=60.0)
    assert len(fleet.runs) >= 1
    # Each closed run is bounded by the last good poll, never the full gap.
    assert all(r.duration_seconds <= 60 for r in fleet.runs)
```

- [ ] **Step 7: Run the loop tests to verify they fail**

Run: `.venv/bin/pytest tests/test_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aw_watcher_herdr.main'`.

- [ ] **Step 8: Write `main.py`**

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
    "herdr is reachable but is not returning usable snapshots. Its API "
    "protocol may have changed in an update. Check `herdr api snapshot` by hand "
    "and file an issue. (consecutive failures: %s; last error: %s)"
)


def _now() -> datetime:
    """Indirected so tests can drive a deterministic clock."""
    return datetime.now(timezone.utc)


def run(source, attention_writer, fleet_writer, tracker, config,
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
            snapshot = source.snapshot()
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

- [ ] **Step 9: Run the loop tests to verify they pass**

Run: `.venv/bin/pytest tests/test_loop.py -v`
Expected: PASS (7 passed).

- [ ] **Step 10: Wire up `__main__.py`**

In `aw_watcher_herdr/__main__.py`, replace the import block written in Task 1 with:

```python
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket as socketlib
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aw_client import ActivityWatchClient
from aw_core.config import load_config_toml
from aw_core.dirs import get_data_dir
from aw_core.log import setup_logging

from . import __version__
from . import main as loop
from .emit import AttentionWriter, FleetWriter
from .herdr import HerdrUnavailable, resolve_source
from .lock import AlreadyRunning, single_instance
from .state import FleetTracker
```

Then replace the whole `main()` function with:

```python
def lock_path() -> str:
    """Where the single-instance lock lives (spec §10.3)."""
    return os.path.join(get_data_dir(CLIENT_NAME), "watcher.lock")


def run_snapshot(config: Config) -> int:
    """Print herdr's live snapshot as JSON (for capturing test fixtures).

    Remember to replace real workspace names with synthetic ones before
    committing anything derived from this.
    """
    try:
        snap = resolve_source(config).snapshot()
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

    try:
        lock = single_instance(lock_path())
        lock.__enter__()
    except AlreadyRunning as exc:
        # Both launchd and aw-qt can start this watcher; two copies would
        # silently double the fleet bucket (spec §10.3).
        logger.error("%s", exc)
        print(f"aw-watcher-herdr: {exc}", file=sys.stderr)
        return 1

    try:
        client = ActivityWatchClient(CLIENT_NAME, testing=args.testing)
        hostname = client.client_hostname or socketlib.gethostname()
        suffix = "-testing" if args.testing else ""
        attention_bucket = f"{CLIENT_NAME}_{hostname}{suffix}"
        fleet_bucket = f"{CLIENT_NAME}-agents_{hostname}{suffix}"

        # currentwindow reuses aw's window-activity views and categorization (§5).
        client.create_bucket(attention_bucket, event_type="currentwindow",
                             queued=True)
        if config.fleet_enabled:
            client.create_bucket(fleet_bucket, event_type="app.agent.activity",
                                 queued=True)

        source = resolve_source(config)
        logger.info("reading herdr via %s", type(source).__name__)
        tracker = FleetTracker(statuses=config.fleet_statuses,
                               max_run_seconds=config.max_run_seconds)
        attention_writer = AttentionWriter(client, attention_bucket,
                                           config.pulsetime,
                                           config.generic_terminal_label)
        fleet_writer = FleetWriter(client, fleet_bucket)

        # SIGTERM (launchd or aw-qt stop) must close open runs, or their
        # intervals are lost.
        stopping = {"flag": False}

        def _stop(_signum, _frame):
            stopping["flag"] = True

        signal.signal(signal.SIGTERM, _stop)

        with client:
            try:
                loop.run(source, attention_writer, fleet_writer, tracker,
                         config, stop=lambda: stopping["flag"])
            except KeyboardInterrupt:
                logger.info("interrupted; shutting down")
            finally:
                if config.fleet_enabled:
                    fleet_writer.write(
                        tracker.close_all(datetime.now(timezone.utc)))
                    fleet_writer.flush()
    finally:
        lock.__exit__(None, None, None)
    return 0
```

The lock is entered manually rather than with a `with` statement so that
`AlreadyRunning` can be turned into a clean exit code instead of a traceback.

- [ ] **Step 11: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (config + herdr + attention + fleet + emit + lock + loop).

- [ ] **Step 12: Verify `--snapshot` against live herdr, on both sources**

```bash
.venv/bin/python -m aw_watcher_herdr --snapshot | head -3
.venv/bin/python -m aw_watcher_herdr --source cli --snapshot | head -3
```
Expected: both print JSON beginning with `{` and containing `"protocol"`.

- [ ] **Step 13: Verify the lock refuses a second instance**

```bash
.venv/bin/python -m aw_watcher_herdr --testing --poll-interval 5 &
FIRST=$!
sleep 2
.venv/bin/python -m aw_watcher_herdr --testing --poll-interval 5; echo "second exit: $?"
kill $FIRST
```
Expected: the second invocation prints `already running` and reports `second exit: 1`.

- [ ] **Step 14: Commit**

```bash
git add aw_watcher_herdr/lock.py aw_watcher_herdr/main.py aw_watcher_herdr/__main__.py tests/test_lock.py tests/test_loop.py
git commit -m "feat: poll loop, single-instance lock, and entry-point wiring"
```

---

## Task 7: Terminal detection and query rendering

**Files:**
- Create: `aw_watcher_herdr/query.py`
- Modify: `aw_watcher_herdr/__main__.py` (add two flags and their branches)
- Test: `tests/test_query.py`

**Interfaces:**
- Consumes: `Config` (Task 1).
- Produces:
  - `query.render_attention_query(window_apps: list[str], window_title: str | None = None) -> str`
  - `query.render_fleet_query(status: str = "working") -> str`
  - `query.top_window_apps(client, bucket_id: str, hours: float = 24.0, limit: int = 10) -> list[tuple[str, float]]`
  - `query.window_bucket_id(client) -> str | None`
  - `__main__.run_detect_terminal(config, testing) -> int` and `__main__.run_print_query(config) -> int`

- [ ] **Step 1: Write the failing query tests**

Create `tests/test_query.py`:

```python
"""Query rendering and terminal detection (spec §7, §7.1).

These two commands turn window_app and window_title from documentation-only
config keys into working features: one reads the user's real terminal name out
of their own window bucket, the other renders a pasteable query from it.
"""
from datetime import datetime, timedelta, timezone

from aw_watcher_herdr.query import (
    render_attention_query, render_fleet_query, top_window_apps,
    window_bucket_id,
)

T0 = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_query.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aw_watcher_herdr.query'`.

- [ ] **Step 3: Write `query.py`**

Create `aw_watcher_herdr/query.py`:

```python
"""Query rendering and terminal detection (spec §7, §7.1).

herdr is a multiplexer, so the frontmost application is always the terminal
emulator hosting it, never herdr. aw-watcher-window reports that terminal
differently on every platform — `Ghostty` on macOS, the WM_CLASS on Linux/X11,
the executable name on Windows — so the name is read from the user's own window
bucket rather than guessed by this package.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

WINDOW_BUCKET_PREFIX = "aw-watcher-window_"


def window_bucket_id(client) -> str | None:
    """The local window-watcher bucket, or None if that watcher never ran."""
    for bucket_id in client.get_buckets():
        if bucket_id.startswith(WINDOW_BUCKET_PREFIX):
            return bucket_id
    return None


def top_window_apps(client, bucket_id: str, hours: float = 24.0,
                    limit: int = 10) -> list[tuple[str, float]]:
    """(app, total_seconds) pairs from the window bucket, busiest first."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    totals: dict[str, float] = {}
    for event in client.get_events(bucket_id, start=start, end=end, limit=-1):
        app = (event.data or {}).get("app")
        if not app:
            continue
        totals[app] = totals.get(app, 0.0) + event.duration.total_seconds()
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    return ranked[:limit]


def render_attention_query(window_apps, window_title: str | None = None) -> str:
    """Render the attention query with the configured terminal names in place.

    An empty `window_apps` drops the frontmost filter entirely rather than
    emitting a filter that matches nothing: the honest reading of "not
    configured" is "do not gate on the window", not "return nothing".
    """
    apps = list(window_apps or [])
    lines = [
        'afk      = flood(query_bucket(find_bucket("aw-watcher-afk_")));',
        'herdr    = flood(query_bucket(find_bucket("aw-watcher-herdr_")));',
        'not_afk  = filter_keyvals(afk, "status", ["not-afk"]);',
    ]
    if apps:
        lines.append(
            'window   = flood(query_bucket(find_bucket("aw-watcher-window_")));')
        lines.append(f'in_term  = filter_keyvals(window, "app", {json.dumps(apps)});')
        if window_title:
            # Regex, not exact match: window titles carry document names and
            # other churn around the part worth matching.
            lines.append(
                f'in_term  = filter_keyvals_regex(in_term, "title", '
                f'{json.dumps(window_title)});')
        lines.append('events   = filter_period_intersect(herdr, in_term);')
        lines.append('events   = filter_period_intersect(events, not_afk);')
    else:
        lines.append('events   = filter_period_intersect(herdr, not_afk);')
    lines.append('RETURN   = merge_events_by_keys(events, ["app", "title"]);')
    return "\n".join(lines)


def render_fleet_query(status: str = "working") -> str:
    """Render the agent-hours query.

    Deliberately not gated on AFK or frontmost: agent work happening while the
    user is away is the point of this bucket. flood() must never appear here —
    it closes gaps by stretching events within one timeline, which corrupts
    deliberately overlapping ones (spec §7).
    """
    return "\n".join([
        'agents   = query_bucket(find_bucket("aw-watcher-herdr-agents_"));',
        f'agents   = filter_keyvals(agents, "status", {json.dumps([status])});',
        'RETURN   = merge_events_by_keys(agents, ["app"]);',
    ])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_query.py -v`
Expected: PASS (13 passed — 5 attention rendering, 2 fleet rendering, 6 detection).

- [ ] **Step 5: Add the two flags to `parse_args` in `__main__.py`**

Insert these two arguments immediately before the `--snapshot` argument:

```python
    p.add_argument("--detect-terminal", dest="detect_terminal",
                   action="store_true",
                   help="list the apps your window watcher recorded, so you "
                        "can set window_app correctly, and exit")
    p.add_argument("--print-query", dest="print_query", action="store_true",
                   help="print the ActivityWatch queries for your config "
                        "and exit")
```

- [ ] **Step 6: Add the two command implementations to `__main__.py`**

Insert these two functions immediately after `run_snapshot`:

```python
def run_detect_terminal(config: Config, testing: bool) -> int:
    """Print the apps the window watcher saw, so window_app can be set (§7.1)."""
    client = ActivityWatchClient(f"{CLIENT_NAME}-detect", testing=testing)
    bucket_id = query.window_bucket_id(client)
    if bucket_id is None:
        print("No aw-watcher-window bucket found. Start ActivityWatch's window "
              "watcher first.\nOn Linux/Wayland use aw-watcher-window-wayland "
              "or awatcher; the stock watcher is X11 only.")
        return 1

    rows = query.top_window_apps(client, bucket_id, hours=24)
    if not rows:
        print(f"{bucket_id} has no events in the last 24 hours.")
        return 1

    print(f"Apps recorded in {bucket_id} over the last 24 hours:\n")
    for app, seconds in rows:
        print(f"  {seconds / 3600:6.2f} h  {app}")
    print("\nPut the terminal you run herdr in into your config, for example:\n")
    print(f'  window_app = {json.dumps([rows[0][0]])}')
    print(f"\nCurrent setting: window_app = {json.dumps(config.window_app)}")
    return 0


def run_print_query(config: Config) -> int:
    """Print pasteable ActivityWatch queries for the current config (§7)."""
    print("# Attention: herdr time, gated on your terminal being frontmost")
    print("# and you being present.")
    if not config.window_app:
        print("# window_app is unset, so the frontmost filter is omitted.")
        print("# Run --detect-terminal to find the right value.")
    print(query.render_attention_query(config.window_app, config.window_title))
    print()
    print("# Agent-hours per project. Never apply flood() to this bucket:")
    print("# its events overlap by design.")
    print(query.render_fleet_query())
    return 0
```

Add `from . import query` to the import block, next to `from . import main as loop`.

- [ ] **Step 7: Dispatch the new flags in `main()`**

In `main()`, replace the `--snapshot` early-exit block with all three one-shot
modes, keeping them ahead of `setup_logging` so none of them opens a log file:

```python
    # One-shot diagnostic modes print to stdout and exit.
    if args.snapshot:
        return run_snapshot(config)
    if args.detect_terminal:
        return run_detect_terminal(config, args.testing)
    if args.print_query:
        return run_print_query(config)
```

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 9: Try both commands against real data**

```bash
.venv/bin/python -m aw_watcher_herdr --detect-terminal
.venv/bin/python -m aw_watcher_herdr --print-query
```
Expected: the first lists apps with hour totals (`Ghostty` should be among them on this machine); the second prints two query blocks, the first containing `["Ghostty"]`.

- [ ] **Step 10: Commit**

```bash
git add aw_watcher_herdr/query.py aw_watcher_herdr/__main__.py tests/test_query.py
git commit -m "feat(query): --detect-terminal and --print-query diagnostics"
```

---

## Task 8: Packaging, install scripts, and documentation

**Files:**
- Create: `scripts/install.sh`, `scripts/uninstall.sh`, `scripts/verify.sh`
- Create: `packaging/com.activitywatch.aw-watcher-herdr.plist`
- Create: `aw-watcher-herdr.toml.example`, `Makefile`
- Modify: `README.md` (replace the stub)
- Modify: the spec's status line

**Interfaces:**
- Consumes: the `aw-watcher-herdr` console script from Task 1's `pyproject.toml`.
- Produces: no Python interfaces.

- [ ] **Step 1: Write the installer**

Create `scripts/install.sh` (then `chmod +x scripts/install.sh`):

```bash
#!/usr/bin/env bash
#
# One-command install for aw-watcher-herdr as a macOS launchd LaunchAgent
# (spec §10.2 — the default route on macOS).
#
# What it does:
#   1. Creates a self-contained venv at ~/.local/share/aw-watcher-herdr/venv
#      and installs aw-watcher-herdr into it (no pipx / global state needed).
#   2. Symlinks the entry point into ~/.local/bin, so the aw-qt tray route
#      (§10.1) becomes available if ActivityWatch is later upgraded to 0.14.x.
#   3. Installs + loads a LaunchAgent (auto-starts at login, survives
#      ActivityWatch updates, writes nothing into the AW app).
#
# Unlike aw-watcher-cmux this needs NO macOS Accessibility permission: herdr's
# API answers from any process owned by the user.
#
# Re-running is safe (idempotent): it reinstalls and reloads.
set -euo pipefail

if [ "$(uname)" != "Darwin" ]; then
  echo "This installer targets macOS launchd." >&2
  echo "On Linux and Windows, install with pipx and let aw-qt manage it:" >&2
  echo "    pipx install ." >&2
  echo "  then add \"aw-watcher-herdr\" to autostart_modules in aw-qt.toml." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$HOME/.local/share/aw-watcher-herdr"
VENV="$APP_DIR/venv"
BIN_DIR="$HOME/.local/bin"
LABEL="com.activitywatch.aw-watcher-herdr"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/activitywatch"
EXEC="$VENV/bin/aw-watcher-herdr"
PYTHON="${PYTHON:-python3}"

# An x86_64 Python on an arm64 Mac runs the watcher under Rosetta for no
# reason. Set ALLOW_ARCH_MISMATCH=1 if that is deliberate.
HOST_ARCH="$(uname -m)"
PY_ARCH="$("$PYTHON" -c 'import platform; print(platform.machine())')"
if [ "$HOST_ARCH" != "$PY_ARCH" ] && [ "${ALLOW_ARCH_MISMATCH:-0}" != "1" ]; then
  echo "ERROR: $PYTHON is $PY_ARCH but this Mac is $HOST_ARCH." >&2
  echo "Install a native Python (e.g. brew install python) or re-run with" >&2
  echo "    PYTHON=/opt/homebrew/bin/python3 ./scripts/install.sh" >&2
  echo "To proceed anyway: ALLOW_ARCH_MISMATCH=1 ./scripts/install.sh" >&2
  exit 1
fi

AW_QT_TOML="$HOME/Library/Application Support/activitywatch/aw-qt/aw-qt.toml"
if [ -f "$AW_QT_TOML" ] && grep -q '^[^#]*aw-watcher-herdr' "$AW_QT_TOML"; then
  echo "WARNING: aw-watcher-herdr appears in autostart_modules in" >&2
  echo "  $AW_QT_TOML" >&2
  echo "  Two supervisors would both start it. The watcher's single-instance" >&2
  echo "  lock stops the duplicate, but pick one route to avoid noisy logs." >&2
fi

echo "==> Creating venv at $VENV ($PY_ARCH)"
mkdir -p "$APP_DIR" "$LOG_DIR" "$BIN_DIR" "$HOME/Library/LaunchAgents"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
echo "==> Installing aw-watcher-herdr from $REPO_ROOT"
"$VENV/bin/pip" install --quiet "$REPO_ROOT"

echo "==> Verifying the install"
"$EXEC" --version

echo "==> Linking $BIN_DIR/aw-watcher-herdr"
ln -sf "$EXEC" "$BIN_DIR/aw-watcher-herdr"

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

Find your terminal's app name for the query recipes:

    $EXEC --detect-terminal

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
# Remove the aw-watcher-herdr LaunchAgent, its venv, and its ~/.local/bin link.
# Recorded ActivityWatch data is NOT touched.
set -euo pipefail

LABEL="com.activitywatch.aw-watcher-herdr"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP_DIR="$HOME/.local/share/aw-watcher-herdr"
LINK="$HOME/.local/bin/aw-watcher-herdr"

echo "==> Stopping the LaunchAgent"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

echo "==> Removing $PLIST"
rm -f "$PLIST"

# Only remove the link if it points into the venv we installed.
if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$APP_DIR/venv/bin/aw-watcher-herdr" ]; then
  echo "==> Removing $LINK"
  rm -f "$LINK"
fi

echo "==> Removing $APP_DIR"
rm -rf "$APP_DIR"

cat <<EOF

Uninstalled. Your recorded data is untouched; the buckets
aw-watcher-herdr_<host> and aw-watcher-herdr-agents_<host> remain in
ActivityWatch. Delete them from the aw web UI if you want them gone.

If you also listed aw-watcher-herdr in aw-qt's autostart_modules, remove it
from aw-qt.toml.
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
SOURCE="${SOURCE:-auto}"

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
echo "source    : $SOURCE"
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

echo "running watcher for ${DURATION}s (poll=1s, source=${SOURCE}) ..."
"$PY" -m aw_watcher_herdr --testing --verbose --poll-interval 1 \
  --source "$SOURCE" >/tmp/aw-herdr-verify-watcher.log 2>&1 &
WATCHER_PID=$!
sleep "$DURATION"
kill -TERM "$WATCHER_PID" 2>/dev/null || true
wait "$WATCHER_PID" 2>/dev/null || true
WATCHER_PID=""

if grep -q "already running" /tmp/aw-herdr-verify-watcher.log; then
  echo
  echo "FAIL: another aw-watcher-herdr holds the lock. Stop it and re-run."
  exit 2
fi
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

- [ ] **Step 4: Write the plist template, config example, and Makefile**

Create `packaging/com.activitywatch.aw-watcher-herdr.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!--
  launchd service for aw-watcher-herdr (spec §10.2).

  Reads herdr's local API, so it needs NO Accessibility permission — just the
  user's own GUI login session. scripts/install.sh generates this file with
  real paths filled in; this copy is the hand-editable template.

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
# Copy to your ActivityWatch config directory, which on macOS is:
#   ~/Library/Application Support/activitywatch/aw-watcher-herdr/aw-watcher-herdr.toml
# CLI flags override these values.

[aw-watcher-herdr]
# How to reach herdr: "auto" uses the Unix socket on macOS/Linux and the
# `herdr api snapshot` CLI on Windows, where herdr uses a named pipe.
source = "auto"

# Socket source only. Defaults to ~/.config/herdr/herdr.sock.
# socket_path = "~/.config/herdr/herdr.sock"

# CLI source only: the herdr executable to invoke.
herdr_binary = "herdr"

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

# --- used by --print-query ---
# The terminal you run herdr in, as your window watcher names it. That differs
# per platform, so run `aw-watcher-herdr --detect-terminal` to find yours.
# A list, because you may use more than one terminal.
window_app = ["Ghostty"]
# Optional regex narrowing on the window title.
# window_title = "herdr"
```

Create `Makefile`:

```makefile
.PHONY: install test verify verify-cli clean

install:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -e ".[dev]"

test:
	.venv/bin/pytest -q

verify:
	PY=.venv/bin/python scripts/verify.sh

# Same end-to-end check through the CLI source, which is the Windows transport.
verify-cli:
	PY=.venv/bin/python SOURCE=cli scripts/verify.sh

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

It reads herdr's local API, so it needs **no macOS Accessibility permission**.

## Platform support

| Platform | Transport | Install | Verified |
|---|---|---|---|
| macOS | Unix socket | launchd agent (or aw-qt on AW 0.14.x) | yes |
| Linux | Unix socket | aw-qt module | no |
| Windows | `herdr api snapshot` | aw-qt module | no |

herdr is a multiplexer, so **any terminal emulator works** — the terminal's name
appears only in a query recipe, never in the data path. On Windows herdr uses a
named pipe, which CPython cannot open, so the watcher shells out to herdr's CLI
wrapper instead; that is the route herdr's own documentation recommends.

Only macOS is verified end to end. Linux and Windows are supported by design but
untested, so treat them as unproven.

## How it works

```
   poll (2s)   ┌────────────────────────────────┐   heartbeat   ┌───────────┐
   ┌────────►  │      aw-watcher-herdr          │──────────────►│ aw-server │
   │           │  herdr.py  session.snapshot    │   intervals   │   :5600   │
 herdr API     │  state.py  diff → transitions  │──────────────►└───────────┘
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

### macOS (recommended)

```bash
git clone https://github.com/simensollie/aw-watcher-herdr
cd aw-watcher-herdr
./scripts/install.sh
```

This creates a self-contained venv at `~/.local/share/aw-watcher-herdr` and
installs a launchd LaunchAgent that starts at login and survives ActivityWatch
updates. There are no permission prompts and no manual System Settings step.

Uninstall any time with `./scripts/uninstall.sh`.

### As an aw-qt module (Linux, Windows, or macOS on ActivityWatch 0.14.x)

ActivityWatch has no plugin system — every watcher is a separate process — but
aw-qt discovers executables named `aw-*` on your `PATH`, lists them in the tray
menu, and starts the ones named in `autostart_modules`.

```bash
pipx install .
```

Then add it to `aw-qt.toml`:

```toml
[aw-qt]
autostart_modules = ["aw-server", "aw-watcher-afk", "aw-watcher-window", "aw-watcher-herdr"]
```

**On macOS this needs ActivityWatch 0.14.x.** Older builds are launched as a
login item and inherit `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, all of which are
SIP-protected, so aw-qt cannot see anything you installed. Check with
`grep "system modules" ~/Library/Logs/activitywatch/aw-qt/aw-qt_*.log`: if it
says `Found 0 system modules`, use the launchd route above instead.

Do not install into `/Applications/ActivityWatch.app`. It breaks the bundle's
code signature and is erased by every ActivityWatch update.

Running both routes at once is prevented by a single-instance lock: the second
copy exits rather than doubling every event in the fleet bucket.

### Check it is working

```bash
aw-watcher-herdr --snapshot | head -3
```

## Queries

Print the queries for your own configuration:

```bash
aw-watcher-herdr --print-query
```

### Active herdr time

The watcher over-emits: herdr has no "am I frontmost" concept, so the focused
workspace is reported whether or not you are looking at it. Recover real
attention by intersecting with the window and AFK watchers — the same separation
of concerns ActivityWatch already uses for AFK.

```python
afk      = flood(query_bucket(find_bucket("aw-watcher-afk_")));
herdr    = flood(query_bucket(find_bucket("aw-watcher-herdr_")));
not_afk  = filter_keyvals(afk, "status", ["not-afk"]);
window   = flood(query_bucket(find_bucket("aw-watcher-window_")));
in_term  = filter_keyvals(window, "app", ["Ghostty"]);
events   = filter_period_intersect(herdr, in_term);
events   = filter_period_intersect(events, not_afk);
RETURN   = merge_events_by_keys(events, ["app", "title"]);
```

`"Ghostty"` is what macOS calls it. Linux reports the WM_CLASS and Windows the
executable name, so find yours with:

```bash
aw-watcher-herdr --detect-terminal
```

On Linux/Wayland the stock `aw-watcher-window` records nothing (it is X11 only).
The attention bucket still fills correctly, but you need
`aw-watcher-window-wayland` or `awatcher` for the frontmost gating to work.

### Agent-hours per project

Deliberately **not** gated on AFK or frontmost — agent work happening while you
are away is the whole point.

```python
agents   = query_bucket(find_bucket("aw-watcher-herdr-agents_"));
agents   = filter_keyvals(agents, "status", ["working"]);
RETURN   = merge_events_by_keys(agents, ["app"]);
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

Config lives in your ActivityWatch config directory, which on macOS is
`~/Library/Application Support/activitywatch/aw-watcher-herdr/aw-watcher-herdr.toml`.
See [`aw-watcher-herdr.toml.example`](aw-watcher-herdr.toml.example). CLI flags
override the file.

| Key | Default | Meaning |
|---|---|---|
| `source` | `auto` | `auto`, `socket` or `cli` |
| `socket_path` | `~/.config/herdr/herdr.sock` | herdr API socket (socket source) |
| `herdr_binary` | `herdr` | Executable invoked by the CLI source |
| `poll_interval` | `2.0` | Seconds between snapshots |
| `pulsetime` | `5.0` | Heartbeat merge window (attention bucket) |
| `generic_terminal_label` | `terminal` | Title for panes with no agent |
| `fleet_enabled` | `true` | Emit the agent-fleet bucket at all |
| `fleet_statuses` | `["working", "blocked", "done"]` | Statuses that open a run |
| `max_run_seconds` | `43200` | Hard cap on a single run |
| `gap_factor` | `3.0` | Multiple of `poll_interval` treated as a sleep gap |
| `window_app` | `["Ghostty"]` on macOS, `[]` elsewhere | Terminal names for `--print-query` |
| `window_title` | unset | Optional title regex for `--print-query` |

Flags: `--testing`, `--verbose`, `--source`, `--socket-path`, `--herdr-binary`,
`--poll-interval`, `--pulsetime`, `--generic-terminal-label`, `--no-fleet`,
`--snapshot`, `--detect-terminal`, `--print-query`.

## Development

```bash
make install      # editable venv install + dev deps
make test         # unit tests; no herdr or aw-server needed
make verify       # end-to-end against aw-server --testing on :5666
make verify-cli   # the same, through the CLI source used on Windows
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Permanent gap in the timeline | herdr is not running. That is recorded as an honest gap, not an error. |
| `another aw-watcher-herdr is already running` | Both launchd and aw-qt started it. Pick one route. |
| No buckets created | aw-server is not reachable. Attention heartbeats are queued and flush on reconnect; fleet events are buffered in memory (up to 10 000) and retried. |
| aw-qt tray does not list the watcher | On macOS this needs ActivityWatch 0.14.x; see the install section. |
| Fleet totals exceed 24 h in a day | Expected. Agents run concurrently; see the known limitation above. |
| Timeline splits after renaming a workspace | Expected. `app` is the workspace label; add a categorization rule to merge the two names. |
| Query returns nothing on Linux/Wayland | The stock window watcher is X11 only. Use `aw-watcher-window-wayland` or drop the frontmost filter. |

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

- [ ] **Step 7: Run the end-to-end verification on both transports**

```bash
PY=.venv/bin/python DURATION=8 scripts/verify.sh
PY=.venv/bin/python DURATION=8 SOURCE=cli scripts/verify.sh
```
Expected: `PASS.` with a non-zero attention event count, both times. A fleet count of 0 is acceptable if no agent happened to be working during the window; to exercise it, start an agent in herdr and re-run.

- [ ] **Step 8: Mark the spec implemented and commit**

In `docs/superpowers/specs/2026-08-12-herdr-activitywatch-watcher-design.md`, change `**Status:** Approved (brainstorm)` to `**Status:** Implemented`.

```bash
git add scripts/ packaging/ Makefile README.md aw-watcher-herdr.toml.example docs/
git commit -m "feat: launchd installer, aw-qt install path, verify script, and docs"
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
Task 2, including the CLI envelope and `server_not_running` facts. §4
architecture → Tasks 1-6; §4.1 poll-not-subscribe → Task 6 loop; §4.2 identity
key → Task 3 `workspace_labels` + Task 4 `RunKey`, tested by
`test_workspace_rename_closes_and_reopens_the_run`; §4.3 transport seam →
Task 2 (`SnapshotSource`, `UnixSocketSource`, `CliSource`, `resolve_source`,
shared `_parse_envelope`, error-mapping table covered by
`test_both_sources_agree_on_envelope_handling`). §5 attention bucket → Tasks 3
and 5; §5.1 glyph strip → Task 3 `clean_title` plus Task 4's
`test_run_titles_are_glyph_stripped`. §6 fleet bucket → Tasks 4 and 5; §6.1
lifecycle → Task 4 tests; §6.2 `done` as duration →
`test_done_is_tracked_as_a_real_run`; §6.3 failure modes → Task 4 cap test,
Task 6 gap and unavailable tests, Task 6 SIGTERM handler. §7 query recipes →
Task 7 `render_attention_query` / `render_fleet_query` and Task 8 README; §7.1
terminal identity → Task 7 `--detect-terminal`, `window_app` as a list in
Task 1, Wayland caveat in Task 7's error message and Task 8's README. §8 config
→ Task 1. §9 testing → every task. §10 deployment → Task 8; §10.1 aw-qt route →
Task 8 README plus the installer's `~/.local/bin` symlink and conflict warning;
§10.2 launchd → Task 8 installer, including the arm64 assertion; §10.3
single-instance lock → Task 6 `lock.py`. §11 out of scope → no tasks, correct.
§12 open questions → no tasks, correct (deferred).

**Deliberate omissions, stated so they are not mistaken for gaps.** No systemd
unit and no Windows Task Scheduler entry: spec §11 excludes them, and route
§10.1 covers those platforms. No named-session support: spec §12 defers it, and
`HERDR_SESSION` was measured not to work for `herdr api` anyway.

**Type consistency.** `Attention` is constructed only in
`state.extract_attention` and consumed in `emit.AttentionWriter.write` and
`test_state_attention.py`, with the same six fields throughout. `RunKey` field
order `(pane_id, workspace_label, status, agent, cwd)` is identical in Task 4's
definition, its positional construction in `test_state_fleet.py` and
`test_emit.py`, and its attribute access in `emit.FleetWriter.write`.
`CompletedRun.duration_seconds` is a property (no parentheses) at every use
site. `FleetTracker.__init__` takes `statuses` and `max_run_seconds`, matching
`__main__.py` and `test_loop.py`. `loop.run` takes `(source, attention_writer,
fleet_writer, tracker, config, stop=None)` in `test_loop.py` and `__main__.py`
alike. `clean_title` is defined in Task 3 and used in Task 3's
`extract_attention` and Task 4's `FleetTracker._desired`. `resolve_source(config)`
reads `source`, `socket_path` and `herdr_binary`, all three of which Task 1's
`Config` defines. `single_instance(path)` is a context manager in both
`test_lock.py` and `__main__.py`, where it is entered manually so
`AlreadyRunning` becomes an exit code rather than a traceback.

**Deliberate test-seam note.** `main._now()` exists solely so `test_loop.py` can
drive a deterministic clock; production code calls it with no arguments and it
returns `datetime.now(timezone.utc)`.

**One verification step, not an assumption.** Task 6 Step 1 checks
`aw_core.dirs.get_data_dir` exists before `lock_path()` depends on it, with a
stated fallback. Everything else in this plan was measured against the live
herdr 0.8.0 binary, the running aw-qt process, or ActivityWatch's source.



