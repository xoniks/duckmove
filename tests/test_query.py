import pytest

from duckmove.core.sqlguard import SqlRejected

CSV = "city,lat,lon\nNYC,40.7128,-74.0060\nLA,34.0522,-118.2437\n"


def test_query_rows_and_columns(engine):
    engine.create_table_from_text(CSV, name="cities")
    res = engine.query("SELECT city FROM cities ORDER BY city")
    assert res["columns"] == ["city"]
    assert [r["city"] for r in res["rows"]] == ["LA", "NYC"]
    assert res["truncated"] is False


def test_query_row_cap(engine):
    res = engine.query("SELECT * FROM range(1000)", max_rows=10)
    assert len(res["rows"]) == 10 and res["truncated"] is True


def test_query_rejects_writes(engine):
    with pytest.raises(SqlRejected):
        engine.query("DROP TABLE x")


def test_geometry_returned_as_wkt(engine):
    res = engine.query("SELECT ST_Point(1, 2) AS g")
    assert res["rows"][0]["g"] == "POINT (1 2)"


def test_timestamptz_is_json_safe(engine):
    # 'Z' timestamps load as TIMESTAMPTZ; fetching those needs pytz unless
    # we cast — query() must return them as plain strings.
    engine.create_table_from_text("id,ts\nA,2026-01-01T08:00:00Z\n", name="events")
    res = engine.query("SELECT ts FROM events")
    assert isinstance(res["rows"][0]["ts"], str)
    assert res["rows"][0]["ts"].startswith("2026-01-01")


def test_spheroid_distance_nyc_la(engine):
    # Known geodesic distance NYC<->LA ~ 3,944 km (WGS84). Tolerance 1%.
    # NOTE: DuckDB's ST_Distance_Spheroid expects ST_Point(lat, lon).
    from duckmove.core.engine import geodesic_km_expr

    engine.create_table_from_text(CSV, name="cities")
    expr = geodesic_km_expr("a.lat", "a.lon", "b.lat", "b.lon")
    res = engine.query(
        f"SELECT {expr} AS km FROM cities a, cities b WHERE a.city='NYC' AND b.city='LA'"
    )
    km = res["rows"][0]["km"]
    assert abs(km - 3944) / 3944 < 0.01
