# duckmove

**Ask spatial questions about your business data in plain English.**

duckmove is a DuckDB-powered [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that lets any MCP client (Claude Desktop, Claude Code, …) analyze and map your geospatial data. It is built for data analysts and BI people — no GIS software, no cloud account, no SQL required (the LLM writes it for you).

- **Local-first**: `pip install duckmove`, point your MCP client at it, done. Your data stays on your machine in a single DuckDB file.
- **Any format**: CSV, TSV, Excel, Parquet, GeoJSON, JSON, Shapefile.
- **Real engine**: millions of rows are fine. Full DuckDB SQL plus the spatial extension (`ST_*` functions, spatial joins, accurate WGS84 geodesic distances).
- **Maps**: interactive folium maps with geofence overlays, served from a local preview page.
- **Persistent & deduplicated**: loaded tables survive restarts (`~/.duckmove/data.duckdb`); re-loading the same file reuses the existing table instead of piling up copies.

## Quick start

```bash
pip install duckmove                 # or: pip install -e . from a clone
duckmove init-claude --write --absolute   # registers the server in Claude Desktop
duckmove doctor                      # verify everything works
```

Restart Claude Desktop, then try:

> "Load `examples/shipments_gps.csv` and tell me which shipment travelled the farthest."

The agent will call `load_data`, inspect the schema with `describe_table`, and write the SQL itself with `run_sql`.

### Maps (optional but nice)

```bash
duckmove init-claude --write --absolute --set-preview-env
duckmove start-server        # local preview page at http://127.0.0.1:8765
```

Now "map all delivery destinations" returns a clickable URL. Every generated map is listed on the preview homepage, newest first.

## Tools

| Tool | What it does |
|---|---|
| `load_data` | Load a file (csv/tsv/xlsx/parquet/geojson/json/shp) into a table. Smart reuse: the same file unchanged is reused instantly; if it changed on disk it's refreshed in place — no duplicate `_2` tables |
| `load_csv_text` | Load pasted CSV text (auto-named `points`/`routes`/… from the header) |
| `list_tables` / `describe_table` | Discover tables, schemas, samples, coordinate quality |
| `rename_table` | Rename a loaded table, e.g. `shipments_gps` → `q3_shipments` |
| `run_sql` | **The workhorse.** Read-only SQL with full DuckDB spatial support |
| `register_geofences` | Create a named geofence set from circles (`name,lat,lon,radius_m`), vertex polygons, or WKT. Pass `tag_col` (e.g. `customer`) to tag each fence so it can be filtered later |
| `list_geofence_sets` / `list_geofence_tags` | List geofence sets, and the tags (e.g. customers) within a set |
| `points_in_geofences` | Count points inside each fence (optionally filtered to one `geofence_tag`) |
| `geofence_crossings` | Per-group enter/exit events ordered by timestamp |
| `map_points` | Interactive point map. `color_by` a column (numeric scale or categories, with legend), `size_by` a numeric column, and `style` = markers / heatmap / clustered |
| `map_routes` | Origin→destination lines from a table or SQL query |
| `map_tracks` | Draw each group's path (e.g. per shipment, ordered by timestamp) as a colored line labelled with its total distance |
| `save_query` / `run_saved` / `list_saved` | Save and rerun favorite analyses |
| `help` | Tool roster + example prompts |

All three map tools accept a table name **or a SQL query**, and an optional `geofence_set` + `geofence_tag` to overlay just one tag's (e.g. one customer's) fences.

## Spatial SQL crash course

`run_sql` accepts any read-only DuckDB query. Useful spatial idioms:

```sql
-- Geodesic distance in km between two coordinate pairs (WGS84).
-- NOTE: ST_Distance_Spheroid expects ST_Point(latitude, longitude).
SELECT ST_Distance_Spheroid(ST_Point(40.7128, -74.0060),
                            ST_Point(34.0522, -118.2437)) / 1000.0 AS km;

-- Total distance per shipment from ordered GPS pings
WITH hops AS (
  SELECT shipment_id,
         ST_Distance_Spheroid(
           ST_Point(lat, lon),
           ST_Point(lag(lat) OVER w, lag(lon) OVER w)) AS m
  FROM shipments_gps
  WINDOW w AS (PARTITION BY shipment_id ORDER BY "timestamp")
)
SELECT shipment_id, round(sum(m) / 1000.0, 1) AS total_km
FROM hops GROUP BY shipment_id ORDER BY total_km DESC;
```

Results with GEOMETRY columns come back as WKT text, and queries are capped (default 500 rows) with a truncation note.

## Example data

The `examples/` folder ships realistic dummy datasets:

- `shipments_gps.csv` — GPS pings (shipment_id, customer, timestamp, lat, lon, carrier, status)
- `geofences.csv` — circles + a polygon corridor for `register_geofences`
- `customer_geofences.csv` — geofences with a `customer` column, for `tag_col`/`geofence_tag` filtering
- `sample_shipments.csv` / `shipments_legs.csv` — origin→destination legs
- `stores.csv` / `stores.xlsx` — retail stores with revenue (BI-style)
- `deliveries.parquet` — 200 deliveries for Parquet loading and SQL practice

See `examples/README.md` for prompt ideas. Regenerate with `python scripts/make_dummy_data.py`.

## CLI reference

- `duckmove serve` (alias `start`) — run the MCP server over stdio
- `duckmove init-claude` (alias `setup-claude`) — print/merge Claude Desktop config
  - `--write` merge into config (with `.bak` backup), `--absolute` use the current Python path, `--set-preview-env` add preview env vars
- `duckmove doctor` — check Python, duckdb + spatial, folium, and Claude config
- `duckmove preview` / `duckmove start-server` — serve generated maps over local HTTP

Environment variables:

| Variable | Purpose |
|---|---|
| `DUCKMOVE_DB` | Database file (default `~/.duckmove/data.duckdb`) |
| `DUCKMOVE_PREVIEW_DIR` / `DUCKMOVE_PREVIEW_URL` | Where rendered maps are written and served from |
| `DUCKMOVE_ALLOWED_DIRS` | Restrict `load_data` to these directories (OS path separator delimited) |
| `DUCKMOVE_LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING` (default `WARNING`); logs go to stderr |

If `duckmove` is not on PATH, use `python -m duckmove.cli <command>`.

## Security & limits

**`run_sql` cannot touch the filesystem or the network.** An LLM-driven SQL
tool that can call `read_csv('~/.aws/credentials')` is an exfiltration
primitive — text hidden in a loaded spreadsheet is enough to trigger it. So
the two capabilities are separated:

- **`load_data` is the only path to disk.** It takes a path the user named and
  binds it as a query parameter, so it never passes through the SQL policy.
- **Everything the model composes itself** — `run_sql`, map `table_or_sql`
  arguments, saved queries — is checked by `core/sqlguard.py`, which rejects:
  - anything but `SELECT`/`WITH`/`DESCRIBE`/`SUMMARIZE`/`SHOW`/`EXPLAIN`;
  - writes and side effects (`DROP`, `ATTACH`, `COPY`, `INSTALL`, `PRAGMA`,
    `SET`, …) **anywhere** in the statement, not just as the first word;
  - every file/network reader (`read_*`, `ST_Read`, `glob`, `parquet_scan`,
    `iceberg_scan`, `postgres_scan`, `getenv`, …) and bare paths in `FROM`;
  - multiple statements.

  Checks run with string literals masked, so a payload inside a quoted value
  can neither smuggle a keyword past the scan nor falsely trip it.

Additional limits:

- Set `DUCKMOVE_ALLOWED_DIRS` to confine `load_data` to specific directories.
  Unset (the default), it loads any path the user explicitly names.
- Results are capped at 10,000 rows regardless of the requested `max_rows`,
  with `truncated` and an explanatory `note` in the response.
- The local preview server rejects cross-origin `POST`s, so a page in your
  browser cannot delete or rename your maps behind your back.
- Tool failures return `{error, error_code}` — never a stack trace — with the
  available tables/columns so the agent can self-correct.

Run `duckmove doctor` to see the active policy.

## Development

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -e ".[dev]"

python -m pytest                 # 200 tests; no editable install required
python -m pytest --cov=duckmove  # with coverage
ruff check src tests             # lint
mypy                             # types
```

CI (`.github/workflows/ci.yml`) runs the suite on Linux/macOS/Windows across
Python 3.9–3.13, plus lint and a package build.

## Releasing to PyPI

Releases are published automatically by GitHub Actions (`.github/workflows/publish.yml`)
using **PyPI Trusted Publishing** — no API tokens are stored anywhere.

One-time setup (PyPI account owner):

1. On PyPI → *Your projects* → *Publishing* → **Add a pending publisher**:
   - PyPI Project Name: `duckmove`
   - Owner: `xoniks`  ·  Repository: `duckmove`
   - Workflow name: `publish.yml`  ·  Environment: `pypi`
2. In the GitHub repo → *Settings → Environments* → create an environment named `pypi`.

To cut a release:

1. Bump `version` in `pyproject.toml` and commit.
2. On GitHub → *Releases* → **Draft a new release**, create a tag like `v0.1.0`, publish it.
3. The workflow runs tests, builds, `twine check`s, and publishes to PyPI via OIDC.

A local build for inspection: `python -m build` then `python -m twine check dist/*`.

## Roadmap

- **Phase 2 — Snowflake**: `import_from_snowflake(query)` materializes a warehouse query as a local table (snowflake-connector → Arrow → DuckDB), so analysts pull a slice once and iterate locally for free.
- **Geocoding**: load data with addresses/city names and resolve coordinates automatically.
- **Trust layer**: surface the SQL that ran + a plain-English explanation, and a data-quality profile on load.
- MotherDuck attach; export results to CSV/Excel/GeoJSON/PNG.

## License

MIT
