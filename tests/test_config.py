"""Config loading and CLI-over-file override precedence (spec §8)."""

import pytest

from aw_watcher_herdr import __main__ as cli


def test_defaults_when_no_flags(monkeypatch):
    # Isolate from any real user config file.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {})
    cfg = cli.load_config(cli.parse_args([]))
    assert cfg.source == "auto"
    assert cfg.herdr_binary == "herdr"
    assert cfg.poll_interval == 2.0
    assert cfg.pulsetime == 5.0
    assert cfg.generic_terminal_label == "terminal"
    assert cfg.fleet_enabled is True
    assert cfg.fleet_statuses == ["working", "blocked", "done"]
    assert cfg.max_run_seconds == 43200.0
    assert cfg.gap_factor == 3.0
    assert cfg.socket_path is None
    assert cfg.window_title is None


def test_window_apps_default_is_platform_specific(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    assert cli.default_window_apps() == ["Ghostty"]
    monkeypatch.setattr(cli.sys, "platform", "linux")
    assert cli.default_window_apps() == []


def test_window_app_absent_from_the_file_keeps_the_platform_default(monkeypatch):
    # load_config_toml merges every LIVE key of DEFAULT_CONFIG over the file, so
    # a live `window_app` line there would force ["Ghostty"] onto Linux and
    # Windows too. It must stay commented out.
    assert not any(line.startswith("window_app")
                   for line in cli.DEFAULT_CONFIG.splitlines())
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"poll_interval": 2.0}
    })
    assert cli.load_config(cli.parse_args([])).window_app == []


def test_file_values_applied(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {
            "source": "cli",
            "herdr_binary": "/opt/herdr/bin/herdr",
            "poll_interval": 4.0,
            "pulsetime": 9.0,
            "generic_terminal_label": "shell",
            "fleet_statuses": ["working"],
            "max_run_seconds": 600.0,
            "gap_factor": 5.0,
            "fleet_enabled": False,
            "window_app": ["Alacritty", "Ghostty"],
            "window_title": "herdr",
        }
    })
    cfg = cli.load_config(cli.parse_args([]))
    assert cfg.source == "cli"
    assert cfg.herdr_binary == "/opt/herdr/bin/herdr"
    assert cfg.poll_interval == 4.0
    assert cfg.pulsetime == 9.0
    assert cfg.generic_terminal_label == "shell"
    assert cfg.fleet_statuses == ["working"]
    assert cfg.max_run_seconds == 600.0
    assert cfg.gap_factor == 5.0
    assert cfg.fleet_enabled is False
    assert cfg.window_app == ["Alacritty", "Ghostty"]
    assert cfg.window_title == "herdr"


def test_window_app_string_in_file_is_wrapped_in_a_list(monkeypatch):
    # Tolerate the pre-amendment single-string form rather than crashing on it.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"window_app": "Ghostty"}
    })
    assert cli.load_config(cli.parse_args([])).window_app == ["Ghostty"]


def test_cli_flags_override_file(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"poll_interval": 4.0, "pulsetime": 9.0,
                             "generic_terminal_label": "shell",
                             "source": "socket", "herdr_binary": "herdr"}
    })
    cfg = cli.load_config(cli.parse_args(
        ["--poll-interval", "1.5", "--pulsetime", "6",
         "--generic-terminal-label", "tty", "--socket-path", "/run/h.sock",
         "--source", "cli", "--herdr-binary", "/usr/local/bin/herdr"]))
    assert cfg.poll_interval == 1.5
    assert cfg.pulsetime == 6.0
    assert cfg.generic_terminal_label == "tty"
    assert cfg.socket_path == "/run/h.sock"
    assert cfg.source == "cli"
    assert cfg.herdr_binary == "/usr/local/bin/herdr"


def test_no_fleet_flag_disables_fleet(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"fleet_enabled": True}
    })
    cfg = cli.load_config(cli.parse_args(["--no-fleet"]))
    assert cfg.fleet_enabled is False


def test_unset_flags_do_not_override_file(monkeypatch):
    # fleet_enabled absent on the CLI must leave the file's False intact.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"fleet_enabled": False}
    })
    cfg = cli.load_config(cli.parse_args([]))
    assert cfg.fleet_enabled is False


def test_explicit_zero_flag_is_not_dropped_by_truthiness(monkeypatch):
    # The `is not None` guard, still tested on a falsy value. The file's 9.0 is
    # valid, so the ConfigError can only come from the flag's 0 having been
    # applied: a truthiness check would drop the 0 and hand back 9.0 with no
    # complaint at all, which is exactly what this guard exists to prevent.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"pulsetime": 9.0}
    })
    with pytest.raises(cli.ConfigError):
        cli.load_config(cli.parse_args(["--pulsetime", "0"]))


# --- validation -------------------------------------------------------------

def test_poll_interval_zero_is_rejected(monkeypatch):
    # At 0 the loop calls time.sleep(0) and spins: a pinned core hammering
    # herdr's API thousands of times a second, and gap_threshold becomes 0,
    # which silently disables sleep/suspend detection (it is guarded by
    # gap_threshold > 0). Reject it rather than clamp it silently.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {})
    with pytest.raises(cli.ConfigError) as exc:
        cli.load_config(cli.parse_args(["--poll-interval", "0"]))
    assert "poll_interval" in str(exc.value)


@pytest.mark.parametrize("value", [0.0, -1.0])
def test_non_positive_poll_interval_in_the_file_is_rejected(monkeypatch, value):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"poll_interval": value}
    })
    with pytest.raises(cli.ConfigError):
        cli.load_config(cli.parse_args([]))


def test_non_numeric_poll_interval_in_the_file_is_rejected(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"poll_interval": "soon"}
    })
    with pytest.raises(cli.ConfigError):
        cli.load_config(cli.parse_args([]))


def test_a_small_positive_poll_interval_is_still_allowed(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {})
    cfg = cli.load_config(cli.parse_args(["--poll-interval", "0.25"]))
    assert cfg.poll_interval == 0.25


@pytest.mark.parametrize("key", ["poll_interval", "pulsetime", "max_run_seconds",
                                 "gap_factor"])
@pytest.mark.parametrize("value", [0, -1.0, "soon", None])
def test_every_hazardous_number_is_rejected(monkeypatch, key, value):
    # One table for the four fields whose non-positive or non-numeric values
    # fail silently rather than loudly. Each consequence is spelled out in
    # _validate; the shared property is that nothing in the running watcher
    # would ever complain.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {key: value}
    })
    with pytest.raises(cli.ConfigError) as exc:
        cli.load_config(cli.parse_args([]))
    assert key in str(exc.value)


def test_gap_factor_at_or_below_one_is_rejected(monkeypatch):
    # Measured against the real loop: at gap_factor <= 1 the sleep threshold is
    # at or below one poll interval, so every normal tick trips the gap branch,
    # every open run is closed at the last good poll with a zero duration, and
    # FleetWriter drops all of them. The fleet bucket records nothing at all
    # while agents work, and only info-level "poll gap" lines hint at it.
    for value in (0.5, 1.0):
        monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
            "aw-watcher-herdr": {"gap_factor": value}
        })
        with pytest.raises(cli.ConfigError) as exc:
            cli.load_config(cli.parse_args([]))
        assert "gap_factor" in str(exc.value)


def test_a_gap_factor_just_above_one_is_allowed(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"gap_factor": 1.5}
    })
    assert cli.load_config(cli.parse_args([])).gap_factor == 1.5


def test_the_documented_defaults_all_validate(monkeypatch):
    # The values shipped in DEFAULT_CONFIG and in the example config must not be
    # rejected by the very validation this project added.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {})
    cfg = cli.load_config(cli.parse_args([]))
    assert (cfg.poll_interval, cfg.pulsetime, cfg.max_run_seconds,
            cfg.gap_factor) == (2.0, 5.0, 43200.0, 3.0)


def test_fleet_statuses_as_a_bare_string_is_wrapped_in_a_list(monkeypatch):
    # Same tolerance window_app already has, and the same hazard: tuple("done")
    # iterates the CHARACTERS, so no status ever matches and the fleet bucket
    # stays empty with nothing logged.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"fleet_statuses": "done"}
    })
    assert cli.load_config(cli.parse_args([])).fleet_statuses == ["done"]


def test_source_flag_rejects_unknown_values():
    # argparse choices=() turns a typo into a usage error, not a runtime crash
    # ten minutes into a poll loop.
    with pytest.raises(SystemExit):
        cli.parse_args(["--source", "carrier-pigeon"])


def test_unknown_source_in_the_file_is_rejected(monkeypatch):
    # The FLAG was guarded by argparse but the FILE value was copied straight
    # out of the toml, so a typo there crashed the daemon inside resolve_source
    # instead of reporting a config error.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"source": "carrier-pigeon"}
    })
    with pytest.raises(cli.ConfigError) as exc:
        cli.load_config(cli.parse_args([]))
    message = str(exc.value)
    assert "carrier-pigeon" in message
    for choice in cli.SOURCE_CHOICES:
        assert choice in message


def test_a_valid_source_in_the_file_is_accepted(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"source": "cli"}
    })
    assert cli.load_config(cli.parse_args([])).source == "cli"


def test_a_flag_can_repair_a_broken_file_value(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"source": "carrier-pigeon", "poll_interval": 0}
    })
    cfg = cli.load_config(cli.parse_args(
        ["--source", "cli", "--poll-interval", "2"]))
    assert cfg.source == "cli" and cfg.poll_interval == 2.0


# main()'s handling of a ConfigError lives in test_main.py, where every side
# effect of main() is replaced by a fake.
