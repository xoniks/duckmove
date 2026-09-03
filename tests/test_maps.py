from duckmove.maps import render

ROWS = [
    {"lat": 40.7128, "lon": -74.0060, "shipment_id": "S1"},
    {"lat": 34.0522, "lon": -118.2437, "shipment_id": "S2"},
]


def test_points_map_html():
    html = render.points_map(ROWS, "lat", "lon", popup_cols=["shipment_id"])
    assert "<html" in html.lower() and "S1" in html


def test_routes_map_html():
    rows = [{"from_lat": 40.7, "from_lon": -74.0, "to_lat": 34.0, "to_lon": -118.2}]
    html = render.routes_map(rows, "from_lat", "from_lon", "to_lat", "to_lon")
    assert "<html" in html.lower()


def test_fence_overlay():
    html = render.points_map(
        ROWS,
        "lat",
        "lon",
        fences=[
            {
                "fence": "NYC",
                "wkt": "POLYGON ((-75 40, -73 40, -73 42, -75 42, -75 40))",
            }
        ],
    )
    assert "NYC" in html


def test_points_map_color_by_categorical():
    rows = [
        {"lat": 40, "lon": -74, "status": "delivered"},
        {"lat": 34, "lon": -118, "status": "delayed"},
    ]
    html = render.points_map(rows, "lat", "lon", color_by="status")
    assert "delivered" in html and "delayed" in html  # legend entries


def test_points_map_color_by_numeric():
    rows = [
        {"lat": 40, "lon": -74, "rev": 100},
        {"lat": 34, "lon": -118, "rev": 500},
    ]
    html = render.points_map(rows, "lat", "lon", color_by="rev")
    assert "–" in html  # graduated range legend


def test_points_map_size_by():
    rows = [
        {"lat": 40, "lon": -74, "w": 1},
        {"lat": 34, "lon": -118, "w": 9},
    ]
    html = render.points_map(rows, "lat", "lon", size_by="w")
    assert "<html" in html.lower()


def test_points_map_heatmap():
    rows = [{"lat": 40, "lon": -74}, {"lat": 34, "lon": -118}]
    html = render.points_map(rows, "lat", "lon", style="heatmap")
    assert "heat" in html.lower()


def test_points_map_clustered():
    rows = [{"lat": 40, "lon": -74}, {"lat": 34, "lon": -118}]
    html = render.points_map(rows, "lat", "lon", style="clustered")
    assert "cluster" in html.lower()


def test_tracks_map_html():
    rows = [
        {"sid": "A", "lat": 40.0, "lon": -74.0},
        {"sid": "A", "lat": 41.0, "lon": -74.0},
        {"sid": "B", "lat": 34.0, "lon": -118.0},
        {"sid": "B", "lat": 35.0, "lon": -118.0},
    ]
    html = render.tracks_map(
        rows, "sid", "lat", "lon", group_distances={"A": 111.0, "B": 111.0}
    )
    assert "<html" in html.lower()
    assert "sid=A" in html and "111.0 km" in html


def test_wkt_polygon_coords():
    pts = render.wkt_polygon_coords("POLYGON ((-75 40, -73 41, -75 40))")
    assert pts[0] == (40.0, -75.0) and pts[1] == (41.0, -73.0)


def test_persist_map(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKMOVE_PREVIEW_DIR", str(tmp_path))
    monkeypatch.setenv("DUCKMOVE_PREVIEW_URL", "http://127.0.0.1:8765")
    out = render.persist_map("<html><body></body></html>", kind="points", meta={})
    assert out["url"].startswith("http://127.0.0.1:8765/")
    assert (tmp_path / out["file"]).exists()
    assert (tmp_path / "index.html").exists()


def test_persist_map_unconfigured(monkeypatch):
    monkeypatch.delenv("DUCKMOVE_PREVIEW_DIR", raising=False)
    monkeypatch.delenv("DUCKMOVE_PREVIEW_URL", raising=False)
    out = render.persist_map("<html></html>", kind="points", meta={})
    assert out == {}


def test_color_mapping_clamps_values_outside_the_training_range():
    """The bucket index is derived by arithmetic, so a value below the low end
    would index backwards into the palette and paint an outlier the *top*
    color instead of the bottom one."""
    fn, _ = render._color_mapping([10, 20, 30, 40, 50])
    assert fn(-1000) == render.SEQUENTIAL_COLORS[0]
    assert fn(1000) == render.SEQUENTIAL_COLORS[-1]
    assert fn(10) == render.SEQUENTIAL_COLORS[0]


def test_color_mapping_is_categorical_when_any_value_is_not_numeric():
    fn, legend = render._color_mapping(["north", "south", "north"])
    assert fn("north") != fn("south")
    assert [label for label, _ in legend] == ["north", "south"]
