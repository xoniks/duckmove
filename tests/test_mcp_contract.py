"""The MCP surface itself: schemas, annotations, async dispatch, errors.

Nothing previously exercised the protocol layer — the tests drove the tool
bodies directly, so a change that broke tool registration or schema
generation would have gone unnoticed until a client connected.
"""

import anyio
import pytest

import duckmove.server as srv
from duckmove.core.engine import Engine
from duckmove.server import build_app, get_tool_handlers

CSV = "city,lat,lon\nNYC,40.7128,-74.0060\nLA,34.0522,-118.2437\n"

WRITING_TOOLS = {
    "load_data",
    "load_csv_text",
    "rename_table",
    "register_geofences",
    "save_query",
    "map_points",
    "map_routes",
    "map_tracks",
}


@pytest.fixture()
def app(monkeypatch):
    eng = Engine(db_path=":memory:")
    monkeypatch.setattr(srv, "_ENGINE", eng)
    yield build_app()
    eng.close()


def _tools(app):
    return {t.name: t for t in anyio.run(app.list_tools)}


def test_every_handler_is_registered_as_a_tool(app):
    assert set(_tools(app)) == set(get_tool_handlers())


def test_every_tool_has_a_description_and_annotations(app):
    for name, tool in _tools(app).items():
        assert tool.description, f"{name} has no description"
        assert tool.annotations is not None, f"{name} has no annotations"
        assert tool.annotations.title, f"{name} has no title"
        # Nothing here talks to the outside world.
        assert tool.annotations.openWorldHint is False


def test_read_only_hints_match_what_the_tool_actually_does(app):
    for name, tool in _tools(app).items():
        expected = name not in WRITING_TOOLS
        assert tool.annotations.readOnlyHint is expected, name


def test_no_tool_is_marked_destructive(app):
    """duckmove never deletes user data, so no tool should ask a client to
    warn about it."""
    assert all(t.annotations.destructiveHint is False for t in _tools(app).values())


def test_parameters_carry_descriptions_into_the_schema(app):
    tools = _tools(app)
    assert tools["run_sql"].inputSchema["properties"]["sql"]["description"]
    assert (
        "hard cap"
        in tools["run_sql"].inputSchema["properties"]["max_rows"]["description"]
    )
    for name in ("lat_col", "lon_col", "table_or_sql"):
        assert tools["map_points"].inputSchema["properties"][name]["description"]


def test_map_style_is_an_enum_in_the_schema(app):
    style = _tools(app)["map_points"].inputSchema["properties"]["style"]
    # Pydantic emits Literal as an enum, possibly behind $ref/allOf.
    assert "markers" in str(style) or "$ref" in style


def test_required_parameters_are_marked_required(app):
    schema = _tools(app)["run_sql"].inputSchema
    assert "sql" in schema.get("required", [])
    assert "max_rows" not in schema.get("required", [])


def test_tools_are_dispatchable_over_the_protocol(app):
    async def scenario():
        await app.call_tool("load_csv_text", {"csv_text": CSV, "name": "cities"})
        return await app.call_tool("run_sql", {"sql": "SELECT count(*) AS n FROM cities"})

    result = anyio.run(scenario)
    payload = result[1] if isinstance(result, tuple) else result
    assert payload["result"]["rows"][0]["n"] == 2


def test_a_rejected_query_returns_an_error_object_not_an_exception(app):
    async def scenario():
        return await app.call_tool("run_sql", {"sql": "DROP TABLE x"})

    result = anyio.run(scenario)
    payload = result[1] if isinstance(result, tuple) else result
    assert payload["result"]["error_code"] == "SQL_REJECTED"


def test_tool_bodies_are_registered_as_async_so_they_cannot_block_the_loop(app):
    """FastMCP awaits async tools and calls sync ones inline on the event
    loop; a slow query in a sync tool would stall the whole server."""
    import inspect

    for tool in anyio.run(app.list_tools):
        fn = app._tool_manager.get_tool(tool.name).fn
        assert inspect.iscoroutinefunction(fn), f"{tool.name} is not async"


def test_concurrent_tool_calls_do_not_corrupt_the_shared_connection(app):
    """A single DuckDB connection is not concurrency-safe; the engine lock
    must serialise the worker threads."""

    async def scenario():
        await app.call_tool("load_csv_text", {"csv_text": CSV, "name": "cities"})
        results = []

        async def one(i):
            r = await app.call_tool(
                "run_sql", {"sql": f"SELECT count(*) + {i} AS n FROM cities"}
            )
            results.append(r[1] if isinstance(r, tuple) else r)

        async with anyio.create_task_group() as tg:
            for i in range(12):
                tg.start_soon(one, i)
        return results

    results = anyio.run(scenario)
    assert len(results) == 12
    assert sorted(r["result"]["rows"][0]["n"] for r in results) == list(range(2, 14))


# --- error contract ---------------------------------------------------


def test_no_tool_raises_out_of_the_handler(app):
    """Every handler must convert failure into {error, error_code}. Several
    used to leak raw tracebacks to the client instead."""
    handlers = get_tool_handlers()
    bogus = "no_such_table_anywhere"
    calls = {
        "load_data": {"path": "/nonexistent/nope.csv"},
        "load_csv_text": {"csv_text": ""},
        "rename_table": {"old": bogus, "new": "x"},
        "describe_table": {"table": bogus},
        "run_sql": {"sql": "SELECT * FROM " + bogus},
        "register_geofences": {"source_table": bogus, "set_name": "s"},
        "points_in_geofences": {
            "table": bogus,
            "lat_col": "a",
            "lon_col": "b",
            "geofence_set": "s",
        },
        "geofence_crossings": {
            "table": bogus,
            "lat_col": "a",
            "lon_col": "b",
            "group_by": "g",
            "order_by": "o",
            "geofence_set": "s",
        },
        "map_points": {"table_or_sql": bogus, "lat_col": "a", "lon_col": "b"},
        "map_routes": {
            "table_or_sql": bogus,
            "from_lat": "a",
            "from_lon": "b",
            "to_lat": "c",
            "to_lon": "d",
        },
        "map_tracks": {
            "table_or_sql": bogus,
            "group_by": "g",
            "lat_col": "a",
            "lon_col": "b",
            "order_by": "o",
        },
        "save_query": {"name": "n", "sql": "SELECT * FROM " + bogus},
        "run_saved": {"name": bogus},
    }
    for name, kwargs in calls.items():
        result = handlers[name](**kwargs)  # must not raise
        assert isinstance(result, dict), name
        assert "error_code" in result, f"{name} returned no error_code: {result}"


def test_listing_tools_never_error_on_unknown_names(app):
    """Queries that legitimately have an empty answer return data, not an
    error — an unknown geofence set simply has no tags."""
    handlers = get_tool_handlers()
    assert handlers["list_geofence_tags"](geofence_set="nope") == {
        "set_name": "nope",
        "tags": [],
    }
    assert handlers["list_tables"]() == {"tables": []}


def test_error_codes_come_from_a_known_set(app):
    from duckmove.core.errors import EXPECTED_CODES

    handlers = get_tool_handlers()
    assert handlers["describe_table"](table="nope")["error_code"] in EXPECTED_CODES
    assert handlers["run_sql"](sql="DROP TABLE x")["error_code"] in EXPECTED_CODES


def test_internal_errors_are_labelled_distinctly():
    from duckmove.core.errors import classify

    assert classify(RuntimeError("boom"))["error_code"] == "INTERNAL_ERROR"


def test_handshake_advertises_our_version_not_the_sdks(app):
    """FastMCP takes no `version` and the low-level server defaults it to
    None, which makes the handshake report the mcp SDK's version as ours."""
    from importlib.metadata import version as pkg_version

    from duckmove import __version__

    assert app._mcp_server.version == __version__
    assert app._mcp_server.version != pkg_version("mcp")
