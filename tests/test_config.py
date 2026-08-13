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


def test_poll_interval_zero_is_honored(monkeypatch):
    # `is not None` guard: an explicit 0 must not be dropped by truthiness.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-herdr": {"poll_interval": 2.0}
    })
    cfg = cli.load_config(cli.parse_args(["--poll-interval", "0"]))
    assert cfg.poll_interval == 0.0


def test_source_flag_rejects_unknown_values():
    # argparse choices=() turns a typo into a usage error, not a runtime crash
    # ten minutes into a poll loop.
    with pytest.raises(SystemExit):
        cli.parse_args(["--source", "carrier-pigeon"])
