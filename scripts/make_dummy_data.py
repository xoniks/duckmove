"""Regenerate the dummy datasets in examples/ (stores.csv, stores.xlsx,
deliveries.parquet). Run from the repo root: python scripts/make_dummy_data.py
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import openpyxl

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

STORES = [
    # store_id, name, city, lat, lon, monthly_revenue
    ("ST01", "Downtown SF", "San Francisco", 37.7793, -122.4193, 182000),
    ("ST02", "Mission", "San Francisco", 37.7599, -122.4148, 95000),
    ("ST03", "DTLA", "Los Angeles", 34.0407, -118.2468, 210000),
    ("ST04", "Santa Monica", "Los Angeles", 34.0195, -118.4912, 158000),
    ("ST05", "Pearl District", "Portland", 45.5316, -122.6822, 87000),
    ("ST06", "Capitol Hill", "Seattle", 47.6253, -122.3222, 132000),
    ("ST07", "Belltown", "Seattle", 47.6141, -122.3459, 121000),
    ("ST08", "Tempe", "Phoenix", 33.4255, -111.9400, 76000),
    ("ST09", "LoDo", "Denver", 39.7533, -105.0005, 99000),
    ("ST10", "Wynwood", "Miami", 25.8005, -80.1990, 143000),
]


def write_stores_csv() -> None:
    lines = ["store_id,name,city,lat,lon,monthly_revenue"]
    for r in STORES:
        lines.append(",".join(str(v) for v in r))
    (EXAMPLES / "stores.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


CUSTOMER_GEOFENCES = [
    ("Customer_A", "A_LosAngeles_DC", 34.0522, -118.2437, 30000),
    ("Customer_A", "A_SanFrancisco_Hub", 37.7749, -122.4194, 25000),
    ("Customer_B", "B_Portland_DC", 45.5152, -122.6784, 25000),
    ("Customer_B", "B_Seattle_Hub", 47.6062, -122.3321, 25000),
    ("Customer_C", "C_Miami_DC", 25.7617, -80.1918, 30000),
    ("Customer_C", "C_Chicago_Hub", 41.8781, -87.6298, 30000),
    ("Customer_C", "C_Denver_DC", 39.7392, -104.9903, 30000),
    ("Customer_D", "D_Houston_DC", 29.7604, -95.3698, 30000),
    ("Customer_D", "D_Dallas_Hub", 32.7767, -96.7970, 30000),
]


def write_customer_geofences_csv() -> None:
    lines = ["customer,name,lat,lon,radius_m"]
    for r in CUSTOMER_GEOFENCES:
        lines.append(",".join(str(v) for v in r))
    (EXAMPLES / "customer_geofences.csv").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def write_stores_xlsx() -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "stores"
    ws.append(["store_id", "name", "city", "lat", "lon", "monthly_revenue"])
    for r in STORES:
        ws.append(list(r))
    wb.save(EXAMPLES / "stores.xlsx")


def write_deliveries_parquet() -> None:
    con = duckdb.connect()
    con.execute(
        """
        COPY (
          SELECT
            'D' || lpad(i::VARCHAR, 4, '0')                       AS delivery_id,
            'ST' || lpad(((i % 10) + 1)::VARCHAR, 2, '0')         AS store_id,
            TIMESTAMP '2026-05-01 06:00:00' + INTERVAL (i) HOUR   AS dispatched_at,
            34.0522 + ((i * 37) % 1000) / 250.0 - 2.0             AS dest_lat,
            -118.2437 + ((i * 53) % 1000) / 125.0 - 4.0           AS dest_lon,
            round(5 + ((i * 7) % 120) / 4.0, 2)                   AS weight_kg,
            CASE (i % 4) WHEN 0 THEN 'delivered' WHEN 1 THEN 'in_transit'
                         WHEN 2 THEN 'delivered' ELSE 'delayed' END AS status
          FROM range(1, 201) t(i)
        ) TO '{out}' (FORMAT PARQUET)
        """.format(out=str(EXAMPLES / "deliveries.parquet").replace("\\", "/"))
    )
    con.close()


if __name__ == "__main__":
    write_stores_csv()
    write_stores_xlsx()
    write_deliveries_parquet()
    write_customer_geofences_csv()
    print(
        "Wrote stores.csv, stores.xlsx, deliveries.parquet, customer_geofences.csv to",
        EXAMPLES,
    )
