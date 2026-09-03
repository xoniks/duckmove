from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .logconfig import configure_logging
from .preview.server import find_free_port, is_port_free, serve_forever

DEFAULT_SERVER_ID = "duckmove"
DEFAULT_PREVIEW_PORT = 8765
DEFAULT_PREVIEW_HOST = "127.0.0.1"


def default_preview_dir() -> Path:
    return Path.home() / ".duckmove" / "preview"


def _default_snippet(
    server_id: str = DEFAULT_SERVER_ID, extra_env: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    return {
        "mcpServers": {
            server_id: {
                "command": "duckmove",
                "args": ["serve"],
                "env": extra_env or {},
            }
        }
    }


def _absolute_snippet(
    server_id: str = DEFAULT_SERVER_ID, extra_env: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    # Prefer the exact Python used to invoke this CLI, so Claude gets the same
    # environment even when launched outside the venv.
    return {
        "mcpServers": {
            server_id: {
                "command": sys.executable,
                "args": ["-m", "duckmove.cli", "serve"],
                "env": extra_env or {},
            }
        }
    }


def _claude_config_path() -> Path:
    # Best-effort detection across platforms
    if os.name == "nt":
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(base) / "Claude" / "claude_desktop_config.json"
    darwin_path = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude_desktop_config.json"
    )
    if darwin_path.parent.exists():
        return darwin_path
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "Claude" / "claude_desktop_config.json"


def cmd_serve() -> int:
    from .server import build_app, shutdown

    configure_logging()
    try:
        build_app().run()
    finally:
        shutdown()
    return 0


def _merge_claude_config(
    existing: Dict[str, Any], snippet: Dict[str, Any]
) -> Dict[str, Any]:
    merged = dict(existing)
    servers = dict(merged.get("mcpServers", {}))
    for sid, spec in snippet.get("mcpServers", {}).items():
        if sid in servers and isinstance(servers[sid], dict):
            current = dict(servers[sid])
            env = dict(current.get("env", {}))
            env.update(spec.get("env", {}))
            current.update(spec)
            current["env"] = env
            servers[sid] = current
        else:
            servers[sid] = spec
    merged["mcpServers"] = servers
    return merged


def _backup(path: Path) -> None:
    """Move `path` aside to `<name>.bak`, reporting failure rather than
    silently continuing — the caller is about to overwrite it."""
    try:
        path.replace(path.with_suffix(path.suffix + ".bak"))
    except OSError as e:
        print(f"Warning: could not back up {path}: {e}", file=sys.stderr)


def cmd_init_claude(
    write: bool = False,
    absolute: bool = False,
    server_id: str = DEFAULT_SERVER_ID,
    config_path: Optional[Path] = None,
    preview_dir: Optional[str] = None,
    preview_url: Optional[str] = None,
    set_preview_env: bool = False,
) -> int:
    cfg_path = config_path or _claude_config_path()

    extra_env: Dict[str, str] = {}
    if set_preview_env:
        preview_dir = preview_dir or str(default_preview_dir())
        preview_url = (
            preview_url or f"http://{DEFAULT_PREVIEW_HOST}:{DEFAULT_PREVIEW_PORT}"
        )
        extra_env["DUCKMOVE_PREVIEW_DIR"] = preview_dir
        extra_env["DUCKMOVE_PREVIEW_URL"] = preview_url

    snippet = (
        _absolute_snippet(server_id, extra_env)
        if absolute
        else _default_snippet(server_id, extra_env)
    )
    print("Claude Desktop MCP config snippet (add under top-level mcpServers):\n")
    print(json.dumps(snippet, indent=2))
    print("\nDetected config path:", cfg_path)
    if absolute:
        print("\nUsing absolute command path:")
        for sid, spec in snippet["mcpServers"].items():
            print(f"  {sid}: {spec['command']} {' '.join(spec.get('args', []))}")
        print("This ensures Claude can start the server even outside your venv.")

    if not write:
        flag = " --absolute" if absolute else ""
        print(
            f"\nTip: Run `duckmove init-claude --write{flag}` to merge into the "
            f"detected config file (a .bak backup will be created)."
        )
        return 0

    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    existing: Dict[str, Any] = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                raise ValueError("top level is not an object")
        except (OSError, ValueError) as e:
            print(
                f"Warning: existing config is not usable ({e}); writing a fresh "
                f"config and saving a backup.",
                file=sys.stderr,
            )
            existing = {}
    merged = _merge_claude_config(existing, snippet)
    if cfg_path.exists():
        _backup(cfg_path)
    cfg_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print("\nUpdated:", cfg_path)
    return 0


def _status(ok: bool, label: str) -> None:
    print(f"{'[OK]' if ok else '[FAIL]'} {label}")


def cmd_doctor(
    server_id: str = DEFAULT_SERVER_ID,
    expect_absolute: bool = False,
    quick: bool = False,
) -> int:
    print("duckmove doctor\n===============")

    print("Python:")
    print("  exe:", sys.executable)
    print("  version:", sys.version.replace("\n", " "))
    in_venv = getattr(sys, "base_prefix", sys.prefix) != sys.prefix
    print("  venv:", "yes" if in_venv else "no")

    print("\nDependencies:")
    ok_all = True
    try:
        import mcp  # noqa: F401

        _status(True, "mcp library import")
    except Exception as e:
        ok_all = False
        _status(False, f"mcp import: {e}")

    try:
        import duckdb

        con = duckdb.connect(":memory:")
        con.execute("INSTALL spatial; LOAD spatial;")
        # ST_Distance_Spheroid expects ST_Point(lat, lon) — latitude first.
        row = con.execute(
            "SELECT ST_Distance_Spheroid(ST_Point(40.7128, -74.0060),"
            " ST_Point(34.0522, -118.2437)) / 1000.0"
        ).fetchone()
        con.close()
        if row is None:
            raise RuntimeError("spatial sanity query returned no rows")
        _status(True, f"duckdb + spatial extension (NYC-LA sanity: {row[0]:.0f} km)")
    except Exception as e:
        ok_all = False
        _status(False, f"duckdb/spatial: {e}")

    try:
        import folium

        if not quick:
            folium.Map(location=[0, 0], zoom_start=1).get_root().render()
        _status(True, "folium import" + ("" if quick else " + render"))
    except Exception as e:
        ok_all = False
        _status(False, f"folium: {e}")

    print("\nSecurity policy:")
    from .core.engine import ALLOWED_DIRS_ENV, allowed_dirs

    roots = allowed_dirs()
    _status(True, "run_sql cannot read files or reach the network")
    if roots:
        _status(True, f"load_data restricted to: {', '.join(str(r) for r in roots)}")
    else:
        print(
            f"[INFO] load_data may read any path the user names. "
            f"Set {ALLOWED_DIRS_ENV} to restrict it."
        )

    print("\nClaude Desktop config:")
    cfg_path = _claude_config_path()
    print("  path:", cfg_path)
    if not cfg_path.exists():
        _status(
            False, "config file not found (run: duckmove init-claude --write --absolute)"
        )
        return 1

    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        _status(False, f"config is not valid JSON: {e}")
        return 1

    servers = cfg.get("mcpServers") or {}
    if not isinstance(servers, dict) or not servers:
        _status(False, "config missing mcpServers section")
        return 1

    spec = servers.get(server_id)
    if not spec:
        _status(
            False,
            f"server id '{server_id}' not found; run: "
            f"duckmove init-claude --write --absolute --id {server_id}",
        )
        return 1

    command = spec.get("command")
    args = spec.get("args", [])
    print("  entry:", {"id": server_id, "command": command, "args": args})

    if not command or not isinstance(command, str):
        _status(False, "command is missing or not a string")
        return 1

    if os.path.isabs(command):
        if Path(command).exists():
            _status(True, "absolute command exists")
        else:
            _status(False, "absolute command path does not exist")
            return 1
    else:
        resolved = shutil.which(command)
        if resolved:
            _status(True, f"command found on PATH -> {resolved}")
        else:
            ok_all = False
            _status(False, "command not found on PATH; run init-claude --absolute")
        if expect_absolute:
            ok_all = False
            print(
                "  note: expecting absolute command; run: duckmove init-claude --write --absolute"
            )

    good_args = args == ["serve"] or (
        len(args) >= 3
        and args[0] == "-m"
        and args[1] == "duckmove.cli"
        and args[-1] == "serve"
    )
    _status(good_args, "args invoke 'serve'")
    if not good_args:
        print("  expected args to be ['serve'] or ['-m','duckmove.cli','serve']")

    print("\nSummary:")
    _status(ok_all, "environment ready")
    if not ok_all:
        print("\nFixes:")
        print("  - Install deps in your venv: pip install -e .")
        print("  - Ensure Folium is installed: pip install folium")
        print("  - Refresh Claude config: duckmove init-claude --write --absolute")
    return 0 if ok_all else 1


def cmd_preview(
    preview_dir: Optional[str] = None,
    host: str = DEFAULT_PREVIEW_HOST,
    port: Optional[int] = None,
    show_tip: bool = False,
) -> int:
    """Serve the preview directory. Shared by `preview` and `start-server`."""
    serve_dir = Path(preview_dir) if preview_dir else default_preview_dir()
    if port is None:
        port = find_free_port(host, DEFAULT_PREVIEW_PORT)
    elif not is_port_free(host, port):
        print(
            f"Requested port {port} is in use. Pick another with --port or omit "
            f"to auto-pick.",
            file=sys.stderr,
        )
        return 2

    if show_tip:
        base = f"http://{host}:{port}"
        print(
            "Tip: add preview env to Claude with:\n"
            f"  duckmove init-claude --write --absolute --set-preview-env "
            f'--preview-dir "{serve_dir}" --preview-url {base}'
        )
    return serve_forever(serve_dir, host, port)


def _add_claude_config_args(p: argparse.ArgumentParser) -> None:
    """Shared flags for the `init-claude` / `setup-claude` pair."""
    p.add_argument(
        "--write",
        action="store_true",
        help="Merge into the detected config file (creates .bak backup)",
    )
    p.add_argument(
        "--absolute",
        action="store_true",
        help="Write an absolute command path to your current Python to avoid PATH issues",
    )
    p.add_argument(
        "--id",
        default=DEFAULT_SERVER_ID,
        help=f"Claude server id key (default: {DEFAULT_SERVER_ID})",
    )
    p.add_argument(
        "--config-path", default=None, help="Override Claude config path (advanced)"
    )
    p.add_argument(
        "--set-preview-env",
        action="store_true",
        help="Include preview env (DUCKMOVE_PREVIEW_DIR/URL) in Claude config",
    )
    p.add_argument(
        "--preview-dir",
        default=None,
        help="Directory the preview server will serve (default: ~/.duckmove/preview)",
    )
    p.add_argument(
        "--preview-url",
        default=None,
        help="Base URL for the preview server (default: http://127.0.0.1:8765)",
    )
    p.set_defaults(
        func=lambda args: cmd_init_claude(
            write=args.write,
            absolute=args.absolute,
            server_id=args.id,
            config_path=Path(args.config_path) if args.config_path else None,
            preview_dir=args.preview_dir,
            preview_url=args.preview_url,
            set_preview_env=args.set_preview_env,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="duckmove",
        description="duckmove - DuckDB-powered spatial MCP server CLI",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name, help_text in (
        ("serve", "Run the MCP server over stdio (Claude launches this)"),
        ("start", "Alias for 'serve' - run the MCP server over stdio"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=lambda args: cmd_serve())

    for name, help_text in (
        ("init-claude", "Print or merge Claude Desktop MCP config"),
        ("setup-claude", "Setup Claude Desktop integration (alias of init-claude)"),
    ):
        _add_claude_config_args(sub.add_parser(name, help=help_text))

    p_doc = sub.add_parser(
        "doctor", help="Diagnose setup: Python, deps, and Claude config"
    )
    p_doc.add_argument(
        "--id",
        default=DEFAULT_SERVER_ID,
        help=f"Claude server id key to check (default: {DEFAULT_SERVER_ID})",
    )
    p_doc.add_argument(
        "--expect-absolute",
        action="store_true",
        help="Warn if Claude config does not use an absolute Python command",
    )
    p_doc.add_argument(
        "--quick", action="store_true", help="Skip generating a sample map during checks"
    )
    p_doc.set_defaults(
        func=lambda args: cmd_doctor(
            server_id=args.id, expect_absolute=args.expect_absolute, quick=args.quick
        )
    )

    p_prev = sub.add_parser(
        "preview", help="Serve a directory over HTTP for map previews"
    )
    p_prev.add_argument(
        "--dir", default=None, help="Directory to serve (default: ~/.duckmove/preview)"
    )
    p_prev.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PREVIEW_PORT,
        help=f"Port (default: {DEFAULT_PREVIEW_PORT})",
    )
    p_prev.add_argument(
        "--host",
        default=DEFAULT_PREVIEW_HOST,
        help=f"Host (default: {DEFAULT_PREVIEW_HOST})",
    )
    p_prev.set_defaults(
        func=lambda args: cmd_preview(args.dir, args.host, args.port, show_tip=True)
    )

    p_start = sub.add_parser("start-server", help="Start the local preview server")
    p_start.add_argument("--id", default=DEFAULT_SERVER_ID, help=argparse.SUPPRESS)
    p_start.add_argument(
        "--host",
        default=DEFAULT_PREVIEW_HOST,
        help=f"Preview host (default: {DEFAULT_PREVIEW_HOST})",
    )
    p_start.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Preview port (default: auto-pick from {DEFAULT_PREVIEW_PORT})",
    )
    p_start.add_argument(
        "--dir", default=None, help="Preview directory (default: ~/.duckmove/preview)"
    )
    p_start.set_defaults(func=lambda args: cmd_preview(args.dir, args.host, args.port))

    return parser


def main(argv: Optional[list] = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
