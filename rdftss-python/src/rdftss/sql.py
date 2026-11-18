"""SQL on snippets, with DuckDB.

>>> result = snippet.sql('SELECT avg(value) FROM points')        # doctest: +SKIP
>>> print(result)                                                  # doctest: +SKIP
>>> with rdftss.Database(document.snippets) as database:          # doctest: +SKIP
...     print(database.describe())
...     database.query("SELECT * FROM fields").to_pandas()

A :class:`Database` is an in-memory DuckDB database with:

- one table per snippet, named after the snippet (see :meth:`Database.describe`), holding the
  points in the time window with their decoded values; with one snippet there is also a view
  ``points``. Column names are the field names with characters other than letters, digits and
  ``_`` replaced by ``_``;
- ``snippets``: one row per snippet (class, window, mutability, ...);
- ``fields``: one row per field (role, datatype, unit, observed property, ...), with the SQL
  table and column of each;
- ``summaries``: the stored summaries, so they can be compared with the data.

It is locked down before your query runs: no files, no network, no extensions, and settings
cannot be changed back. A query must be one reading statement (``SELECT``, ``WITH``,
``DESCRIBE``, ``SUMMARIZE``, ``SHOW``), and the answer is limited in rows, size and time.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

from . import xsd
from .errors import RDFTSSError, require


class SQLError(RDFTSSError):
    """A query was refused, failed, took too long or gave too big an answer."""


@dataclass
class QueryResult:
    """The answer to a query: ``columns``, ``rows`` (tuples), whether there were ``more_rows``
    than the row limit, and how many ``seconds`` it took."""

    columns: list
    rows: list
    more_rows: bool
    seconds: float

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def as_dicts(self) -> list[dict]:
        """The rows as dicts, by column name."""
        return [dict(zip(self.columns, row)) for row in self.rows]

    def to_pandas(self):
        """The answer as a pandas DataFrame."""
        pandas = require("pandas", "QueryResult.to_pandas", "pandas")
        return pandas.DataFrame(self.rows, columns=self.columns)

    def __str__(self) -> str:
        shown = [[("NULL" if value is None else str(value)) for value in row] for row in self.rows[:20]]
        widths = [len(column) for column in self.columns]
        for row in shown:
            widths = [max(width, min(len(value), 40)) for width, value in zip(widths, row)]
        lines = [" | ".join(column.ljust(width) for column, width in zip(self.columns, widths)),
                 "-+-".join("-" * width for width in widths)]
        lines += [" | ".join(value[:40].ljust(width) for value, width in zip(row, widths)) for row in shown]
        more = len(self.rows) - len(shown)
        if more > 0 or self.more_rows:
            lines.append(f"... ({more} more rows{' and more beyond the row limit' if self.more_rows else ''})")
        return "\n".join(lines)


def sql_name(name: str) -> str:
    """A name usable in SQL without quotes: letters, digits and ``_``, not starting with a digit."""
    cleaned = re.sub(r"[^0-9A-Za-z_]", "_", name).strip("_") or "t"
    if cleaned[0].isdigit():
        cleaned = "t_" + cleaned
    return cleaned


def _unique(name: str, used: set) -> str:
    candidate, number = name, 2
    while candidate.lower() in used:
        candidate = f"{name}_{number}"
        number += 1
    used.add(candidate.lower())
    return candidate


class Database:
    """An in-memory, locked-down DuckDB database with the points and metadata of snippets.

    - ``snippets``: the snippets to load (a list, a :class:`~rdftss.document.Document` or a
      :class:`~rdftss.catalogue.Catalogue`).
    - ``max_rows``, ``max_bytes``, ``timeout``: the limits of one answer.
    - ``memory_limit``, ``threads``: DuckDB settings.
    - ``strict``: read the snippets strictly (a snippet with a data error is then left out, and
      listed in ``skipped``); with ``strict=False`` the bad values are left out instead.

    Use it as a context manager, or call :meth:`close` when done.
    """

    RESERVED = {"snippets", "fields", "summaries", "points"}

    def __init__(self, snippets, *, max_rows: int = 10_000, max_bytes: int = 50_000_000, timeout: float = 30.0,
                 memory_limit: str = "1GB", threads: int = 4, strict: bool = True):
        duckdb = require("duckdb", "SQL queries", "sql")
        require("pytz", "SQL queries", "sql")   # DuckDB needs it for times with a timezone, but doesn't install it
        self._duckdb = duckdb
        self.max_rows, self.max_bytes, self.timeout = max_rows, max_bytes, timeout
        snippets = list(getattr(snippets, "snippets", snippets))
        self.tables: dict[str, object] = {}
        self.columns: dict[tuple, str] = {}
        self.skipped: list[tuple] = []
        self.connection = duckdb.connect()
        con = self.connection
        try:
            con.execute("SET temp_directory = ''")  # nothing is ever written to disk
            con.execute("SET TimeZone = 'UTC'")
            used = set(self.RESERVED)
            for snippet in snippets:
                try:
                    table = snippet.read(strict=strict).to_arrow()
                except RDFTSSError as error:
                    self.skipped.append((snippet, str(error)))
                    continue
                name = _unique(sql_name(snippet.name), used)
                table = self._rename_columns(snippet, table, name)
                self._create(name, table)
                self.tables[name] = snippet
            if len(self.tables) == 1:
                con.execute(f'CREATE VIEW points AS SELECT * FROM "{next(iter(self.tables))}"')
            self._metadata_tables(snippets)
            con.execute("SET autoinstall_known_extensions = false")
            con.execute("SET autoload_known_extensions = false")
            con.execute(f"SET memory_limit = '{memory_limit}'")
            con.execute(f"SET threads = {int(threads)}")
            con.execute("SET enable_external_access = false")  # no files, no network, no extensions
            con.execute("SET lock_configuration = true")       # and that can't be switched back
        except Exception:
            con.close()
            raise

    def _rename_columns(self, snippet, table, table_name):
        used: set = set()
        names = []
        for field, column in zip(snippet.fields, table.schema.names):
            sql_column = _unique(sql_name(column), used)
            self.columns[(table_name, field.node)] = sql_column
            names.append(sql_column)
        return table.rename_columns(names)

    def _create(self, name: str, table) -> None:
        self.connection.register("__rdftss_load", table)
        try:
            self.connection.execute(f'CREATE TABLE "{name}" AS SELECT * FROM __rdftss_load')
        finally:
            self.connection.unregister("__rdftss_load")

    def _metadata_tables(self, snippets) -> None:
        pa = require("pyarrow", "SQL queries", "sql")
        table_of = {id(snippet): name for name, snippet in self.tables.items()}

        def text(term):
            return str(term) if term is not None else None

        def moment(time_value):
            return time_value.datetime if time_value is not None else None

        def number(value):
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        snippet_rows, field_rows, summary_rows = [], [], []
        for snippet in snippets:
            sql_table = table_of.get(id(snippet))
            snippet_rows.append({
                "sql_table": sql_table, "snippet": text(snippet.node), "label": snippet.label,
                "class": text(snippet.kind.iri) if snippet.kind else None, "data_file": snippet.data_file,
                "time_field": text(snippet.time_field.node) if snippet.time_field else None,
                "window_from": moment(snippet.window.start), "window_until": moment(snippet.window.end),
                "mutable": snippet.mutable, "committed_until": moment(snippet.committed_until),
                "produced_by": text(snippet.produced_by)})
            for field in snippet.fields:
                sql_column = self.columns.get((sql_table, field.node))
                field_rows.append({
                    "sql_table": sql_table, "sql_column": sql_column, "field": text(field.node), "name": field.name,
                    "label": field.label, "role": field.role, "datatype": text(field.datatype),
                    "column_name": field.column_name, "unit": text(field.unit),
                    "observed_property": text(field.observed_property), "sensor": text(field.sensor),
                    "feature_of_interest": text(field.feature_of_interest),
                    "describes": ", ".join(str(other.node) for other in field.describes) or None,
                    "minimum": number(field.minimum), "maximum": number(field.maximum)})
                summary = field.summary
                if summary is not None:
                    summary_rows.append({
                        "sql_table": sql_table, "sql_column": sql_column, "field": text(field.node),
                        "missing_value_policy": text(summary.missing_value_policy), "value_count": summary.count,
                        "missing": summary.missing, "first_time": moment(summary.first_time),
                        "last_time": moment(summary.last_time), "observed_minimum": number(summary.observed_minimum),
                        "observed_maximum": number(summary.observed_maximum), "mean": number(summary.mean),
                        "median": number(summary.median), "generated_at": moment(summary.generated_at)})
        stamp = pa.timestamp("us", tz="UTC")
        schemas = {
            "snippets": [("sql_table", pa.string()), ("snippet", pa.string()), ("label", pa.string()),
                         ("class", pa.string()), ("data_file", pa.string()), ("time_field", pa.string()),
                         ("window_from", stamp), ("window_until", stamp), ("mutable", pa.bool_()),
                         ("committed_until", stamp), ("produced_by", pa.string())],
            "fields": [("sql_table", pa.string()), ("sql_column", pa.string()), ("field", pa.string()),
                       ("name", pa.string()), ("label", pa.string()), ("role", pa.string()),
                       ("datatype", pa.string()), ("column_name", pa.string()), ("unit", pa.string()),
                       ("observed_property", pa.string()), ("sensor", pa.string()),
                       ("feature_of_interest", pa.string()), ("describes", pa.string()),
                       ("minimum", pa.float64()), ("maximum", pa.float64())],
            "summaries": [("sql_table", pa.string()), ("sql_column", pa.string()), ("field", pa.string()),
                          ("missing_value_policy", pa.string()), ("value_count", pa.int64()),
                          ("missing", pa.int64()), ("first_time", stamp), ("last_time", stamp),
                          ("observed_minimum", pa.float64()), ("observed_maximum", pa.float64()),
                          ("mean", pa.float64()), ("median", pa.float64()), ("generated_at", stamp)],
        }
        for name, rows in (("snippets", snippet_rows), ("fields", field_rows), ("summaries", summary_rows)):
            schema = pa.schema(schemas[name])
            table = pa.table({column: pa.array([row[column] for row in rows], type=kind)
                              for column, kind in schemas[name]}, schema=schema)
            self._create(name, table)

    # -- using it --------------------------------------------------------------------------------

    def query(self, sql: str) -> QueryResult:
        """Run one reading query and return its :class:`QueryResult`.

        Raises :class:`SQLError` for more than one statement, a statement that is not a query,
        an error in the query, a query that takes longer than the time limit, or an answer
        larger than the size limit."""
        duckdb = self._duckdb
        con = self.connection
        try:
            statements = con.extract_statements(sql)
        except duckdb.Error as error:
            raise SQLError(str(error)) from None
        if len(statements) != 1:
            raise SQLError(f"Write exactly one query (found {len(statements)} statements).")
        if statements[0].type != duckdb.StatementType.SELECT:
            raise SQLError(f"Only reading queries are allowed (SELECT, WITH, DESCRIBE, SUMMARIZE, SHOW), "
                           f"not {statements[0].type.name}.")
        timer = threading.Timer(self.timeout, con.interrupt)
        started = time.time()
        timer.start()
        try:
            result = con.execute(sql)
            columns = [description[0] for description in result.description]
            rows, size, more_rows = [], 0, False
            while not more_rows:
                portion = result.fetchmany(1000)
                if not portion:
                    break
                for row in portion:
                    if len(rows) == self.max_rows:
                        more_rows = True
                        break
                    size += sum(len(str(value)) for value in row)
                    if size > self.max_bytes:
                        raise SQLError(f"The answer is larger than {self.max_bytes} bytes; ask for fewer rows or "
                                       "columns (with LIMIT, for example).")
                    rows.append(row)
        except duckdb.InterruptException:
            raise SQLError(f"The query was stopped after {self.timeout} seconds.") from None
        except duckdb.Error as error:
            raise SQLError(str(error)) from None
        finally:
            timer.cancel()
        return QueryResult(columns, rows, more_rows, round(time.time() - started, 3))

    def describe(self) -> str:
        """The tables and their columns, in text."""
        lines = []
        for name, snippet in self.tables.items():
            columns = [f"{self.columns[(name, field.node)]} ({field.role}, {xsd.short_name(field.datatype)})"
                       for field in snippet.fields]
            lines.append(f'Table "{name}" — snippet {snippet.document.short(snippet.node)}')
            lines.extend(f"    {column}" for column in columns)
        if len(self.tables) == 1:
            lines.append(f'View "points" — the same as "{next(iter(self.tables))}"')
        lines.append('Tables "snippets", "fields", "summaries" — the metadata')
        for snippet, reason in self.skipped:
            lines.append(f"Not loaded: {snippet.name}: {reason}")
        return "\n".join(lines)

    def examples(self) -> list[str]:
        """Example queries for the loaded tables."""
        found = []
        for name, snippet in self.tables.items():
            time_column = self.columns[(name, snippet.time_field.node)]
            found.append(f'SELECT * FROM "{name}" ORDER BY {time_column} LIMIT 10')
            numeric = [field for field in snippet.value_fields if field.is_numeric]
            if numeric:
                column = self.columns[(name, numeric[0].node)]
                found.append(f"SELECT time_bucket(INTERVAL 1 hour, {time_column}) AS hour, avg({column}) AS mean, "
                             f"min({column}) AS minimum, max({column}) AS maximum, count({column}) AS n "
                             f'FROM "{name}" GROUP BY hour ORDER BY hour')
        found.append("SELECT sql_table, sql_column, role, datatype, unit, observed_property FROM fields")
        found.append("SELECT sql_column, value_count, mean, generated_at FROM summaries")
        return found

    def close(self) -> None:
        """Close the database (it lives in memory only, so nothing is left behind)."""
        self.connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exception) -> None:
        self.close()
