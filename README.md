# aw-watcher-herdr

An [ActivityWatch](https://activitywatch.net/) watcher for
[herdr](https://herdr.dev), the terminal workspace manager for AI coding agents.

## Why this exists

Conventional time tracking rests on two assumptions: one window is one task, and
work happens while you are watching it. Coding agents in a multiplexer break both.

ActivityWatch's window watcher sees one terminal window called `Ghostty`, for
eight hours, however many projects passed through it. herdr keeps each project in
its own workspace inside that single window, so the entire project dimension is
invisible from outside: a day spent across six codebases records as one
undifferentiated block.

Agents break the second assumption. They keep working while you look away, several
at once, so "how long was this window in front of me" stops measuring work done.
Worse, the moments that cost you most are the ones where nothing is happening on
your screen: an agent blocked waiting on your answer, or an agent that finished
while you were somewhere else.

So this watcher records two different things and deliberately keeps them apart:

- **Attention** is where *you* were. One workspace at a time, the project in front
  of you, the task its agent was on, merged into a single timeline you can
  intersect with AFK and frontmost like any other ActivityWatch data.
- **Agent fleet** is what your *agents* were doing. One interval per agent per
  status, freely overlapping, never gated on whether you were present. This is
  concurrency, so it is meant to exceed wall-clock time.

Keeping them in separate buckets is the point. Merged, parallel agent work would
inflate your own hours; gated on presence, the agent work that happened while you
were away would vanish, which is exactly the work worth knowing about.

## What you can answer with it

| Question | How |
|---|---|
| Where did my day go, per project, through one terminal window? | Attention bucket, gated on AFK and frontmost |
| How much agent work ran in parallel with mine? | Fleet bucket, `status = working`, ungated |
| How long did agents sit blocked waiting on me? | Fleet bucket, `status = blocked`. Your response latency, measured |
| How long did finished work sit unnoticed? | Fleet bucket, `status = done`. herdr clears `done` when you focus the tab, so the duration is exactly how long you took to notice |
| Which projects eat attention out of proportion to their agent time? | Both buckets, grouped by `app` |

Ready-made queries for the first four are in [Queries](#queries), and
`--print-query` prints them with your own hostname and terminal filled in.

It reads herdr's local API rather than the window server, so it needs **no macOS
Accessibility permission** and runs from a detached background agent.

## Platform support

| Platform | Transport | Install | Verified |
|---|---|---|---|
| macOS | Unix socket | launchd agent (or aw-qt on AW 0.14.x) | yes, end to end |
| Linux | Unix socket | aw-qt module | no |
| Windows | `herdr api snapshot` | aw-qt module | no |

herdr is a multiplexer, so **any terminal emulator works**: the terminal's name
appears only in a query recipe, never in the data path. On Windows herdr uses a
named pipe, which CPython cannot open, so the watcher shells out to herdr's CLI
wrapper instead; that is the route herdr's own documentation recommends.

macOS is verified end to end, on both transports and through both an isolated
`aw-server --testing` and a real one: `scripts/install.sh`, the LaunchAgent it
loads, live herdr snapshots in, correct events out. Linux and Windows are
supported by design but have never been executed. Nothing longer than a short
session has been measured, so the 12-hour run cap and the sleep/suspend gap
handling rest on unit tests rather than on a real multi-day run.

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

### `aw-watcher-herdr_<host>`: attention

Type `currentwindow`, one event at a time, heartbeat-merged.

| Field | Example | Notes |
|---|---|---|
| `app` | `beta app` | Focused workspace label (the project dimension) |
| `title` | `beta app · import · Add retry logic to the import job` | Space, tab and the focused pane's agent task (or `terminal`), composed |
| `tab` | `import` | Focused tab label, `1` if never renamed |
| `agent` | `claude` | Agent kind, or null |
| `agent_status` | `working` | herdr's own classification |
| `workspace_id` / `tab_id` / `pane_id` | `w2` / `w2:t1` / `w2:p1` | Opaque ids, diagnostic |

`title` repeats the space that `app` already carries because ActivityWatch
renders `app` and `title` only: a field outside those two is invisible in the
timeline and in the "Top window titles" summary, which shows titles alone. The
tab is therefore folded into the title **and** kept as `tab`, so a query can
group by tab without parsing the string apart. An unlabeled segment is dropped
rather than rendered as a bare separator; a default ordinal label (`1`) is not
empty and is kept, since dropping it would give two tabs of one space the same
title and merge unrelated work.

Events are keyed on the workspace **label**, not the working directory: two
workspaces can share one `cwd` while representing different contexts, and
merging them would be unrecoverable. The cost is that renaming a workspace
splits its timeline; fix that with a categorization rule if it bites.

### `aw-watcher-herdr-agents_<host>`: agent fleet

Type `app.agent.activity`. One event per agent per status-run, with real start
and duration. **These events overlap by design**: that is what concurrency
looks like.

| Field | Example |
|---|---|
| `app` | `beta app` |
| `title` | `beta app · import · Add retry logic to the import job` |
| `tab` | `import` |
| `status` | `working` / `blocked` / `done` |
| `agent` | `claude` |
| `cwd` | `/home/dev/beta-app` |
| `tab_id` / `pane_id` | `w2:t1` / `w2:p1` |

Titles are composed identically in both buckets, so one task reads the same in
each. Moving a pane to another tab closes the run and opens a new one, the same
way moving it to another workspace does; **renaming** a tab does not, so a
rename relabels the interval it happened in rather than splitting it. That is
the same rule the terminal title already follows.

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

ActivityWatch has no plugin system (every watcher is a separate process), but
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

### What "installed" means in ActivityWatch

There is nothing to enable on the ActivityWatch side. ActivityWatch has no plugin
system: a watcher is just a process that creates its own buckets and posts events,
so it registers itself the first time it connects. Two consequences:

- Your data appears in the web UI at <http://localhost:5600> under **Raw Data**,
  as `aw-watcher-herdr_<host>` and `aw-watcher-herdr-agents_<host>`, and in the
  query view. No setting, no restart of aw-server.
- The watcher does **not** appear in the aw-qt tray menu on the launchd route. The
  tray only lists modules aw-qt itself discovered and supervises, and launchd is
  supervising this one instead. That is a cosmetic difference, not a broken
  install. Use the aw-qt route above if you want the tray entry.

The stock Activity view will not show this data usefully (it assumes one
non-overlapping timeline per bucket and knows nothing about workspaces), which is
why the [Queries](#queries) below exist.

### Check it is working

```bash
aw-watcher-herdr --snapshot | head -3          # can it read herdr?
tail -5 ~/Library/Logs/activitywatch/aw-watcher-herdr.log
launchctl list | grep aw-watcher-herdr          # pid, and 0 as the last exit status
curl -s "http://localhost:5600/api/0/buckets/aw-watcher-herdr_$(hostname)/events?limit=1"
```

The last command returning an event with a non-zero `duration` is the real proof:
herdr read, event written, bucket registered.

## Queries

Print the queries for your own configuration, with your hostname already filled
in:

```bash
aw-watcher-herdr --print-query
```

Every `find_bucket` below takes the hostname as its second argument, and that is
not optional. Without it, `find_bucket` returns the first bucket whose id merely
*contains* the filter. A machine that has been renamed keeps one bucket per old
hostname, so the unqualified form resolves to a long-dead bucket and the query
reports zero hours worked without raising a single error. Qualified, a
mismatched hostname fails loudly with `Unable to find bucket` instead. Replace
`YOUR-HOSTNAME` with the value `--print-query` shows (the hostname in your
bucket names, visible in the ActivityWatch UI).

### Active herdr time

The watcher over-emits: herdr has no "am I frontmost" concept, so the focused
workspace is reported whether or not you are looking at it. Recover real
attention by intersecting with the window and AFK watchers, the same separation
of concerns ActivityWatch already uses for AFK.

```python
afk      = flood(query_bucket(find_bucket("aw-watcher-afk_", "YOUR-HOSTNAME")));
herdr    = flood(query_bucket(find_bucket("aw-watcher-herdr_", "YOUR-HOSTNAME")));
not_afk  = filter_keyvals(afk, "status", ["not-afk"]);
window   = flood(query_bucket(find_bucket("aw-watcher-window_", "YOUR-HOSTNAME")));
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

That reads the window bucket belonging to *this* machine's hostname, so the
stale buckets left behind by earlier hostnames are ignored.

On Linux/Wayland the stock `aw-watcher-window` records nothing (it is X11 only).
The attention bucket still fills correctly, but you need
`aw-watcher-window-wayland` or `awatcher` for the frontmost gating to work.

### Agent-hours per project

Deliberately **not** gated on AFK or frontmost: agent work happening while you
are away is the whole point.

```python
agents   = query_bucket(find_bucket("aw-watcher-herdr-agents_", "YOUR-HOSTNAME"));
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
| `source` | `auto` | `auto`, `socket` or `cli`; anything else is a config error |
| `socket_path` | `~/.config/herdr/herdr.sock` | herdr API socket (socket source); a leading `~` is expanded |
| `herdr_binary` | `herdr` | Executable invoked by the CLI source |
| `poll_interval` | `2.0` | Seconds between snapshots; must be greater than 0 |
| `pulsetime` | `5.0` | Heartbeat merge window (attention bucket); must be greater than 0 |
| `generic_terminal_label` | `terminal` | Title for panes with no agent |
| `fleet_enabled` | `true` | Emit the agent-fleet bucket at all |
| `fleet_statuses` | `["working", "blocked", "done"]` | Statuses that open a run; a bare string is accepted as a one-element list |
| `max_run_seconds` | `43200` | Hard cap on a single run; must be greater than 0 |
| `gap_factor` | `3.0` | Multiple of `poll_interval` treated as a sleep gap; must be greater than 1 |
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
| No buckets created | aw-server is not reachable. Attention heartbeats are queued and flush on reconnect; fleet events are buffered in memory (up to 10 000) and retried. Ten consecutive failed retries log a warning, so a permanent failure (a deleted bucket, a rejected payload) does not stay silent. |
| `--detect-terminal` says it cannot read from ActivityWatch | aw-server is not running. Start ActivityWatch (or `aw-server --testing` for a `--testing` run). |
| aw-qt tray does not list the watcher | On macOS this needs ActivityWatch 0.14.x; see the install section. |
| Fleet totals exceed 24 h in a day | Expected. Agents run concurrently; see the known limitation above. |
| Timeline splits after renaming a workspace | Expected. `app` is the workspace label; add a categorization rule to merge the two names. |
| Attention events with an empty `app` | The focused herdr workspace has no label. Time is still recorded (the fleet bucket does the same) and the watcher logs a warning once; label the workspace in herdr to make it attributable. |
| Query returns nothing on Linux/Wayland | The stock window watcher is X11 only. Use `aw-watcher-window-wayland` or drop the frontmost filter. |
| Query returns zero hours, no error | A `find_bucket` without the hostname argument matched a stale bucket from an earlier hostname. Re-generate the query with `--print-query`. |
| `Unable to find bucket matching ...` | The hostname in the query is not the one your watchers recorded. Take it from your bucket names in the ActivityWatch UI. |
| Exits immediately with a `poll_interval`/`gap_factor`/... message | A config value would fail silently (a spinning loop, an empty bucket, a zero-duration timeline), so it is rejected instead of clamped. The message says which key and why. |

## Status

Implemented and running. Verified end to end on macOS in three ways: against an
isolated aw-server on both transports (`scripts/verify.sh`), and as an installed
LaunchAgent writing to a real aw-server, where a detached background agent reads
herdr's socket and produces merged `currentwindow` attention events alongside
overlapping `app.agent.activity` fleet events.

Not verified: Linux and Windows, which are supported by design but have never
been executed, and anything longer than a short session, so the run cap and the
sleep/suspend gap handling rest on unit tests with a scripted clock rather than
on a real multi-day run.

See [the design spec](docs/superpowers/specs/2026-08-12-herdr-activitywatch-watcher-design.md)
and [the implementation plan](docs/superpowers/plans/).

## Related

[aw-watcher-cmux](https://github.com/simensollie/aw-watcher-cmux) does the
equivalent job for cmux and is maintained separately.

## License

[MPL-2.0](LICENSE), matching the ActivityWatch ecosystem.
