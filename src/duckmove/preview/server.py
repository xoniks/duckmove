"""Local HTTP server for browsing rendered maps.

Security note: this binds a *mutating* API (delete/clear/rename) on
localhost. Any page in the user's browser can send a cross-site POST to
127.0.0.1, so the endpoints verify the request came from this UI rather than
from another origin. Two independent checks:

* `Sec-Fetch-Site` must be `same-origin` (or absent, for non-browser callers
  like curl, which cannot be driven by a malicious page).
* `Origin`, when present, must match the server's own origin.

Both are enforced on every state-changing request; GET is unaffected.
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.parse as _u
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from .index import render_index_html

log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
MAX_NAME_LEN = 200


def is_port_free(host: str, port: int) -> bool:
    """True if nothing is listening on `host:port`.

    Deliberately does *not* set SO_REUSEADDR: on Windows that option lets a
    bind succeed against a port that is already being listened on, so the
    probe would report every port as free.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def find_free_port(host: str, start: int = 8765, max_tries: int = 50) -> int:
    """First free TCP port at or after `start`."""
    for offset in range(max_tries):
        if is_port_free(host, start + offset):
            return start + offset
    return start


def _safe_member(serve_dir: Path, filename: str) -> Optional[Path]:
    """Resolve `filename` inside `serve_dir`, or None if it escapes.

    Defence in depth: callers already restrict deletions to names present in
    the manifest, but the manifest is a file on disk and this keeps a
    tampered entry from reaching outside the preview directory.
    """
    if not filename or "\x00" in filename:
        return None
    candidate = (serve_dir / filename).resolve()
    root = serve_dir.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        log.warning("rejected path traversal attempt: %r", filename)
        return None
    return candidate


class PreviewStore:
    """Manifest + index.html on disk."""

    def __init__(self, serve_dir: Path) -> None:
        self.dir = serve_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def manifest_path(self) -> Path:
        return self.dir / MANIFEST_NAME

    def read(self) -> List[Dict[str, Any]]:
        try:
            if self.manifest_path.exists():
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                return data if isinstance(data, list) else []
        except (OSError, ValueError):
            log.warning("preview manifest unreadable; treating as empty", exc_info=True)
        return []

    def write(self, items: List[Dict[str, Any]]) -> None:
        self.manifest_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
        self.dir.joinpath("index.html").write_text(
            render_index_html(items, datetime.now().isoformat()), encoding="utf-8"
        )

    def delete(self, filename: str) -> bool:
        items = self.read()
        remaining = [e for e in items if e.get("file") != filename]
        if remaining == items:
            return False
        target = _safe_member(self.dir, filename)
        if target is not None:
            target.unlink(missing_ok=True)
        self.write(remaining)
        return True

    def clear(self) -> None:
        for entry in self.read():
            target = _safe_member(self.dir, str(entry.get("file", "")))
            if target is not None:
                target.unlink(missing_ok=True)
        self.write([])

    def prune(self) -> int:
        items = self.read()
        keep = [e for e in items if (self.dir / str(e.get("file", ""))).exists()]
        if len(keep) != len(items):
            self.write(keep)
        return len(items) - len(keep)

    def rename(self, filename: str, new_name: str) -> bool:
        items = self.read()
        for entry in items:
            if entry.get("file") == filename:
                meta = entry.get("meta") or {}
                meta["dataset_name"] = new_name
                entry["meta"] = meta
                self.write(items)
                return True
        return False


def make_handler(store: PreviewStore, origin: str) -> type:
    class Handler(SimpleHTTPRequestHandler):
        # Bound at class creation so each server instance validates its own
        # origin.
        _store = store
        _origin = origin

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("preview %s - %s", self.address_string(), fmt % args)

        def _same_origin(self) -> bool:
            """Reject cross-site state-changing requests (CSRF)."""
            site = self.headers.get("Sec-Fetch-Site")
            if site is not None and site != "same-origin":
                return False
            sent = self.headers.get("Origin")
            return sent is None or sent.rstrip("/") == self._origin.rstrip("/")

        def _reply(self, code: int, body: bytes = b"") -> None:
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_POST(self) -> None:
            parsed = _u.urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self._reply(404)
                return
            if not self._same_origin():
                log.warning(
                    "blocked cross-origin %s from Origin=%r Sec-Fetch-Site=%r",
                    parsed.path,
                    self.headers.get("Origin"),
                    self.headers.get("Sec-Fetch-Site"),
                )
                self._reply(403, b"cross-origin request blocked")
                return

            qs = _u.parse_qs(parsed.query)
            filename = (qs.get("file") or [""])[0]
            try:
                if parsed.path == "/api/delete":
                    ok = bool(filename) and self._store.delete(filename)
                    self._reply(200 if ok else 400, b"ok" if ok else b"unknown file")
                elif parsed.path == "/api/clear":
                    self._store.clear()
                    self._reply(200, b"ok")
                elif parsed.path == "/api/prune":
                    removed = self._store.prune()
                    self._reply(200, f"pruned {removed}".encode())
                elif parsed.path == "/api/rename":
                    new_name = (qs.get("name") or [""])[0]
                    if not filename or len(new_name) > MAX_NAME_LEN:
                        self._reply(400, b"bad request")
                        return
                    if any(ord(ch) < 32 for ch in new_name):
                        self._reply(400, b"bad request")
                        return
                    ok = self._store.rename(filename, new_name)
                    self._reply(200 if ok else 400, b"ok" if ok else b"unknown file")
                else:
                    self._reply(404)
            except OSError:
                log.exception("preview API %s failed", parsed.path)
                self._reply(500, b"server error")

    return Handler


def serve_forever(serve_dir: Path, host: str, port: int) -> int:
    store = PreviewStore(serve_dir)
    # Render the index up front so a fresh directory serves the duckmove
    # homepage rather than a bare directory listing.
    store.write(store.read())
    origin = f"http://{host}:{port}"
    handler = partial(make_handler(store, origin), directory=str(serve_dir))
    httpd = ThreadingHTTPServer((host, port), handler)
    print("Serving", serve_dir, "at", origin)
    print("Press Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping preview server...")
    finally:
        httpd.server_close()
    return 0
