from duckmove.core.quality import coordinate_quality, guess_coordinate_columns


def test_guess_columns():
    g = guess_coordinate_columns(["from_lat", "from_lon", "to_lat", "to_lon", "id"])
    assert g["from_lat"] == "from_lat" and g["to_lon"] == "to_lon"
    g2 = guess_coordinate_columns(["latitude", "longitude"])
    assert g2["lat"] == "latitude" and g2["lon"] == "longitude"


def test_quality_counts(engine):
    engine.create_table_from_text("lat,lon\n40.7,-74.0\n,\n95.0,-74.0\n", name="pts")
    q = coordinate_quality(engine, "pts", "lat", "lon")
    assert q["valid"] == 1 and q["missing"] == 1 and q["invalid"] == 1
