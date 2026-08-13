"""ActivityWatch bucket writers (spec §5, §6).

Two buckets, two deliberately different disciplines:

  * Attention is a single timeline, so it uses heartbeat() with a pulsetime and
    lets aw-server merge consecutive identical events.
  * The fleet bucket records concurrent agents, so its events OVERLAP by design
    and cannot be heartbeated: heartbeat only merges against the bucket's last
    event, and interleaved agents would never match, fragmenting the timeline.
    It posts completed events with explicit durations instead.

aw-client's insert_events() has no queued/retry mode (unlike heartbeat), so
FleetWriter keeps its own bounded buffer and retries on the next flush.
"""

from __future__ import annotations

import logging
from datetime import datetime

from aw_core.models import Event

from .state import Attention, CompletedRun

logger = logging.getLogger(__name__)

DEFAULT_MAX_PENDING = 10_000


class AttentionWriter:
    """Heartbeats the focused workspace/pane into a `currentwindow` bucket."""

    def __init__(self, client, bucket_id: str, pulsetime: float,
                 generic_terminal_label: str = "terminal"):
        self._client = client
        self._bucket = bucket_id
        self._pulsetime = pulsetime
        self._generic = generic_terminal_label

    def write(self, attention: Attention, now: datetime) -> None:
        data = {
            # app/title rather than custom keys: aw merges heartbeats on these
            # and its categorization rules match them (spec §5).
            "app": attention.workspace_label,
            "title": attention.title or self._generic,
            "agent": attention.agent,
            "agent_status": attention.agent_status,
            "workspace_id": attention.workspace_id,
            "pane_id": attention.pane_id,
        }
        self._client.heartbeat(self._bucket, Event(timestamp=now, data=data),
                               pulsetime=self._pulsetime, queued=True)


class FleetWriter:
    """Posts completed, overlapping agent-run intervals."""

    def __init__(self, client, bucket_id: str,
                 max_pending: int = DEFAULT_MAX_PENDING):
        self._client = client
        self._bucket = bucket_id
        self._max_pending = max_pending
        self._pending: list[Event] = []

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def write(self, runs: list[CompletedRun]) -> None:
        for run in runs:
            if run.duration_seconds <= 0:
                continue
            self._pending.append(Event(
                timestamp=run.start,
                duration=run.end - run.start,
                data={
                    "app": run.key.workspace_label,
                    "title": run.title or "",
                    "status": run.key.status,
                    "agent": run.key.agent,
                    "cwd": run.key.cwd,
                    "pane_id": run.key.pane_id,
                },
            ))
        self.flush()

    def flush(self) -> None:
        """Try to post everything buffered; keep it buffered on failure."""
        if not self._pending:
            return
        try:
            self._client.insert_events(self._bucket, list(self._pending))
        except Exception as exc:  # noqa: BLE001 - any transport failure retries
            if len(self._pending) > self._max_pending:
                dropped = len(self._pending) - self._max_pending
                self._pending = self._pending[-self._max_pending:]
                logger.warning(
                    "fleet buffer full; dropped %s oldest event(s)", dropped)
            logger.debug("fleet flush failed, %s event(s) pending: %s",
                         len(self._pending), exc)
            return
        self._pending = []
