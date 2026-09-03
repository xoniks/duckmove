import pytest

from duckmove.core.quality import coordinate_quality
from duckmove.geofences import ops
from duckmove.preview.index import render_index_html

CSV = "city,lat,lon\nNYC,40.7128,-74.0060\n"
FENCES = "name,lat,lon,radius_m\nHQ,40.7128,-74.0060,5000\n"

INJECTION = 'lat" FROM x; DROP TABLE pings; --'


def _setup(engine):
    engine.create_table_from_text(CSV, name="pings")
    engine.create_table_from_text(FENCES, name="fences")
    ops.register_geofences(engine, source_table="fences", set_name="hq")


def test_membership_rejects_unknown_column(engine):
    _setup(engine)
    with pytest.raises(KeyError):
        ops.points_in_geofences(engine, "pings", INJECTION, "lon", "hq")
    assert engine.describe("pings")["row_count"] == 1  # table untouched


def test_crossings_rejects_unknown_columns(engine):
    _setup(engine)
    with pytest.raises(KeyError):
        ops.geofence_crossings(
            engine,
            "pings",
            "lat",
            "lon",
            group_by=INJECTION,
            order_by="lat",
            set_name="hq",
        )


def test_quality_rejects_unknown_column(engine):
    engine.create_table_from_text(CSV, name="pings")
    with pytest.raises(KeyError):
        coordinate_quality(engine, "pings", INJECTION, "lon")


def test_preview_index_escapes_html():
    items = [
        {
            "file": "a.html",
            "url": "http://x/a.html",
            "created_at": "2026-01-01T00:00:00",
            "kind": "points",
            "meta": {"dataset_name": "<script>alert(1)</script>", "dataset_id": "x"},
        }
    ]
    html_out = render_index_html(items, "2026-01-01T00:00:00")
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_preview_index_escapes_js_handler_args():
    items = [
        {
            "file": "a');alert(1);//.html",
            "url": "http://x/a.html",
            "created_at": "2026-01-01T00:00:00",
            "kind": "points",
            "meta": {"dataset_name": "n'); alert(1); //", "dataset_id": "x"},
        }
    ]
    html_out = render_index_html(items, "2026-01-01T00:00:00")
    # raw single-quote payload must not appear inside an onclick handler
    assert "onclick=\"renameItem('" not in html_out
    assert "');alert(1);//" not in html_out
