from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_load_csv_path(engine):
    info = engine.create_table_from_file(str(EXAMPLES / "sample_shipments.csv"))
    assert info["row_count"] == 5
    assert info["table"] == "sample_shipments"


def test_load_geojson(engine):
    info = engine.create_table_from_file(str(FIXTURES / "points.geojson"))
    assert info["row_count"] == 2
    types = {c["name"]: c["type"] for c in info["columns"]}
    assert any(t.startswith("GEOMETRY") for t in types.values())
    # Geometry columns come back as WKT through query()
    res = engine.query("SELECT geom FROM points ORDER BY name LIMIT 1")
    assert res["rows"][0]["geom"].startswith("POINT")


def test_load_excel(engine):
    info = engine.create_table_from_file(str(EXAMPLES / "stores.xlsx"))
    assert info["row_count"] >= 5
    names = {c["name"].lower() for c in info["columns"]}
    assert {"lat", "lon"} <= names


def test_load_parquet(engine):
    info = engine.create_table_from_file(str(EXAMPLES / "deliveries.parquet"))
    assert info["row_count"] >= 5


def test_load_missing_file(engine):
    with pytest.raises(FileNotFoundError):
        engine.create_table_from_file("nope/missing.csv")


def test_load_unsupported_extension(engine, tmp_path):
    p = tmp_path / "x.pdf"
    p.write_text("x")
    with pytest.raises(ValueError):
        engine.create_table_from_file(str(p))
