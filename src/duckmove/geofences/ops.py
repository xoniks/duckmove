from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to a checker
    from ..core.engine import Engine

GEOFENCE_TABLE = "_geofences"
EARTH_R = 6371008.8  # mean earth radius, meters


def _ensure_table(engine: Engine) -> None:
    engine.con.execute(
        f"CREATE TABLE IF NOT EXISTS {GEOFENCE_TABLE} ("
        "set_name VARCHAR, fence_name VARCHAR, tag VARCHAR, geom GEOMETRY)"
    )
    # Migrate older DBs created before the tag column existed.
    cols = {c[0] for c in engine.con.execute(f"DESCRIBE {GEOFENCE_TABLE}").fetchall()}
    if "tag" not in cols:
        engine.con.execute(f"ALTER TABLE {GEOFENCE_TABLE} ADD COLUMN tag VARCHAR")


def circle_wkt(lat: float, lon: float, radius_m: float, sides: int = 64) -> str:
    """Spherical-destination polygon approximating a geodesic circle."""
    lat1, lon1 = math.radians(lat), math.radians(lon)
    d = radius_m / EARTH_R
    pts = []
    for i in range(sides + 1):
        az = math.radians(i * 360.0 / sides)
        lat2 = math.asin(
            math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(az)
        )
        lon2 = lon1 + math.atan2(
            math.sin(az) * math.sin(d) * math.cos(lat1),
            math.cos(d) - math.sin(lat1) * math.sin(lat2),
        )
        pts.append(f"{math.degrees(lon2):.7f} {math.degrees(lat2):.7f}")
    return f"POLYGON (({', '.join(pts)}))"


def vertices_wkt(vertices: str) -> str:
    """'lat,lon; lat,lon; ...' -> POLYGON WKT (closing the ring if needed)."""
    pts = []
    for pair in vertices.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        lat_s, lon_s = pair.split(",")[:2]
        pts.append(f"{float(lon_s):.7f} {float(lat_s):.7f}")
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return f"POLYGON (({', '.join(pts)}))"


def register_geofences(
    engine: Engine,
    source_table: str,
    set_name: str,
    tag_col: Optional[str] = None,
) -> Dict[str, Any]:
    """Register a geofence set. `tag_col`, if given, is a column in the source
    table (e.g. 'customer') whose value tags each fence, so overlays and
    membership can later be filtered to one tag."""
    _ensure_table(engine)
    cols = {c["name"].lower() for c in engine.describe(source_table)["columns"]}
    if tag_col is not None:
        engine.assert_columns(source_table, tag_col)  # validate identifier
    tag_expr = f'"{tag_col}"' if tag_col else "NULL"
    engine.con.execute(f"DELETE FROM {GEOFENCE_TABLE} WHERE set_name = ?", [set_name])
    insert = (
        f"INSERT INTO {GEOFENCE_TABLE} (set_name, fence_name, tag, geom) "
        f"VALUES (?, ?, ?, ST_GeomFromText(?))"
    )
    if {"lat", "lon", "radius_m"} <= cols:
        has_vertices = "vertices" in cols
        vert_sel = ", vertices" if has_vertices else ", NULL"
        rows = engine.con.execute(
            f"SELECT name, TRY_CAST(lat AS DOUBLE), TRY_CAST(lon AS DOUBLE), "
            f'TRY_CAST(radius_m AS DOUBLE){vert_sel}, {tag_expr} FROM "{source_table}"'
        ).fetchall()
        for name, lat, lon, r, verts, tag in rows:
            if lat is not None and lon is not None and r is not None:
                wkt = circle_wkt(lat, lon, r)
            elif verts:
                wkt = vertices_wkt(str(verts))
            else:
                continue
            engine.con.execute(
                insert,
                [set_name, str(name), (str(tag) if tag is not None else None), wkt],
            )
    elif "wkt" in cols:
        engine.con.execute(
            f"INSERT INTO {GEOFENCE_TABLE} (set_name, fence_name, tag, geom) "
            f'SELECT ?, name, {tag_expr}, ST_GeomFromText(wkt) FROM "{source_table}"',
            [set_name],
        )
    else:
        raise ValueError(
            "Geofence source needs columns (name, lat, lon, radius_m) or (name, wkt)"
        )
    row = engine.con.execute(
        f"SELECT count(*) FROM {GEOFENCE_TABLE} WHERE set_name = ?", [set_name]
    ).fetchone()
    n = int(row[0]) if row else 0
    return {"set_name": set_name, "fence_count": n, "tagged_by": tag_col}


def list_geofence_sets(engine: Engine) -> List[Dict[str, Any]]:
    _ensure_table(engine)
    rows = engine.con.execute(
        f"SELECT set_name, count(*) FROM {GEOFENCE_TABLE} "
        "GROUP BY set_name ORDER BY set_name"
    ).fetchall()
    return [{"set_name": r[0], "fence_count": r[1]} for r in rows]


def get_fences_wkt(
    engine: Engine, set_name: str, tag: Optional[str] = None
) -> List[Dict[str, str]]:
    _ensure_table(engine)
    if tag is not None:
        rows = engine.con.execute(
            f"SELECT fence_name, ST_AsText(geom), tag FROM {GEOFENCE_TABLE} "
            "WHERE set_name = ? AND tag = ?",
            [set_name, tag],
        ).fetchall()
    else:
        rows = engine.con.execute(
            f"SELECT fence_name, ST_AsText(geom), tag FROM {GEOFENCE_TABLE} "
            "WHERE set_name = ?",
            [set_name],
        ).fetchall()
    return [{"fence": r[0], "wkt": r[1], "tag": r[2]} for r in rows]


def list_geofence_tags(engine: Engine, set_name: str) -> List[str]:
    _ensure_table(engine)
    rows = engine.con.execute(
        f"SELECT DISTINCT tag FROM {GEOFENCE_TABLE} "
        "WHERE set_name = ? AND tag IS NOT NULL ORDER BY tag",
        [set_name],
    ).fetchall()
    return [r[0] for r in rows]


def _points_cte(table: str, lat_col: str, lon_col: str) -> str:
    return (
        f'SELECT *, ST_Point(TRY_CAST("{lon_col}" AS DOUBLE), '
        f'TRY_CAST("{lat_col}" AS DOUBLE)) AS _pt FROM main."{table}" '
        f'WHERE TRY_CAST("{lat_col}" AS DOUBLE) BETWEEN -90 AND 90 '
        f'AND TRY_CAST("{lon_col}" AS DOUBLE) BETWEEN -180 AND 180'
    )


def points_in_geofences(
    engine: Engine,
    table: str,
    lat_col: str,
    lon_col: str,
    set_name: str,
    tag: Optional[str] = None,
) -> Dict[str, Any]:
    engine.assert_columns(table, lat_col, lon_col)
    tag_clause = "AND g.tag = ?" if tag is not None else ""
    params = [set_name] + ([tag] if tag is not None else [])
    per_fence = engine.con.execute(
        f"""
        WITH _dm_pts AS ({_points_cte(table, lat_col, lon_col)})
        SELECT g.fence_name, count(p._pt) AS inside
        FROM {GEOFENCE_TABLE} g
        LEFT JOIN _dm_pts p ON ST_Contains(g.geom, p._pt)
        WHERE g.set_name = ? {tag_clause}
        GROUP BY g.fence_name ORDER BY g.fence_name
        """,
        params,
    ).fetchall()
    return {
        "set_name": set_name,
        "tag": tag,
        "per_fence": [{"fence": r[0], "points_inside": r[1]} for r in per_fence],
    }


def geofence_crossings(
    engine: Engine,
    table: str,
    lat_col: str,
    lon_col: str,
    group_by: str,
    order_by: str,
    set_name: str,
) -> Dict[str, Any]:
    engine.assert_columns(table, lat_col, lon_col, group_by, order_by)
    rows = engine.con.execute(
        f"""
        WITH _dm_pts AS ({_points_cte(table, lat_col, lon_col)}),
        _dm_flags AS (
          SELECT p."{group_by}" AS grp, p."{order_by}" AS ord, g.fence_name,
                 ST_Contains(g.geom, p._pt) AS inside
          FROM _dm_pts p CROSS JOIN
               (SELECT * FROM {GEOFENCE_TABLE} WHERE set_name = ?) g
        ),
        _dm_seq AS (
          SELECT *, lag(inside) OVER (
                   PARTITION BY grp, fence_name ORDER BY ord) AS prev
          FROM _dm_flags
        )
        SELECT grp, CAST(ord AS VARCHAR) AS ord, fence_name,
               CASE WHEN inside AND NOT prev THEN 'enter' ELSE 'exit' END AS event
        FROM _dm_seq
        WHERE prev IS NOT NULL AND inside != prev
        ORDER BY grp, ord
        """,
        [set_name],
    ).fetchall()
    return {
        "events": [
            {"group": r[0], "at": str(r[1]), "fence": r[2], "event": r[3]} for r in rows
        ]
    }
