"""Routes for the SQL box: the tables it can query and running a read-only query with DuckDB
(sql_box.py does the DuckDB part). main.py adds this router to the app."""
from fastapi import APIRouter, HTTPException, Request
from rdflib import Literal, RDFS
from pydantic import BaseModel
from decimal import Decimal
import csv
import duckdb

import files
import source_reading
import value_reading
import data_reading
import documents
import sql_box
from rdf_generation import TSS, LEXICAL_FORMS, TIME_DATATYPES

# The routes of this file, all under /api (main.py adds them to the app with include_router)
router = APIRouter(prefix="/api")


def column_role(field):
    """What a field is, in one word, for the role column of the SQL table "fields":
    'time' (the source's tss:timeField), 'describes' (it has a tss:describesField, like a
    quality code), 'value' (it has a tss:observedProperty), or None"""
    if field["isTime"]:
        return "time"
    if field["describes"] is not None:
        return "describes"
    if field["observedProperty"] is not None:
        return "value"
    return None


def text_or_none(term):
    """An RDF term as text for a SQL table: an IRI in full, a literal's text. None stays None (NULL)."""
    if term is None:
        return None
    return str(term)


def sql_value(literal, column):
    """A value of an embedded point, for its column of the SQL table: the Python value rdflib
    reads from the literal (a float for xsd:double, a datetime for xsd:dateTime, ...), or the
    literal's text for a text column. None (NULL) when the point has no value."""
    if literal is None:
        return None
    if column["type"] == "VARCHAR":
        return str(literal)
    if literal.datatype in TIME_DATATYPES:
        # A time is read like /query reads it (see value_reading.literal_time), also an xsd:dateTimeStamp,
        # which rdflib's toPython() leaves unread
        value = value_reading.literal_time(literal)
        if value is None:
            raise ValueError(f"Column {column['name']} should be {sql_box.TYPE_WORDS[column['type']]}, "
                             f"but the data has: {literal}")
        return value
    value = literal.toPython()
    # rdflib gives the literal itself back when its text does not fit the datatype. It also
    # reads some texts that are not in the XSD form ("yes" as xsd:boolean is false, " 12 " as
    # xsd:double is 12.0), so the text is checked against the XSD form too, as SHACL does.
    form = value_reading.DATA_FORMS.get(literal.datatype)
    if isinstance(value, Literal) or (form is not None and form.fullmatch(str(literal)) is None):
        raise ValueError(f"Column {column['name']} should be {sql_box.TYPE_WORDS[column['type']]}, "
                         f"but the data has: {literal}")
    return value


def summary_value(term, sql_type):
    """One attribute of a stored summary (an RDF term, or None), for its column of the SQL table
    "summaries": a time as a datetime (read like every other time, see value_reading.literal_time),
    a number as a number (read by rdflib), an IRI as its full text. A value that does not fit its
    column, e.g. "abc"^^xsd:integer, becomes NULL here: the SHACL tab reports it."""
    if term is None:
        return None
    if sql_type == "VARCHAR":
        return str(term)
    if not isinstance(term, Literal):
        return None
    if sql_type == "TIMESTAMPTZ":
        return value_reading.literal_time(term)
    value = term.toPython()  # an int, a float or a Decimal; the literal itself when rdflib can't read it
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    if sql_type == "BIGINT":
        return int(value) if value == int(value) else None
    return float(value)


def summary_rows(graph, source, loaded_name):
    """The rows of the SQL table "summaries": one per field that has a stored summary (tss:summary)"""
    sql_types = dict(sql_box.METADATA_TABLES["summaries"]["columns"])  # column name -> its SQL type
    rows = []
    for field in source["fields"]:
        summary = source_reading.read_summary(graph, field["node"])
        if summary is None:
            continue
        row = {"field": str(field["node"]), "column_name": field["columnName"], "sql_table": loaded_name}
        for name, prop in source_reading.SUMMARY_ATTRIBUTES:
            row[name] = summary_value(summary[name], sql_types[name])
        rows.append(row)
    return rows


def summarized_fields(graph, source):
    """The IRIs of the fields that have a stored summary (tss:summary)"""
    summarized = []
    for field in source["fields"]:
        if graph.value(field["node"], TSS.summary) is not None:
            summarized.append(field["node"])
    return summarized


def document_modified_at(graph):
    """When the metadata document was last edited: its tss:modifiedAt, as a datetime, or None.
    The document states it about itself (<> in Turtle, read as the document's own IRI), and only
    the document has one (SHACL's check D1), so the one subject with a tss:modifiedAt is the document.
    None when there is no such statement, or more than one."""
    values = list(graph.subject_objects(TSS.modifiedAt))
    if len(values) != 1 or not isinstance(values[0][1], Literal):
        return None
    return value_reading.literal_time(values[0][1])


def sql_table(graph, source):
    """The SQL table of the source: its name, where its data is, and its columns
    (one per data column, typed by the field's tss:datatype; Parquet keeps its own types).
    "error" says why it can't be loaded."""
    by_column = {}  # column name -> its field
    for field in source["fields"]:
        if field["columnName"] is not None:
            by_column[field["columnName"]] = field

    table = {"name": "data", "kind": None, "columns": [], "error": None}
    names = []          # the columns, in the order of the data
    native_types = {}   # Parquet: column name -> its type in the file
    points = []         # embedded: the points, read as RDF (see data_reading.point_rows)
    try:
        if source["embedded"]:
            # The table is named after the source, e.g. ex:zevenaarshaven-source -> zevenaarshaven_source
            table["name"] = sql_box.table_name(str(source["node"]).split("#")[-1].split("/")[-1])
            table["kind"] = "points"
            # One column per field with a column name (a key that is no field's is left out)
            for field in source["fields"]:
                if field["columnName"] is not None:
                    names.append(field["columnName"])
            points = data_reading.point_rows(graph, source)
        else:
            location = source["dataFile"]
            if location is None:
                raise ValueError("The source has no tss:dataFile")
            table["name"] = sql_box.table_name(location)
            if not files.file_exists(location):
                raise ValueError(f"Data file not found: {location}")
            table["path"] = files.local_copy(location).as_posix()

            if source["format"] == TSS.Parquet:
                table["kind"] = "parquet"
                with duckdb.connect() as connection:
                    described = connection.execute(
                        "DESCRIBE SELECT * FROM read_parquet(?, hive_partitioning = false)", [table["path"]]
                    ).fetchall()
                for column in described:
                    names.append(column[0])            # e.g. Timestamp
                    native_types[column[0]] = column[1]  # e.g. TIMESTAMP WITH TIME ZONE
            elif source["format"] == TSS.CSV:
                table["kind"] = "csv"
                data_reading.check_csv_settings(source)
                table["delimiter"] = source["delimiter"]
                table["header"] = source["header"]
                first_line = next(csv.reader(files.read_lines(location), delimiter=table["delimiter"]), [])
                if table["header"]:
                    names = first_line
                else:
                    # No header line: a column gets the column name of the field with its tss:columnIndex
                    names = data_reading.csv_header(source, first_line)
            else:
                raise ValueError(f"The SQL box can't read data in tss:format {source_reading.message_text(graph, source['format'])}")
    except (ValueError, duckdb.Error) as error:
        table["error"] = str(error)
        return table

    # CSV: the XSD form each column's cells must have, by column name (None: any text). These are
    # the same checks /query makes (LEXICAL_FORMS), so " 12 " is refused here too (sql_box.column_expression).
    table["forms"] = {}
    for name in names:
        if names.count(name) > 1:
            table["error"] = f"Two columns are called '{name}'; a SQL table needs different column names."
            return table
        field = by_column.get(name)
        datatype = field["datatype"] if field is not None else None
        if table["kind"] == "parquet":
            sql_type = native_types[name]
        else:
            sql_type = sql_box.duckdb_type(datatype)
        # Each column says which field it holds (the field's IRI), or None when no field describes it
        field_iri = str(field["node"]) if field is not None else None
        table["columns"].append({"name": name, "type": sql_type, "field": field_iri})
        table["forms"][name] = LEXICAL_FORMS.get(datatype)

    # Embedded points: one row per point, one value per column (see sql_value)
    if table["kind"] == "points":
        table["rows"] = []
        try:
            for point in points:
                row = []
                for column in table["columns"]:
                    row.append(sql_value(point.get(by_column[column["name"]]["node"]), column))
                table["rows"].append(row)
        except ValueError as error:
            table["error"] = f"Could not load the embedded points: {error}"
    return table


@router.get("/endpoints/{endpoint_id}/sql/tables")
def sql_schema(endpoint_id, request: Request = None):
    """What the SQL box can query for this endpoint: the data table, the metadata tables, and example queries"""
    graph, source = documents.load_source(endpoint_id, request)
    table = sql_table(graph, source)

    metadata_tables = []
    for name, metadata in sql_box.METADATA_TABLES.items():
        columns = []
        for column_name, sql_type in metadata["columns"]:
            columns.append({"name": column_name, "type": sql_type})
        metadata_tables.append({"name": name, "explanation": metadata["explanation"], "columns": columns})

    # Where the data is (tss:dataFile or tss:points) and its format, the browser reads from the RDF
    return {
        "tables": [{
            "name": table["name"],
            "error": table["error"],
            "columns": table["columns"],   # [{"name", "type", "field": <the field's IRI, or None>}]
        }],
        "metadataTables": metadata_tables,
        "examples": sql_box.example_queries(table if table["error"] is None else None, source["fields"],
                                            summarized_fields(graph, source)),
        "maxRows": sql_box.MAX_ROWS,
        "maxSeconds": sql_box.MAX_SECONDS,
    }


class SqlQuery(BaseModel):
    """What the browser sends: {"sql": "SELECT ..."} (in the body, because a query can be long)"""
    sql: str


@router.post("/endpoints/{endpoint_id}/sql")
def run_sql(endpoint_id, query: SqlQuery, request: Request = None):
    """Run one read-only SQL query with DuckDB on the data of this endpoint"""
    graph, source = documents.load_source(endpoint_id, request)

    # 1. The data table. If it can't be made (e.g. the data file is missing), it is
    #    skipped: the metadata tables still work.
    table = sql_table(graph, source)
    tables = []
    skipped = []
    if table["error"] is None:
        tables.append(table)
    else:
        skipped.append({"name": table["name"], "error": table["error"]})
    loaded_name = table["name"] if table["error"] is None else None

    # 2. The TSS metadata, for the "source", "fields" and "summaries" tables (see sql_box.METADATA_TABLES).
    #    IRIs are written in full, so a query does not depend on the prefixes of the file.
    node = source["node"]
    label = graph.value(node, RDFS.label)
    source_row = {
        "source": str(node),
        "label": str(label) if label is not None else endpoint_id,
        "sql_table": loaded_name,
        "format": text_or_none(source["format"]),
        "time_field": text_or_none(source["timeField"]),
        "from_time": value_reading.window_moment(graph, node, "from"),    # a datetime, read by rdflib
        "until_time": value_reading.window_moment(graph, node, "until"),
        "produced_by": text_or_none(graph.value(node, TSS.producedBy)),
        "mutable": source_reading.boolean_value(graph, node, TSS.mutable),
        "modified_at": document_modified_at(graph),
    }
    field_rows = []
    for field in source["fields"]:
        field_rows.append({
            "column_name": field["columnName"],
            "sql_table": loaded_name,
            "field": str(field["node"]),
            "label": text_or_none(field["label"]),
            "role": column_role(field),
            "datatype": text_or_none(field["datatype"]),
            "unit": text_or_none(field["unit"]),
            "observed_property": text_or_none(field["observedProperty"]),
            "sensor": text_or_none(graph.value(field["node"], TSS.sensor)),
            "feature_of_interest": text_or_none(graph.value(field["node"], TSS.featureOfInterest)),
            "describes": text_or_none(field["describes"]),
        })

    # 3. Load, lock, run, throw away
    try:
        con, not_loaded = sql_box.open_database(tables, {
            "source": [source_row],
            "fields": field_rows,
            "summaries": summary_rows(graph, source, loaded_name),
        })
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    skipped = skipped + not_loaded  # a missing data file + data that could not be loaded
    try:
        result = sql_box.run_query(con, query.sql)
        result["skipped"] = skipped  # the tables that could not be loaded, and why
        return result
    except (ValueError, sql_box.duckdb.Error) as error:
        # a mistake in the query: show DuckDB's own message, it says what is wrong.
        # If the data table was not loaded, say so too: that may be why the query fails.
        message = str(error)
        for skipped_table in skipped:
            message += f"\n(Table {skipped_table['name']} was not loaded: {skipped_table['error']})"
        raise HTTPException(status_code=400, detail=message)
    finally:
        con.close()
