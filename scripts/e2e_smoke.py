"""End-to-end smoke test: full analyst workflow through the tool handlers.
Run: python scripts/e2e_smoke.py
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

preview_dir = Path(tempfile.mkdtemp(prefix="duckmove_e2e_"))
os.environ["DUCKMOVE_PREVIEW_DIR"] = str(preview_dir)
os.environ["DUCKMOVE_PREVIEW_URL"] = "http://127.0.0.1:8765"
os.environ["DUCKMOVE_DB"] = ":memory:"

from duckmove.server import build_app, get_tool_handlers  # noqa: E402

build_app()
h = get_tool_handlers()

# 1. Load GPS pings + geofences + parquet + excel
gps = h["load_data"](path=str(EXAMPLES / "shipments_gps.csv"))
assert gps["table"] == "shipments_gps", gps
gf = h["load_data"](path=str(EXAMPLES / "geofences.csv"))
dl = h["load_data"](path=str(EXAMPLES / "deliveries.parquet"))
st = h["load_data"](path=str(EXAMPLES / "stores.xlsx"))
assert dl["row_count"] == 200 and st["row_count"] == 10

# 2. Register fences
reg = h["register_geofences"](source_table="geofences", set_name="areas")
assert reg["fence_count"] == 12, reg

# 3. Distance per shipment via SQL (window + spheroid)
res = h["run_sql"](
    sql="""
    WITH hops AS (
      SELECT shipment_id,
             ST_Distance_Spheroid(ST_Point(lat, lon),
               ST_Point(lag(lat) OVER w, lag(lon) OVER w)) AS m
      FROM shipments_gps
      WINDOW w AS (PARTITION BY shipment_id ORDER BY "timestamp")
    )
    SELECT shipment_id, round(sum(m)/1000.0, 1) AS total_km
    FROM hops GROUP BY shipment_id ORDER BY total_km DESC
    """
)
assert "error" not in res, res
assert res["rows"] and res["rows"][0]["total_km"] > 0, res
print("distances:", res["rows"])

# 4. Geofence membership + crossings
mem = h["points_in_geofences"](
    table="shipments_gps", lat_col="lat", lon_col="lon", geofence_set="areas"
)
total_inside = sum(f["points_inside"] for f in mem["per_fence"])
assert total_inside > 0, mem
cr = h["geofence_crossings"](
    table="shipments_gps", lat_col="lat", lon_col="lon",
    group_by="shipment_id", order_by="timestamp", geofence_set="areas",
)
print("crossing events:", len(cr["events"]))

# 5. BI join across formats
join = h["run_sql"](
    sql="""
    SELECT s.city, count(*) AS deliveries, round(sum(d.weight_kg), 1) AS kg
    FROM deliveries d JOIN stores s ON d.store_id = s.store_id
    GROUP BY s.city ORDER BY deliveries DESC
    """
)
assert "error" not in join and join["rows"], join
print("join:", join["rows"][:3])

# 6. Map with fence overlay -> preview file
m = h["map_points"](
    table_or_sql="shipments_gps", lat_col="lat", lon_col="lon",
    popup_cols=["shipment_id", "status"], geofence_set="areas",
)
assert m["url"].startswith("http://127.0.0.1:8765/"), m
assert (preview_dir / m["file"]).exists()
assert (preview_dir / "index.html").exists()
print("map:", m["url"], f"({m['points_plotted']} points)")

# 7. Saved queries
h["save_query"](name="busiest_city", sql="SELECT count(*) AS n FROM deliveries")
sv = h["run_saved"](name="busiest_city")
assert sv["rows"][0]["n"] == 200, sv

print("\nE2E smoke: ALL OK")
