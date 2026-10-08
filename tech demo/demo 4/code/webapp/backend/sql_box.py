"""The SQL box: read-only SQL with DuckDB on the CSV files of one metadata endpoint.

DuckDB is a SQL database that runs inside this Python program (no server).
For every query we:
  1. open a new, empty DuckDB database in memory,
  2. copy each CSV file into it as one table (one table per CSV file), with the
     column names and types from the CSVW file, plus a "snippets" table with the
     TSS2 metadata (which column is which series, its unit, its time window),
  3. lock it: no more reading or writing files, no internet, no extensions,
     settings can't be changed back,
  4. run the user's query: only one SELECT, at most 30 seconds, at most 1000 rows
     and 5 MB back,
  5. throw the database away.
"""
import csv
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

# How the error messages describe each type
TYPE_WORDS = {
    "TIMESTAMPTZ": "a date-time like 2021-03-03T23:45:00Z",
    "DATE": "a date like 2021-03-03",
    "TIME": "a time like 23:45:00",
    "DOUBLE": "a number",
    "BIGINT": "a whole number",
    "BOOLEAN": "true, false, 1 or 0",
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
    """A column or table name for in SQL. Simple names stay as they are; anything
    else, and words SQL uses itself (like order or end), gets "double quotes"."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and name.lower() not in RESERVED_WORDS:
        return name
    return '"' + name.replace('"', '""') + '"'


def table_name(csv_file, taken):
    """A SQL table name made from the CSV file's name, e.g. data/waterinfo_289423042.csv -> waterinfo_289423042"""
    name = csv_file.split("?")[0].split("#")[0].split("/")[-1].lower()
    name = re.sub(r"\.csv$", "", name)
    name = re.sub(r"[^a-z0-9_]", "_", name)
    if name == "" or not name[0].isalpha():
        name = "t_" + name
    if name in RESERVED_WORDS:
        name = name + "_table"  # e.g. "order" -> "order_table", so nobody has to quote it
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


def column_expression(column):
    """How one CSV column becomes one typed SQL column.

    DuckDB first reads every column as text. Here each column is turned into its
    CSVW type. A value that does not fit (a timestamp without seconds, 1.5 in a
    whole-number column, ...) stops the loading with a clear message, instead
    of quietly becoming NULL or another number."""
    name = sql_identifier(column["name"])
    sql_type = column["type"]

    # CSVW "null" values (e.g. "NA") become NULL. Empty cells are always NULL.
    nulls = []
    for null_value in column["nulls"]:
        if null_value != "":
            nulls.append(sql_text(null_value))
    is_null = f"{name} IS NULL"
    if len(nulls) > 0:
        is_null += f" OR {name} IN ({', '.join(nulls)})"

    if sql_type == "VARCHAR":
        return f"CASE WHEN {is_null} THEN NULL ELSE {name} END AS {name}"

    fits = f"TRY_CAST(trim({name}) AS {sql_type}) IS NOT NULL"
    if sql_type == "BIGINT":
        # TRY_CAST('1.5' AS BIGINT) would quietly round to 2, so also check for a whole number
        fits += f" AND regexp_full_match(trim({name}), '[+-]?[0-9]+')"
    if sql_type == "BOOLEAN":
        # DuckDB would also accept words like "yes"; CSVW only allows these four
        fits += f" AND regexp_full_match(lower(trim({name})), 'true|false|1|0')"
    message = sql_text(f"Column {column['name']} should be {TYPE_WORDS[sql_type]}, but the CSV has: ")
    return (f"CASE WHEN {is_null} THEN NULL "
            f"WHEN {fits} THEN CAST(trim({name}) AS {sql_type}) "
            f"ELSE error({message} || {name}) END AS {name}")


def check_header(table):
    """The header line of the CSV must match the CSVW columns, in the same order.
    DuckDB puts the values into the columns by their position, so a CSV with its
    columns in another order would quietly mix them up."""
    with open(table["path"], encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=table["delimiter"], quotechar=table["quote"] or '"')
        found = None
        skipped = 0
        for row in reader:
            if skipped < table["skip"]:
                skipped += 1
                continue
            if table["comment"] and len(row) > 0 and row[0].startswith(table["comment"]):
                continue  # a comment line
            found = row
            break
    if found is None:
        raise ValueError(f"{table['file']} is empty.")

    columns = table["columns"]
    if len(found) != len(columns):
        raise ValueError(f"{table['file']} has {len(found)} columns in its header line, "
                         f"but its CSVW file describes {len(columns)}.")
    for position in range(len(columns)):
        header = found[position].strip()
        if header not in columns[position]["titles"]:
            raise ValueError(f"Column {position + 1} of {table['file']} is called '{header}', but the CSVW file "
                             f"expects '{columns[position]['titles'][0]}' (column {columns[position]['name']}) there.")


def load_table(con, table):
    """Copy one CSV file into the database as one table"""
    # DuckDB would treat * ? [ ] in a file name as a pattern and maybe read other files
    if re.search(r"[*?\[\]]", table["path"]):
        raise ValueError(f"The SQL box can't read files with * ? [ or ] in their name: {table['file']}")
    if table["header"]:
        check_header(table)

    # read_csv gets the delimiter, quote character, rows to skip and the column names
    # from the CSVW file, and reads every column as text (VARCHAR) ...
    as_text = []
    typed = []
    for column in table["columns"]:
        as_text.append(f"{sql_text(column['name'])}: 'VARCHAR'")
        typed.append(column_expression(column))
    options = [
        "auto_detect = false",  # don't let DuckDB guess anything: the CSVW file says how to read the CSV
        f"delim = {sql_text(table['delimiter'])}",
        f"quote = {sql_text(table['quote'])}",
        f"escape = {sql_text(table['escape'])}",
        f"header = {'true' if table['header'] else 'false'}",
        f"skip = {int(table['skip'])}",
        "nullstr = ''",
        f"columns = {{{', '.join(as_text)}}}",
    ]
    if table["comment"]:
        options.append(f"comment = {sql_text(table['comment'])}")

    # ... and then every column is turned into its CSVW type (see column_expression)
    sql = (f"CREATE TABLE {sql_identifier(table['name'])} AS\n"
           f"SELECT {', '.join(typed)}\n"
           f"FROM read_csv({sql_text(table['path'])}, {', '.join(options)})")
    try:
        con.execute(sql)
    except duckdb.Error as error:
        message = str(error)
        if "Expected Number of Columns" in message:
            message = (f"a row does not have the {len(table['columns'])} columns the CSVW file describes. "
                       f"DuckDB says: {message.splitlines()[0]}")
        raise ValueError(f"Could not load {table['file']} as described by its CSVW file: {message}")


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


def open_database(tables, snippets):
    """A new in-memory DuckDB with our tables in it, locked down.
    Returns (the database, the tables that could not be loaded and why).

    tables: [{"name", "file", "path", "delimiter", "quote", "escape", "header", "skip",
              "comment", "columns": [{"name", "type", "titles", "nulls"}]}]
    snippets: rows for the "snippets" table (see SNIPPET_COLUMNS)"""
    con = duckdb.connect()  # no file name: the database only lives in memory
    try:
        con.execute("SET TimeZone = 'UTC'")
        # No scratch folder on disk: otherwise the files in it would stay readable
        con.execute("SET temp_directory = ''")

        # 1. One table per CSV file. A file that does not match its CSVW file is
        #    skipped (with the reason), so the other tables can still be used.
        skipped = []
        for table in tables:
            try:
                load_table(con, table)
            except ValueError as error:
                skipped.append({"name": table["name"], "error": str(error)})

        # 2. The TSS2 metadata as a table, so it can be joined with the data
        column_list = []
        for name, sql_type in SNIPPET_COLUMNS:
            column_list.append(f"{name} {sql_type}")
        con.execute(f"CREATE TABLE snippets ({', '.join(column_list)})")
        for row in snippets:
            values = []
            for name, sql_type in SNIPPET_COLUMNS:
                values.append(row[name])
            try:
                con.execute(f"INSERT INTO snippets VALUES ({', '.join(['?'] * len(values))})", values)
            except duckdb.Error as error:
                raise ValueError(f"Snippet '{row['label']}' can't go into the snippets table "
                                 f"(is its tss:from / tss:until a date-time?): {error}")

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


def example_queries(tables):
    """A few queries to start from, made for this endpoint's tables and columns"""
    examples = [{"title": "Which column is which series (the TSS2 metadata)",
                 "sql": "SELECT label, sql_table, value_column, unit, from_time, until_time\nFROM snippets"}]
    if len(tables) == 0:
        return examples

    table = tables[0]
    t = sql_identifier(table["name"])
    examples.insert(0, {"title": f"The first 10 rows of {table['name']}", "sql": f"SELECT *\nFROM {t}\nLIMIT 10"})
    examples.append({"title": f"Summary of every column of {table['name']}", "sql": f"SUMMARIZE {t}"})

    # The examples below need a series with both a time column and a value column
    series = []
    for snippet in table["snippets"]:
        if snippet["timeColumn"] and snippet["valueColumn"]:
            series.append(snippet)
    if len(series) == 0:
        return examples

    first = series[0]
    when = sql_identifier(first["timeColumn"])
    value = sql_identifier(first["valueColumn"])
    label = first["label"]

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
        "title": f"Only the readings inside the series' own time window (data + metadata): {label}",
        "sql": f"SELECT d.{when}, d.{value}\n"
               f"FROM {t} AS d, snippets AS s\n"
               f"WHERE s.snippet = {sql_text(first['id'])}\n"
               f"  AND d.{when} >= s.from_time AND d.{when} < s.until_time\n"
               f"ORDER BY d.{when}\nLIMIT 100",
    })

    # Two series side by side: only when the table has a second series in another column
    second = None
    for snippet in series[1:]:
        if snippet["valueColumn"] != first["valueColumn"] and second is None:
            second = snippet
    if second is not None:
        other = sql_identifier(second["valueColumn"])
        examples.append({
            "title": f"Two series side by side, per day: {label} / {second['label']}",
            "sql": f"SELECT date_trunc('day', {when}) AS day,\n"
                   f"       round(avg({value}), 3) AS {value},\n"
                   f"       round(avg({other}), 3) AS {other}\n"
                   f"FROM {t}\nGROUP BY ALL\nORDER BY day",
        })
    return examples
