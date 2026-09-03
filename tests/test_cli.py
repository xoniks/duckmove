"""cli.py had no test coverage at all before this."""

import json

import pytest

from duckmove import cli


def test_merge_preserves_other_servers():
    existing = {"mcpServers": {"other": {"command": "x", "args": []}}}
    merged = cli._merge_claude_config(existing, cli._default_snippet("duckmove"))
    assert set(merged["mcpServers"]) == {"other", "duckmove"}
    assert merged["mcpServers"]["other"]["command"] == "x"


def test_merge_keeps_unrelated_env_of_an_existing_entry():
    existing = {"mcpServers": {"duckmove": {"command": "old", "env": {"KEEP": "1"}}}}
    snippet = cli._default_snippet("duckmove", {"DUCKMOVE_PREVIEW_URL": "u"})
    merged = cli._merge_claude_config(existing, snippet)
    env = merged["mcpServers"]["duckmove"]["env"]
    assert env == {"KEEP": "1", "DUCKMOVE_PREVIEW_URL": "u"}
    assert merged["mcpServers"]["duckmove"]["command"] == "duckmove"


def test_merge_leaves_top_level_keys_alone():
    existing = {"globalShortcut": "Cmd+X", "mcpServers": {}}
    merged = cli._merge_claude_config(existing, cli._default_snippet())
    assert merged["globalShortcut"] == "Cmd+X"


def test_init_claude_dry_run_writes_nothing(tmp_path, capsys):
    cfg = tmp_path / "claude_desktop_config.json"
    assert cli.cmd_init_claude(write=False, config_path=cfg) == 0
    assert not cfg.exists()
    assert "mcpServers" in capsys.readouterr().out


def test_init_claude_write_creates_config(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    assert cli.cmd_init_claude(write=True, config_path=cfg) == 0
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mcpServers"]["duckmove"]["args"] == ["serve"]


def test_init_claude_backs_up_before_overwriting(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), "utf-8")
    cli.cmd_init_claude(write=True, config_path=cfg)
    backup = cfg.with_suffix(cfg.suffix + ".bak")
    assert backup.exists()
    assert "other" in json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]


def test_init_claude_recovers_from_a_corrupt_config(tmp_path, capsys):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text("{ this is not json", encoding="utf-8")
    assert cli.cmd_init_claude(write=True, config_path=cfg) == 0
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "duckmove" in data["mcpServers"]
    assert "not usable" in capsys.readouterr().err


def test_init_claude_rejects_a_non_object_config(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text("[1, 2, 3]", encoding="utf-8")
    assert cli.cmd_init_claude(write=True, config_path=cfg) == 0
    assert "duckmove" in json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]


def test_absolute_snippet_uses_the_running_interpreter():
    import sys

    spec = cli._absolute_snippet()["mcpServers"]["duckmove"]
    assert spec["command"] == sys.executable
    assert spec["args"] == ["-m", "duckmove.cli", "serve"]


def test_preview_env_is_included_when_requested(tmp_path):
    cfg = tmp_path / "c.json"
    cli.cmd_init_claude(
        write=True,
        config_path=cfg,
        set_preview_env=True,
        preview_dir="/d",
        preview_url="http://h:1",
    )
    env = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["duckmove"]["env"]
    assert env["DUCKMOVE_PREVIEW_DIR"] == "/d"
    assert env["DUCKMOVE_PREVIEW_URL"] == "http://h:1"


# --- parser -----------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "serve",
        "start",
        "init-claude",
        "setup-claude",
        "doctor",
        "preview",
        "start-server",
    ],
)
def test_every_subcommand_parses_and_binds_a_handler(cmd):
    args = cli.build_parser().parse_args([cmd])
    assert callable(args.func)


def test_setup_claude_is_an_alias_of_init_claude(tmp_path):
    """Both spellings must accept the same flags; they used to be two
    hand-maintained copies of the same argparse block."""
    parser = cli.build_parser()
    a = parser.parse_args(["init-claude", "--write", "--absolute", "--id", "x"])
    b = parser.parse_args(["setup-claude", "--write", "--absolute", "--id", "x"])
    assert (a.write, a.absolute, a.id) == (b.write, b.absolute, b.id)


def test_missing_subcommand_exits_nonzero():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_preview_reports_a_busy_port(tmp_path, capsys):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy = s.getsockname()[1]
        rc = cli.cmd_preview(str(tmp_path), "127.0.0.1", busy)
    assert rc == 2
    assert "in use" in capsys.readouterr().err


def test_doctor_runs_without_a_claude_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_claude_config_path", lambda: tmp_path / "missing.json")
    assert cli.cmd_doctor(quick=True) == 1
    out = capsys.readouterr().out
    assert "duckmove doctor" in out
    assert "run_sql cannot read files" in out


def test_doctor_reports_a_configured_allowlist(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DUCKMOVE_ALLOWED_DIRS", str(tmp_path))
    monkeypatch.setattr(cli, "_claude_config_path", lambda: tmp_path / "missing.json")
    cli.cmd_doctor(quick=True)
    assert "load_data restricted to" in capsys.readouterr().out
