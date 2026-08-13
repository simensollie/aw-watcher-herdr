"""Query rendering and terminal detection (spec §7, §7.1).

herdr is a multiplexer, so the frontmost application is always the terminal
emulator hosting it, never herdr. aw-watcher-window reports that terminal
differently on every platform (`Ghostty` on macOS, the WM_CLASS on Linux/X11,
the executable name on Windows), so the name is read from the user's own window
bucket rather than guessed by this package.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

WINDOW_BUCKET_PREFIX = "aw-watcher-window_"


def window_bucket_id(client) -> str | None:
    """The local window-watcher bucket, or None if that watcher never ran."""
    for bucket_id in client.get_buckets():
        if bucket_id.startswith(WINDOW_BUCKET_PREFIX):
            return bucket_id
    return None


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


def render_attention_query(window_apps, window_title: str | None = None) -> str:
    """Render the attention query with the configured terminal names in place.

    An empty `window_apps` drops the frontmost filter entirely rather than
    emitting a filter that matches nothing: the honest reading of "not
    configured" is "do not gate on the window", not "return nothing".
    """
    apps = list(window_apps or [])
    lines = [
        'afk      = flood(query_bucket(find_bucket("aw-watcher-afk_")));',
        'herdr    = flood(query_bucket(find_bucket("aw-watcher-herdr_")));',
        'not_afk  = filter_keyvals(afk, "status", ["not-afk"]);',
    ]
    if apps:
        lines.append(
            'window   = flood(query_bucket(find_bucket("aw-watcher-window_")));')
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


def render_fleet_query(status: str = "working") -> str:
    """Render the agent-hours query.

    Deliberately not gated on AFK or frontmost: agent work happening while the
    user is away is the point of this bucket. flood() must never appear here:
    it closes gaps by stretching events within one timeline, which corrupts
    deliberately overlapping ones (spec §7).
    """
    return "\n".join([
        'agents   = query_bucket(find_bucket("aw-watcher-herdr-agents_"));',
        f'agents   = filter_keyvals(agents, "status", {json.dumps([status])});',
        'RETURN   = merge_events_by_keys(agents, ["app"]);',
    ])
