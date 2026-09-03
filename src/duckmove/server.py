from __future__ import annotations

import functools
import logging
from typing import Annotated, Any, Callable, Dict, List, Literal, Optional, Tuple

import anyio.to_thread
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .core import errors, quality
from .core.engine import (
    DEFAULT_MAX_ROWS,
    MAX_ROWS_LIMIT,
    Engine,
    _sanitize_name,
    geodesic_km_expr,
)
from .core.errors import ToolError
from .geofences import ops as geofence_ops
from .maps import render

log = logging.getLogger(__name__)

_ENGINE: Optional[Engine] = None
_HANDLERS: Dict[str, Any] = {}

SAVED_TABLE = "_saved_queries"
SAMPLE_ROWS = 5
#: Row ceilings for map rendering. Beyond these a browser map stops being
#: readable long before it stops being renderable.
MAP_ROW_LIMIT = 10_000
TRACK_ROW_LIMIT = 20_000

EXAMPLE_PROMPTS = [
    "Load examples/shipments_gps.csv and describe it.",
    "Using run_sql, compute the total distance per shipment_id ordered by "
    "timestamp (hint: lag() window + ST_Distance_Spheroid(ST_Point(lat, lon), ...)).",
    "Register examples/geofences.csv as geofences named 'areas', then count "
    "which GPS points fall inside each fence.",
    "Load examples/customer_geofences.csv and register it tagged by 'customer'. "
    "Then show all shipments of Customer_A and only Customer_A's geofences "
    "(map_tracks with geofence_tag='Customer_A').",
    "Show me every shipment's route and how far each one travelled "
    "(map_tracks grouped by shipment_id, ordered by timestamp).",
    "Map the stores colored by city and sized by monthly_revenue "
    "(map_points with color_by and size_by).",
    "Show a heatmap of delivery destinations (map_points style='heatmap').",
    "Save that distance query as 'total_distance' so I can rerun it later.",
]


def _engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = Engine()
    return _ENGINE


def shutdown() -> None:
    """Close the shared engine. Safe to call more than once."""
    global _ENGINE
    if _ENGINE is not None:
        try:
            _ENGINE.close()
        finally:
            _ENGINE = None


def get_tool_handlers() -> Dict[str, Any]:
    return _HANDLERS


def _table_hint() -> str:
    tables = ", ".join(t["table"] for t in _engine().list_tables()) or "(none)"
    return f"Available tables: {tables}. Use describe_table to see columns."


def _ensure_saved_table(eng: Engine) -> None:
    eng.con.execute(
        f"CREATE TABLE IF NOT EXISTS {SAVED_TABLE} ("
        "name VARCHAR PRIMARY KEY, sql VARCHAR, description VARCHAR)"
    )


def _suggest_csv_name(csv_text: str) -> str:
    """Pick a friendly default table name from a pasted CSV's header row."""
    first = next((ln for ln in csv_text.splitlines() if ln.strip()), "")
    cols: List[str] = []
    for delim in (",", ";", "\t", "|"):
        if delim in first:
            cols = [c.strip().strip('"') for c in first.split(delim)]
            break
    if not cols and first.strip():
        cols = [first.strip()]
    if not cols:
        return "pasted_data"
    g = quality.guess_coordinate_columns(cols)
    if g.get("from_lat") and g.get("to_lat"):
        return "routes"
    if g.get("lat") and g.get("lon"):
        return "points"
    return f"{_sanitize_name(cols[0]) or 'pasted'}_data"


def _with_summary(info: Dict[str, Any]) -> Dict[str, Any]:
    eng = _engine()
    table = info["table"]
    sample = eng.query(f'SELECT * FROM main."{table}" LIMIT {SAMPLE_ROWS}')
    info["sample"] = sample["rows"]
    col_names = [c["name"] for c in info["columns"]]
    coords = quality.guess_coordinate_columns(col_names)
    info["detected_coordinates"] = {k: v for k, v in coords.items() if v}
    warnings: List[str] = []
    lat_col, lon_col = coords.get("lat"), coords.get("lon")
    if lat_col and lon_col:
        q = quality.coordinate_quality(eng, table, lat_col, lon_col)
        warnings.extend(quality.quality_warnings(q))
    info["warnings"] = warnings
    return info


def _resolve_source(table_or_sql: str) -> Tuple[str, List[str]]:
    """Resolve a table name or SQL query to a (subquery_sql, columns) pair.

    No user-supplied identifier is interpolated here, so callers can validate
    column names against `columns` before building SQL with them. Table names
    are schema-qualified so a table sharing a name with one of our internal
    CTE aliases cannot create a circular reference.
    """
    eng = _engine()
    s = table_or_sql.strip()
    if not s:
        raise ToolError("Empty table name or query.", "INVALID_MAP_INPUT")
    if not any(ch.isspace() for ch in s):
        eng._assert_table(s)
        cols = [c["name"] for c in eng.describe(s)["columns"]]
        return f'main."{s}"', cols
    src = f"({s})"
    cols = eng.query(f"SELECT * FROM {src} LIMIT 0")["columns"]
    return src, cols


def _require_columns(cols: List[str], needed: List[str]) -> None:
    missing = [c for c in needed if c not in cols]
    if missing:
        raise ToolError(
            f"Unknown column(s): {', '.join(missing)}. Available: {', '.join(cols)}",
            "INVALID_MAP_INPUT",
        )


def _rows_for_map(table_or_sql: str, needed_cols: List[str]) -> List[Dict[str, Any]]:
    src, cols = _resolve_source(table_or_sql)
    _require_columns(cols, needed_cols)
    return _engine().query(f"SELECT * FROM {src}", max_rows=MAP_ROW_LIMIT)["rows"]


def _map_response(html: str, kind: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    persisted = render.persist_map(html, kind=kind, meta=meta)
    if persisted:
        return persisted
    return {
        "data_url": render.to_data_url(html),
        "info": "No preview server configured; open the data_url in a browser "
        "or run `duckmove start-server` and set DUCKMOVE_PREVIEW_DIR/_URL.",
    }


# -- parameter types ----------------------------------------------------
# Descriptions land in the tool's JSON schema, so the model sees them
# without having to infer intent from the docstring alone.
TableOrSql = Annotated[
    str,
    Field(description="A loaded table name, or a read-only SQL query to map."),
]
MaxRows = Annotated[
    int,
    Field(
        description=(
            f"Maximum rows to return (default {DEFAULT_MAX_ROWS}, "
            f"hard cap {MAX_ROWS_LIMIT}). Aggregate in SQL instead of raising this."
        ),
    ),
]
GeofenceTag = Annotated[
    Optional[str],
    Field(description="Restrict to fences carrying this tag, e.g. one customer."),
]
PopupCols = Annotated[
    Optional[List[str]],
    Field(description="Columns to show in each feature's popup."),
]


def build_app() -> FastMCP:
    app = FastMCP("duckmove")
    # FastMCP takes no `version`, and the low-level server defaults it to None,
    # which makes the MCP handshake advertise the *SDK* version as ours.
    app._mcp_server.version = __version__
    _HANDLERS.clear()

    def tool(
        *,
        name: Optional[str] = None,
        read_only: bool = True,
        destructive: bool = False,
        idempotent: bool = True,
        title: Optional[str] = None,
        hint: Optional[Callable[[], str]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a tool body.

        The body stays synchronous (that is what the tests drive), while the
        function handed to FastMCP is async and offloads to a worker thread
        holding the engine lock. FastMCP invokes sync tools inline on the
        event loop, so without this a slow query would stall the whole
        server; DuckDB connections are not concurrency-safe, so the lock
        serialises access.
        """

        def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
            public_name = name or fn.__name__
            guarded = errors.guard(fn, hint=hint)
            _HANDLERS[public_name] = guarded

            @functools.wraps(fn)
            async def async_tool(**kwargs: Any) -> Any:
                eng = _engine()

                def run() -> Any:
                    with eng.lock:
                        return guarded(**kwargs)

                return await anyio.to_thread.run_sync(run)

            app.tool(
                name=public_name,
                annotations=ToolAnnotations(
                    title=title or public_name.replace("_", " ").title(),
                    readOnlyHint=read_only,
                    destructiveHint=destructive,
                    idempotentHint=idempotent,
                    openWorldHint=False,
                ),
            )(async_tool)
            return guarded

        return decorate

    # -- data in -----------------------------------------------------
    @tool(read_only=False, idempotent=True, title="Load a data file")
    def load_data(
        path: Annotated[str, Field(description="Filesystem path to the file to load.")],
        name: Annotated[
            Optional[str], Field(description="Table name; defaults to the file stem.")
        ] = None,
    ) -> Dict[str, Any]:
        """Load a file into a table. Supports CSV, TSV, Excel (.xlsx),
        Parquet, GeoJSON, JSON, and Shapefile. Returns table name, schema,
        row count, sample rows, and detected coordinate columns.
        This is the only way to read a file: run_sql cannot touch the disk."""
        info = _engine().create_table_from_file(path, name)
        return _with_summary(info)

    @tool(read_only=False, idempotent=False, title="Load pasted CSV text")
    def load_csv_text(
        csv_text: Annotated[str, Field(description="Raw CSV text including header.")],
        name: Annotated[
            Optional[str],
            Field(description="Table name; derived from the header when omitted."),
        ] = None,
    ) -> Dict[str, Any]:
        """Load CSV text pasted by the user into a table. If no name is given,
        a friendly one is derived from the header (e.g. 'points', 'routes')."""
        if not csv_text.strip():
            raise ToolError(
                "csv_text is empty; paste CSV including a header row.",
                "INVALID_INPUT",
            )
        info = _engine().create_table_from_text(
            csv_text, name or _suggest_csv_name(csv_text)
        )
        return _with_summary(info)

    @tool(read_only=False, title="Rename a table")
    def rename_table(
        old: Annotated[str, Field(description="Current table name.")],
        new: Annotated[str, Field(description="New name; sanitised to snake_case.")],
    ) -> Dict[str, Any]:
        """Rename a loaded table to something more memorable, e.g. rename
        'shipments_gps' to 'q3_shipments'."""
        try:
            new_name = _engine().rename_table(old, new)
        except KeyError as e:
            raise ToolError(str(e.args[0]), "TABLE_NOT_FOUND") from e
        except ValueError as e:
            raise ToolError(str(e), "NAME_CONFLICT") from e
        return {"table": new_name, "renamed_from": old}

    # -- discovery ---------------------------------------------------
    @tool(title="List tables")
    def list_tables() -> Dict[str, Any]:
        """List all loaded tables with row counts."""
        return {"tables": _engine().list_tables()}

    @tool(title="Describe a table")
    def describe_table(
        table: Annotated[str, Field(description="Name of a loaded table.")],
    ) -> Dict[str, Any]:
        """Columns, types, row count, sample rows, detected coordinate
        columns, and coordinate-quality warnings for a table."""
        try:
            info = _engine().describe(table)
        except KeyError as e:
            raise ToolError(str(e.args[0]), "TABLE_NOT_FOUND") from e
        return _with_summary(info)

    # -- the workhorse -----------------------------------------------
    @tool(title="Run read-only SQL", hint=_table_hint)
    def run_sql(
        sql: Annotated[str, Field(description="A single read-only SQL statement.")],
        max_rows: MaxRows = DEFAULT_MAX_ROWS,
    ) -> Dict[str, Any]:
        """Run a read-only SQL query (SELECT/WITH/DESCRIBE/SUMMARIZE/SHOW).
        Full DuckDB spatial SQL is available (ST_* functions, spatial joins).
        Geodesic distance in meters: ST_Distance_Spheroid(ST_Point(lat, lon),
        ST_Point(lat2, lon2)) — note latitude comes FIRST in ST_Point for
        spheroid functions. GEOMETRY columns are returned as WKT text.
        Cannot read files or reach the network — use load_data for that."""
        return _engine().query(sql, max_rows=max_rows)

    # -- geofences ----------------------------------------------------
    @tool(read_only=False, title="Register a geofence set")
    def register_geofences(
        source_table: Annotated[
            str, Field(description="Loaded table holding the fence definitions.")
        ],
        set_name: Annotated[str, Field(description="Name for this geofence set.")],
        tag_col: Annotated[
            Optional[str],
            Field(description="Column whose value tags each fence, e.g. 'customer'."),
        ] = None,
    ) -> Dict[str, Any]:
        """Register a geofence set from a loaded table. The table needs
        columns (name, lat, lon, radius_m) for circles, or (name, wkt) for
        polygons. Pass `tag_col` (e.g. 'customer') to tag each fence by that
        column so maps and membership can later be filtered to one tag."""
        try:
            return geofence_ops.register_geofences(
                _engine(), source_table, set_name, tag_col
            )
        except KeyError as e:
            raise ToolError(str(e.args[0]), "TABLE_NOT_FOUND") from e
        except ValueError as e:
            raise ToolError(str(e), "INVALID_GEOFENCE_SOURCE") from e

    @tool(title="List geofence sets")
    def list_geofence_sets() -> Dict[str, Any]:
        """List registered geofence sets."""
        return {"sets": geofence_ops.list_geofence_sets(_engine())}

    @tool(title="List geofence tags")
    def list_geofence_tags(
        geofence_set: Annotated[str, Field(description="Registered geofence set.")],
    ) -> Dict[str, Any]:
        """List the distinct tags (e.g. customer names) in a geofence set, so
        you know what values you can filter overlays/membership by."""
        return {
            "set_name": geofence_set,
            "tags": geofence_ops.list_geofence_tags(_engine(), geofence_set),
        }

    @tool(title="Count points in geofences")
    def points_in_geofences(
        table: Annotated[str, Field(description="Loaded table of points.")],
        lat_col: Annotated[str, Field(description="Latitude column.")],
        lon_col: Annotated[str, Field(description="Longitude column.")],
        geofence_set: Annotated[str, Field(description="Registered geofence set.")],
        geofence_tag: GeofenceTag = None,
    ) -> Dict[str, Any]:
        """Count points of a table inside each fence of a geofence set.
        Pass `geofence_tag` to restrict to fences with that tag (e.g. one
        customer)."""
        try:
            return geofence_ops.points_in_geofences(
                _engine(), table, lat_col, lon_col, geofence_set, geofence_tag
            )
        except KeyError as e:
            raise ToolError(str(e.args[0]), "TABLE_NOT_FOUND") from e

    @tool(title="Geofence enter/exit events")
    def geofence_crossings(
        table: Annotated[str, Field(description="Loaded table of points.")],
        lat_col: Annotated[str, Field(description="Latitude column.")],
        lon_col: Annotated[str, Field(description="Longitude column.")],
        group_by: Annotated[str, Field(description="Entity column, e.g. shipment_id.")],
        order_by: Annotated[str, Field(description="Ordering column, e.g. timestamp.")],
        geofence_set: Annotated[str, Field(description="Registered geofence set.")],
    ) -> Dict[str, Any]:
        """Per-group enter/exit events for a geofence set, ordered by a
        timestamp column (e.g. GPS pings grouped by shipment_id)."""
        try:
            return geofence_ops.geofence_crossings(
                _engine(), table, lat_col, lon_col, group_by, order_by, geofence_set
            )
        except KeyError as e:
            raise ToolError(str(e.args[0]), "TABLE_NOT_FOUND") from e

    # -- maps ----------------------------------------------------------
    @tool(read_only=False, idempotent=False, title="Map points")
    def map_points(
        table_or_sql: TableOrSql,
        lat_col: Annotated[str, Field(description="Latitude column.")],
        lon_col: Annotated[str, Field(description="Longitude column.")],
        popup_cols: PopupCols = None,
        geofence_set: Annotated[
            Optional[str], Field(description="Geofence set to overlay.")
        ] = None,
        geofence_tag: GeofenceTag = None,
        color_by: Annotated[
            Optional[str],
            Field(description="Column driving marker color; adds a legend."),
        ] = None,
        size_by: Annotated[
            Optional[str], Field(description="Numeric column driving marker size.")
        ] = None,
        style: Annotated[
            Literal["markers", "heatmap", "clustered"],
            Field(description="Marker style for the points layer."),
        ] = "markers",
    ) -> Dict[str, Any]:
        """Render an interactive map of points from a table name or a SQL
        query. `color_by` colors markers by a column (numeric scale or
        categories, with a legend), `size_by` scales marker size by a numeric
        column, and `style` is 'markers' (default), 'heatmap', or 'clustered'.
        Optionally overlay a geofence set (and `geofence_tag` for one tag).
        Returns a preview URL (or inline data_url when no preview server)."""
        needed = [lat_col, lon_col] + [c for c in (color_by, size_by) if c]
        rows = _rows_for_map(table_or_sql, needed)
        fences = (
            geofence_ops.get_fences_wkt(_engine(), geofence_set, geofence_tag)
            if geofence_set
            else None
        )
        html = render.points_map(
            rows, lat_col, lon_col, popup_cols, fences, color_by, size_by, style
        )
        out = _map_response(html, "points", {"title": f"Points: {table_or_sql[:40]}"})
        out["points_plotted"] = render.count_plottable(rows, lat_col, lon_col)
        return out

    @tool(read_only=False, idempotent=False, title="Map routes")
    def map_routes(
        table_or_sql: TableOrSql,
        from_lat: Annotated[str, Field(description="Origin latitude column.")],
        from_lon: Annotated[str, Field(description="Origin longitude column.")],
        to_lat: Annotated[str, Field(description="Destination latitude column.")],
        to_lon: Annotated[str, Field(description="Destination longitude column.")],
        popup_cols: PopupCols = None,
        geofence_set: Annotated[
            Optional[str], Field(description="Geofence set to overlay.")
        ] = None,
        geofence_tag: GeofenceTag = None,
    ) -> Dict[str, Any]:
        """Render origin->destination lines from a table name or SQL query.
        Optionally overlay a geofence set (and `geofence_tag` to show only one
        tag's fences)."""
        rows = _rows_for_map(table_or_sql, [from_lat, from_lon, to_lat, to_lon])
        fences = (
            geofence_ops.get_fences_wkt(_engine(), geofence_set, geofence_tag)
            if geofence_set
            else None
        )
        html = render.routes_map(
            rows, from_lat, from_lon, to_lat, to_lon, popup_cols, fences
        )
        out = _map_response(html, "routes", {"title": f"Routes: {table_or_sql[:40]}"})
        out["routes_plotted"] = len(rows)
        return out

    @tool(read_only=False, idempotent=False, title="Map tracks with distances")
    def map_tracks(
        table_or_sql: TableOrSql,
        group_by: Annotated[str, Field(description="Entity column, e.g. shipment_id.")],
        lat_col: Annotated[str, Field(description="Latitude column.")],
        lon_col: Annotated[str, Field(description="Longitude column.")],
        order_by: Annotated[str, Field(description="Ordering column, e.g. timestamp.")],
        popup_cols: PopupCols = None,
        geofence_set: Annotated[
            Optional[str], Field(description="Geofence set to overlay.")
        ] = None,
        geofence_tag: GeofenceTag = None,
    ) -> Dict[str, Any]:
        """Draw the path each group travels and label its total distance.
        Connects each group's points (ordered by `order_by`, e.g. timestamp)
        into a colored line, and computes geodesic distance per group. Ideal
        for "show me each shipment's route and how far it travelled".
        Optionally overlay a geofence set (and `geofence_tag` to show only one
        tag's fences, e.g. one customer). Returns the map URL plus `per_group`
        distances in kilometers."""
        eng = _engine()
        src, cols = _resolve_source(table_or_sql)
        _require_columns(cols, [group_by, lat_col, lon_col, order_by])

        ordered = eng.query(
            f'SELECT * FROM {src} ORDER BY "{group_by}", "{order_by}"',
            max_rows=TRACK_ROW_LIMIT,
        )
        dist_expr = geodesic_km_expr(
            f'"{lat_col}"',
            f'"{lon_col}"',
            f'lag("{lat_col}") OVER w',
            f'lag("{lon_col}") OVER w',
        )
        dist = eng.query(
            f'WITH _dm_hops AS (SELECT "{group_by}" AS grp, {dist_expr} AS km '
            f"FROM {src} "
            f'WINDOW w AS (PARTITION BY "{group_by}" ORDER BY "{order_by}")) '
            f"SELECT grp, round(sum(km), 1) AS total_km FROM _dm_hops "
            f"GROUP BY grp ORDER BY total_km DESC",
            max_rows=TRACK_ROW_LIMIT,
        )
        group_km = {r["grp"]: r["total_km"] for r in dist["rows"]}
        fences = (
            geofence_ops.get_fences_wkt(eng, geofence_set, geofence_tag)
            if geofence_set
            else None
        )
        html = render.tracks_map(
            ordered["rows"], group_by, lat_col, lon_col, group_km, popup_cols, fences
        )
        out = _map_response(html, "tracks", {"title": f"Tracks by {group_by}"})
        out["groups_plotted"] = len(group_km)
        out["per_group"] = [
            {"group": r["grp"], "total_km": r["total_km"]} for r in dist["rows"]
        ]
        return out

    # -- saved queries --------------------------------------------------
    @tool(read_only=False, title="Save a query")
    def save_query(
        name: Annotated[str, Field(description="Name to save the query under.")],
        sql: Annotated[str, Field(description="A single read-only SQL statement.")],
        description: Annotated[
            Optional[str], Field(description="What the query answers.")
        ] = None,
    ) -> Dict[str, Any]:
        """Save a SQL query under a name for later reuse with run_saved."""
        eng = _engine()
        eng.query(sql, max_rows=1)  # validate before saving
        _ensure_saved_table(eng)
        eng.con.execute(f"DELETE FROM {SAVED_TABLE} WHERE name = ?", [name])
        eng.con.execute(
            f"INSERT INTO {SAVED_TABLE} VALUES (?, ?, ?)", [name, sql, description]
        )
        return {"saved": name}

    @tool(title="Run a saved query", hint=_table_hint)
    def run_saved(
        name: Annotated[str, Field(description="Name given to save_query.")],
        max_rows: MaxRows = DEFAULT_MAX_ROWS,
    ) -> Dict[str, Any]:
        """Run a previously saved query by name."""
        eng = _engine()
        _ensure_saved_table(eng)
        row = eng.con.execute(
            f"SELECT sql FROM {SAVED_TABLE} WHERE name = ?", [name]
        ).fetchone()
        if not row:
            raise ToolError(f"Unknown saved query: {name}", "NOT_FOUND")
        return eng.query(row[0], max_rows=max_rows)

    @tool(title="List saved queries")
    def list_saved() -> Dict[str, Any]:
        """List saved queries."""
        eng = _engine()
        _ensure_saved_table(eng)
        rows = eng.con.execute(
            f"SELECT name, sql, description FROM {SAVED_TABLE} ORDER BY name"
        ).fetchall()
        return {
            "queries": [{"name": r[0], "sql": r[1], "description": r[2]} for r in rows]
        }

    # -- meta -----------------------------------------------------------
    @tool(name="help", title="Help and examples")
    def help_() -> Dict[str, Any]:
        """List available tools and example prompts."""
        tools = [
            {"name": n, "description": (f.__doc__ or "").strip()}
            for n, f in sorted(_HANDLERS.items())
        ]
        return {
            "tools": tools,
            "examples": EXAMPLE_PROMPTS,
            "geodesic_distance_hint": geodesic_km_expr(
                "lat_a", "lon_a", "lat_b", "lon_b"
            ),
        }

    return app


def main() -> None:  # Entry point if invoked directly
    from .logconfig import configure_logging

    configure_logging()
    try:
        build_app().run()
    finally:
        shutdown()


if __name__ == "__main__":
    main()
