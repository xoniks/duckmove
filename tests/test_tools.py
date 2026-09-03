import pytest

import duckmove.server as srv
from duckmove.core.engine import Engine
from duckmove.server import build_app, get_tool_handlers

CSV = "city,lat,lon\nNYC,40.7128,-74.0060\nLA,34.0522,-118.2437\n"


@pytest.fixture()
def handlers(monkeypatch):
    eng = Engine(db_path=":memory:")
    monkeypatch.setattr(srv, "_ENGINE", eng)
    build_app()
    yield get_tool_handlers()
    eng.close()


def test_load_and_sql_roundtrip(handlers):
    info = handlers["load_csv_text"](csv_text=CSV, name="cities")
    assert info["table"] == "cities"
    assert info["sample"]
    res = handlers["run_sql"](sql="SELECT count(*) AS n FROM cities")
    assert res["rows"][0]["n"] == 2


def test_run_sql_rejection_is_friendly(handlers):
    res = handlers["run_sql"](sql="DROP TABLE cities")
    assert res["error_code"] == "SQL_REJECTED"


def test_sql_error_includes_tables(handlers):
    handlers["load_csv_text"](csv_text=CSV, name="cities")
    res = handlers["run_sql"](sql="SELECT nope FROM cities")
    assert res["error_code"] == "SQL_ERROR"
    assert "cities" in res["hint"]


def test_describe_warns_on_coords(handlers):
    handlers["load_csv_text"](csv_text="lat,lon\n95,0\n", name="bad")
    d = handlers["describe_table"](table="bad")
    assert any("out-of-range" in w for w in d["warnings"])


def test_describe_unknown_table(handlers):
    d = handlers["describe_table"](table="nope")
    assert d["error_code"] == "TABLE_NOT_FOUND"


def test_geofence_tools(handlers):
    handlers["load_csv_text"](
        csv_text="name,lat,lon,radius_m\nHQ,40.7128,-74.0060,5000\n", name="fences"
    )
    handlers["load_csv_text"](csv_text=CSV, name="cities")
    reg = handlers["register_geofences"](source_table="fences", set_name="hq")
    assert reg["fence_count"] == 1
    mem = handlers["points_in_geofences"](
        table="cities", lat_col="lat", lon_col="lon", geofence_set="hq"
    )
    assert mem["per_fence"][0]["points_inside"] == 1
    assert handlers["list_geofence_sets"]()["sets"][0]["set_name"] == "hq"


def test_customer_tagged_geofence_workflow(handlers, monkeypatch):
    monkeypatch.delenv("DUCKMOVE_PREVIEW_DIR", raising=False)
    monkeypatch.delenv("DUCKMOVE_PREVIEW_URL", raising=False)
    handlers["load_csv_text"](
        csv_text=(
            "customer,name,lat,lon,radius_m\n"
            "Customer_A,A_HQ,40.7128,-74.0060,9000\n"
            "Customer_B,B_HQ,34.0522,-118.2437,9000\n"
        ),
        name="cust_fences",
    )
    handlers["load_csv_text"](
        csv_text=(
            "customer,shipment_id,ts,lat,lon\n"
            "Customer_A,SA,1,40.71,-74.00\nCustomer_A,SA,2,40.72,-74.01\n"
            "Customer_B,SB,1,34.05,-118.24\nCustomer_B,SB,2,34.06,-118.25\n"
        ),
        name="ships",
    )
    reg = handlers["register_geofences"](
        source_table="cust_fences", set_name="customers", tag_col="customer"
    )
    assert reg["tagged_by"] == "customer"
    assert handlers["list_geofence_tags"](geofence_set="customers")["tags"] == [
        "Customer_A",
        "Customer_B",
    ]
    # "show all shipments of Customer_A and its geofences"
    res = handlers["map_tracks"](
        table_or_sql="SELECT * FROM ships WHERE customer = 'Customer_A'",
        group_by="shipment_id",
        lat_col="lat",
        lon_col="lon",
        order_by="ts",
        geofence_set="customers",
        geofence_tag="Customer_A",
    )
    assert res["groups_plotted"] == 1  # only SA
    assert res["data_url"].startswith("data:text/html")


def test_map_points_tool(handlers, tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKMOVE_PREVIEW_DIR", str(tmp_path))
    monkeypatch.setenv("DUCKMOVE_PREVIEW_URL", "http://127.0.0.1:8765")
    handlers["load_csv_text"](csv_text=CSV, name="cities")
    res = handlers["map_points"](
        table_or_sql="cities", lat_col="lat", lon_col="lon", popup_cols=["city"]
    )
    assert res["url"].startswith("http://127.0.0.1:8765/")
    assert res["points_plotted"] == 2


def test_map_points_color_and_size(handlers, monkeypatch):
    monkeypatch.delenv("DUCKMOVE_PREVIEW_DIR", raising=False)
    monkeypatch.delenv("DUCKMOVE_PREVIEW_URL", raising=False)
    handlers["load_csv_text"](
        csv_text="lat,lon,status,rev\n40,-74,ok,100\n34,-118,late,500\n", name="d"
    )
    res = handlers["map_points"](
        table_or_sql="d",
        lat_col="lat",
        lon_col="lon",
        color_by="status",
        size_by="rev",
        style="clustered",
    )
    assert res["points_plotted"] == 2
    assert res["data_url"].startswith("data:text/html")


def test_map_points_color_by_unknown_column(handlers):
    handlers["load_csv_text"](csv_text="lat,lon\n40,-74\n", name="d")
    res = handlers["map_points"](
        table_or_sql="d", lat_col="lat", lon_col="lon", color_by="nope"
    )
    assert res["error_code"] == "INVALID_MAP_INPUT"


def test_map_points_sql_input(handlers, monkeypatch):
    monkeypatch.delenv("DUCKMOVE_PREVIEW_DIR", raising=False)
    monkeypatch.delenv("DUCKMOVE_PREVIEW_URL", raising=False)
    handlers["load_csv_text"](csv_text=CSV, name="cities")
    res = handlers["map_points"](
        table_or_sql="SELECT * FROM cities WHERE city = 'NYC'",
        lat_col="lat",
        lon_col="lon",
    )
    assert res["points_plotted"] == 1
    assert res["data_url"].startswith("data:text/html;base64,")


def test_map_tracks_tool(handlers, monkeypatch):
    monkeypatch.delenv("DUCKMOVE_PREVIEW_DIR", raising=False)
    monkeypatch.delenv("DUCKMOVE_PREVIEW_URL", raising=False)
    csv = (
        "sid,ts,lat,lon\n"
        "A,1,40.0,-74.0\nA,2,41.0,-74.0\n"
        "B,1,34.0,-118.0\nB,2,35.0,-118.0\n"
    )
    handlers["load_csv_text"](csv_text=csv, name="trk")
    res = handlers["map_tracks"](
        table_or_sql="trk",
        group_by="sid",
        lat_col="lat",
        lon_col="lon",
        order_by="ts",
    )
    assert res["groups_plotted"] == 2
    assert {g["group"] for g in res["per_group"]} == {"A", "B"}
    assert all(g["total_km"] > 0 for g in res["per_group"])
    assert res["data_url"].startswith("data:text/html")


def test_map_tracks_rejects_injection(handlers):
    handlers["load_csv_text"](csv_text="sid,ts,lat,lon\nA,1,40.0,-74.0\n", name="trk")
    res = handlers["map_tracks"](
        table_or_sql="trk",
        group_by='sid"; DROP TABLE trk; --',
        lat_col="lat",
        lon_col="lon",
        order_by="ts",
    )
    assert res["error_code"] == "INVALID_MAP_INPUT"


def test_saved_queries(handlers):
    handlers["load_csv_text"](csv_text=CSV, name="cities")
    handlers["save_query"](name="count_cities", sql="SELECT count(*) AS n FROM cities")
    res = handlers["run_saved"](name="count_cities")
    assert res["rows"][0]["n"] == 2
    assert handlers["list_saved"]()["queries"][0]["name"] == "count_cities"


def test_load_data_reuse_status(handlers, tmp_path):
    p = tmp_path / "cities.csv"
    p.write_text(CSV, encoding="utf-8")
    a = handlers["load_data"](path=str(p))
    assert a["status"] == "created"
    b = handlers["load_data"](path=str(p))
    assert b["status"] == "reused" and b["table"] == "cities"
    assert [t["table"] for t in handlers["list_tables"]()["tables"]] == ["cities"]


def test_rename_table_tool(handlers):
    handlers["load_csv_text"](csv_text=CSV, name="cities")
    r = handlers["rename_table"](old="cities", new="my places")
    assert r["table"] == "my_places"
    n = handlers["run_sql"](sql="SELECT count(*) AS n FROM my_places")["rows"][0]["n"]
    assert n == 2


def test_rename_unknown_table(handlers):
    r = handlers["rename_table"](old="nope", new="x")
    assert r["error_code"] == "TABLE_NOT_FOUND"


def test_load_csv_text_friendly_default(handlers):
    info = handlers["load_csv_text"](csv_text=CSV)  # CSV has lat/lon -> "points"
    assert info["table"] == "points"


def test_help_lists_tools(handlers):
    h = handlers["help"]()
    assert any(t["name"] == "run_sql" for t in h["tools"])
    assert h["examples"]
