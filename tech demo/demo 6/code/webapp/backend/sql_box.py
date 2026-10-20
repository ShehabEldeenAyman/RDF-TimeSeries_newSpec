"""The SQL box: read-only SQL with DuckDB on the data of one metadata endpoint.

DuckDB is a SQL database that runs inside this Python program (no server).
For every query we:
  1. open a new, empty DuckDB database in memory,
  2. copy the source's data into it as one table, with one column per data column
     (a CSV file, a Parquet file, or the JSON points embedded in the Turtle), plus
     two tables with the TSS metadata: "source" (one row) and "fields" (one row per field),
  3. lock it: no more reading or writing files, no internet, no extensions,
     settings can't be changed back,
  4. run the user's query: only one SELECT, at most 30 seconds, at most 1000 rows
     and 5 MB back,
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

MAX_ROWS = 1000          # rows sent back to the browser
MAX_BYTES = 5_000_000    # about 5 MB of values sent back to the browser
MAX_SECONDS = 30         # a query that takes longer is stopped

# Words SQL itself uses (select, from, order, end, ...). A table or column with
# such a name must be written "in double quotes", or DuckDB misreads the query.
RESERVED_WORDS = set()
for (word,) in duckdb.connect().execute(
        "SELECT keyword_name FROM duckdb_keywords() WHERE keyword_category = 'reserved'").fetchall():
    RESERVED_WORDS.add(word)

# The XSD datatype (the part after xsd:) of a field -> DuckDB column type.
# Anything not in here stays text (VARCHAR).
XSD_TO_DUCKDB = {
    "dateTime": "TIMESTAMPTZ",
    "dateTimeStamp": "TIMESTAMPTZ",
    "date": "DATE",
    "time": "TIME",
    "double": "DOUBLE",
    "float": "DOUBLE",
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

# How the error messages describe each type
TYPE_WORDS = {
    "TIMESTAMPTZ": "a date-time like 2021-03-03T23:45:00Z",
    "DATE": "a date like 2021-03-03",
    "TIME": "a time like 23:45:00",
    "DOUBLE": "a number",
    "BIGINT": "a whole number",
    "BOOLEAN": "true, false, 1 or 0",
}

# The two metadata tables, and their columns
METADATA_TABLES = {
    "source": {
        "explanation": "The TSS source: one row, with what holds for every field.",
        "columns": [
            ("source", "VARCHAR"),
            ("label", "VARCHAR"),
            ("sql_table", "VARCHAR"),
            ("format", "VARCHAR"),
            ("data_file", "VARCHAR"),
            ("time_column", "VARCHAR"),
            ("from_time", "TIMESTAMPTZ"),
            ("until_time", "TIMESTAMPTZ"),
            ("produced_by", "VARCHAR"),
            ("mutable", "BOOLEAN"),
        ],
    },
    "fields": {
        "explanation": "The TSS fields: one row per column of the data table.",
        "columns": [
            ("column_name", "VARCHAR"),
            ("sql_table", "VARCHAR"),
            ("field", "VARCHAR"),
            ("label", "VARCHAR"),
            ("role", "VARCHAR"),
            ("datatype", "VARCHAR"),
            ("unit", "VARCHAR"),
            ("observed_property", "VARCHAR"),
            ("sensor", "VARCHAR"),
            ("feature_of_interest", "VARCHAR"),
            ("describes", "VARCHAR"),
        ],
    },
}


def duckdb_type(datatype):
    """The DuckDB type for a field's tss:datatype as the dashboard shows it, e.g.
    "xsd:double" -> DOUBLE. No datatype, or one that is not XSD, stays text."""
    if datatype is None or not datatype.startswith("xsd:"):
        return "VARCHAR"
    return XSD_TO_DUCKDB.get(datatype[len("xsd:"):], "VARCHAR")


def sql_text(text):
    """A text as a SQL string: 'like this'. A ' inside it is written twice, as SQL wants."""
    return "'" + str(text).replace("'", "''") + "'"


def sql_identifier(name):
    """A column or table name for in SQL. Simple names stay as they are; anything
    else, and words SQL uses itself (like order or end), gets "double quotes"."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and name.lower() not in RESERVED_WORDS:
        return name
    return '"' + name.replace('"', '""') + '"'


def table_name(location):
    """A SQL table name made from a file name, e.g. data/waterinfo_289423042.csv -> waterinfo_289423042"""
    name = location.split("?")[0].split("#")[0].split("/")[-1].lower()
    name = re.sub(r"\.(csv|parquet)$", "", name)
    name = re.sub(r"[^a-z0-9_]", "_", name)
    if name == "" or not name[0].isalpha():
        name = "t_" + name
    if name in RESERVED_WORDS or name in METADATA_TABLES:
        name = name + "_data"  # e.g. "order" -> "order_data", so nobody has to quote it
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


def column_expression(column):
    """How one text column becomes one typed SQL column.

    DuckDB first reads every column as text. Here each column is turned into the
    type of its field's tss:datatype. A value that does not fit (a timestamp without
    seconds, 1.5 in a whole-number column, ...) stops the loading with a clear message,
    instead of quietly becoming NULL or another number. Empty cells are NULL."""
    name = sql_identifier(column["name"])
    sql_type = column["type"]
    if sql_type == "VARCHAR":
        return f"{name} AS {name}"

    fits = f"TRY_CAST(trim({name}) AS {sql_type}) IS NOT NULL"
    if sql_type == "BIGINT":
        # TRY_CAST('1.5' AS BIGINT) would quietly round to 2, so also check for a whole number
        fits += f" AND regexp_full_match(trim({name}), '[+-]?[0-9]+')"
    if sql_type == "BOOLEAN":
        # DuckDB would also accept words like "yes"; XSD only allows these four
        fits += f" AND regexp_full_match(lower(trim({name})), 'true|false|1|0')"
    message = sql_text(f"Column {column['name']} should be {TYPE_WORDS[sql_type]}, but the data has: ")
    return (f"CASE WHEN {name} IS NULL OR trim({name}) = '' THEN NULL "
            f"WHEN {fits} THEN CAST(trim({name}) AS {sql_type}) "
            f"ELSE error({message} || {name}) END AS {name}")


def load_table(con, table):
    """Copy the source's data into the database as one table.

    table: {"name", "file", "kind": "csv" | "parquet" | "points", "columns": [{"name", "type"}], ...}
      csv:     "path", "delimiter", "header"
      parquet: "path"
      points:  "rows" (one list of texts per point, in the order of the columns)"""
    name = sql_identifier(table["name"])

    # CSV cells and JSON values are text: each column gets its field's type (a Parquet file has its own types)
    typed = []
    if table["kind"] != "parquet":
        for column in table["columns"]:
            typed.append(column_expression(column))

    if table["kind"] == "points":
        # First every value as text in a helper table, then typed like a CSV file
        as_text = []
        for column in table["columns"]:
            as_text.append(f"{sql_identifier(column['name'])} VARCHAR")
        con.execute(f"CREATE TABLE points_as_text ({', '.join(as_text)})")
        try:
            if len(table["rows"]) > 0:
                places = ", ".join(["?"] * len(table["columns"]))
                con.executemany(f"INSERT INTO points_as_text VALUES ({places})", table["rows"])
            con.execute(f"CREATE TABLE {name} AS SELECT {', '.join(typed)} FROM points_as_text")
        except duckdb.Error as error:
            raise ValueError(f"Could not load the embedded points: {error}")
        finally:
            con.execute("DROP TABLE points_as_text")
        return

    # DuckDB would treat * ? [ ] in a file name as a pattern and maybe read other files
    if re.search(r"[*?\[\]]", table["path"]):
        raise ValueError(f"The SQL box can't read files with * ? [ or ] in their name: {table['file']}")

    if table["kind"] == "parquet":
        # A Parquet file has its own column types, so DuckDB keeps them
        sql = f"CREATE TABLE {name} AS SELECT * FROM read_parquet({sql_text(table['path'])}, hive_partitioning = false)"
    else:
        # read_csv gets the delimiter and the column names from the TSS source,
        # and reads every column as text (VARCHAR) ...
        as_text = []
        for column in table["columns"]:
            as_text.append(f"{sql_text(column['name'])}: 'VARCHAR'")
        options = [
            "auto_detect = false",  # don't let DuckDB guess anything: the TSS source says how to read the CSV
            f"delim = {sql_text(table['delimiter'])}",
            "quote = '\"'",
            "escape = '\"'",
            f"header = {'true' if table['header'] else 'false'}",
            "nullstr = ''",
            f"columns = {{{', '.join(as_text)}}}",
        ]
        # ... and then every column is turned into its field's type (see column_expression)
        sql = (f"CREATE TABLE {name} AS\n"
               f"SELECT {', '.join(typed)}\n"
               f"FROM read_csv({sql_text(table['path'])}, {', '.join(options)})")
    try:
        con.execute(sql)
    except duckdb.Error as error:
        message = str(error)
        if "Expected Number of Columns" in message:
            message = (f"a row does not have the {len(table['columns'])} columns of the header line. "
                       f"DuckDB says: {message.splitlines()[0]}")
        raise ValueError(f"Could not load {table['file']}: {message}")


def open_database(tables, metadata_rows):
    """A new in-memory DuckDB with our tables in it, locked down.
    Returns (the database, the tables that could not be loaded and why).

    tables: the data tables (see load_table)
    metadata_rows: {"source": [row], "fields": [row, ...]}, each row a dict with
                   the columns of METADATA_TABLES"""
    con = duckdb.connect()  # no file name: the database only lives in memory
    try:
        con.execute("SET TimeZone = 'UTC'")
        # No scratch folder on disk: otherwise the files in it would stay readable
        con.execute("SET temp_directory = ''")

        # 1. The data. A table that can't be loaded is skipped (with the reason),
        #    so the metadata tables can still be used.
        skipped = []
        for table in tables:
            try:
                load_table(con, table)
            except ValueError as error:
                skipped.append({"name": table["name"], "error": str(error)})

        # 2. The TSS metadata as tables, so it can be joined with the data
        for metadata_name, metadata in METADATA_TABLES.items():
            column_list = []
            for name, sql_type in metadata["columns"]:
                column_list.append(f"{name} {sql_type}")
            con.execute(f"CREATE TABLE {metadata_name} ({', '.join(column_list)})")
            for row in metadata_rows[metadata_name]:
                values = []
                for name, sql_type in metadata["columns"]:
                    values.append(row[name])
                try:
                    con.execute(f"INSERT INTO {metadata_name} VALUES ({', '.join(['?'] * len(values))})", values)
                except duckdb.Error as error:
                    raise ValueError(f"A row can't go into the {metadata_name} table "
                                     f"(is tss:from / tss:until a date-time?): {error}")

        # 3. Lock it. From here on the database can only work with what is already in it.
        con.execute("SET autoinstall_known_extensions = false")
        con.execute("SET autoload_known_extensions = false")
        con.execute("SET memory_limit = '1GB'")
        con.execute("SET threads = 4")
        con.execute("SET enable_external_access = false")  # no files, no internet, no extensions
        con.execute("SET lock_configuration = true")       # and nobody can switch that back on
        return con, skipped
    except Exception:
        con.close()
        raise


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

        # Fetch the rows in small portions, and stop as soon as the answer gets too big
        rows = []
        size = 0
        more_rows = False
        while True:
            portion = result.fetchmany(100)
            if len(portion) == 0:
                break
            for row in portion:
                if len(rows) == MAX_ROWS:
                    more_rows = True  # one row more than we send: there are more
                    break
                values = []
                for value in row:
                    value = to_json_value(value)
                    size += len(str(value))
                    values.append(value)
                if size > MAX_BYTES:
                    raise ValueError(f"The answer is too big to show (more than {MAX_BYTES // 1_000_000} MB). "
                                     "Ask for fewer rows or columns, or shorter values, e.g. with LIMIT or left(text, 100).")
                rows.append(values)
            if more_rows:
                break
    except duckdb.InterruptException:
        raise ValueError(f"The query was stopped after {MAX_SECONDS} seconds.")
    except duckdb.Error as error:
        message = str(error)
        if "STDDEV_SAMP is out of range" in message:
            message += ("\n(SUMMARIZE also computes a standard deviation, and that fails when a column holds "
                        "NaN or infinity. Use SELECT with min(), max() and avg() instead.)")
        raise ValueError(message)
    finally:
        timer.cancel()

    return {
        "columns": columns,
        "rows": rows,
        "moreRows": more_rows,
        "seconds": round(time.time() - started, 2),
    }


def example_queries(table, fields):
    """A few queries to start from, made for this source's table and fields.
    table: the data table (None when it can't be loaded); fields: the fields from get_endpoint_info"""
    examples = [{"title": "Which column is which field (the TSS metadata)",
                 "sql": "SELECT column_name, role, datatype, unit, observed_property\nFROM fields"}]
    if table is None:
        return examples

    t = sql_identifier(table["name"])
    examples.insert(0, {"title": f"The first 10 rows of {table['name']}", "sql": f"SELECT *\nFROM {t}\nLIMIT 10"})
    examples.append({"title": f"Summary of every column of {table['name']}", "sql": f"SUMMARIZE {t}"})

    # The examples below need the time column and numeric value columns in the table
    types = {}
    for column in table["columns"]:
        types[column["name"]] = column["type"]
    time_column = None
    measured = []  # fields with an observed property that don't describe another field, with numbers in them
    for field in fields:
        if field["columnName"] not in types:
            continue
        if field["isTime"]:
            time_column = field["columnName"]
        elif field["observedProperty"] and not field["describesField"] and types[field["columnName"]] in ("DOUBLE", "BIGINT"):
            measured.append(field)
    if time_column is None or len(measured) == 0:
        return examples

    first = measured[0]
    when = sql_identifier(time_column)
    value = sql_identifier(first["columnName"])
    label = first["label"] if first["label"] else first["columnName"]

    examples.append({
        "title": f"Daily mean, min and max: {label}",
        # GROUP BY ALL: group by every column that is not a sum/avg/min/max/count
        "sql": f"SELECT date_trunc('day', {when}) AS day,\n"
               f"       round(avg({value}), 3) AS mean,\n"
               f"       min({value}) AS min,\n"
               f"       max({value}) AS max,\n"
               f"       count({value}) AS readings\n"
               f"FROM {t}\nGROUP BY ALL\nORDER BY day",
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
        "title": f"Only the readings inside the source's time window (data + metadata): {label}",
        "sql": f"SELECT d.{when}, d.{value}\n"
               f"FROM {t} AS d, source AS s\n"
               f"WHERE d.{when} >= s.from_time AND d.{when} < s.until_time\n"
               f"ORDER BY d.{when}\nLIMIT 100",
    })

    # A field that describes the first value (e.g. a quality code): the readings per code
    for field in fields:
        if field["describesField"] == first["columnName"] and field["columnName"] in types:
            code = sql_identifier(field["columnName"])
            examples.append({
                "title": f"Readings per {field['columnName']}: {label}",
                "sql": f"SELECT {code}, count(*) AS readings, round(avg({value}), 3) AS mean\n"
                       f"FROM {t}\nGROUP BY ALL\nORDER BY readings DESC",
            })
            break

    # Two series side by side: only when the table has a second measured field
    if len(measured) > 1:
        second = measured[1]
        other = sql_identifier(second["columnName"])
        examples.append({
            "title": f"Two series side by side, per day: {first['columnName']} / {second['columnName']}",
            "sql": f"SELECT date_trunc('day', {when}) AS day,\n"
                   f"       round(avg({value}), 3) AS {value},\n"
                   f"       round(avg({other}), 3) AS {other}\n"
                   f"FROM {t}\nGROUP BY ALL\nORDER BY day",
        })
    return examples
