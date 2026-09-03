from duckmove.geofences import ops

FENCES = "name,lat,lon,radius_m\nNYC_HQ,40.7128,-74.0060,5000\n"
POINTS = (
    "shipment_id,timestamp,lat,lon\n"
    "S1,2026-01-01T00:00:00,40.7130,-74.0050\n"  # inside NYC_HQ
    "S1,2026-01-01T01:00:00,41.5,-74.0\n"  # outside
    "S2,2026-01-01T00:00:00,34.0,-118.2\n"  # outside
)


def setup_data(engine):
    engine.create_table_from_text(FENCES, name="fences")
    engine.create_table_from_text(POINTS, name="pings")
    ops.register_geofences(engine, source_table="fences", set_name="customers")


def test_register_and_list(engine):
    setup_data(engine)
    sets = ops.list_geofence_sets(engine)
    assert sets == [{"set_name": "customers", "fence_count": 1}]


def test_reregister_replaces(engine):
    setup_data(engine)
    ops.register_geofences(engine, source_table="fences", set_name="customers")
    assert ops.list_geofence_sets(engine) == [{"set_name": "customers", "fence_count": 1}]


def test_membership(engine):
    setup_data(engine)
    res = ops.points_in_geofences(engine, "pings", "lat", "lon", "customers")
    per_fence = {r["fence"]: r["points_inside"] for r in res["per_fence"]}
    assert per_fence["NYC_HQ"] == 1


def test_crossings(engine):
    setup_data(engine)
    res = ops.geofence_crossings(
        engine,
        "pings",
        "lat",
        "lon",
        group_by="shipment_id",
        order_by="timestamp",
        set_name="customers",
    )
    events = [(e["group"], e["event"], e["fence"]) for e in res["events"]]
    assert ("S1", "exit", "NYC_HQ") in events


TAGGED = (
    "customer,name,lat,lon,radius_m\n"
    "Customer_A,A_HQ,40.7128,-74.0060,5000\n"
    "Customer_B,B_HQ,34.0522,-118.2437,5000\n"
)
TAGGED_POINTS = (
    "customer,lat,lon\n"
    "Customer_A,40.7130,-74.0050\n"  # inside A_HQ
    "Customer_B,34.0520,-118.2430\n"  # inside B_HQ
)


def test_register_with_tag(engine):
    engine.create_table_from_text(TAGGED, name="cust_fences")
    res = ops.register_geofences(
        engine, source_table="cust_fences", set_name="customers", tag_col="customer"
    )
    assert res["fence_count"] == 2 and res["tagged_by"] == "customer"
    assert ops.list_geofence_tags(engine, "customers") == ["Customer_A", "Customer_B"]


def test_get_fences_filtered_by_tag(engine):
    engine.create_table_from_text(TAGGED, name="cust_fences")
    ops.register_geofences(engine, "cust_fences", "customers", tag_col="customer")
    only_a = ops.get_fences_wkt(engine, "customers", tag="Customer_A")
    assert [f["fence"] for f in only_a] == ["A_HQ"]


def test_membership_filtered_by_tag(engine):
    engine.create_table_from_text(TAGGED, name="cust_fences")
    engine.create_table_from_text(TAGGED_POINTS, name="pts")
    ops.register_geofences(engine, "cust_fences", "customers", tag_col="customer")
    res = ops.points_in_geofences(
        engine, "pts", "lat", "lon", "customers", tag="Customer_A"
    )
    assert [f["fence"] for f in res["per_fence"]] == ["A_HQ"]
    assert res["per_fence"][0]["points_inside"] == 1  # only A's point counted vs A_HQ


def test_register_tag_col_validated(engine):
    engine.create_table_from_text(TAGGED, name="cust_fences")
    import pytest

    with pytest.raises(KeyError):
        ops.register_geofences(
            engine, "cust_fences", "customers", tag_col='x"; DROP TABLE pts; --'
        )


def test_examples_geofences_csv(engine):
    from pathlib import Path

    examples = Path(__file__).resolve().parents[1] / "examples"
    engine.create_table_from_file(str(examples / "geofences.csv"), name="gf")
    res = ops.register_geofences(engine, source_table="gf", set_name="areas")
    # 10 circles + 2 vertex polygons — nothing silently dropped
    assert res["fence_count"] == 12
    fences = {f["fence"] for f in ops.get_fences_wkt(engine, "areas")}
    assert "West_Coast_Corridor" in fences
