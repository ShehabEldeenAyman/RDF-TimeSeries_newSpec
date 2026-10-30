"""Routes for the data: a preview and a download of the data file, the first rows, the columns to
filter on, and the query form's readings, summary and gaps. main.py adds this router to the app."""
from fastapi import APIRouter, HTTPException, Query, Request
from rdflib import Literal, URIRef
from rdflib.namespace import XSD
from typing import Annotated
import csv
import io
import duckdb

import files
import source_reading
import value_reading
import data_reading
import query_helpers
import documents
from rdf_generation import TSS

# The routes of this file, all under /api (main.py adds them to the app with include_router)
router = APIRouter(prefix="/api")

# Data routes: what the browser can't read from the RDF itself (the data, and numbers
# computed from it). The answers are JSON, and don't repeat what the metadata says
# (formats, datatypes, units, column names, ...): the browser reads that from the RDF.
# Fields are named by their full IRI: the IRIs the browser reads, because both read the
# metadata with the same base IRI (see documents.load_source; that is what the routes' request is
# for. A route called without a request, from a test or a script, uses paths.DEFAULT_BASE).


@router.get("/endpoints/{endpoint_id}/data")
def get_data_file(endpoint_id, lines: int = 20, request: Request = None):
    """Preview the source's data file (tss:dataFile): the first lines of a CSV file,
    or the first rows of a Parquet file converted to CSV text."""
    graph, source = documents.load_source(endpoint_id, request)
    if source["embedded"] is True:
        raise HTTPException(status_code=400, detail="This source has no data file: its points are embedded in the Turtle file (tss:pointOf).")
    lines = max(1, min(lines, 1000))
    location = source["dataFile"]
    if location is None:
        raise HTTPException(status_code=400, detail="The source has no tss:dataFile.")
    if not files.file_exists(location):
        return {"found": False, "note": "The data file (tss:dataFile) was not found.", "text": None}

    if source["format"] == TSS.Parquet:
        try:
            columns, rows = data_reading.parquet_head(location, lines - 1)  # lines - 1: the header line is a line too
        except duckdb.Error as error:
            raise HTTPException(status_code=400, detail=f"Could not preview Parquet file {location}: {error}") from error
        text = io.StringIO(newline="")
        writer = csv.writer(text, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)
        return {
            "found": True,
            "note": f"CSV preview of the first {len(rows)} Parquet rows; download contains the original Parquet file",
            "text": text.getvalue(),
        }
    if source["format"] != TSS.CSV:
        raise HTTPException(status_code=400, detail=f"A data preview is available for tss:CSV and tss:Parquet sources only, "
                                                    f"not {source_reading.message_text(graph, source['format'])}.")

    first_lines = []
    for line in files.read_lines(location):
        first_lines.append(line)
        if len(first_lines) == lines:
            break

    size_mb = round(files.local_path(location).stat().st_size / 1_000_000, 1)
    return {
        "found": True,
        "note": f"first {len(first_lines)} lines of a {size_mb} MB file",
        "text": "\n".join(first_lines),
    }


@router.get("/endpoints/{endpoint_id}/data/download")
def download_data_file(endpoint_id, request: Request = None):
    """Download the original data file (tss:dataFile), CSV or Parquet"""
    graph, source = documents.load_source(endpoint_id, request)
    if source["embedded"] is True or source["dataFile"] is None:
        raise HTTPException(status_code=400, detail="This source has no data file to download.")
    media_type = "application/vnd.apache.parquet" if source["format"] == TSS.Parquet else "text/csv"
    return files.send_as_download(source["dataFile"], media_type)


@router.get("/endpoints/{endpoint_id}/rows")
def get_first_rows(endpoint_id, limit: int = 10, request: Request = None):
    """The first rows of the source's data, for the series inspector: a CSV file, a Parquet
    file or the embedded points (read as RDF). One column per field that has a column in the
    data, named by the field's IRI; each cell is the text of the value, or None (no value)."""
    graph, source = documents.load_source(endpoint_id, request)
    limit = max(1, min(limit, 1000))
    try:
        columns, rows = data_reading.first_rows(graph, source, limit)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    column_iris = []
    for field in columns:
        column_iris.append(str(field))
    return {"columns": column_iris, "rows": rows}


@router.get("/endpoints/{endpoint_id}/filter-columns")
def filter_columns(endpoint_id, field, request: Request = None):
    """The other columns the query form can filter on (like a quality code),
    with the values that occur in them. Only columns with a few different values
    (2 to 30) are useful for that, so only those are sent."""
    graph, source = documents.load_source(endpoint_id, request)
    chosen = source_reading.find_field(source, field)

    # Start with every field except the time and this field, and drop a field
    # as soon as it has too many different values
    candidates = {}  # field (its IRI) -> the different values seen so far
    for other in source["fields"]:
        if other["columnName"] is None or other["isTime"] or other["node"] == chosen["node"]:
            continue
        candidates[other["node"]] = set()

    try:
        for row in data_reading.data_rows(graph, source, only=set(candidates)):
            for node in list(candidates):  # list(...): we may delete from candidates inside the loop
                candidates[node].add(value_reading.value_text(row, node))
                if len(candidates[node]) > 30:
                    del candidates[node]
            if len(candidates) == 0:
                break
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    columns = []
    for other in source["fields"]:
        values = candidates.get(other["node"])
        if values is not None and len(values) >= 2:  # a column with one value is no use as a filter
            columns.append({"field": str(other["node"]), "values": sorted(values)})
    return columns


@router.get("/endpoints/{endpoint_id}/query")
def query_series(endpoint_id, field, start=None, end=None, min_value=None, max_value=None,
                 filter_field: Annotated[str | None, Query(alias="filterField")] = None,
                 filter_value=None, resample="", aggregate="mean", limit: int = 100, request: Request = None):
    """Get the readings of one field (one time series), with the filters of the query form,
    plus summary numbers and the gaps in the data.
    field and filter_field (called filterField in the address) are field IRIs.

    The source tells us where the data is (tss:dataFile or the embedded points, tss:pointOf),
    how to read it (tss:format, and for CSV tss:delimiter, tss:header, tss:encoding),
    which field holds the time (tss:timeField) and which time window it covers
    (tss:from, tss:until). The field tells us its own column (tss:columnName) and
    its datatype (tss:datatype): only numeric fields have value ranges and mean/min/max."""
    graph, source = documents.load_source(endpoint_id, request)
    chosen = source_reading.find_field(source, field)
    limit = max(1, min(limit, 5000))  # don't send huge answers to the browser

    # 1. What the form asks for
    start_time = query_helpers.parse_time(start) if start else None
    end_time = query_helpers.parse_time(end) if end else None
    low = query_helpers.parse_number(min_value, "The lowest value")
    high = query_helpers.parse_number(max_value, "The highest value")
    if resample not in ["", "hour", "day"]:
        raise HTTPException(status_code=400, detail="resample must be empty, 'hour' or 'day'")
    if aggregate not in ["mean", "min", "max"]:
        raise HTTPException(status_code=400, detail="aggregate must be 'mean', 'min' or 'max'")

    # 2. The fields to read: the time field, this field, and the field to filter on
    time_field = None
    for other in source["fields"]:
        if other["isTime"]:
            time_field = other
    if time_field is None:
        raise HTTPException(status_code=400, detail="The source has no tss:timeField (one of its fields), so the readings have no time.")
    if chosen["isTime"]:
        raise HTTPException(status_code=400, detail="This is the time field itself. Pick a field that holds values.")
    filtered = None  # the field to filter on (filterField), by its IRI
    if filter_field:
        for other in source["fields"]:
            if other["node"] == URIRef(filter_field):
                filtered = other
        if filtered is None:
            raise HTTPException(status_code=400, detail=f"filterField <{filter_field}> is not a field of this source")
        if filter_value is None:
            # (filter_value= with nothing after it is fine: it keeps the rows without a value there)
            raise HTTPException(status_code=400, detail="filterField needs a filter_value too")

    datatype = chosen["datatype"]
    numeric = value_reading.is_numeric(datatype)
    if not numeric and (low is not None or high is not None or resample):
        holds = source_reading.message_text(graph, datatype) if datatype is not None else "values without a tss:datatype"
        raise HTTPException(status_code=400, detail=f"Value ranges and mean/min/max aggregation need a numeric field. This field holds {holds}.")

    # 3. The source only covers its own time window: from is included, until is not
    window_start = value_reading.window_moment(graph, source["node"], "from")
    window_end = value_reading.window_moment(graph, source["node"], "until")

    # 4. Go through the data row by row
    moments = []  # the time of every row inside the time range: to find the gaps
    kept = []     # the readings that also pass the column filter and the value range
    needed_fields = [time_field, chosen]
    if filtered is not None:
        needed_fields.append(filtered)
    only = set()
    for needed in needed_fields:
        only.add(needed["node"])
    first = True
    try:
        for row in data_reading.data_rows(graph, source, only):
            # The columns must be in the data file (an embedded point may leave out an empty value)
            if first and not source["embedded"]:
                for needed in needed_fields:
                    if needed["node"] not in row:
                        name = needed["columnName"] or source_reading.message_text(graph, needed["node"])
                        raise HTTPException(status_code=400, detail=f"Column '{name}' is not in {source['dataFile']}")
            first = False

            # The time: an embedded point's literal (see value_reading.literal_time), or the text of a
            # cell (see value_reading.data_time)
            time_value = row.get(time_field["node"], "")
            if time_value == "":
                raise HTTPException(status_code=400, detail=f"A row has no time in column '{time_field['columnName']}'")
            if isinstance(time_value, Literal):
                row_time = value_reading.literal_time(time_value)
            else:
                row_time = value_reading.data_time(time_value, time_field["datatype"])
            if row_time is None:
                kind = "an xsd:dateTime"
                if time_field["datatype"] == XSD.dateTimeStamp:
                    kind = "an xsd:dateTimeStamp (a date-time with a timezone)"
                raise HTTPException(status_code=400, detail=f"A row has {str(time_value)!r} in column '{time_field['columnName']}' "
                                                            f"(the tss:timeField), which is not {kind} like 2021-03-03T23:45:00Z")
            if window_start and row_time < window_start:
                continue
            if window_end and row_time >= window_end:
                continue
            if start_time and row_time < start_time:
                continue
            if end_time and row_time > end_time:
                continue
            moments.append(row_time)

            if filtered is not None and value_reading.value_text(row, filtered["node"]) != filter_value:
                continue
            cell = row.get(chosen["node"], "")  # the text of a cell, or an embedded point's literal
            if numeric and isinstance(cell, Literal):
                value = value_reading.literal_number(cell, datatype, graph)
            elif numeric:
                value = value_reading.read_number(cell, datatype, graph)
            else:
                text = value_reading.value_text(row, chosen["node"])
                value = text if text != "" else None
            if value is None and (low is not None or high is not None):
                continue  # with a value range, an empty cell can never match
            if low is not None and value < low:
                continue
            if high is not None and value > high:
                continue
            kept.append({"moment": row_time, "value": value})
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    # Files are usually in time order, but nothing guarantees it
    moments.sort()
    kept.sort(key=lambda reading: reading["moment"])

    # 5. The rows to show: every reading, or one value per hour or per day.
    #    Only the rows that are sent get their time written as text (that saves time on big files).
    if resample:
        rows = query_helpers.resample_readings(kept, resample, aggregate)
        total_rows = len(rows)
    else:
        rows = []
        for reading in kept[:limit]:
            rows.append({"time": query_helpers.time_text(reading["moment"]), "value": reading["value"]})
        total_rows = len(kept)

    if numeric:
        summary = query_helpers.summarize(kept)
    else:
        summary = query_helpers.text_summary(kept)

    # Only what was computed from the data, and what the request asked for: the browser
    # reads everything about the field and the source (label, unit, datatype, ...) from the RDF
    return {
        "resample": resample,
        "aggregate": aggregate,
        "filterValue": filter_value if filtered is not None else None,
        "summary": summary,
        "gaps": query_helpers.find_gaps(moments),   # over every reading in the time range
        "totalRows": total_rows,
        "limitReached": total_rows > limit,
        "readings": rows[:limit],     # [:limit] = only the first "limit" rows
    }
