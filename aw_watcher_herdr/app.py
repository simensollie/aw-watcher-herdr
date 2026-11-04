"""Snapshot source for the Herdr native app (herdr-app window + herdr-core).

The native app is a different program from the Herdr TUI: its own window
(`herdr-app`), its own runtime (`herdr-core`, the `hc` CLI), its own sockets,
and no shared agents. The TUI stays supported, so this module ADDS a source
rather than replacing herdr.py. Everything downstream (state.py, emit.py, the
buckets and their field names) consumes the TUI's snapshot shape, so the job
here is to read the app and the core and adapt them into that shape.

Two sockets, two protocols, both verified against herdr-app 0.1.12 and
herdr-core 0.1.17 (protocol generation 1):

  * herdr-app listens on `<app home>/herdr-app.sock` (home defaults to
    ~/.config/herdr-app, HERDR_APP_HOME overrides). A request is one JSON
    object per line, `{"status": true}` or `{"ls": true}`; the answer is one
    JSON line and the connection closes. An unknown request answers
    `{"error": "expected { do }, { ls }, { commands } or { status }"}`. That
    format is not documented anywhere; it was read off that error message.
    `status` carries the focused space, tab and pane; `ls` the spaces, their
    tabs and the terminals in each tab, with the status the APP shows.
  * herdr-core listens on `<runtime dir>/herdr-core/<session>.sock` and speaks
    JSON-RPC 2.0, one message per line, no handshake needed. `terminal.list`
    returns every terminal with its title, working directory, the program's
    own status report, its labels and its parent.

Why both: the core knows titles, folders and parents, the app knows focus and
which terminal is which pane. They also DISAGREE on `done`, and the app is
right for this watcher's purposes. The core keeps `done` until the program's
next turn; the app flips it to `idle` the moment the person looks at the tab
(it records that in a `herdr.seen` label). The fleet bucket's `done` duration
is defined as "how long finished work sat unnoticed", which is the app's
reading, so a pane's status is always taken from the app.

Core terminals the app does not list as a pane (sub-agents an agent started
with `hc run`, and parked terminals) are still agent work. They are attached
to the tab of the nearest ancestor that IS a pane, or to the tab named by
their own `herdr.tab` label, and recorded under their terminal address in
place of a pane id. Their `done` is translated to `idle`: nobody "notices" a
sub-agent, its parent reads it over hc, so the unnoticed-duration metric has
no meaning there and the raw core value would record `done` until the next
turn, hours later.

This module reads sys.platform-adjacent state (getuid, XDG variables) only to
reproduce herdr-core's own socket path rule, in default_core_socket_path. It
is the transport module for the app, the same role herdr.py plays for the
TUI, and so the same spec §4.3 allowance applies.
"""

from __future__ import annotations

import json
import os
import socket

from .herdr import HerdrError, HerdrUnavailable

DEFAULT_CORE_SESSION = "default"
TERMINAL_LIST_METHOD = "terminal.list"

# Labels herdr-app puts on the terminals it starts (or adopts).
SPACE_LABEL = "herdr.space"
TAB_LABEL = "herdr.tab"


# --- default socket paths ---------------------------------------------------

def app_home() -> str:
    """herdr-app's home folder: HERDR_APP_HOME, else ~/.config/herdr-app."""
    return os.environ.get("HERDR_APP_HOME") or os.path.expanduser(
        "~/.config/herdr-app")


def default_app_socket_path() -> str:
    return os.path.join(app_home(), "herdr-app.sock")


def default_core_socket_path() -> str:
    """herdr-core's socket, derived the way the core derives it (paths.rs).

    HC_SOCKET wins. Otherwise `<run>/herdr-core/<session>.sock`, where `run`
    is XDG_RUNTIME_DIR, else /run/user/<uid> if that directory exists, else
    the state directory (XDG_STATE_HOME or ~/.local/state) joined with
    `herdr-core/<session>`. macOS normally has no XDG_RUNTIME_DIR and no
    /run/user, so there the socket sits in the state directory. `session` is
    HC_SESSION, trimmed, or `default`. A `socket` key in the core's own
    config.toml also overrides the path; that is not read here, so set
    core_socket_path in this watcher's config if you use it.
    """
    explicit = os.environ.get("HC_SOCKET")
    if explicit:
        return explicit
    session = (os.environ.get("HC_SESSION") or "").strip() or DEFAULT_CORE_SESSION
    run = os.environ.get("XDG_RUNTIME_DIR") or None
    if not run and hasattr(os, "getuid"):
        candidate = f"/run/user/{os.getuid()}"
        if os.path.isdir(candidate):
            run = candidate
    if run:
        return os.path.join(run, "herdr-core", f"{session}.sock")
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(state, "herdr-core", session, f"{session}.sock")


# --- line protocol ----------------------------------------------------------

def _request_line(path: str, payload: dict, timeout: float, what: str) -> dict:
    """One JSON line out, one JSON line back, on a fresh connection.

    Both herdr-app and herdr-core answer a plain-line request this way, and a
    fresh connection per request makes reconnect-after-failure the normal path
    (the same reasoning as UnixSocketSource). A connection failure is
    HerdrUnavailable: the program is simply not running, which is a gap, not a
    fault.
    """
    if not hasattr(socket, "AF_UNIX"):
        raise HerdrUnavailable(
            f"{what}: this platform has no Unix sockets (herdr-core uses a "
            f"named pipe on Windows, which CPython cannot open)")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(path)
    except OSError as exc:
        sock.close()
        raise HerdrUnavailable(f"{what}: cannot connect to {path}: {exc}") from exc
    try:
        with sock.makefile("rwb") as f:
            f.write(json.dumps(payload).encode() + b"\n")
            f.flush()
            line = f.readline()
    except OSError as exc:
        raise HerdrError(f"{what}: socket error: {exc}") from exc
    finally:
        sock.close()
    if not line:
        raise HerdrError(f"{what}: connection closed without a response")
    try:
        msg = json.loads(line.decode("utf-8", "replace"))
    except ValueError as exc:
        raise HerdrError(f"{what}: malformed response: {exc}") from exc
    if not isinstance(msg, dict):
        raise HerdrError(f"{what}: response was not an object")
    return msg


class AppSource:
    """Reads herdr-app (focus, panes) and herdr-core (terminals) and adapts
    them into the TUI snapshot shape consumed by state.py."""

    def __init__(self, app_socket_path: str | None = None,
                 core_socket_path: str | None = None, timeout: float = 5.0):
        self.app_socket_path = os.path.expanduser(
            app_socket_path or default_app_socket_path())
        self.core_socket_path = os.path.expanduser(
            core_socket_path or default_core_socket_path())
        self.timeout = timeout
        self._seq = 0

    def app_request(self, kind: str) -> dict:
        """`{"status": true}` or `{"ls": true}` against the app window."""
        what = f"herdr-app {kind}"
        msg = _request_line(self.app_socket_path, {kind: True}, self.timeout, what)
        if "error" in msg:
            raise HerdrError(f"{what}: {msg['error']}")
        return msg

    def core_request(self, method: str, params: dict | None = None) -> dict:
        """One JSON-RPC 2.0 call against herdr-core; returns its `result`."""
        self._seq += 1
        what = f"herdr-core {method}"
        msg = _request_line(self.core_socket_path, {
            "jsonrpc": "2.0",
            "id": f"aw-watcher-herdr:{self._seq}",
            "method": method,
            "params": params if params is not None else {},
        }, self.timeout, what)
        if "error" in msg:
            err = msg["error"] or {}
            kind = (err.get("data") or {}).get("kind") if isinstance(err, dict) else None
            message = err.get("message", "") if isinstance(err, dict) else str(err)
            raise HerdrError(f"{what}: {kind or 'error'}: {message}")
        if "result" not in msg:
            raise HerdrError(f"{what}: response contained no result")
        return msg["result"]

    def snapshot(self) -> dict:
        status = self.app_request("status")
        listing = self.app_request("ls")
        result = self.core_request(TERMINAL_LIST_METHOD, {"all": False})
        terminals = result.get("terminals") if isinstance(result, dict) else None
        if not isinstance(terminals, list):
            raise HerdrError(
                f"herdr-core {TERMINAL_LIST_METHOD}: result has no terminals "
                f"list; the core's API may have changed")
        return adapt_snapshot(status, listing, terminals)


# --- adaptation (pure) ------------------------------------------------------

def _core_status_word(terminal: dict) -> str | None:
    status = terminal.get("status")
    if isinstance(status, dict):
        return status.get("state") or None
    return None


def _core_agent(terminal: dict) -> str | None:
    status = terminal.get("status")
    if isinstance(status, dict):
        return status.get("app") or None
    return None


def _core_cwd(terminal: dict) -> str | None:
    """The live folder: the foreground program's, else the terminal's own."""
    fg = terminal.get("foreground")
    if isinstance(fg, dict) and fg.get("cwd"):
        return fg["cwd"]
    return terminal.get("cwd") or None


def adapt_snapshot(status: dict, listing: dict, terminals: list) -> dict:
    """Compose a TUI-shaped snapshot from the app's status and ls and the
    core's terminal list. Pure, so it is table-tested against fixtures.

    Raises HerdrError for a shape this adapter does not understand, rather
    than returning an empty snapshot: an empty snapshot records nothing and
    looks exactly like "no agents", which is the failure mode that must never
    be silent.
    """
    if not isinstance(status, dict):
        raise HerdrError("herdr-app status: not an object")
    spaces = listing.get("spaces") if isinstance(listing, dict) else None
    if not isinstance(spaces, list):
        raise HerdrError(
            "herdr-app ls: no spaces list; the app's protocol may have changed")

    by_address = {t["address"]: t for t in terminals
                  if isinstance(t, dict) and t.get("address")}

    workspaces: list[dict] = []
    tabs: list[dict] = []
    panes: list[dict] = []
    agents: list[dict] = []
    tab_of_terminal: dict[str, tuple[str, str]] = {}   # address -> (ws, tab)
    tab_to_workspace: dict[str, str] = {}

    for space in spaces:
        if not isinstance(space, dict) or not space.get("id"):
            continue
        ws_id = space["id"]
        machine = space.get("machine")
        workspaces.append({
            "workspace_id": ws_id,
            "label": space.get("name") or "",
            "machine": machine,
            "focused": bool(space.get("active")),
        })
        for tab in space.get("tabs") or []:
            if not isinstance(tab, dict) or not tab.get("id"):
                continue
            tab_id = tab["id"]
            tab_to_workspace[tab_id] = ws_id
            tabs.append({
                "tab_id": tab_id,
                "workspace_id": ws_id,
                "label": tab.get("name") or "",
                "focused": bool(tab.get("active")),
            })
            for term in tab.get("terminals") or []:
                if not isinstance(term, dict):
                    continue
                address = term.get("terminal") or ""
                pane_id = term.get("pane") or address
                if not pane_id:
                    continue
                core = by_address.get(address, {})
                tab_of_terminal[address] = (ws_id, tab_id)
                pane = {
                    "pane_id": pane_id,
                    "tab_id": tab_id,
                    "workspace_id": ws_id,
                    "terminal_id": address,
                    "machine": machine,
                    # The app's word, not the core's: see the module docstring
                    # on `done`.
                    "agent_status": term.get("status") or None,
                    "agent": term.get("app") or _core_agent(core),
                    "terminal_title_stripped": core.get("title") or None,
                    "cwd": _core_cwd(core),
                    "status_since_ms": core.get("status_ms"),
                    "focused": pane_id == status.get("focused_pane"),
                }
                panes.append(pane)
                if pane["agent_status"]:
                    agents.append(pane)

    # Core terminals without a pane: sub-agents (by parent) and parked or
    # adopted terminals (by label). Their status word is the core's.
    for address, core in by_address.items():
        if address in tab_of_terminal:
            continue
        word = _core_status_word(core)
        if not word:
            continue
        placed = _place_orphan(address, by_address, tab_of_terminal,
                               tab_to_workspace)
        if placed is None:
            continue
        ws_id, tab_id = placed
        tab_of_terminal[address] = placed
        agents.append({
            "pane_id": address,
            "tab_id": tab_id,
            "workspace_id": ws_id,
            "terminal_id": address,
            "machine": _machine_of(workspaces, ws_id),
            "agent_status": "idle" if word == "done" else word,
            "agent": _core_agent(core),
            "terminal_title_stripped": core.get("title") or None,
            "cwd": _core_cwd(core),
            "status_since_ms": core.get("status_ms"),
            "parent": core.get("parent"),
            "focused": False,
        })

    return {
        "source": "app",
        "focused_workspace_id": status.get("space") or None,
        "focused_tab_id": status.get("tab") or None,
        "focused_pane_id": status.get("focused_pane") or None,
        "workspaces": workspaces,
        "tabs": tabs,
        "panes": panes,
        "agents": agents,
    }


def _machine_of(workspaces: list[dict], ws_id: str):
    for ws in workspaces:
        if ws["workspace_id"] == ws_id:
            return ws.get("machine")
    return None


def _place_orphan(address: str, by_address: dict,
                  tab_of_terminal: dict, tab_to_workspace: dict):
    """(workspace_id, tab_id) for a terminal with no pane, or None.

    Its own `herdr.tab` label wins (a parked terminal keeps it). Otherwise walk
    `parent` until a placed terminal is found. The walk is bounded by the
    number of terminals, so a cycle in the data cannot hang the poll loop.
    """
    core = by_address.get(address, {})
    labels = core.get("labels") or {}
    tab_id = labels.get(TAB_LABEL)
    if tab_id in tab_to_workspace:
        return tab_to_workspace[tab_id], tab_id
    seen = set()
    current = core.get("parent")
    while current and current not in seen and current in by_address:
        if current in tab_of_terminal:
            return tab_of_terminal[current]
        seen.add(current)
        parent = by_address[current]
        parent_tab = (parent.get("labels") or {}).get(TAB_LABEL)
        if parent_tab in tab_to_workspace:
            return tab_to_workspace[parent_tab], parent_tab
        current = parent.get("parent")
    return None
