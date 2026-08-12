# aw-watcher-herdr — Design

**Status:** Approved (brainstorm)
**Date:** 2026-08-12
**Supersedes (in spirit):** `simensollie/aw-watcher-cmux` — that repo remains
untouched and installable for cmux users. This is a separate project, not a port.

---

## 1. Problem

[herdr](https://herdr.dev) is a terminal workspace manager for AI coding agents,
run inside Ghostty. Work is organised into **workspaces** (projects), **tabs**,
and **panes**, and several coding agents run **concurrently** — typically three
or four working in the background while the user watches one.

Two distinct things go unrecorded today:

1. **Attention** — which project the user was actually looking at. The previous
   cmux watcher covered this, but its bucket went dark on 2026-07-10 when the
   user migrated to herdr, leaving a growing gap in the ActivityWatch timeline.
2. **Agent activity** — what the fleet of background agents was doing, on which
   project, and how long finished work sat unnoticed. This was never trackable
   before, because cmux exposed no per-agent state.

## 2. Users and outcomes

Single user (the author), on macOS, running herdr inside Ghostty with
ActivityWatch on `localhost:5600`.

Questions the system must answer:

| Question | Bucket |
|---|---|
| How much of *my* time went to project X vs Y? | attention |
| Which agent task was I watching at 14:30? | attention |
| How many agent-hours did project X consume? | fleet |
| How long were my agents blocked waiting on me? | fleet |
| How long did completed work sit before I noticed it? | fleet |

## 3. Data source

herdr exposes a JSON-lines Unix socket API at `~/.config/herdr/herdr.sock`
(protocol 19 as of herdr 0.8.0).

Verified during design:

- The socket answers from **outside** a herdr-managed pane, with `HERDR_ENV`,
  `HERDR_WORKSPACE_ID`, `HERDR_TAB_ID` and `HERDR_PANE_ID` all unset. A detached
  launchd agent can therefore read it.
- **No macOS Accessibility permission is required.** This removes the entire
  onboarding burden that dominated the cmux watcher's installer and README.
- `session.snapshot` returns full state: `focused_workspace_id`,
  `focused_tab_id`, `focused_pane_id`, plus every workspace, tab, pane and agent
  with its own status.
- `events.subscribe` exists and streams live events (`workspace.focused`,
  `tab.focused`, `pane.focused`, `pane.agent_detected`, `workspace.renamed`,
  `layout.updated`, and others). Verified working.

Representative snapshot fields, trimmed:

```json
{
  "focused_workspace_id": "w5",
  "focused_tab_id": "w5:t1",
  "focused_pane_id": "w5:p1",
  "workspaces": [
    {"workspace_id": "w5", "label": "acme risa", "agent_status": "idle", "focused": true}
  ],
  "agents": [
    {"agent": "claude", "agent_status": "working", "workspace_id": "w4",
     "pane_id": "w4:p1", "cwd": "/Users/me/proj",
     "terminal_title": "◐ Ingest latest meetings",
     "terminal_title_stripped": "Ingest latest meetings", "focused": false}
  ]
}
```

herdr classifies agent state itself as one of `working`, `idle`, `blocked`,
`done`, `unknown`. Per herdr's documentation:

- `idle` — ready for input, and its tab has been seen in the focused UI.
- `done` — the same underlying idle state, but reached after **unseen**
  background work finished. Focusing the tab converts `done` into `idle`.
- `blocked` — herdr recognised an approval or question UI.
- `unknown` — an agent is present but unclassifiable. Does **not** imply
  completion.

## 4. Architecture

New repository `simensollie/aw-watcher-herdr`. Python 3.10+, dependencies
`aw-client` and `tomli` only. No `pyobjc`.

```
aw_watcher_herdr/
  herdr.py    socket client      — connect, JSON-lines request/response, reconnect
  state.py    pure state machine — snapshots in, transitions out. No I/O.
  emit.py     AW writers         — AttentionWriter (heartbeat) + FleetWriter (intervals)
  main.py     wiring, config, CLI, loop
```

```
   poll (2s)   ┌────────────────────────────────┐   heartbeat   ┌───────────┐
   ┌────────►  │      aw-watcher-herdr          │──────────────►│ aw-server │
   │           │  herdr.py  session.snapshot    │   intervals   │   :5600   │
 herdr.sock    │  state.py  diff → transitions  │──────────────►└───────────┘
   ◄───────────┤  emit.py   two bucket writers  │
               └────────────────────────────────┘
```

`state.py` performs no I/O. Every transition rule in §6 is therefore a
table-driven unit test over fixture snapshots, needing neither a socket nor an
aw-server.

### 4.1 Poll, not subscribe

The watcher polls `session.snapshot` on an interval (default 2 s) and diffs
consecutive snapshots. It does **not** use `events.subscribe`, despite that
stream being verified to work.

Rationale:

- `pane.agent_status_changed` requires a `pane_id` in its subscription (the
  server rejects the subscription with `missing field 'pane_id'` otherwise). Per-
  pane status subscriptions must therefore be created and torn down as panes come
  and go.
- A snapshot is needed anyway to seed state on connect and to resync after any
  disconnect, so the push path is strictly additional machinery.
- 2-second resolution is more than adequate for time tracking.

**Risk / counterargument:** polling parses roughly 6 KB of JSON every 2 s
indefinitely and prevents the CPU settling on battery, where the event stream
would be near-silent when idle. If that cost shows up in practice,
`events.subscribe` is a clean replacement behind the same `state.py` interface —
`state.py` consumes snapshots, and a push implementation would maintain an
equivalent snapshot incrementally.

### 4.2 Identity key

Events are keyed on the workspace **`label`**, not on `cwd` or `identity_cwd`.

This is forced by real usage: two live workspaces (`w5` "acme risa" and `w6`
"acme apotek1") share the identical `identity_cwd`. Keying on the path would
silently merge two different customer contexts into one bucket of time.

**Cost:** renaming a workspace splits its timeline across two names. A live
`workspace_renamed` event was observed during design, changing a label from
`acme risa` to `acme - risa`. This is accepted: an incorrect merge is
unrecoverable, whereas a split is repairable with an ActivityWatch
categorization rule.

## 5. Bucket 1 — attention

- **ID:** `aw-watcher-herdr_<host>`
- **Type:** `currentwindow`
- **Write mode:** `heartbeat(..., pulsetime=...)`, one event at a time

| Field | Type | Source | Example |
|---|---|---|---|
| `app` | string | `label` of `focused_workspace_id` | `acme risa` |
| `title` | string | focused pane's `terminal_title_stripped`, else the generic label | `Sync cards with tickets` |
| `agent` | string \| null | focused pane's `agent` | `claude` |
| `agent_status` | string | focused pane's `agent_status` | `idle` |
| `workspace_id` | string | opaque id, diagnostic | `w5` |
| `pane_id` | string | opaque id, diagnostic | `w5:p1` |

Using `app`/`title` rather than custom keys is deliberate and carried over from
the cmux watcher: ActivityWatch heartbeat-merges consecutive identical events on
these keys, and its categorization rules match them.

The cmux watcher's `normalize.py` has **no equivalent here and is not ported**.
It existed to guess "is this an agent?" from title regexes and to strip spinner
glyphs. herdr answers the first directly via `agent`/`agent_status`, and
`terminal_title_stripped` already handles the second. Panes with no agent use the
configured `generic_terminal_label` (default `terminal`) so plain-shell time
merges into long blocks instead of fragmenting.

Like its predecessor this watcher **over-emits**: it reports the focused
workspace even when Ghostty is not frontmost, because herdr has no "is my window
frontmost" concept. Correctness is restored at query time (§7).

## 6. Bucket 2 — fleet

- **ID:** `aw-watcher-herdr-agents_<host>`
- **Type:** `app.agent.activity`
- **Write mode:** completed events posted with explicit start + duration.
  **Overlapping events are expected and correct.**

Verified against an isolated `aw-server --testing` on port 5666: three
deliberately overlapping events inserted into one bucket were stored and read
back intact, and `merge_events_by_keys(events, ["app"])` summed their durations
correctly — 1320 s of agent-time inside a 420 s wall-clock window.

| Field | Type | Source | Example |
|---|---|---|---|
| `app` | string | workspace label | `acme risa` |
| `title` | string | last `terminal_title_stripped` seen in the run | `Compare spec with requirements` |
| `status` | string | `working` \| `blocked` \| `done` | `working` |
| `agent` | string | agent kind | `claude` |
| `cwd` | string | agent `cwd` | `/Users/me/proj` |
| `pane_id` | string | opaque id | `w6:p1` |

### 6.1 Run lifecycle

A **run** is one continuous interval of one pane holding one status. Runs are
keyed by `pane_id`.

- **Open** when a pane's `agent_status` enters `working`, `blocked` or `done`.
- **Close** on status change or pane close, writing the event with its computed
  duration. A pane moved to another workspace closes its run and immediately
  opens a new one, because `app` (the workspace label) is part of the event and
  must not change mid-interval. A workspace *rename* is treated identically.
- **`idle` and `unknown` open nothing.** `idle` is the resting state and would
  dominate the bucket; `unknown` explicitly does not prove completion.
- **Never segment on title change.** Coding agents rewrite the terminal title
  continuously as they work, so segmenting there would shred every run into
  fragments. The title recorded is the last one observed before close.

### 6.2 `done` is a duration, not a marker

Because herdr clears `done` the moment the user focuses the tab, the duration of
a `done` run is precisely **how long completed work sat unnoticed** — a response-
latency metric. It is therefore recorded as a true interval rather than a
fixed-length stamp.

A `done` run that spans a night is not a bug; it is a true statement that
finished work waited ten hours for attention.

### 6.3 Failure modes

**Sleep.** A run left open across a laptop sleep would otherwise record hours of
`working` that never happened. If the wall-clock gap between two polls exceeds
`gap_factor × poll_interval` (default 3), all open runs are closed at the last
good poll timestamp and reopened fresh from the new snapshot. This mirrors how
the AFK watcher handles suspension.

**herdr not running.** If the socket is absent or refuses a connection, the
watcher emits nothing — neither bucket — producing a legitimate gap in the
timeline, and logs at debug level only. It does not warn, because "herdr is not
running" is a normal state, not an error. Any open runs are closed first, at the
last good poll timestamp. On the next successful connection the watcher reseeds
from a fresh snapshot rather than assuming continuity.

**Crash.** A `SIGKILL` loses runs still open in memory. Mitigated by closing all
open runs on `SIGTERM`, and by capping any single run at `max_run_seconds`
(default 43200, 12 h) so a wedged watcher cannot emit a three-day event.

*Fallback if crash loss proves real in practice:* emit each run in fixed chunks
(e.g. 60 s) so a crash loses at most one chunk, relying on
`merge_events_by_keys` to sum them back at query time. Not the starting position,
because it multiplies event volume by roughly 60×.

## 7. Query recipes

Attention — gated, as the cmux watcher was, on frontmost application and user
presence. The frontmost app is `Ghostty`, not herdr; since Ghostty may host
non-herdr windows, the window title (`herdr`) further narrows it.

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

Fleet — deliberately **not** gated on AFK or frontmost. Agent work happening
while the user is away is the point of this bucket.

```python
agents = query_bucket(find_bucket("aw-watcher-herdr-agents_"))
RETURN = merge_events_by_keys(filter_keyvals(agents, "status", ["working"]), ["app"])
```

**Never apply `flood()` to the fleet bucket.** `flood` closes gaps by stretching
events within a single timeline; applied to deliberately overlapping events it
corrupts them. This belongs in the README as an explicit warning.

**Known limitation:** ActivityWatch's stock Activity view assumes one
non-overlapping timeline per bucket. The fleet bucket will therefore render
stacked and its "top apps" totals will exceed wall-clock time. This is inherent
to modelling concurrency and is accepted; the fleet bucket is intended to be read
through the queries above rather than the default view.

## 8. Configuration

Config at
`~/Library/Application Support/activitywatch/aw-watcher-herdr/aw-watcher-herdr.toml`.
CLI flags override the file.

| Key | Default | Meaning |
|---|---|---|
| `socket_path` | `~/.config/herdr/herdr.sock` | herdr API socket |
| `poll_interval` | `2.0` | Seconds between snapshots |
| `pulsetime` | `5.0` | Heartbeat merge window (attention bucket) |
| `generic_terminal_label` | `terminal` | Title for panes with no agent |
| `fleet_enabled` | `true` | Emit the agent-fleet bucket at all |
| `fleet_statuses` | `["working", "blocked", "done"]` | Statuses that open a run |
| `max_run_seconds` | `43200` | Hard cap on a single run (§6.3) |
| `gap_factor` | `3.0` | Multiple of `poll_interval` treated as a sleep gap |
| `window_app` | `Ghostty` | Frontmost app name, for the README query recipe |
| `window_title` | `herdr` | Window title, for the README query recipe |

## 9. Testing

**Fixtures must be anonymised.** Live herdr state contains real customer and
project names. The predecessor repo already established this rule in commit
`ccb06b4` ("replace real workspace/company names with generic examples"); the
same applies here. Fixtures use synthetic names throughout.

- `state.py` — table-driven tests over fixture snapshot pairs: run open, run
  close, status change, pane close mid-run, workspace rename mid-run, sleep-gap
  detection, `max_run_seconds` cap, `idle`/`unknown` opening nothing, title churn
  *not* segmenting a run, and four concurrent runs across four panes.
- `herdr.py` — tests against a fake Unix socket server, covering a well-formed
  response, a server error response, a truncated line, and reconnect after the
  socket disappears.
- `emit.py` — tests that the attention writer heartbeats and the fleet writer
  posts non-heartbeat events with explicit durations.
- `scripts/verify.sh` — end-to-end against `aw-server --testing` on port 5666,
  asserting events land in both `aw-watcher-herdr_<host>-testing` and
  `aw-watcher-herdr-agents_<host>-testing`. Simpler than its predecessor: there
  is no Accessibility permission to fail on.

## 10. Deployment

Unchanged in shape from the predecessor: a self-contained venv under
`~/.local/share/aw-watcher-herdr`, installed as a launchd LaunchAgent via
`scripts/install.sh`, so it survives ActivityWatch updates and does not require
editing `/Applications/ActivityWatch.app`.

The installer is materially simpler: no Accessibility prompt, no
`aw-watcher-herdr.app` interpreter wrapper (which existed in the predecessor
purely to give the Accessibility permission entry a clean name), and no manual
System Settings step.

## 11. Out of scope

- Migrating or backfilling historical `aw-watcher-cmux` data.
- Any change to `simensollie/aw-watcher-cmux`, which remains as-is.
- Non-macOS support. herdr runs elsewhere, but the deployment story here is
  launchd-specific.
- A custom ActivityWatch visualisation for the overlapping fleet bucket. The
  query recipes in §7 are the intended interface for now.
- Remote herdr sessions (`herdr --remote`); only the local socket is read.

## 12. Open questions

- **Multiple herdr sessions.** `herdr session list` supports named sessions, each
  presumably with its own socket. The design assumes the single default session.
  If named sessions come into use, the socket path becomes a list and bucket IDs
  need a session discriminator.
- **Window title stability.** `window_title = "herdr"` is drawn from observed
  window-watcher data, and herdr's API includes `client.window_title.set`, so the
  title may vary. It is a config key rather than a constant for that reason, but
  the default should be confirmed against a longer sample.
