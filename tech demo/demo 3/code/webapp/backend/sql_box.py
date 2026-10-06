"""The SQL box: read-only SQL with DuckDB on the CSV files of one metadata endpoint.

DuckDB is a SQL database that runs inside this Python program (no server).
For every query we:
  1. open a new, empty DuckDB database in memory,
  2. copy each CSV file into it as one table (one table per CSV file), with the
     column names and types from the CSVW file, plus a "snippets" table with the
     TSS2 metadata (which column is which series, its unit, its time window),
  3. lock it: no more reading or writing files, no internet, no extensions,
     settings can't be changed back,
  4. run the user's query: only one SELECT, at most 30 seconds, at most 1000 rows back,
  5. throw the database away.
"""
import duckdb
import math
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as time_of_day
from decimal import Decimal

MAX_ROWS = 1000      # rows sent back to the browser
MAX_SECONDS = 30     # a query that takes longer is stopped

# CSVW datatype (the "base") -> DuckDB column type. Anything not in here stays text (VARCHAR).
CSVW_TO_DUCKDB = {
    "dateTime": "TIMESTAMPTZ",
    "datetime": "TIMESTAMPTZ",
    "dateTimeStamp": "TIMESTAMPTZ",
    "date": "DATE",
    "time": "TIME",
    "double": "DOUBLE",
    "float": "DOUBLE",
    "number": "DOUBLE",
    "decimal": "DOUBLE",
    "integer": "BIGINT",
    "int": "BIGINT",
    "long": "BIGINT",
    "short": "BIGINT",
    "byte": "BIGINT",
    "nonNegativeInteger": "BIGINT",
    "positiveInteger": "BIGINT",
    "nonPositiveInteger": "BIGINT",
    "negativeInteger": "BIGINT",
    "unsignedLong": "BIGINT",
    "unsignedInt": "BIGINT",
    "unsignedShort": "BIGINT",
    "unsignedByte": "BIGINT",
    "boolean": "BOOLEAN",
}


def duckdb_type(datatype):
    """The DuckDB type for a CSVW datatype. A CSVW datatype is a word ("double")
    or an object ({"base": "double", "minimum": 0}). A column with its own
    "format" (e.g. dates written as 31-12-2025) stays text: DuckDB would not
    know how to read it."""
    if isinstance(datatype, dict):
        if "format" in datatype:
            return "VARCHAR"
        datatype = datatype.get("base", "string")
    return CSVW_TO_DUCKDB.get(datatype, "VARCHAR")


def sql_text(text):
    """A text as a SQL string: 'like this'. A ' inside it is written twice, as SQL wants."""
    return "'" + str(text).replace("'", "''") + "'"


def sql_identifier(name):
    """A column or table name for in SQL. Simple names stay as they are,
    anything else gets "double quotes"."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return name
    return '"' + name.replace('"', '""') + '"'


def table_name(short_iri, taken):
    """A SQL table name made from the csvw:Table IRI, e.g. ex:grootdok-table -> grootdok_table"""
    name = short_iri.split(":")[-1].split("#")[-1].split("/")[-1].lower()
    name = re.sub(r"[^a-z0-9_]", "_", name)
    if name == "" or not name[0].isalpha():
        name = "t_" + name
    base = name
    number = 2
    while name in taken or name == "snippets":  # "snippets" is the name of our metadata table
        name = f"{base}_{number}"
        number += 1
    return name


def to_json_value(value):
    """Turn a value from DuckDB into something the browser can receive as JSON"""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)  # JSON has no NaN or infinity
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, (date, time_of_day)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, timedelta):
        return str(value)  # e.g. "0:15:00" or "85 days, 0:15:00"
    return str(value)      # anything else (lists, structs, ...) as text


def open_database(tables, snippets):
    """A new in-memory DuckDB with our tables in it, locked down.

    tables: [{"name", "path", "delimiter", "header", "columns": [{"name", "type"}]}]
    snippets: rows for the "snippets" table (see SNIPPET_COLUMNS)"""
    con = duckdb.connect()  # no file name: the database only lives in memory
    con.execute("SET TimeZone = 'UTC'")

    # 1. One table per CSV file. read_csv gets the names and types from the CSVW file,
    #    so DuckDB does not have to guess them.
    for table in tables:
        columns = []
        for column in table["columns"]:
            columns.append(f"{sql_text(column['name'])}: {sql_text(column['type'])}")
        try:
            con.execute(
                f"CREATE TABLE {sql_identifier(table['name'])} AS "
                f"SELECT * FROM read_csv({sql_text(table['path'])}, "
                f"delim = {sql_text(table['delimiter'])}, header = {'true' if table['header'] else 'false'}, "
                f"columns = {{{', '.join(columns)}}})"
            )
        except duckdb.Error as error:
            con.close()
            raise ValueError(f"Could not load {table['file']} with the columns and types of its CSVW file: {error}")

    # 2. The TSS2 metadata as a table, so it can be joined with the data
    column_list = []
    for name, sql_type in SNIPPET_COLUMNS:
        column_list.append(f"{name} {sql_type}")
    con.execute(f"CREATE TABLE snippets ({', '.join(column_list)})")
    for row in snippets:
        values = []
        for name, sql_type in SNIPPET_COLUMNS:
            values.append(row[name])
        con.execute(f"INSERT INTO snippets VALUES ({', '.join(['?'] * len(values))})", values)

    # 3. Lock it. From here on the database can only work with what is already in it.
    con.execute("SET autoinstall_known_extensions = false")
    con.execute("SET autoload_known_extensions = false")
    con.execute("SET memory_limit = '1GB'")
    con.execute("SET threads = 4")
    con.execute("SET enable_external_access = false")  # no files, no internet, no extensions
    con.execute("SET lock_configuration = true")       # and nobody can switch that back on
    return con


# The columns of the "snippets" table: one row per series (snippet)
SNIPPET_COLUMNS = [
    ("label", "VARCHAR"),
    ("snippet", "VARCHAR"),
    ("sql_table", "VARCHAR"),
    ("time_column", "VARCHAR"),
    ("value_column", "VARCHAR"),
    ("unit", "VARCHAR"),
    ("from_time", "TIMESTAMPTZ"),
    ("until_time", "TIMESTAMPTZ"),
    ("observed_property", "VARCHAR"),
    ("sensor", "VARCHAR"),
    ("feature_of_interest", "VARCHAR"),
]


def run_query(con, sql):
    """Run one read-only query. Returns columns, rows, whether there were more rows, and the time it took."""
    # Only one statement, and it must be a SELECT (SELECT, WITH ... SELECT, FROM ..., DESCRIBE, SUMMARIZE, SHOW)
    statements = con.extract_statements(sql)
    if len(statements) != 1:
        raise ValueError(f"Write exactly one query (found {len(statements)}).")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError(f"Only reading queries (SELECT) are allowed, not {statements[0].type.name}.")

    # A timer that stops the query after MAX_SECONDS
    timer = threading.Timer(MAX_SECONDS, con.interrupt)
    started = time.time()
    timer.start()
    try:
        result = con.execute(sql)
        columns = []
        for description in result.description:
            columns.append(description[0])
        fetched = result.fetchmany(MAX_ROWS + 1)  # one extra row tells us if there are more
    except duckdb.InterruptException:
        raise ValueError(f"The query was stopped after {MAX_SECONDS} seconds.")
    finally:
        timer.cancel()

    rows = []
    for row in fetched[:MAX_ROWS]:
        values = []
        for value in row:
            values.append(to_json_value(value))
        rows.append(values)

    return {
        "columns": columns,
        "rows": rows,
        "moreRows": len(fetched) > MAX_ROWS,
        "seconds": round(time.time() - started, 2),
    }


def example_queries(tables):
    """A few queries to start from, made for this endpoint's tables and columns"""
    examples = [{"title": "Which column is which series (the TSS2 metadata)",
                 "sql": "SELECT label, sql_table, value_column, unit, from_time, until_time\nFROM snippets"}]
    if len(tables) == 0 or len(tables[0]["snippets"]) == 0:
        return examples

    table = tables[0]
    first = table["snippets"][0]
    t = sql_identifier(table["name"])
    when = sql_identifier(first["timeColumn"])
    value = sql_identifier(first["valueColumn"])
    label = first["label"]

    examples.insert(0, {"title": f"The first 10 rows of {table['name']}", "sql": f"SELECT *\nFROM {t}\nLIMIT 10"})
    examples.append({"title": f"Summary of every column of {table['name']}", "sql": f"SUMMARIZE {t}"})
    examples.append({
        "title": f"Daily mean, min and max: {label}",
        "sql": f"SELECT date_trunc('day', {when}) AS day,\n"
               f"       round(avg({value}), 3) AS mean,\n"
               f"       min({value}) AS min,\n"
               f"       max({value}) AS max,\n"
               f"       count({value}) AS readings\n"
               f"FROM {t}\nGROUP BY day\nORDER BY day",
    })
    examples.append({
        "title": f"Moving average over the last 4 readings: {label}",
        "sql": f"SELECT {when}, {value},\n"
               f"       round(avg({value}) OVER (ORDER BY {when} ROWS BETWEEN 3 PRECEDING AND CURRENT ROW), 3) AS moving_avg\n"
               f"FROM {t}\nORDER BY {when}\nLIMIT 100",
    })
    examples.append({
        "title": f"The 10 longest gaps between readings in {table['name']}",
        "sql": f"SELECT lag({when}) OVER (ORDER BY {when}) AS previous_reading,\n"
               f"       {when} AS next_reading,\n"
               f"       {when} - lag({when}) OVER (ORDER BY {when}) AS gap\n"
               f"FROM {t}\nORDER BY gap DESC NULLS LAST\nLIMIT 10",
    })
    examples.append({
        "title": f"Only the readings inside the series' own time window (data + metadata): {label}",
        "sql": f"SELECT d.{when}, d.{value}\n"
               f"FROM {t} AS d, snippets AS s\n"
               f"WHERE s.value_column = {sql_text(first['valueColumn'])}\n"
               f"  AND d.{when} >= s.from_time AND d.{when} < s.until_time\n"
               f"ORDER BY d.{when}\nLIMIT 100",
    })
    if len(table["snippets"]) >= 2:
        second = table["snippets"][1]
        other = sql_identifier(second["valueColumn"])
        examples.append({
            "title": f"Two series side by side, per day: {label} / {second['label']}",
            "sql": f"SELECT date_trunc('day', {when}) AS day,\n"
                   f"       round(avg({value}), 3) AS {value},\n"
                   f"       round(avg({other}), 3) AS {other}\n"
                   f"FROM {t}\nGROUP BY day\nORDER BY day",
        })
    return examples
