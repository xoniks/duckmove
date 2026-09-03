"""The preview server's mutating API must not be drivable cross-origin.

The API lives on 127.0.0.1, so any page in the user's browser can aim a POST
at it. These tests pin the CSRF checks and the path-traversal guard.
"""

import json
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from duckmove.preview.server import (
    PreviewStore,
    _safe_member,
    find_free_port,
    is_port_free,
    make_handler,
)

ENTRY = {
    "file": "points_1.html",
    "url": "http://127.0.0.1:9/points_1.html",
    "created_at": "2026-01-01T00:00:00",
    "kind": "points",
    "meta": {"dataset_name": "cities", "dataset_id": "abc123"},
}


@pytest.fixture()
def server(tmp_path):
    store = PreviewStore(tmp_path)
    store.write([dict(ENTRY)])
    (tmp_path / ENTRY["file"]).write_text("<html>map</html>", encoding="utf-8")

    port = find_free_port("127.0.0.1", 8900)
    origin = f"http://127.0.0.1:{port}"
    handler = partial(make_handler(store, origin), directory=str(tmp_path))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield origin, tmp_path, store
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _post(origin, path, headers=None):
    req = urllib.request.Request(origin + path, method="POST", data=b"")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _manifest(tmp_path):
    return json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))


def test_cross_site_post_is_blocked(server):
    """A malicious page fetching 127.0.0.1 sends Sec-Fetch-Site: cross-site."""
    origin, tmp_path, _ = server
    status = _post(origin, "/api/clear", {"Sec-Fetch-Site": "cross-site"})
    assert status == 403
    assert len(_manifest(tmp_path)) == 1
    assert (tmp_path / ENTRY["file"]).exists()


def test_foreign_origin_header_is_blocked(server):
    origin, tmp_path, _ = server
    status = _post(
        origin, "/api/delete?file=points_1.html", {"Origin": "http://evil.example"}
    )
    assert status == 403
    assert len(_manifest(tmp_path)) == 1


def test_same_site_post_is_blocked_too(server):
    origin, tmp_path, _ = server
    assert _post(origin, "/api/clear", {"Sec-Fetch-Site": "same-site"}) == 403
    assert len(_manifest(tmp_path)) == 1


def test_same_origin_post_succeeds(server):
    origin, tmp_path, _ = server
    status = _post(
        origin,
        "/api/delete?file=points_1.html",
        {"Sec-Fetch-Site": "same-origin", "Origin": origin},
    )
    assert status == 200
    assert _manifest(tmp_path) == []
    assert not (tmp_path / ENTRY["file"]).exists()


def test_non_browser_client_without_fetch_metadata_is_allowed(server):
    """curl and friends send neither header and cannot be driven by a page."""
    origin, _, _ = server
    assert _post(origin, "/api/prune") == 200


def test_unknown_api_path_is_404(server):
    origin, _, _ = server
    assert _post(origin, "/api/nope", {"Sec-Fetch-Site": "same-origin"}) == 404


def test_rename_rejects_overlong_and_control_characters(server):
    origin, _, _ = server
    same = {"Sec-Fetch-Site": "same-origin", "Origin": origin}
    assert _post(origin, f"/api/rename?file=points_1.html&name={'x' * 300}", same) == 400
    assert _post(origin, "/api/rename?file=points_1.html&name=a%00b", same) == 400


def test_rename_updates_the_manifest(server):
    origin, tmp_path, _ = server
    same = {"Sec-Fetch-Site": "same-origin", "Origin": origin}
    assert _post(origin, "/api/rename?file=points_1.html&name=renamed", same) == 200
    assert _manifest(tmp_path)[0]["meta"]["dataset_name"] == "renamed"


# --- path traversal ---------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["../outside.txt", "../../etc/passwd", "sub/../../escape.txt", "", "a\x00b"],
)
def test_safe_member_rejects_escapes(tmp_path, name):
    assert _safe_member(tmp_path, name) is None


def test_safe_member_allows_a_plain_child(tmp_path):
    assert _safe_member(tmp_path, "map.html") == (tmp_path / "map.html").resolve()


def test_delete_of_a_tampered_manifest_entry_stays_inside_the_dir(tmp_path):
    """Even if the manifest names a path outside the preview dir, the file
    outside must survive."""
    outside = tmp_path / "keepme.txt"
    outside.write_text("important", encoding="utf-8")
    serve_dir = tmp_path / "preview"
    store = PreviewStore(serve_dir)
    store.write([{"file": "../keepme.txt", "kind": "points", "meta": {}}])

    store.delete("../keepme.txt")

    assert outside.exists()
    assert store.read() == []


# --- store behaviour ---------------------------------------------------


def test_prune_drops_entries_whose_file_is_gone(tmp_path):
    store = PreviewStore(tmp_path)
    store.write([dict(ENTRY)])
    assert store.prune() == 1
    assert store.read() == []


def test_unreadable_manifest_is_treated_as_empty(tmp_path):
    store = PreviewStore(tmp_path)
    store.manifest_path.write_text("{not json", encoding="utf-8")
    assert store.read() == []


def test_is_port_free_reports_a_bound_port(server):
    origin, _, _ = server
    port = int(origin.rsplit(":", 1)[1])
    assert is_port_free("127.0.0.1", port) is False
