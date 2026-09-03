CSV = "city,lat,lon\nNYC,40.7128,-74.0060\nLA,34.0522,-118.2437\n"


def test_create_table_from_text(engine):
    info = engine.create_table_from_text(CSV, name="cities")
    assert info["table"] == "cities"
    assert info["row_count"] == 2
    assert {"city", "lat", "lon"} <= {c["name"] for c in info["columns"]}


def test_name_uniquified(engine):
    engine.create_table_from_text(CSV, name="cities")
    info2 = engine.create_table_from_text(CSV, name="cities")
    assert info2["table"] == "cities_2"


def test_name_sanitized(engine):
    info = engine.create_table_from_text(CSV, name="My Shipments (2026)!")
    assert info["table"] == "my_shipments_2026"


def test_list_tables_hides_internal(engine):
    engine.create_table_from_text(CSV, name="cities")
    engine.con.execute("CREATE TABLE _secret(i INT)")
    names = [t["table"] for t in engine.list_tables()]
    assert "cities" in names and "_secret" not in names


def test_describe_and_drop(engine):
    engine.create_table_from_text(CSV, name="cities")
    d = engine.describe("cities")
    assert d["row_count"] == 2
    engine.drop_table("cities")
    assert engine.list_tables() == []


def test_reload_unchanged_reuses(engine, tmp_path):
    p = tmp_path / "cities.csv"
    p.write_text(CSV, encoding="utf-8")
    a = engine.create_table_from_file(str(p))
    assert a["status"] == "created" and a["table"] == "cities"
    b = engine.create_table_from_file(str(p))
    assert b["status"] == "reused" and b["table"] == "cities"
    assert [t["table"] for t in engine.list_tables()] == ["cities"]


def test_reload_changed_refreshes(engine, tmp_path):
    p = tmp_path / "cities.csv"
    p.write_text(CSV, encoding="utf-8")
    engine.create_table_from_file(str(p))
    p.write_text(CSV + "SF,37.7749,-122.4194\n", encoding="utf-8")
    b = engine.create_table_from_file(str(p))
    assert b["status"] == "refreshed" and b["row_count"] == 3
    assert [t["table"] for t in engine.list_tables()] == ["cities"]


def test_rename_table(engine):
    engine.create_table_from_text(CSV, name="cities")
    new = engine.rename_table("cities", "q3 cities")
    assert new == "q3_cities"
    assert [t["table"] for t in engine.list_tables()] == ["q3_cities"]


def test_rename_conflict(engine):
    engine.create_table_from_text(CSV, name="a")
    engine.create_table_from_text(CSV, name="b")
    import pytest

    with pytest.raises(ValueError):
        engine.rename_table("a", "b")


def test_drop_clears_source_then_recreates(engine, tmp_path):
    p = tmp_path / "cities.csv"
    p.write_text(CSV, encoding="utf-8")
    engine.create_table_from_file(str(p))
    engine.drop_table("cities")
    c = engine.create_table_from_file(str(p))
    assert c["status"] == "created"


def test_persistence(tmp_path):
    from duckmove.core.engine import Engine

    db = str(tmp_path / "t.duckdb")
    e1 = Engine(db_path=db)
    e1.create_table_from_text(CSV, name="cities")
    e1.close()
    e2 = Engine(db_path=db)
    assert [t["table"] for t in e2.list_tables()] == ["cities"]
    e2.close()
