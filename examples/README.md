# Example Datasets

| File | Contents | Try it with |
|---|---|---|
| `shipments_gps.csv` | GPS pings for four shipments (shipment_id, customer, timestamp, lat, lon, carrier, status) | distance SQL, geofences, `map_points` |
| `geofences.csv` | Circles (LA, Portland, Denver, Dallas, Miami, customer DCs) + a West Coast polygon corridor | `register_geofences` |
| `customer_geofences.csv` | Geofences with a `customer` column (Customer_A…D) | `register_geofences` with `tag_col='customer'`, then filter overlays by `geofence_tag` |
| `sample_shipments.csv` | 5 origin→destination legs between US cities | `map_routes`, distance SQL |
| `shipments_legs.csv` | OD legs with timestamps, carrier, cost, weight | `run_sql` aggregations, `map_routes` |
| `stores.csv` / `stores.xlsx` | 10 retail stores with city and monthly revenue | Excel loading, BI-style SQL + maps |
| `deliveries.parquet` | 200 deliveries (store_id, dispatched_at, dest lat/lon, weight, status) | Parquet loading, joins with stores |

Regenerate the dummy data: `python scripts/make_dummy_data.py`

## Suggested prompts (paste into your MCP client)

Getting started

- "Load `examples/shipments_gps.csv` and describe it."
- "Which shipment travelled the farthest? Use the timestamps to order the pings."

Geofences

- "Load `examples/geofences.csv` and register it as geofences named `areas`."
- "How many GPS points fall inside each fence?"
- "Did any shipment enter or leave a fence during transit? Show enter/exit events."

Maps

- "Map all GPS points with shipment_id and status as popups, overlay the `areas` fences."
- "Map the routes in `examples/shipments_legs.csv`."
- "Map `stores.xlsx` colored by city and sized by monthly_revenue." (legend + graduated markers)
- "Show a heatmap of delivery destinations from `deliveries.parquet`."
- "Cluster the delivery destinations and color them by status."

Per-customer view (the "show Customer_A's shipments and its geofences" workflow)

- "Load `examples/customer_geofences.csv` and register it as `customers` tagged by `customer`."
- "Load `examples/shipments_gps.csv`."
- "Show all shipments of Customer_A and only Customer_A's geofences." → the agent
  runs `map_tracks` on `SELECT * FROM shipments_gps WHERE customer='Customer_A'`
  with `geofence_set='customers'`, `geofence_tag='Customer_A'`.

BI-style analysis (multi-format)

- "Load `examples/stores.xlsx` and `examples/deliveries.parquet`. Join them and show delivery counts and total weight per store city."
- "Which store has the highest revenue per delivery? Map the top 3 with revenue as popup."
- "What's the average distance from each store to its delivery destinations? (stores have lat/lon, deliveries have dest_lat/dest_lon)"

Reuse

- "Save that distance query as `total_distance` so I can rerun it tomorrow."
- "Run the saved query `total_distance`."

## Data quality

The example files are clean, but `describe_table` reports detected coordinate
columns and warnings for missing/out-of-range lat/lon pairs — check those
warnings on your own data before running distance or map analyses.
