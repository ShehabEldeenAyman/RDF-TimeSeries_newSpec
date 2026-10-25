"""Reading the data of a source: a CSV file, a Parquet file, or the points embedded in the Turtle
(tss:points, read as RDF). data_rows gives every row; first_rows the first few, for the inspector.
Used by the data routes and the SQL routes."""
from rdflib import Literal
from datetime import datetime, timezone
import csv
import duckdb

import files
import source_reading
import value_reading
import sql_box
from rdf_generation import TSS, lift_points


def point_time(row, time_field):
    """How points are put in time order: by their time, and a point without a valid time last.
    A valid time is an xsd:dateTime (or xsd:dateTimeStamp) literal in the XSD form (see
    value_reading.literal_time), the same rule as /query and SHACL: "2021-03-03" alone is no valid time."""
    moment = None
    value = row.get(time_field)
    if isinstance(value, Literal):
        moment = value_reading.literal_time(value)
    if moment is None:
        return (1, datetime.min.replace(tzinfo=timezone.utc))
    return (0, moment)


def point_rows(graph, source):
    """The points embedded in a source (tss:points), read as RDF, as rows in time order.
    lift_points (code/TSS2RDF/tss2rdf.py, via rdf_generation.py) parses the rdf:JSON literal
    as JSON-LD, with a context made from the source's fields, so each point becomes a blank
    node, each key a field IRI, and each value a literal typed by the field's tss:datatype:
        _:p1  ex:zevenaarshaven-ec20-value-field  "4797.72"^^xsd:double .
    Each row is a dict: field (its IRI) -> its literal. Keys that are no field's tss:columnName
    (predicates under urn:tss:unmapped:) are left out; the SHACL check warns about them.
    Raises ValueError with the reason when the points can't be read."""
    points = lift_points(graph, source["node"])
    fields = set()
    for field in source["fields"]:
        fields.add(field["node"])

    rows = []
    for point in points.subjects(unique=True):
        # A value that is itself a JSON object becomes a node inside a point: that is no point
        if (None, None, point) in points:
            continue
        row = {}
        for predicate, value in points.predicate_objects(point):
            if predicate not in fields:
                continue  # a key that is no field's column name (urn:tss:unmapped:...)
            if not isinstance(value, Literal):
                raise ValueError(f"A point in tss:points has a JSON object as its "
                                 f"{source_reading.column_name(graph, predicate)!r} value, not a value.")
            if predicate in row:
                raise ValueError(f"A point in tss:points has more than one {source_reading.column_name(graph, predicate)!r} value.")
            row[predicate] = value
        # A point with no value for any field (only keys that are no column name, or only empty
        # values) has nothing to show: it is skipped, as the browser does (SHACL's P6 still warns
        # about its keys)
        if len(row) == 0:
            continue
        rows.append(row)

    # Points have no order in RDF, so put them in time order
    time_field = source["timeField"]
    rows.sort(key=lambda row: point_time(row, time_field))
    return rows


# (The converter has its own cell_text, in code/TSS2RDF/tss2rdf.py: it writes a value in its XSD
# form, e.g. NaN where this one writes nan. This one stays as it is, so the previews do not change.)
def cell_text(value):
    """One cell of a Parquet file as text, like it would be in a CSV file ("" when empty)"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return sql_box.to_json_value(value)  # e.g. 2021-03-03T23:15:00Z
    return str(value)


def column_positions(source, header, only=None):
    """Where each field's values are in the rows of a data file: field (its IRI) -> column position.
    header: the column names (a CSV header line, or a Parquet file's columns): a field's column
    is the one named by its tss:columnName. header None: a CSV file without a header line
    (tss:header false), where a field's column is its tss:columnIndex.
    only: the fields (their IRIs) to find; None means every field."""
    positions = {}
    for field in source["fields"]:
        if only is not None and field["node"] not in only:
            continue
        if header is None:
            if field["columnIndex"] is not None:
                positions[field["node"]] = field["columnIndex"]
        elif field["columnName"] in header:
            positions[field["node"]] = header.index(field["columnName"])
    return positions


def csv_header(source, first_cells):
    """Column names for a CSV file without a header line (tss:header false): each column is
    named after the field with that tss:columnIndex, the others "column 0", "column 1", ..."""
    names = []
    for index in range(len(first_cells)):
        name = f"column {index}"
        for field in source["fields"]:
            if field["columnIndex"] == index and field["columnName"] is not None:
                name = field["columnName"]
        names.append(name)
    return names


def check_csv_settings(source):
    """Stop with the reason when this dashboard can't read the source's CSV file"""
    if len(source["delimiter"]) != 1:
        raise ValueError(f"tss:delimiter must be one character, not '{source['delimiter']}'")
    if source["encoding"].lower() not in ("utf-8", "utf8"):
        raise ValueError(f"This dashboard only reads UTF-8 files, not tss:encoding '{source['encoding']}'")


def data_location(source):
    """Where the source's data file is; raises ValueError when it has none, or when it is not there"""
    location = source["dataFile"]
    if location is None:
        raise ValueError("The source has no tss:dataFile")
    if not files.file_exists(location):
        raise ValueError(f"Data file not found: {location}")
    return location


def parquet_head(location, count):
    """The column names and the first rows of a Parquet file, each value as text (see cell_text)"""
    with sql_box.in_memory_database() as connection:
        result = connection.execute(
            "SELECT * FROM read_parquet(?, hive_partitioning = false) LIMIT ?", [str(files.local_path(location)), count]
        )
        names = [column[0] for column in result.description]
        found = result.fetchall()
    rows = []
    for values in found:
        cells = []
        for value in values:
            cells.append(cell_text(value))
        rows.append(cells)
    return names, rows


def data_rows(graph, source, only=None):
    """Every row of a source's data, one at a time, as a dict: field (its IRI) -> its value.
    A field that is missing, or "", has no value in that row.
    only: the fields (their IRIs) a route needs; None means every field. Reading fewer
    columns of a big file is faster.
    - CSV file: the text of the cell in the field's column, exactly as it is in the file (see column_positions)
    - Parquet file: the value in the field's column, as text (see cell_text)
    - points embedded in the Turtle: the field's literal itself, an rdflib Literal (see point_rows)
    Read a typed value from a text with value_reading.data_time or read_number, and from a
    literal with literal_time or literal_number (all of them check the XSD form first).
    value_reading.value_text gives the text of either.
    Raises ValueError with the reason when the data can't be read.
    (A function with "yield" hands out its rows one by one, so a big file never has to fit in memory.)"""
    if source["embedded"]:
        # The points are RDF already: each row holds their literals (field -> Literal)
        for point in point_rows(graph, source):
            yield point
        return

    location = data_location(source)

    if source["format"] == TSS.Parquet:
        try:
            with sql_box.in_memory_database() as connection:
                result = connection.execute(
                    "SELECT * FROM read_parquet(?, hive_partitioning = false)", [str(files.local_path(location))]
                )
                names = [column[0] for column in result.description]
                positions = column_positions(source, names, only)
                while True:
                    batch = result.fetchmany(10000)  # 10000 rows at a time
                    if len(batch) == 0:
                        break
                    for found in batch:
                        row = {}
                        for field, index in positions.items():
                            row[field] = cell_text(found[index])
                        yield row
        except duckdb.Error as error:
            raise ValueError(f"Could not read the Parquet file {location}: {error}")
        return

    if source["format"] == TSS.CSV:
        check_csv_settings(source)
        reader = csv.reader(files.read_lines(location), delimiter=source["delimiter"])
        header = None
        if source["header"]:
            header = next(reader, [])
        positions = column_positions(source, header, only)
        for cells in reader:
            row = {}
            for field, index in positions.items():
                if index < len(cells):
                    row[field] = cells[index]
            yield row
        return

    raise ValueError(f"The dashboard can't read data in tss:format {source_reading.message_text(graph, source['format'])}")


def data_columns(graph, source):
    """The fields that have a column in the source's data (their IRIs), in the order of the
    data's columns. Embedded points have no column order: they keep the order of the fields
    (see source_reading.field_order). Raises ValueError with the reason when the data can't be read."""
    if source["embedded"]:
        columns = []
        for field in source["fields"]:
            if field["columnName"] is not None:
                columns.append(field["node"])
        return columns

    location = data_location(source)
    if source["format"] == TSS.Parquet:
        try:
            names, rows = parquet_head(location, 0)  # 0 rows: only the column names
        except duckdb.Error as error:
            raise ValueError(f"Could not read the Parquet file {location}: {error}")
        positions = column_positions(source, names)
    elif source["format"] == TSS.CSV:
        check_csv_settings(source)
        header = None  # no header line (tss:header false): the fields' tss:columnIndex says where they are
        if source["header"]:
            header = next(csv.reader(files.read_lines(location), delimiter=source["delimiter"]), [])
        positions = column_positions(source, header)
    else:
        raise ValueError(f"The dashboard can't read data in tss:format {source_reading.message_text(graph, source['format'])}")

    # The fields, in the order of their columns
    order = []
    for field, position in positions.items():
        order.append((position, field))
    order.sort()
    columns = []
    for position, field in order:
        columns.append(field)
    return columns


def first_rows(graph, source, count):
    """The first rows of a source's data, for the inspector's table, as (columns, rows):
    the fields that have a column in the data (see data_columns), and each row as a list
    with the text of each field's value in that row, None when it has no value ("").
    Raises ValueError with the reason when the data can't be read."""
    columns = data_columns(graph, source)
    rows = []
    if count == 0:
        return columns, rows
    for row in data_rows(graph, source):
        cells = []
        for field in columns:
            text = value_reading.value_text(row, field)
            if text == "":
                cells.append(None)
            else:
                cells.append(text)
        rows.append(cells)
        if len(rows) == count:
            break
    return columns, rows
