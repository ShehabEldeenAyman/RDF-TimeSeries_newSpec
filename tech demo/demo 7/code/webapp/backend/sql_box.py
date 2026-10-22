"""The SQL box: read-only SQL with DuckDB on the data of one metadata endpoint.

DuckDB is a SQL database that runs inside this Python program (no server).
For every query we:
  1. open a new, empty DuckDB database in memory,
  2. copy the source's data into it as one table, with one column per data column
     (a CSV file, a Parquet file, or the points embedded in the Turtle, read as RDF), plus
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

from rdflib.namespace import XSD

MAX_ROWS = 1000          # rows sent back to the browser
MAX_BYTES = 5_000_000    # about 5 MB of values sent back to the browser
MAX_SECONDS = 30         # a query that takes longer is stopped

# Words SQL itself uses (select, from, order, end, ...). A table or column with
# such a name must be written "in double quotes", or DuckDB misreads the query.
RESERVED_WORDS = set()
for (word,) in duckdb.connect().execute(
        "SELECT keyword_name FROM duckdb_keywords() WHERE keyword_category = 'reserved'").fetchall():
    RESERVED_WORDS.add(word)

# A field's tss:datatype (an IRI, e.g. XSD.double = http://www.w3.org/2001/XMLSchema#double)
# -> DuckDB column type. Anything not in here stays text (VARCHAR).
# The keys are the full IRIs (rdflib URIRefs), so it does not matter which prefix a
# metadata file uses for XSD (xsd:, x:, or none at all).
# main.py uses this map too: a field is numeric when its type here is DOUBLE or BIGINT.
XSD_TO_DUCKDB = {
    XSD.dateTime: "TIMESTAMPTZ",
    XSD.dateTimeStamp: "TIMESTAMPTZ",
    XSD.date: "DATE",
    XSD.time: "TIME",
    XSD.double: "DOUBLE",
    XSD.float: "DOUBLE",
    XSD.decimal: "DOUBLE",
    XSD.integer: "BIGINT",
    XSD.int: "BIGINT",
    XSD.long: "BIGINT",
    XSD.short: "BIGINT",
    XSD.byte: "BIGINT",
    XSD.nonNegativeInteger: "BIGINT",
    XSD.positiveInteger: "BIGINT",
    XSD.nonPositiveInteger: "BIGINT",
    XSD.negativeInteger: "BIGINT",
    XSD.unsignedLong: "BIGINT",
    XSD.unsignedInt: "BIGINT",
    XSD.unsignedShort: "BIGINT",
    XSD.unsignedByte: "BIGINT",
    XSD.boolean: "BOOLEAN",
}

# The DuckDB types of numeric columns: only those have mean/min/max and value ranges
NUMERIC_TYPES = ("DOUBLE", "BIGINT")

# How the error messages describe each type
TYPE_WORDS = {
    "TIMESTAMPTZ": "a date-time like 2021-03-03T23:45:00Z",
    "DATE": "a date like 2021-03-03",
    "TIME": "a time like 23:45:00",
    "DOUBLE": "a number",
    "BIGINT": "a whole number",
    "BOOLEAN": "true, false, 1 or 0",
}

# The two metadata tables, and their columns. IRIs (source, format, field, datatype, unit, ...)
# are written in full, e.g. https://w3id.org/tss#CSV, whatever prefixes the metadata file uses.
METADATA_TABLES = {
    "source": {
        "explanation": "The TSS source: one row, with what holds for every field. IRIs are written in full.",
        "columns": [
            ("source", "VARCHAR"),
            ("label", "VARCHAR"),
            ("sql_table", "VARCHAR"),
            ("format", "VARCHAR"),
            ("time_field", "VARCHAR"),
            ("from_time", "TIMESTAMPTZ"),
            ("until_time", "TIMESTAMPTZ"),
            ("produced_by", "VARCHAR"),
            ("mutable", "BOOLEAN"),
        ],
    },
    "fields": {
        "explanation": "The TSS fields: one row per field. IRIs are written in full; "
                       "role is 'time', 'value', 'describes' (e.g. a quality code) or NULL; "
                       "describes is the IRI of the field it describes.",
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
    """The DuckDB type for a field's tss:datatype (an rdflib URIRef, or None), e.g.
    XSD.double -> DOUBLE. No datatype, or one that is not in XSD_TO_DUCKDB, stays text."""
    return XSD_TO_DUCKDB.get(datatype, "VARCHAR")


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


def column_expression(column, form):
    """How one text column of a CSV file becomes one typed SQL column.

    DuckDB first reads every column of the CSV file as text. Here each column is turned into the
    type of its field's tss:datatype. Each cell's text must have the XSD form of that datatype
    (form: a regular expression, from LEXICAL_FORMS in code/TSS2RDF/tss2rdf.py), the same check
    as the query form and SHACL make. So " 12 ", "1_000" or 1.5 in a whole-number column, a date
    without a time, or "yes" as a boolean stop the loading with a clear message, instead of
    quietly becoming another value (DuckDB itself would read " 12 " as 12 and "yes" as true).
    A text in the right form that is still no value (30 February) stops it too. Empty cells are NULL.
    form: None for a datatype without a known XSD form (then only DuckDB's own reading counts)."""
    name = sql_identifier(column["name"])
    sql_type = column["type"]
    if sql_type == "VARCHAR":
        return f"{name} AS {name}"

    fits = f"TRY_CAST({name} AS {sql_type}) IS NOT NULL"
    if form is not None:
        # regexp_full_match: the whole text must have the form, not just a part of it
        fits = f"regexp_full_match({name}, {sql_text(form)}) AND {fits}"
    message = sql_text(f"Column {column['name']} should be {TYPE_WORDS[sql_type]}, but the data has: ")
    return (f"CASE WHEN {name} IS NULL THEN NULL "
            f"WHEN {fits} THEN CAST({name} AS {sql_type}) "
            f"ELSE error({message} || {name}) END AS {name}")


def load_table(con, table):
    """Copy the source's data into the database as one table.

    table: {"name", "kind": "csv" | "parquet" | "points", "columns": [{"name", "type"}], ...}
      csv:     "path", "delimiter", "header", and "forms": column name -> the XSD form its
               cells must have (a regular expression), or None (see column_expression)
      parquet: "path"
      points:  "rows" (one list of values per point, in the order of the columns: the Python
               values rdflib read from the points' literals, see sql_table in main.py)"""
    name = sql_identifier(table["name"])

    if table["kind"] == "points":
        # The points were read as RDF, so every value already has its field's type
        # (a float for xsd:double, a datetime for xsd:dateTime, ...): the table gets
        # those types straight away, no text in between
        column_list = []
        for column in table["columns"]:
            column_list.append(f"{sql_identifier(column['name'])} {column['type']}")
        con.execute(f"CREATE TABLE {name} ({', '.join(column_list)})")
        try:
            if len(table["rows"]) > 0:
                places = ", ".join(["?"] * len(table["columns"]))
                con.executemany(f"INSERT INTO {name} VALUES ({places})", table["rows"])
        except duckdb.Error as error:
            con.execute(f"DROP TABLE {name}")  # no half-filled table
            raise ValueError(f"Could not load the embedded points: {error}")
        return

    # DuckDB would treat * ? [ ] in a file name as a pattern and maybe read other files
    if re.search(r"[*?\[\]]", table["path"]):
        raise ValueError(f"Could not load table {table['name']}: the SQL box can't read a data file "
                         "with * ? [ or ] in its name.")

    if table["kind"] == "parquet":
        # A Parquet file has its own column types, so DuckDB keeps them
        sql = f"CREATE TABLE {name} AS SELECT * FROM read_parquet({sql_text(table['path'])}, hive_partitioning = false)"
    else:
        # CSV cells are text: each column gets its field's type (see column_expression)
        typed = []
        for column in table["columns"]:
            typed.append(column_expression(column, table["forms"].get(column["name"])))

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
        raise ValueError(f"Could not load table {table['name']}: {message}")


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
        #    so the metadata tables can still be used. (duckdb.Error too: e.g. two embedded
        #    columns "Value" and "value", which are one name for DuckDB)
        skipped = []
        for table in tables:
            try:
                load_table(con, table)
            except ValueError as error:
                skipped.append({"name": table["name"], "error": str(error)})
            except duckdb.Error as error:
                skipped.append({"name": table["name"], "error": f"Could not load table {table['name']}: {error}"})

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
    """A few queries to start from, made for this source's table and fields. Each example is
    {"title": a fixed English text, "sql": the query, "field": the IRI of the field it is about, or None}.
    The title names no field (no label, no column name): the browser reads the field's label
    from the metadata by its IRI and adds it (SqlBox.jsx). The SQL itself needs the column names.
    table: the data table (None when it can't be loaded)
    fields: the fields from read_source in main.py. Each is a dict with RDF terms (rdflib):
      "node" (the field's IRI), "columnName", "isTime", "label",
      "observedProperty" and "describes" (the field it says something about), or None"""
    examples = [{"title": "Which column is which field (the TSS metadata)",
                 "sql": "SELECT column_name, role, datatype, unit, observed_property\nFROM fields",
                 "field": None}]
    if table is None:
        return examples

    t = sql_identifier(table["name"])
    examples.insert(0, {"title": "The first 10 rows of the data", "sql": f"SELECT *\nFROM {t}\nLIMIT 10", "field": None})
    examples.append({"title": "Summary of every column of the data", "sql": f"SUMMARIZE {t}", "field": None})

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
        elif (field["observedProperty"] is not None and field["describes"] is None
              and types[field["columnName"]] in NUMERIC_TYPES):
            measured.append(field)
    if time_column is None or len(measured) == 0:
        return examples

    first = measured[0]
    first_iri = str(first["node"])  # the examples about the first series name its field by IRI
    when = sql_identifier(time_column)
    value = sql_identifier(first["columnName"])

    examples.append({
        "title": "Daily mean, min and max",
        # GROUP BY ALL: group by every column that is not a sum/avg/min/max/count
        "sql": f"SELECT date_trunc('day', {when}) AS day,\n"
               f"       round(avg({value}), 3) AS mean,\n"
               f"       min({value}) AS min,\n"
               f"       max({value}) AS max,\n"
               f"       count({value}) AS readings\n"
               f"FROM {t}\nGROUP BY ALL\nORDER BY day",
        "field": first_iri,
    })
    examples.append({
        "title": "Moving average over the last 4 readings",
        "sql": f"SELECT {when}, {value},\n"
               f"       round(avg({value}) OVER (ORDER BY {when} ROWS BETWEEN 3 PRECEDING AND CURRENT ROW), 3) AS moving_avg\n"
               f"FROM {t}\nORDER BY {when}\nLIMIT 100",
        "field": first_iri,
    })
    examples.append({
        "title": "The 10 longest gaps between readings",
        "sql": f"SELECT lag({when}) OVER (ORDER BY {when}) AS previous_reading,\n"
               f"       {when} AS next_reading,\n"
               f"       {when} - lag({when}) OVER (ORDER BY {when}) AS gap\n"
               f"FROM {t}\nORDER BY gap DESC NULLS LAST\nLIMIT 10",
        "field": None,
    })
    examples.append({
        "title": "Only the readings inside the source's time window (data + metadata)",
        "sql": f"SELECT d.{when}, d.{value}\n"
               f"FROM {t} AS d, source AS s\n"
               f"WHERE d.{when} >= s.from_time AND d.{when} < s.until_time\n"
               f"ORDER BY d.{when}\nLIMIT 100",
        "field": first_iri,
    })

    # A field that describes the first value (e.g. a quality code): the readings per code.
    # tss:describesField points to the field itself, so we compare the two IRIs.
    # This example is about the describing field (the code), so it carries that field's IRI.
    for field in fields:
        if field["describes"] == first["node"] and field["columnName"] in types:
            code = sql_identifier(field["columnName"])
            examples.append({
                "title": "Readings per value",
                "sql": f"SELECT {code}, count(*) AS readings, round(avg({value}), 3) AS mean\n"
                       f"FROM {t}\nGROUP BY ALL\nORDER BY readings DESC",
                "field": str(field["node"]),
            })
            break

    # Two series side by side: only when the table has a second measured field.
    # (It is about two fields: the SQL names both columns, the title neither.)
    if len(measured) > 1:
        second = measured[1]
        other = sql_identifier(second["columnName"])
        examples.append({
            "title": "Two series side by side, per day",
            "sql": f"SELECT date_trunc('day', {when}) AS day,\n"
                   f"       round(avg({value}), 3) AS {value},\n"
                   f"       round(avg({other}), 3) AS {other}\n"
                   f"FROM {t}\nGROUP BY ALL\nORDER BY day",
            "field": None,
        })
    return examples
