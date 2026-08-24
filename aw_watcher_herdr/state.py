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
    tab_id: str | None
    tab_label: str
    pane_id: str | None
    title: str | None
    agent: str | None
    agent_status: str | None


# Unicode categories stripped from the START of a title:
#   So  Symbol, other        the spinner glyphs and emoji themselves
#   Mn  Mark, nonspacing     VARIATION SELECTOR-16, which follows an emoji glyph
#   Me  Mark, enclosing      the enclosing half of a keycap-style sequence
#   Cf  Format               ZERO WIDTH JOINER and friends inside emoji sequences
# Marks and format characters matter because a glyph is often a SEQUENCE: stop
# at the first of them and a stray zero-width character survives, which is a
# second distinct title for one task.
_GLYPH_CATEGORIES = frozenset({"So", "Mn", "Me", "Cf"})


def clean_title(title: str | None) -> str | None:
    """Strip leading status glyphs from a terminal title (spec §5.1).

    herdr's `terminal_title_stripped` removes some agent glyphs but not all:
    measured on 0.8.0 it strips U+2733 but leaves U+25D0. Without this, one
    task yields two distinct titles either side of a working-to-idle
    transition and fragments the attention timeline.

    Only whitespace and the categories in _GLYPH_CATEGORIES are stripped, so
    `~`, `[`, `(` and `/` survive: a plain-shell title like
    `~/dev/alpha-service`, a `[dev] run the suite` prefix and a `(2) pending
    review` counter must all come through unharmed, which is why the broader
    reading of spec §5.1 ("a leading run of non-alphanumeric characters") is
    deliberately not implemented as "strip every non-alphanumeric".
    """
    if not title:
        return None
    index = 0
    for char in title:
        if char.isspace() or unicodedata.category(char) in _GLYPH_CATEGORIES:
            index += 1
        else:
            break
    return title[index:].strip() or None


# U+00B7 MIDDLE DOT, not a hyphen: workspace and tab labels routinely contain
# hyphens (this project's own space is `aw-watcher-herdr`), so a hyphenated
# composition could not be split back into its parts by eye or by query.
TITLE_SEPARATOR = " \u00b7 "


def display_title(app: str | None, tab: str | None,
                  title: str | None) -> str:
    """Compose `space \u00b7 tab \u00b7 terminal name` for the ActivityWatch UI.

    ActivityWatch renders only `app` and `title`, so anything not folded into
    one of them is invisible outside a hand-written query. The space is
    repeated here even though `app` already carries it, which keeps a title
    self-describing in the views that show titles alone.

    Empty segments are dropped rather than rendered as a bare separator: an
    unlabeled tab is an absent value, and `space \u00b7  \u00b7 title` would read as a
    missing one. A DEFAULT ordinal label ("1") is not empty and is kept, since
    dropping it would give two tabs of one space the same title and merge
    unrelated work into a single block.
    """
    parts = [(part or "").strip() for part in (app, tab, title)]
    return TITLE_SEPARATOR.join(part for part in parts if part)


def _index(items, key: str) -> dict:
    return {i[key]: i for i in (items or []) if isinstance(i, dict) and key in i}


def workspace_labels(snapshot: dict) -> dict[str, str]:
    """workspace_id -> label, for both attention and fleet lookups."""
    return {
        w["workspace_id"]: w.get("label") or ""
        for w in (snapshot.get("workspaces") or [])
        if isinstance(w, dict) and "workspace_id" in w
    }


def tab_labels(snapshot: dict) -> dict[str, str]:
    """tab_id -> label, for both attention and fleet lookups.

    A default label is the tab's ordinal ("1"), which is kept as-is: see
    display_title for why an ordinal is not treated as absent.
    """
    return {
        t["tab_id"]: t.get("label") or ""
        for t in (snapshot.get("tabs") or [])
        if isinstance(t, dict) and "tab_id" in t
    }


def extract_attention(snapshot: dict) -> Attention | None:
    """Focused workspace + pane, or None when nothing is focused.

    Returning None (rather than guessing) is deliberate: the caller emits
    nothing, leaving an honest gap in the timeline.

    None means exactly two things: nothing is focused, or the focused id names
    a workspace this snapshot does not describe (an inconsistent snapshot). An
    unlabeled workspace is NOT one of them: it is recorded with an empty label,
    the same way _desired() records it with app "" in the fleet bucket. Anything
    else would stop all attention recording for as long as that workspace stayed
    focused. This module is pure and has no logger, so making that visible is
    the caller's job (see main.run).
    """
    ws_id = snapshot.get("focused_workspace_id")
    if not ws_id:
        return None
    labels = workspace_labels(snapshot)
    if ws_id not in labels:
        return None
    label = labels[ws_id]

    pane_id = snapshot.get("focused_pane_id")
    pane = _index(snapshot.get("panes"), "pane_id").get(pane_id) if pane_id else None
    pane = pane or {}

    # The pane is the authority on which tab it lives in; `focused_tab_id` is
    # the fallback for a snapshot with no focused pane. A tab missing from the
    # list yields an empty label rather than None-ing the whole Attention, for
    # the same reason an unlabeled workspace does.
    tab_id = pane.get("tab_id") or snapshot.get("focused_tab_id")

    return Attention(
        workspace_label=label,
        workspace_id=ws_id,
        tab_id=tab_id,
        tab_label=tab_labels(snapshot).get(tab_id, "") if tab_id else "",
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
    they work, and segmenting on it would shred every run (spec §6.1). The tab
    LABEL is absent for the same reason, while the tab ID is present: moving a
    pane to another tab is a change of context, renaming its tab is not."""

    pane_id: str
    tab_id: str
    workspace_label: str
    status: str
    agent: str
    cwd: str


@dataclass(frozen=True)
class CompletedRun:
    """A closed interval, ready to become one ActivityWatch event."""

    key: RunKey
    title: str
    tab_label: str
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
        # pane_id -> (key, latest_title, latest_tab_label, started_at)
        self._open: dict[str, tuple[RunKey, str, str, datetime]] = {}

    @property
    def open_count(self) -> int:
        return len(self._open)

    def _desired(self, snapshot: dict) -> dict[str, tuple[RunKey, str, str]]:
        """pane_id -> (key, title, tab_label) for every agent that should be open."""
        labels = workspace_labels(snapshot)
        tabs = tab_labels(snapshot)
        desired: dict[str, tuple[RunKey, str, str]] = {}
        for agent in (snapshot.get("agents") or []):
            if not isinstance(agent, dict):
                continue
            pane_id = agent.get("pane_id")
            status = agent.get("agent_status")
            if not pane_id or status not in self._statuses:
                continue
            tab_id = agent.get("tab_id") or ""
            key = RunKey(
                pane_id=pane_id,
                tab_id=tab_id,
                workspace_label=labels.get(agent.get("workspace_id"), ""),
                status=status,
                agent=agent.get("agent") or "",
                cwd=agent.get("cwd") or "",
            )
            # Same cleaning as the attention bucket (spec §5.1), so one task
            # reads identically in both.
            desired[pane_id] = (
                key, clean_title(agent.get("terminal_title_stripped")) or "",
                tabs.get(tab_id, ""))
        return desired

    def update(self, snapshot: dict, now: datetime) -> list[CompletedRun]:
        """Reconcile open runs against a snapshot; return the runs that closed."""
        desired = self._desired(snapshot)
        closed: list[CompletedRun] = []

        for pane_id, (key, title, tab_label, start) in list(self._open.items()):
            incoming = desired.get(pane_id)
            expired = (now - start).total_seconds() >= self._max_run_seconds
            if incoming is None or incoming[0] != key or expired:
                closed.append(CompletedRun(key, title, tab_label, start, now))
                del self._open[pane_id]
            else:
                # Same run continues; keep the most recent non-empty title and
                # tab label, so a rename relabels the interval it happened in
                # rather than splitting it.
                self._open[pane_id] = (
                    key, incoming[1] or title, incoming[2] or tab_label, start)

        # Anything still desired but not open starts now. This also reopens the
        # runs just closed by a key change or the cap, so tracking continues.
        for pane_id, (key, title, tab_label) in desired.items():
            if pane_id not in self._open:
                self._open[pane_id] = (key, title, tab_label, now)

        return closed

    def close_all(self, at: datetime) -> list[CompletedRun]:
        """Close every open run at `at`. Used on shutdown and on a sleep gap."""
        closed = [CompletedRun(key, title, tab_label, start, at)
                  for (key, title, tab_label, start) in self._open.values()]
        self._open.clear()
        return closed
