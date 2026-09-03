import pytest

from duckmove.core.sqlguard import SqlRejected, validate_sql

# --- statements that must be refused ---------------------------------

FILESYSTEM_ESCAPES = [
    "SELECT * FROM read_text('C:/Windows/win.ini')",
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_csv_auto('a.csv')",
    "SELECT * FROM read_parquet('s3://bucket/key')",
    "SELECT * FROM read_json_auto('a.json')",
    "SELECT * FROM read_blob('id_rsa')",
    "SELECT * FROM read_xlsx('book.xlsx')",
    "SELECT * FROM ST_Read('shape.shp')",
    "SELECT * FROM glob('C:/**')",
    "SELECT * FROM sniff_csv('a.csv')",
    "SELECT * FROM parquet_scan('a.parquet')",
    "SELECT * FROM parquet_metadata('a.parquet')",
    "SELECT * FROM iceberg_scan('t')",
    "SELECT * FROM delta_scan('t')",
    "SELECT * FROM postgres_scan('h', 'public', 't')",
    "SELECT getenv('AWS_SECRET_ACCESS_KEY')",
    # hidden in a CTE / subquery rather than at top level
    "WITH x AS (SELECT * FROM read_parquet('s3://b/k')) SELECT * FROM x",
    "SELECT * FROM t WHERE id IN (SELECT id FROM read_csv('leak.csv'))",
    "SELECT * FROM t UNION ALL SELECT * FROM read_json('a.json')",
    # a bare path in FROM position is a file read too
    "SELECT * FROM 'secrets.csv'",
    "SELECT * FROM t JOIN 'f.csv' ON 1=1",
    # case and comments must not disguise it
    "SeLeCt * FrOm ReAd_TeXt('a')",
    "SELECT/*c*/ * FROM read_text('a')",
    "SELECT * FROM read_text('a') -- trailing",
]

WRITE_STATEMENTS = [
    "DROP TABLE t",
    "DELETE FROM t",
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a=1",
    "CREATE TABLE x AS SELECT 1",
    "ALTER TABLE t ADD COLUMN y INT",
    "ATTACH 'x.db'",
    "COPY t TO 'out.csv'",
    "INSTALL httpfs",
    "LOAD httpfs",
    "PRAGMA database_list",
    "SET memory_limit='1GB'",
    "CALL pragma_version()",
    # buried inside an otherwise read-only statement
    "SELECT 1; DROP TABLE t",
    "WITH x AS (SELECT 1) DELETE FROM t",
]


@pytest.mark.parametrize("sql", FILESYSTEM_ESCAPES)
def test_rejects_filesystem_and_network_access(sql):
    with pytest.raises(SqlRejected):
        validate_sql(sql)


@pytest.mark.parametrize("sql", WRITE_STATEMENTS)
def test_rejects_writes_and_side_effects(sql):
    with pytest.raises(SqlRejected):
        validate_sql(sql)


def test_rejects_empty():
    with pytest.raises(SqlRejected):
        validate_sql("   -- just a comment\n")


# --- statements that must be allowed ---------------------------------

LEGITIMATE = [
    "SELECT 1",
    "  select * from t",
    '''SELECT * FROM "my table"''',
    '''SELECT * FROM main."pts"''',
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "DESCRIBE t",
    "SUMMARIZE t",
    "SHOW TABLES",
    "EXPLAIN SELECT 1",
    "-- comment\nSELECT 1",
    "/* c */ SELECT 1",
    "SELECT lag(lat) OVER (PARTITION BY id ORDER BY ts) FROM pings",
    "SELECT ST_Distance_Spheroid(ST_Point(a, b), ST_Point(c, d))/1000.0 FROM t",
    "SELECT * FROM t WHERE updated_at > now()",  # 'update' is a substring only
    "SELECT payload, dataset_id FROM t",  # 'load' is a substring only
    "SELECT count(*) FROM t;",  # trailing semicolon is fine
    "select * from t; -- trailing comment",
    "SELECT * FROM (SELECT 1) s",
]


@pytest.mark.parametrize("sql", LEGITIMATE)
def test_allows_read_only_queries(sql):
    assert validate_sql(sql)


def test_semicolon_inside_a_string_literal_is_not_a_statement_separator():
    """Regression: the old guard rejected any ';' anywhere, including inside
    a quoted value, and asked the user to rephrase a perfectly valid query."""
    sql = "SELECT * FROM t WHERE name = '; DROP TABLE t; --'"
    assert validate_sql(sql) == sql


def test_keywords_inside_a_string_literal_do_not_trip_the_guard():
    sql = "SELECT * FROM t WHERE note = 'please read_csv and DROP TABLE'"
    assert validate_sql(sql) == sql


def test_escaped_quote_inside_literal_is_handled():
    sql = "SELECT * FROM t WHERE q = 'it''s fine'"
    assert validate_sql(sql) == sql


def test_strips_trailing_semicolons_and_comments():
    assert validate_sql("SELECT 1;  -- note\n") == "SELECT 1"


def test_trailing_semicolon_ok():
    validate_sql("SELECT 1;")


def test_statement_with_no_leading_keyword_is_rejected_clearly():
    """A statement starting with a symbol has no leading word to check, so the
    guard must fail closed with a real message rather than an opaque 'Got: X'."""
    with pytest.raises(SqlRejected) as e:
        validate_sql("(SELECT 1)")
    assert "must start with" in str(e.value).lower()
    assert " x" not in str(e.value).lower()
