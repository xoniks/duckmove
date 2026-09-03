"""Demo driver: load example data through the duckmove tool handlers and
generate a few real maps into a preview directory, exactly as an MCP client
would. Run: python scripts/demo_launch.py <preview_dir>
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

preview_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else (ROOT / ".demo_preview")
preview_dir.mkdir(parents=True, exist_ok=True)

os.environ["DUCKMOVE_PREVIEW_DIR"] = str(preview_dir)
os.environ["DUCKMOVE_PREVIEW_URL"] = "http://127.0.0.1:8765"
os.environ["DUCKMOVE_DB"] = ":memory:"

from duckmove.server import build_app, get_tool_handlers  # noqa: E402

build_app()
h = get_tool_handlers()

print("== Loading example data ==")
gps = h["load_data"](path=str(EXAMPLES / "shipments_gps.csv"))
print(f"  shipments_gps: {gps['row_count']} rows, cols={[c['name'] for c in gps['columns']]}")
print(f"  detected coords: {gps['detected_coordinates']}")
gf = h["load_data"](path=str(EXAMPLES / "geofences.csv"))
legs = h["load_data"](path=str(EXAMPLES / "sample_shipments.csv"))
stores = h["load_data"](path=str(EXAMPLES / "stores.xlsx"))
deliveries = h["load_data"](path=str(EXAMPLES / "deliveries.parquet"))
print(f"  stores(xlsx): {stores['row_count']} rows | deliveries(parquet): {deliveries['row_count']} rows")

print("\n== register geofences ==")
reg = h["register_geofences"](source_table="geofences", set_name="areas")
print(f"  registered {reg['fence_count']} fences as 'areas'")

print("\n== run_sql: total distance per shipment ==")
res = h["run_sql"](sql="""
  WITH hops AS (
    SELECT shipment_id,
           ST_Distance_Spheroid(ST_Point(lat, lon),
             ST_Point(lag(lat) OVER w, lag(lon) OVER w)) AS m
    FROM shipments_gps
    WINDOW w AS (PARTITION BY shipment_id ORDER BY "timestamp")
  )
  SELECT shipment_id, round(sum(m)/1000.0, 1) AS total_km
  FROM hops GROUP BY shipment_id ORDER BY total_km DESC
""")
for row in res["rows"]:
    print(f"    {row['shipment_id']}: {row['total_km']} km")

print("\n== generating maps ==")
m1 = h["map_points"](table_or_sql="shipments_gps", lat_col="lat", lon_col="lon",
                     popup_cols=["shipment_id", "status", "carrier"], geofence_set="areas")
print(f"  points map: {m1['url']}  ({m1['points_plotted']} points, fences overlaid)")

m2 = h["map_routes"](table_or_sql="sample_shipments", from_lat="from_lat",
                     from_lon="from_lon", to_lat="to_lat", to_lon="to_lon",
                     popup_cols=["shipment_id", "description"])
print(f"  routes map: {m2['url']}  ({m2['routes_plotted']} routes)")

mt = h["map_tracks"](table_or_sql="shipments_gps", group_by="shipment_id",
                     lat_col="lat", lon_col="lon", order_by="timestamp",
                     popup_cols=["customer", "status"], geofence_set="areas")
print(f"  TRACKS map: {mt['url']}  ({mt['groups_plotted']} shipment paths)")
for g in mt["per_group"]:
    print(f"      {g['group']}: {g['total_km']} km")

m3 = h["map_points"](
    table_or_sql="SELECT s.name, s.city, s.lat, s.lon, s.monthly_revenue "
                 "FROM stores s ORDER BY s.monthly_revenue DESC",
    lat_col="lat", lon_col="lon", popup_cols=["name", "city", "monthly_revenue"])
print(f"  stores revenue map: {m3['url']}  ({m3['points_plotted']} stores)")

print(f"\nPreview dir: {preview_dir}")
print("Open http://127.0.0.1:8765 once the server is up.")
