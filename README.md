# aw-watcher-herdr

An [ActivityWatch](https://activitywatch.net/) watcher for
[herdr](https://herdr.dev), the terminal workspace manager for AI coding agents.

It records two things:

- **Attention:** which herdr workspace and agent session you are looking at.
- **Agent fleet:** what every agent is doing *concurrently*, on which project,
  including how long agents sit blocked waiting on you and how long finished
  work goes unnoticed.

It reads herdr's local API, so it needs **no macOS Accessibility permission**.

## Platform support

| Platform | Transport | Install | Verified |
|---|---|---|---|
| macOS | Unix socket | launchd agent (or aw-qt on AW 0.14.x) | yes |
| Linux | Unix socket | aw-qt module | no |
| Windows | `herdr api snapshot` | aw-qt module | no |

herdr is a multiplexer, so **any terminal emulator works**: the terminal's name
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

### `aw-watcher-herdr_<host>`: attention

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

### `aw-watcher-herdr-agents_<host>`: agent fleet

Type `app.agent.activity`. One event per agent per status-run, with real start
and duration. **These events overlap by design**: that is what concurrency
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
attention by intersecting with the window and AFK watchers, the same separation
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

Deliberately **not** gated on AFK or frontmost: agent work happening while you
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

## Status

Implemented and verified end to end on macOS (launchd route, both the socket
and CLI transports). See [the design spec](docs/superpowers/specs/2026-08-12-herdr-activitywatch-watcher-design.md)
and [the implementation plan](docs/superpowers/plans/). macOS is the only
verified platform; Linux and Windows are supported by design but untested.

## Related

[aw-watcher-cmux](https://github.com/simensollie/aw-watcher-cmux) does the
equivalent job for cmux and is maintained separately.

## License

[MPL-2.0](LICENSE), matching the ActivityWatch ecosystem.
