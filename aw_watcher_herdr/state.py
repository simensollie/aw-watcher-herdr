"""Pure state machine over herdr session snapshots (spec §5, §6).

Deliberately I/O-free: no socket, no aw-server, no clock of its own (callers
pass `now`). Every rule here is therefore exercised by table-driven tests
against fixture snapshots.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime


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
