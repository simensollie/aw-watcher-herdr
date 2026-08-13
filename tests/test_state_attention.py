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
