from __future__ import annotations

from typing import Dict, List, Optional

LAT_CANDS = ["lat", "latitude", "y"]
LON_CANDS = ["lon", "lng", "longitude", "x"]


def guess_coordinate_columns(columns: List[str]) -> Dict[str, Optional[str]]:
    low = {c.lower(): c for c in columns}

    def find(*cands: str) -> Optional[str]:
        for c in cands:
            if c in low:
                return low[c]
        return None

    out: Dict[str, Optional[str]] = {
        "lat": find(*LAT_CANDS),
        "lon": find(*LON_CANDS),
    }
    for prefix in ("from", "to"):
        out[f"{prefix}_lat"] = find(*[f"{prefix}_{c}" for c in LAT_CANDS])
        out[f"{prefix}_lon"] = find(*[f"{prefix}_{c}" for c in LON_CANDS])
    return out


def coordinate_quality(engine, table: str, lat_col: str, lon_col: str) -> Dict[str, int]:
    engine.assert_columns(table, lat_col, lon_col)
    row = engine.con.execute(
        f'''
        SELECT
          count(*) FILTER (lat IS NOT NULL AND lon IS NOT NULL
                           AND lat BETWEEN -90 AND 90
                           AND lon BETWEEN -180 AND 180)             AS valid,
          count(*) FILTER (lat IS NULL OR lon IS NULL)               AS missing,
          count(*) FILTER (lat IS NOT NULL AND lon IS NOT NULL
                           AND NOT (lat BETWEEN -90 AND 90
                                    AND lon BETWEEN -180 AND 180))   AS invalid
        FROM (SELECT TRY_CAST("{lat_col}" AS DOUBLE) AS lat,
                     TRY_CAST("{lon_col}" AS DOUBLE) AS lon
              FROM "{table}")
        '''
    ).fetchone()
    return {"valid": row[0], "missing": row[1], "invalid": row[2]}


def quality_warnings(q: Dict[str, int]) -> List[str]:
    out = []
    if q["missing"]:
        out.append(f"{q['missing']} row(s) have missing coordinates and will be skipped.")
    if q["invalid"]:
        out.append(
            f"{q['invalid']} row(s) have out-of-range coordinates and will be skipped."
        )
    return out
