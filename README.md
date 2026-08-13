# aw-watcher-herdr

An [ActivityWatch](https://activitywatch.net/) watcher for
[herdr](https://herdr.dev), the terminal workspace manager for AI coding agents.

It records two things:

- **Attention** — which herdr workspace and agent session you are looking at.
- **Agent fleet** — what every agent is doing concurrently, on which project,
  including how long they sit blocked waiting on you and how long finished work
  goes unnoticed.

It reads herdr's local socket API, so it needs **no macOS Accessibility
permission** and works from a detached launchd agent.

Built and verified on macOS. Nothing in the data path is macOS-specific or
terminal-specific: herdr is a multiplexer, so any terminal works, and the one
platform-bound piece (the transport) sits behind an interface with a
`herdr api snapshot` implementation for Windows, where herdr uses a named pipe.
Linux and Windows are installed through aw-qt's module discovery rather than
launchd, and neither is end-to-end verified yet.

> **Status: design complete, implementation not started.**
> See [the design spec](docs/superpowers/specs/2026-08-12-herdr-activitywatch-watcher-design.md)
> and [the implementation plan](docs/superpowers/plans/).

Not to be confused with [aw-watcher-cmux](https://github.com/simensollie/aw-watcher-cmux),
which does the equivalent job for cmux and remains separately maintained.

## License

[MPL-2.0](LICENSE), matching the ActivityWatch ecosystem.
