"""End-to-end proof that a query cannot reach the filesystem.

These go through the real Engine and the real tool handlers rather than
calling `validate_sql` directly, so they would catch a regression that
bypasses the guard (a new code path calling `con.execute` unchecked, say).
"""

import pytest

import duckmove.server as srv
from duckmove.core.engine import (
    MAX_ROWS_LIMIT,
    Engine,
    PathNotAllowed,
    assert_path_allowed,
)
from duckmove.core.sqlguard import SqlRejected
from duckmove.server import build_app, get_tool_handlers

CSV = "city,lat,lon\nNYC,40.7128,-74.0060\n"


@pytest.fixture()
def handlers(monkeypatch):
    eng = Engine(db_path=":memory:")
    monkeypatch.setattr(srv, "_ENGINE", eng)
    build_app()
    yield get_tool_handlers()
    eng.close()


def test_engine_query_cannot_read_a_real_file(engine, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("token=hunter2", encoding="utf-8")
    posix = str(secret).replace("\\", "/")
    for sql in (
        f"SELECT * FROM read_text('{posix}')",
        f"SELECT * FROM read_csv('{posix}', ignore_errors=true)",
        f"SELECT * FROM '{posix}'",
    ):
        with pytest.raises(SqlRejected):
            engine.query(sql)


def test_run_sql_tool_reports_a_recoverable_error_not_a_leak(handlers, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("token=hunter2", encoding="utf-8")
    res = handlers["run_sql"](
        sql=f"SELECT * FROM read_text('{str(secret).replace(chr(92), '/')}')"
    )
    assert res["error_code"] == "SQL_REJECTED"
    assert "hunter2" not in str(res)
    assert "load_data" in res["error"]


def test_saved_query_cannot_smuggle_a_file_read(handlers):
    res = handlers["save_query"](
        name="leak", sql="SELECT * FROM read_text('/etc/passwd')"
    )
    assert res["error_code"] == "SQL_REJECTED"
    assert handlers["list_saved"]()["queries"] == []


def test_map_tool_source_cannot_smuggle_a_file_read(handlers):
    res = handlers["map_points"](
        table_or_sql="SELECT 1 AS lat, 2 AS lon FROM read_csv('/etc/passwd')",
        lat_col="lat",
        lon_col="lon",
    )
    assert res["error_code"] == "SQL_REJECTED"


def test_load_data_still_reads_files(handlers, tmp_path):
    """The guard must not break the one legitimate path to the disk."""
    p = tmp_path / "cities.csv"
    p.write_text(CSV, encoding="utf-8")
    info = handlers["load_data"](path=str(p))
    assert info["row_count"] == 1


# --- load-path allowlist ---------------------------------------------


def test_allowlist_is_inactive_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("DUCKMOVE_ALLOWED_DIRS", raising=False)
    assert_path_allowed(tmp_path / "anything.csv")  # no raise


def test_allowlist_permits_paths_inside_it(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKMOVE_ALLOWED_DIRS", str(tmp_path))
    (tmp_path / "sub").mkdir()
    assert_path_allowed(tmp_path / "sub" / "a.csv")  # no raise


def test_allowlist_blocks_paths_outside_it(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("DUCKMOVE_ALLOWED_DIRS", str(allowed))
    with pytest.raises(PathNotAllowed):
        assert_path_allowed(tmp_path / "elsewhere.csv")


def test_allowlist_blocks_traversal_out_of_an_allowed_dir(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("DUCKMOVE_ALLOWED_DIRS", str(allowed))
    with pytest.raises(PathNotAllowed):
        assert_path_allowed(allowed / ".." / "escape.csv")


def test_load_data_tool_surfaces_the_allowlist_error(handlers, tmp_path, monkeypatch):
    outside = tmp_path / "outside.csv"
    outside.write_text(CSV, encoding="utf-8")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("DUCKMOVE_ALLOWED_DIRS", str(allowed))
    res = handlers["load_data"](path=str(outside))
    assert res["error_code"] == "PATH_NOT_ALLOWED"


# --- result-size ceiling ---------------------------------------------


def test_max_rows_is_clamped_to_the_hard_ceiling(engine):
    engine.con.execute("CREATE TABLE big AS SELECT i AS n FROM range(0, 30000) t(i)")
    res = engine.query("SELECT * FROM big", max_rows=10_000_000)
    assert len(res["rows"]) == MAX_ROWS_LIMIT
    assert res["truncated"] is True
    assert "capped" in res["note"]


def test_max_rows_rejects_nonsense_values(engine):
    engine.con.execute("CREATE TABLE t AS SELECT 1 AS n")
    assert len(engine.query("SELECT * FROM t", max_rows=0)["rows"]) == 1
    assert len(engine.query("SELECT * FROM t", max_rows=-5)["rows"]) == 1
