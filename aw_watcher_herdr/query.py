"""Query rendering and terminal detection (spec §7, §7.1).

herdr is a multiplexer, so the frontmost application is always the terminal
emulator hosting it, never herdr. aw-watcher-window reports that terminal
differently on every platform (`Ghostty` on macOS, the WM_CLASS on Linux/X11,
the executable name on Windows), so the name is read from the user's own window
bucket rather than guessed by this package.
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timedelta, timezone

WINDOW_BUCKET_PREFIX = "aw-watcher-window_"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def local_hostname(client=None) -> str:
    """The hostname aw-server records in this machine's bucket ids.

    Sourced the same way the watcher's own bucket id is built in __main__:
    aw-client's client_hostname (itself socket.gethostname()), with a direct
    fallback so the query renderers work without a connected client.
    """
    name = getattr(client, "client_hostname", None) if client is not None else None
    return name or socket.gethostname()


def _last_updated(metadata) -> datetime:
    """Bucket last_updated as an aware datetime, or the epoch if unusable.

    The REST API hands back an ISO string, an in-process datastore a datetime.
    """
    value = (metadata or {}).get("last_updated")
    if isinstance(value, str):
        # Python 3.10 is the declared floor and its fromisoformat rejects a
        # trailing `Z`, which aw-server's REST API may serve. Unnormalized,
        # every candidate would fall back to the epoch and window_bucket_id
        # would silently reinstate the stale-bucket bug it exists to prevent.
        # scripts/verify.sh normalizes the same way.
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return _EPOCH
    if not isinstance(value, datetime):
        return _EPOCH
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def window_bucket_id(client) -> str | None:
    """The local window-watcher bucket, or None if that watcher never ran.

    A machine that has been renamed accumulates one aw-watcher-window_* bucket
    per hostname it has ever had, and only the current one is still written to.
    Returning whichever the server happens to list first therefore picks a dead
    bucket at random, so prefer the one whose recorded hostname is this
    machine's and fall back to the most recently updated match.
    """
    candidates = {
        bucket_id: metadata or {}
        for bucket_id, metadata in client.get_buckets().items()
        if bucket_id.startswith(WINDOW_BUCKET_PREFIX)
    }
    if not candidates:
        return None
    hostname = local_hostname(client)
    mine = {bucket_id: metadata for bucket_id, metadata in candidates.items()
            if metadata.get("hostname") == hostname}
    pool = mine or candidates
    return max(pool, key=lambda bucket_id: _last_updated(pool[bucket_id]))


def top_window_apps(client, bucket_id: str, hours: float = 24.0,
                    limit: int = 10) -> list[tuple[str, float]]:
    """(app, total_seconds) pairs from the window bucket, busiest first."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    totals: dict[str, float] = {}
    for event in client.get_events(bucket_id, start=start, end=end, limit=-1):
        app = (event.data or {}).get("app")
        if not app:
            continue
        totals[app] = totals.get(app, 0.0) + event.duration.total_seconds()
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    return ranked[:limit]


def _find_bucket(prefix: str, hostname: str) -> str:
    """A hostname-qualified find_bucket call.

    The hostname argument is not optional in practice. aw-server's find_bucket
    returns the FIRST bucket whose id contains the filter and ignores hostname
    unless one is passed, so on a machine that has been renamed the unqualified
    form silently resolves to a stale bucket: every gate in the attention query
    then intersects against nothing and the query reports zero hours worked with
    no error at all. Qualified, a mismatch raises instead of lying.
    """
    return f'find_bucket({json.dumps(prefix)}, {json.dumps(hostname)})'


def render_attention_query(window_apps, window_title: str | None = None,
                           hostname: str | None = None) -> str:
    """Render the attention query with the configured terminal names in place.

    An empty `window_apps` drops the frontmost filter entirely rather than
    emitting a filter that matches nothing: the honest reading of "not
    configured" is "do not gate on the window", not "return nothing".

    `hostname` defaults to this machine's, so no caller can accidentally render
    an unqualified (and therefore silently wrong) find_bucket.
    """
    host = hostname or local_hostname()
    apps = list(window_apps or [])
    lines = [
        f'afk      = flood(query_bucket({_find_bucket("aw-watcher-afk_", host)}));',
        f'herdr    = flood(query_bucket({_find_bucket("aw-watcher-herdr_", host)}));',
        'not_afk  = filter_keyvals(afk, "status", ["not-afk"]);',
    ]
    if apps:
        lines.append(
            f'window   = flood(query_bucket('
            f'{_find_bucket(WINDOW_BUCKET_PREFIX, host)}));')
        lines.append(f'in_term  = filter_keyvals(window, "app", {json.dumps(apps)});')
        if window_title:
            # Regex, not exact match: window titles carry document names and
            # other churn around the part worth matching.
            lines.append(
                f'in_term  = filter_keyvals_regex(in_term, "title", '
                f'{json.dumps(window_title)});')
        lines.append('events   = filter_period_intersect(herdr, in_term);')
        lines.append('events   = filter_period_intersect(events, not_afk);')
    else:
        lines.append('events   = filter_period_intersect(herdr, not_afk);')
    lines.append('RETURN   = merge_events_by_keys(events, ["app", "title"]);')
    return "\n".join(lines)


def render_fleet_query(status: str = "working",
                       hostname: str | None = None) -> str:
    """Render the agent-hours query.

    Deliberately not gated on AFK or frontmost: agent work happening while the
    user is away is the point of this bucket. flood() must never appear here:
    it closes gaps by stretching events within one timeline, which corrupts
    deliberately overlapping ones (spec §7).
    """
    host = hostname or local_hostname()
    return "\n".join([
        f'agents   = query_bucket('
        f'{_find_bucket("aw-watcher-herdr-agents_", host)});',
        f'agents   = filter_keyvals(agents, "status", {json.dumps([status])});',
        'RETURN   = merge_events_by_keys(agents, ["app"]);',
    ])
