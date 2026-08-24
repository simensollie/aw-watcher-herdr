# aw-watcher-herdr — Design

**Status:** Implemented
**Date:** 2026-08-12, amended 2026-08-13
**Supersedes (in spirit):** `simensollie/aw-watcher-cmux` — that repo remains
untouched and installable for cmux users. This is a separate project, not a port.

**Amendment 2026-08-13 (portability).** Sections 3, 4, 5, 7, 8, 9, 10, 11 and 12
were revised after verifying herdr's cross-platform behaviour, ActivityWatch's
module discovery, and herdr's title handling. The changes: a transport seam so a
Windows port needs no rewrite (§4.3), two documented deployment routes including
aw-qt module discovery (§10), a terminal-agnostic frontmost query with detection
helpers (§7), and a correction to the claim that herdr strips all spinner
glyphs (§5). Nothing in the bucket schemas or the run lifecycle changed.

---

## 1. Problem

[herdr](https://herdr.dev) is a terminal workspace manager for AI coding agents.
Being a multiplexer, it runs inside whichever terminal emulator hosts it; here
that is Ghostty. Work is organised into **workspaces** (projects), **tabs**,
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
ActivityWatch on `localhost:5600`. That is the environment the watcher is built
and verified against. It is not, however, the environment the design *depends*
on: the terminal appears only in a query (§7.1), and the operating system only in
the transport and the installer (§4.3, §10).

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
(protocol 19 as of herdr 0.8.0) on macOS and Linux, and a **named pipe** on
Windows. herdr itself supports macOS, Linux and Windows (beta).

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
- The CLI wrapper `herdr api snapshot` returns the **identical envelope** to a
  raw `session.snapshot` call, in roughly 5 to 10 ms per invocation. herdr's own
  documentation recommends the CLI wrappers over raw socket clients for plugins
  on Windows, because they handle the platform-native socket form. This makes the
  wrapper the Windows transport (§4.3).
- With no server reachable, `herdr api snapshot` exits 1 and prints
  `{"id": ..., "error": {"code": "server_not_running", "message": ...}}`. The
  failure is therefore machine-readable, not a bare crash.
- `HERDR_SESSION=<name>` does **not** select a session for `herdr api`: setting
  it to a non-existent name silently returned the default session's snapshot.
  Only `HERDR_SOCKET_PATH` was observed to redirect the call. This constrains any
  future named-session support (§12).

Representative snapshot fields, trimmed:

```json
{
  "focused_workspace_id": "w5",
  "focused_tab_id": "w5:t1",
  "focused_pane_id": "w5:p1",
  "workspaces": [
    {"workspace_id": "w5", "label": "beta client-one", "agent_status": "idle", "focused": true}
  ],
  "agents": [
    {"agent": "claude", "agent_status": "working", "workspace_id": "w4",
     "pane_id": "w4:p1", "cwd": "/Users/me/proj",
     "terminal_title": "✳ Ingest latest meetings",
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

New repository `simensollie/aw-watcher-herdr`. Python 3.10+, with `aw-client` as
the only runtime dependency — it brings `aw-core`, which supplies `Event`,
`load_config_toml` and `setup_logging`. No `pyobjc`.

```
aw_watcher_herdr/
  herdr.py    snapshot sources   — shared envelope parsing, two transports (§4.3)
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

This is forced by real usage: two live workspaces (`w5` "beta client-one" and
`w6` "beta client-two") share the identical `identity_cwd`. Keying on the path
would silently merge two different customer contexts into one bucket of time.

**Cost:** renaming a workspace splits its timeline across two names. A live
`workspace_renamed` event was observed during design, changing a label from
`beta client-one` to `beta - client-one`. This is accepted: an incorrect merge is
unrecoverable, whereas a split is repairable with an ActivityWatch
categorization rule.

### 4.3 Transport seam

`state.py` and `emit.py` consume plain dicts and are already platform-free. Every
platform difference in the data path is therefore confined to `herdr.py`, which
exposes one interface and two implementations:

| Component | Role |
|---|---|
| `SnapshotSource` | Protocol with a single method, `snapshot() -> dict` |
| `UnixSocketSource` | Connect-per-request client over `AF_UNIX` (macOS, Linux) |
| `CliSource` | `subprocess.run(["herdr", "api", "snapshot"])` (Windows, and available anywhere) |
| `_parse_envelope` | Shared result / error / malformed handling for both |
| `resolve_source` | `auto` picks the socket on POSIX and the CLI on Windows |

The Windows path needs the CLI because herdr uses a named pipe there and CPython
exposes no `socket.AF_UNIX` on Windows. Attempting to open the pipe directly is
rejected: its path is undocumented, whereas the CLI wrapper is the route herdr's
own documentation endorses.

`CliSource` maps failures onto the existing exception pair:

| Observed | Raised |
|---|---|
| exit 1 with `error.code == "server_not_running"` | `HerdrUnavailable` |
| binary absent from `PATH` (`FileNotFoundError`) | `HerdrUnavailable` |
| subprocess timeout | `HerdrUnavailable` |
| any other `error` envelope | `HerdrError` |
| non-JSON on stdout | `HerdrError` |

**Cost / counterargument.** A subprocess per poll costs roughly 5 to 10 ms
against roughly 0.1 ms for a socket read, so at a 2 s interval the CLI path uses
about 0.5% of one core. That is acceptable for the platform that has no
alternative, and it is not the default anywhere else. The seam is built now
rather than at port time because the alternative is rewriting `herdr.py` and its
whole test file later; building it now costs one protocol, one class and one test
module.

**Verifiability.** `CliSource` cannot be exercised on Windows from the
development machine. Because `source` is an explicit config key, `--source cli`
runs the identical code path on macOS, so the implementation is covered by real
use rather than by fakes alone. Only the platform *selection* in `resolve_source`
is tested with a patched `sys.platform`.

**Where the operating system may be consulted (exhaustive).** Three code sites,
and no others, are allowed to read `sys.platform` (or otherwise branch on the
OS). Any new site is a design change, not an implementation detail:

| Site | Why it must branch | Blast radius |
|---|---|---|
| `herdr.py` (`resolve_source`, `UnixSocketSource`) | herdr speaks a Unix socket on POSIX and a named pipe on Windows (this section) | The transport, behind `SnapshotSource` |
| `lock.py` | the single-instance lock uses `fcntl.flock` on POSIX and is a no-op on Windows (§10.3) | Startup only |
| `__main__.default_window_apps()` | §7.1: `aw-watcher-window` reports a different `app` string per platform, so the *default* for `window_app` is necessarily per-platform | One default config value, overridable by file or flag |

Installer shell under `scripts/` is outside this rule: it selects a deployment
route per OS by construction (§10).

The third site is a deliberate carve-out rather than an oversight. It is a
static value lookup, touches no platform-specific API (no `socket.AF_UNIX`, no
`subprocess`, no OS-specific paths), and holds no resource, so it carries none
of the portability risk the rule exists to contain. Moving it into `herdr.py`
would put a windowing-query default inside the herdr transport abstraction,
coupling two unrelated concerns to satisfy the letter of the rule.

**Counterargument.** An enumerated exception is weaker than an absolute ban: a
future contributor can cite it as precedent for a fourth site. The mitigation is
that this list is exhaustive and normative, so adding to it requires editing the
spec, which is visible in review.

## 5. Bucket 1 — attention

- **ID:** `aw-watcher-herdr_<host>`
- **Type:** `currentwindow`
- **Write mode:** `heartbeat(..., pulsetime=...)`, one event at a time

| Field | Type | Source | Example |
|---|---|---|---|
| `app` | string | `label` of `focused_workspace_id` | `beta client-one` |
| `title` | string | `app`, tab label and terminal name composed (§5.2) | `beta client-one · cards · Sync cards with tickets` |
| `tab` | string | `label` of the focused pane's tab | `cards` |
| `agent` | string \| null | focused pane's `agent` | `claude` |
| `agent_status` | string | focused pane's `agent_status` | `idle` |
| `workspace_id` | string | opaque id, diagnostic | `w5` |
| `tab_id` | string \| null | opaque id, diagnostic | `w5:t1` |
| `pane_id` | string | opaque id, diagnostic | `w5:p1` |

The tab comes from the focused **pane's** `tab_id`, falling back to
`focused_tab_id` when no pane is focused: the pane is the authority on which tab
it lives in, and a snapshot caught mid-move can disagree. A tab absent from the
`tabs` list yields an empty label rather than suppressing the whole Attention,
for the same reason an unlabeled workspace does.

Using `app`/`title` rather than custom keys is deliberate and carried over from
the cmux watcher: ActivityWatch heartbeat-merges consecutive identical events on
these keys, and its categorization rules match them.

The cmux watcher's `normalize.py` has **no equivalent here and is not ported**.
It existed to guess "is this an agent?" from title regexes and to strip spinner
glyphs. herdr answers the first directly via `agent`/`agent_status`, which
removes the regex guessing entirely. It answers the second only partially, so a
much smaller replacement is needed (§5.1). Panes with no agent use the configured
`generic_terminal_label` (default `terminal`) so plain-shell time merges into
long blocks instead of fragmenting.

### 5.2 Composed display title (amended 2026-08-24)

ActivityWatch renders `app` and `title` only. Any other field is queryable but
invisible in the timeline and in the "Top window titles" summary, so a tab label
recorded solely as its own key would never be seen. `title` is therefore
composed as `space · tab · terminal name` by one shared pure function, applied
to both buckets so a task reads identically in each. An empty terminal name is
substituted with `generic_terminal_label` before composition in both writers,
so the identical-composition rule holds for plain shells as well as agent tasks.

- The separator is U+00B7 MIDDLE DOT, not a hyphen: workspace and tab labels
  routinely contain hyphens (`aw-watcher-herdr`), and a hyphenated composition
  could not be split back into its parts.
- The space is repeated even though `app` carries it, because the titles-only
  summary shows no `app`, and tab labels are not unique across spaces (two
  spaces can both hold a tab called `Status`).
- Empty segments are dropped rather than rendered as a bare separator, which
  would read as a missing value instead of an absent one. A **default ordinal**
  label (`1`) is not empty and is kept: dropping it would give two tabs of one
  space the same title and silently merge unrelated work.
- `tab` and `tab_id` are recorded as their own fields as well, so a query can
  group by tab without parsing the composed string apart.

**Breaking change.** The `title` format differs from every event written before
this amendment, so events either side of the change do not heartbeat-merge and
any categorization rule matching the old titles needs updating. Historical data
is left as written, not migrated.

### 5.1 Leading glyph strip

`terminal_title_stripped` does not remove every status glyph. Measured against
live herdr 0.8.0:

```
'✳ Fetch and ingest latest meetings' -> 'Fetch and ingest latest meetings'   stripped
'◐ Brainstorm terminal architecture' -> '◐ Brainstorm terminal architecture' kept
```

Left alone, one task therefore produces two distinct titles either side of a
working-to-idle transition, fragmenting the attention timeline and putting glyphs
into the ActivityWatch UI. Both writers therefore strip a leading run of
non-alphanumeric characters (and the whitespace after it) from the title before
emitting. This is a few lines in `state.py`, applied to both buckets so the same
task reads identically in each.

**Limit of the evidence:** six samples about a second apart were identical, so
`◐` was not observed animating within a snapshot. This is not a claim that it
never does. If it does animate, the same strip removes the churn, which is part
of why the fix is worth making rather than deferring.

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
| `app` | string | workspace label | `beta client-one` |
| `title` | string | `app`, last tab label and last `terminal_title_stripped` seen in the run, glyph-stripped per §5.1 and composed per §5.2 | `beta client-one · specs · Compare spec with requirements` |
| `tab` | string | last tab label seen in the run | `specs` |
| `status` | string | `working` \| `blocked` \| `done` | `working` |
| `agent` | string | agent kind | `claude` |
| `cwd` | string | agent `cwd` | `/Users/me/proj` |
| `tab_id` | string | opaque id | `w6:t1` |
| `pane_id` | string | opaque id | `w6:p1` |

### 6.1 Run lifecycle

A **run** is one continuous interval of one pane holding one status. Runs are
keyed by `pane_id`.

`tab_id` is part of the run key and the tab **label** is not: moving a pane to
another tab is a change of context and must close the interval, exactly as
moving it to another workspace does, while renaming a tab is presentation only
and must not split it. The label follows the same rule as the terminal title —
the most recent non-empty value wins, so a rename relabels the interval it
happened in.

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
presence. herdr is a multiplexer running *inside* a terminal, so the frontmost
application is the terminal emulator, never herdr itself. Nothing in the data
path is Ghostty-specific: the terminal name appears only in this query.

```python
afk      = flood(query_bucket(find_bucket("aw-watcher-afk_", "<host>")))
window   = flood(query_bucket(find_bucket("aw-watcher-window_", "<host>")))
herdr    = flood(query_bucket(find_bucket("aw-watcher-herdr_", "<host>")))
not_afk  = filter_keyvals(afk, "status", ["not-afk"])
in_herdr = filter_keyvals(window, "app", ["Ghostty"])   # from window_app
events   = filter_period_intersect(herdr, in_herdr)
events   = filter_period_intersect(events, not_afk)
RETURN   = merge_events_by_keys(events, ["app", "title"])
```

**The hostname argument is mandatory** (amended 2026-08-13 after end-to-end
verification). `find_bucket` returns the first bucket whose id merely *contains*
the filter string and ignores hostname unless one is passed. A machine that has
been renamed keeps one bucket per hostname it has ever had, and only the current
one still receives events, so the unqualified form resolves to a dead bucket:
both gates then intersect against nothing and the query reports zero hours worked
without raising. Qualified, a mismatch fails loudly instead. `--print-query`
substitutes the local hostname the same way the watcher builds its own bucket id.

### 7.1 Terminal identity is not portable

`aw-watcher-window` reports a different `app` value per platform for the same
terminal: `Ghostty` on macOS, the WM_CLASS on Linux/X11, the executable name on
Windows. One hardcoded default is therefore wrong on two platforms out of three,
and a single string cannot express "Ghostty at my desk, Terminal over a remote
session". `window_app` is consequently a **list** (§8), defaulting to
`["Ghostty"]` on macOS and to `[]` elsewhere, where an empty list means "not yet
configured" and drops the filter from the rendered query rather than silently
matching nothing.

That default is computed by `__main__.default_window_apps()`, the third and last
site permitted to read `sys.platform` (§4.3 lists all three).

Two commands turn what were previously documentation-only config keys into
working features:

- `--detect-terminal` reads `aw-watcher-window_<host>` over the last 24 hours and
  prints the `app` values by total duration, so the correct value is read off the
  user's own data rather than guessed. It picks the bucket whose recorded
  hostname is this machine's (falling back to the most recently updated match),
  never whichever the server happens to list first, for the same
  renamed-machine reason as above.
- `--print-query` renders the query above with the configured `window_app` list
  substituted, ready to paste. The `window_title` narrowing is emitted only when
  that key is set.

**Linux caveat.** `aw-watcher-window` is X11 only. On Wayland the attention
bucket still fills correctly, because this watcher never touches the window
system, but the gating half of the query needs `aw-watcher-window-wayland` or
`awatcher` to have any window data to intersect with.

Fleet — deliberately **not** gated on AFK or frontmost. Agent work happening
while the user is away is the point of this bucket.

```python
agents = query_bucket(find_bucket("aw-watcher-herdr-agents_", "<host>"))
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

Config file location is whatever `aw-core` resolves for the client name, which on
macOS is
`~/Library/Application Support/activitywatch/aw-watcher-herdr/aw-watcher-herdr.toml`
and differs on Linux and Windows. Nothing in the watcher hardcodes it. CLI flags
override the file.

| Key | Default | Meaning |
|---|---|---|
| `source` | `auto` | Transport: `auto`, `socket` or `cli` (§4.3) |
| `socket_path` | `~/.config/herdr/herdr.sock` | herdr API socket, used by the socket source |
| `herdr_binary` | `herdr` | Executable invoked by the CLI source |
| `poll_interval` | `2.0` | Seconds between snapshots |
| `pulsetime` | `5.0` | Heartbeat merge window (attention bucket) |
| `generic_terminal_label` | `terminal` | Title for panes with no agent |
| `fleet_enabled` | `true` | Emit the agent-fleet bucket at all |
| `fleet_statuses` | `["working", "blocked", "done"]` | Statuses that open a run |
| `max_run_seconds` | `43200` | Hard cap on a single run (§6.3) |
| `gap_factor` | `3.0` | Multiple of `poll_interval` treated as a sleep gap |
| `window_app` | `["Ghostty"]` on macOS, `[]` elsewhere | Terminal app names for the gating query (§7.1) |
| `window_title` | unset | Optional window-title narrowing for that query |

`window_app` is a list. `window_title` now defaults to unset rather than `herdr`,
because `--detect-terminal` supplies the real value from the user's own window
data instead of the design guessing it.

## 9. Testing

**Fixtures must be anonymised.** Live herdr state contains real customer and
project names. The predecessor repo already established this rule in commit
`ccb06b4` ("replace real workspace/company names with generic examples"); the
same applies here. Fixtures use synthetic names throughout.

- `state.py` — table-driven tests over fixture snapshot pairs: run open, run
  close, status change, pane close mid-run, workspace rename mid-run, sleep-gap
  detection, `max_run_seconds` cap, `idle`/`unknown` opening nothing, title churn
  *not* segmenting a run, and four concurrent runs across four panes.
- `state.py` — the §5.1 glyph strip: a stripped title, an unstripped one, a title
  that is only glyphs, and an already-clean title left untouched.
- `herdr.py` (socket) — tests against a fake Unix socket server, covering a
  well-formed response, a server error response, a truncated line, and reconnect
  after the socket disappears.
- `herdr.py` (CLI) — tests against a fake `herdr` script on a temporary `PATH`,
  covering a well-formed envelope, `server_not_running` with exit 1, a missing
  binary, a timeout, and non-JSON stdout. Envelope handling is shared, so those
  cases are parametrised across both sources rather than duplicated.
- `resolve_source` — platform selection with `sys.platform` patched.
- `emit.py` — tests that the attention writer heartbeats and the fleet writer
  posts non-heartbeat events with explicit durations.
- `--print-query` — rendering with one app, several apps, and no `window_title`.
- Single-instance lock — a second instance refuses to start while the first holds
  the lock, and the lock is released on exit.
- `scripts/verify.sh` — end-to-end against `aw-server --testing` on port 5666,
  asserting events land in both `aw-watcher-herdr_<host>-testing` and
  `aw-watcher-herdr-agents_<host>-testing`. Simpler than its predecessor: there
  is no Accessibility permission to fail on.

## 10. Deployment

ActivityWatch has **no plugin system**. Every watcher is a separate operating
system process, so something is always installed on the machine; there is no
route that drops a file into ActivityWatch and has it loaded. What exists instead
is aw-qt's module discovery, and that is close enough to serve as one.

Two routes are supported and both are documented. **On macOS the default is the
launchd agent (§10.2), which works on the current stable ActivityWatch.** The
aw-qt route (§10.1) exists because it is the only answer for Linux and Windows;
on macOS it is an option that becomes available on ActivityWatch 0.14.x, not a
prerequisite for anything.

### 10.1 aw-qt module (Linux and Windows; macOS on 0.14.x)

`aw-qt` discovers executables named `aw-*` both alongside its own binary and on
`PATH`, lists them in the tray menu, and starts them when they appear in
`autostart_modules` in `aw-qt.toml`. Installing with `pipx` puts
`aw-watcher-herdr` in `~/.local/bin`, which qualifies. No launchd job, no systemd
unit and no Windows startup entry is needed, which is why this is the **only**
install route documented for Linux and Windows in v1.

**Why this needs 0.14.x on macOS.** ActivityWatch is a login item, so aw-qt
inherits launchd's default environment. Measured on the running process:

```
$ ps eww -p <aw-qt pid> | tr ' ' '\n' | grep ^PATH=
PATH=/usr/bin:/bin:/usr/sbin:/sbin
```

All four are SIP-protected, so on 0.13.2 there is no directory a user can write
to that aw-qt searches, which is why its log reads `Found 0 system modules`. The
commit adding `~/.local/bin`, `/opt/homebrew/bin` and `/usr/local/bin` to the
macOS search is dated 2026-03-12, after the 0.13.2 release of 2024-10-05, so this
route needs **0.14.x** (0.14.0b3, 2026-07-28, at time of writing). The same line
added `.bat`/`.cmd` discovery on Windows and auto-restart of crashed watchers.

Linux and Windows are unaffected by that commit, because aw-qt there is normally
launched from a session that already has a user `PATH`.

**Rejected workaround.** `/Applications/ActivityWatch.app/Contents/MacOS/` is
writable and is scanned by `_discover_modules_bundled` on 0.13.2. Installing
there breaks the bundle's code signature and is erased by every ActivityWatch
update, which is the exact failure §10.2 was designed to avoid. Do not do this.

**Counterargument:** 0.14.x is a beta, and a watcher managed by aw-qt stops
whenever ActivityWatch stops.

### 10.2 launchd LaunchAgent (macOS, the default)

As the predecessor: a self-contained venv under `~/.local/share/aw-watcher-herdr`
installed by `scripts/install.sh`, surviving ActivityWatch updates and requiring
no edits to `/Applications/ActivityWatch.app`. It works on 0.13.2 today and is
independent of ActivityWatch's lifecycle. The installer additionally symlinks the
entry point into `~/.local/bin`, so route 10.1 becomes available as soon as
ActivityWatch is upgraded, without reinstalling.

A second advantage on Apple Silicon: the installed aw-qt 0.13.2 is an `x86_64`
binary running under Rosetta, and a watcher it spawns can inherit that
translation preference. launchd starts the venv interpreter directly, so the
watcher runs natively regardless of how ActivityWatch itself was built. The venv
must therefore be created with a native `arm64` Python, which `install.sh`
asserts.

The installer is materially simpler than the predecessor's: no Accessibility
prompt, no `aw-watcher-herdr.app` interpreter wrapper (which existed purely to
give the Accessibility permission entry a clean name), and no manual System
Settings step.

### 10.3 Single-instance lock

Two routes that can both start the watcher means both can run at once. That would
double every event in the fleet bucket, and the fleet bucket cannot detect it,
because overlapping events are expected and correct there (§6). Silent
double-counting is the worst available failure.

The watcher therefore takes an advisory `flock` on a lock file in its config
directory at startup and exits with a clear message if another instance holds it.
On Windows this is a no-op with a comment, since only route 10.1 exists there.

## 11. Out of scope

- Migrating or backfilling historical `aw-watcher-cmux` data.
- Any change to `simensollie/aw-watcher-cmux`, which remains as-is.
- Platform-native service files beyond macOS: no systemd user unit, no Windows
  Task Scheduler entry. Linux and Windows are served by route 10.1 instead. The
  transport seam (§4.3) means adding them later needs no rewrite.
- End-to-end verification on Linux or Windows. Neither platform is available on
  the development machine, and the README says so rather than implying coverage
  that does not exist.
- A custom ActivityWatch visualisation for the overlapping fleet bucket. The
  query recipes in §7 are the intended interface for now.
- Remote herdr sessions (`herdr --remote`); only the local socket is read.

## 12. Open questions

- **Multiple herdr sessions.** Named sessions live at
  `~/.config/herdr/sessions/<name>/herdr.sock`. The design assumes the single
  default session; a named one produces an honest gap rather than wrong data,
  because the default socket simply is not there. Support would need a session
  discriminator in the bucket IDs, and must route through `HERDR_SOCKET_PATH`:
  `HERDR_SESSION` was verified **not** to select a session for `herdr api` (§3).
- **Windows named pipe path.** Undocumented, which is why `CliSource` shells out
  rather than opening it. If herdr documents the path later, a direct
  `NamedPipeSource` would drop into the same seam and remove the subprocess cost.
- **ActivityWatch 0.14 stability.** Route 10.1 depends on a beta. If 0.14 proves
  unstable, macOS falls back to route 10.2 and Linux needs the systemd unit that
  §11 currently excludes.
