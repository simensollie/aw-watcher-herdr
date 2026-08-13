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
