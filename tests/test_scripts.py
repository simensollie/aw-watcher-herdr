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


# --- scripts/reload-agent.sh ------------------------------------------------
#
# The bug this covers: install.sh ran `launchctl bootout` and `bootstrap`
# back to back, and a bootstrap issued while the old job is still unloading
# fails with "5: Input/output error". Because the failing bootstrap was the
# last command before an unconditional heredoc, the script printed
# "Installed and started" while the agent was in fact unloaded: the watcher
# recorded nothing and nothing said so. Driving the reload through a stub
# launchctl makes the retry and the post-check real tests rather than greps.

RELOAD = Path(__file__).parent.parent / "scripts" / "reload-agent.sh"

STUB = """#!/usr/bin/env bash
# Records every call, and fails `bootstrap` until FAIL_BOOTSTRAPS is exhausted.
echo "$@" >> "$CALLS"
case "$1" in
  bootout)   exit 0 ;;
  bootstrap)
    n=$(cat "$COUNTER" 2>/dev/null || echo 0)
    echo $((n + 1)) > "$COUNTER"
    if [ "$n" -lt "$FAIL_BOOTSTRAPS" ]; then
      echo "Bootstrap failed: 5: Input/output error" >&2
      exit 5
    fi
    exit 0 ;;
  print)     exit "$PRINT_RC" ;;
  *)         exit 0 ;;
esac
"""


def reload_agent(tmp_path, fail_bootstraps=0, print_rc=0):
    import os
    import subprocess
    stub = tmp_path / "launchctl"
    stub.write_text(STUB)
    stub.chmod(0o755)
    calls = tmp_path / "calls"
    env = {**os.environ, "LAUNCHCTL": str(stub), "CALLS": str(calls),
           "COUNTER": str(tmp_path / "counter"),
           "FAIL_BOOTSTRAPS": str(fail_bootstraps), "PRINT_RC": str(print_rc),
           "RETRY_SLEEP": "0"}
    proc = subprocess.run(
        ["bash", str(RELOAD), "com.example.agent", str(tmp_path / "a.plist")],
        capture_output=True, text=True, env=env)
    return proc, (calls.read_text().splitlines() if calls.exists() else [])


def test_reload_boots_the_agent_out_before_bootstrapping_it():
    import tempfile
    from pathlib import Path as P
    with tempfile.TemporaryDirectory() as d:
        proc, calls = reload_agent(P(d))
        assert proc.returncode == 0, proc.stderr
        assert calls[0].startswith("bootout")
        assert any(c.startswith("bootstrap") for c in calls)


def test_reload_retries_a_bootstrap_that_lost_the_unload_race():
    import tempfile
    from pathlib import Path as P
    with tempfile.TemporaryDirectory() as d:
        proc, calls = reload_agent(P(d), fail_bootstraps=2)
        assert proc.returncode == 0, proc.stderr
        assert len([c for c in calls if c.startswith("bootstrap")]) == 3


def test_reload_fails_loudly_when_every_bootstrap_fails():
    # set -e in install.sh then aborts before it can claim success.
    import tempfile
    from pathlib import Path as P
    with tempfile.TemporaryDirectory() as d:
        proc, _ = reload_agent(P(d), fail_bootstraps=99)
        assert proc.returncode != 0
        assert "Input/output error" in proc.stderr
        assert "bootstrap" in proc.stderr.lower()


def test_reload_fails_when_the_agent_is_not_loaded_afterwards():
    # A bootstrap that exits 0 is not proof the job is running; that gap is
    # exactly how a stopped watcher looked healthy.
    import tempfile
    from pathlib import Path as P
    with tempfile.TemporaryDirectory() as d:
        proc, _ = reload_agent(P(d), print_rc=1)
        assert proc.returncode != 0
        assert "not loaded" in proc.stderr.lower()


def test_install_delegates_the_reload_and_no_longer_bootstraps_inline():
    install = (Path(__file__).parent.parent / "scripts" / "install.sh").read_text()
    assert "reload-agent.sh" in install
    assert "launchctl bootstrap" not in install
