"""Attention extraction and title cleaning (spec §5, §5.1).

Runs entirely offline against fixture snapshots: no socket, no aw-server.
The fixtures are the contract: a herdr snapshot-shape change breaks these.
"""
import json
from pathlib import Path

import pytest

from aw_watcher_herdr.state import (
    Attention,
    clean_title,
    display_title,
    extract_attention,
)

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
    # The emoji-presentation form of the same glyph: U+2733 followed by
    # VARIATION SELECTOR-16 (category Mn). Stopping at the selector leaves a
    # stray glyph in the UI and, worse, a second distinct title for one task.
    ("✳️ Fetch the latest meetings", "Fetch the latest meetings"),
    # ZERO WIDTH JOINER (category Cf) between two glyphs, as in emoji sequences.
    ("✳‍◐ Fetch the latest meetings", "Fetch the latest meetings"),
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


def test_glyph_variants_of_one_task_collapse_to_one_title():
    # The reason clean_title exists: herdr marks the same task with a different
    # glyph per agent state, so any variant surviving the strip fragments the
    # attention timeline across a working-to-idle transition.
    variants = [
        "◐ Fetch the latest meetings",     # working
        "✳ Fetch the latest meetings",     # idle, text presentation
        "✳️ Fetch the latest meetings",    # idle, emoji presentation (VS16)
        "Fetch the latest meetings",       # already stripped by herdr
    ]
    assert len({clean_title(v) for v in variants}) == 1


# --- display title composition ----------------------------------------------

@pytest.mark.parametrize("app,tab,title,expected", [
    # The ordinary case: space, tab, terminal name.
    ("certainqms", "QMS", "Rewrite the guide", "certainqms \u00b7 QMS \u00b7 Rewrite the guide"),
    # A default ordinal tab label carries little meaning but is kept verbatim:
    # dropping it would collapse two tabs of one space into one title and
    # silently merge unrelated work.
    ("aw-watcher-herdr", "1", "Session names", "aw-watcher-herdr \u00b7 1 \u00b7 Session names"),
    # Empty segments are skipped rather than rendered as a bare separator,
    # which would read as a missing value instead of an absent one.
    ("certainqms", "", "Rewrite the guide", "certainqms \u00b7 Rewrite the guide"),
    ("certainqms", "QMS", "", "certainqms \u00b7 QMS"),
    ("", "QMS", "Rewrite the guide", "QMS \u00b7 Rewrite the guide"),
    ("certainqms", None, None, "certainqms"),
    (None, None, None, ""),
    # Whitespace-only is absent, not present-but-blank.
    ("certainqms", "   ", "Rewrite the guide", "certainqms \u00b7 Rewrite the guide"),
])
def test_display_title(app, tab, title, expected):
    assert display_title(app, tab, title) == expected


def test_display_title_does_not_mangle_a_title_containing_the_separator():
    # A hyphen separator could not survive this: the space is called
    # `aw-watcher-herdr`, so a hyphenated composition is unsplittable.
    assert display_title("aw-watcher-herdr", "1", "fix a - b ordering") == (
        "aw-watcher-herdr \u00b7 1 \u00b7 fix a - b ordering")


# --- attention extraction ---------------------------------------------------

def test_extracts_focused_workspace_and_agent():
    a = extract_attention(load("snapshot_basic.json"))
    assert a == Attention(
        workspace_label="gamma docs",
        workspace_id="w3",
        tab_id="w3:t1",
        tab_label="guide",
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


def test_attention_tab_label_comes_from_the_focused_panes_own_tab():
    # The pane, not `focused_tab_id`, is the authority on which tab a pane is
    # in: a snapshot caught mid-move can disagree, and attributing the pane's
    # work to a tab it has left would mislabel the interval.
    snap = load("snapshot_basic.json")
    snap["focused_tab_id"] = "w1:t1"
    a = extract_attention(snap)
    assert a.tab_id == "w3:t1"
    assert a.tab_label == "guide"


def test_attention_falls_back_to_focused_tab_id_when_no_pane_is_focused():
    snap = load("snapshot_basic.json")
    snap["focused_pane_id"] = None
    a = extract_attention(snap)
    assert a.tab_id == "w3:t1"
    assert a.tab_label == "guide"


def test_attention_tab_label_is_empty_when_the_tab_is_absent_from_the_list():
    # An inconsistent snapshot must not stop attention recording: the same
    # reasoning as an unlabeled workspace (see below).
    snap = load("snapshot_basic.json")
    snap["tabs"] = [t for t in snap["tabs"] if t["tab_id"] != "w3:t1"]
    a = extract_attention(snap)
    assert a is not None
    assert a.tab_id == "w3:t1"
    assert a.tab_label == ""


def test_attention_keeps_a_default_ordinal_tab_label():
    snap = load("snapshot_basic.json")
    snap["focused_workspace_id"] = "w1"
    snap["focused_pane_id"] = "w1:p1"
    assert extract_attention(snap).tab_label == "1"


def _blank_the_label(snap, ws_id="w3"):
    for w in snap["workspaces"]:
        if w["workspace_id"] == ws_id:
            w["label"] = ""
    return snap


def test_empty_label_still_yields_attention():
    # "The focused workspace has no label" is NOT "nothing is focused". Treating
    # them alike stopped all attention recording, silently and at no log level,
    # for as long as that workspace stayed focused, while the fleet path went on
    # recording the very same workspace with app "". The two paths now agree,
    # and the loop is what makes the situation visible.
    a = extract_attention(_blank_the_label(load("snapshot_basic.json")))
    assert a is not None
    assert a.workspace_label == ""
    assert a.workspace_id == "w3"
    assert a.title == "Rewrite the onboarding guide"


def test_missing_label_key_is_the_same_as_an_empty_one():
    snap = load("snapshot_basic.json")
    for w in snap["workspaces"]:
        if w["workspace_id"] == "w3":
            del w["label"]
    assert extract_attention(snap).workspace_label == ""


def test_an_unfocused_snapshot_is_still_distinguishable_from_an_unlabeled_one():
    # The distinction the old None-for-empty-label behaviour destroyed.
    assert extract_attention(load("snapshot_no_focus.json")) is None
    assert extract_attention(
        _blank_the_label(load("snapshot_basic.json"))) is not None
