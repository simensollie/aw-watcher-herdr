"""Guards on scripts/verify.sh, the end-to-end check.

verify.sh needs a live herdr and an aw-server, so the script itself cannot run
inside the unit suite. What CAN be pinned here is the timing budget it depends
on, which is the part that silently rots: a value that is merely too small makes
a working watcher report FAIL on a slower machine, and nothing else in the
project would notice.
"""
import re
from pathlib import Path

VERIFY = Path(__file__).parent.parent / "scripts" / "verify.sh"
MAKEFILE = Path(__file__).parent.parent / "Makefile"

# aw-client pre-merges queued heartbeats in memory until the accumulated
# duration reaches commit_interval, which is 5 s under the `client-testing`
# config verify.sh runs with. At --poll-interval 1 the first commit therefore
# lands around the 7th tick, and only then does the dispatch thread POST it. A
# budget of 8 s left a margin of a few hundred milliseconds.
MIN_DURATION = 15


def script() -> str:
    return VERIFY.read_text()


def test_default_duration_clears_the_commit_interval():
    match = re.search(r'DURATION="\$\{DURATION:-(\d+(?:\.\d+)?)\}"', script())
    assert match, "verify.sh must default DURATION through a ${DURATION:-N} override"
    assert float(match.group(1)) >= MIN_DURATION


def test_duration_stays_overridable():
    # The Makefile targets and the controller's own runs pass DURATION in, so
    # the default must remain a default.
    assert 'DURATION="${DURATION:-' in script()


def test_the_makefile_targets_still_call_the_script():
    makefile = MAKEFILE.read_text()
    assert makefile.count("scripts/verify.sh") == 2
    assert "SOURCE=cli scripts/verify.sh" in makefile


def test_the_watcher_is_polled_faster_than_the_budget():
    # The two numbers are coupled: the budget is only meaningful relative to the
    # poll interval the script starts the watcher with.
    assert "--poll-interval 1" in script()
