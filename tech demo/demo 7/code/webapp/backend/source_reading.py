"""Reading an endpoint's tss:Source and its tss:Field nodes from the metadata graph, as RDF terms and
Python values (read_source), plus the small helpers for that (find_source, boolean_value, column_name,
message_text for error messages) and where a tss:dataFile is (iri_to_location).
Used by documents.load_source and by the modules that read the data and answer the routes."""
from fastapi import HTTPException
from rdflib import Literal, URIRef, RDF, RDFS
from rdflib.namespace import PROV
from pathlib import Path
from urllib.parse import urlparse, unquote
from urllib.request import url2pathname

import paths
from rdf_generation import TSS, column_index, embedded_mode

# The backend never compares prefixed names like "tss:CSV": a document may use any
# prefix it likes, or none at all. They compare the RDF terms rdflib gives
# (graph.value(source, TSS["format"]) == TSS.CSV), and read typed values with
# Literal.toPython() (a boolean, a whole number, a datetime, ...).


def message_text(graph, term):
    """How an RDF term is written in an error message: a prefixed name like tss:CSV,
    <a full IRI>, or a literal like "abc". Only for people to read: the code never
    compares these texts."""
    if term is None:
        return "(not stated)"
    return term.n3(graph.namespace_manager)


def find_source(graph):
    """The one tss:Source of an endpoint's metadata (this dashboard reads one source per endpoint)"""
    sources = list(graph.subjects(RDF.type, TSS.Source))
    if len(sources) == 0:
        raise HTTPException(status_code=404, detail="The metadata has no tss:Source")
    if len(sources) > 1:
        raise HTTPException(status_code=400, detail=f"The metadata has {len(sources)} tss:Source nodes; "
                                                    "this dashboard reads one source per endpoint")
    return sources[0]


def boolean_value(graph, node, prop):
    """A boolean property, like tss:header or tss:mutable, as True or False, read by rdflib.
    None when it is not stated, or not a boolean."""
    value = graph.value(node, prop)
    if isinstance(value, Literal) and isinstance(value.toPython(), bool):
        return value.toPython()
    return None


def column_name(graph, field):
    """The column a field reads, e.g. "Timestamp", for error messages.
    A field without tss:columnName is written as its IRI."""
    name = graph.value(field, TSS.columnName)
    if name is not None:
        return str(name)
    return message_text(graph, field)


def read_field(graph, field, time_field):
    """One field of the source (one column of its data), as RDF terms and plain values"""
    column = graph.value(field, TSS.columnName)
    return {
        "node": field,                                      # the field's IRI: routes and rows use it
        "columnName": str(column) if column is not None else None,   # e.g. "Value"
        "columnIndex": column_index(graph.value(field, TSS.columnIndex)),
        "datatype": graph.value(field, TSS.datatype),       # an IRI such as XSD.double, or None
        "isTime": field == time_field,
        "label": graph.value(field, RDFS.label),
        "observedProperty": graph.value(field, TSS.observedProperty),
        "unit": graph.value(field, TSS.unit),
        "describes": graph.value(field, TSS.describesField),  # the field this one says something about
    }


# The attributes of a field's stored summary (tss:summary): the name each one has in the SQL
# table "summaries" (sql_box.METADATA_TABLES), and its property. Every attribute is optional.
SUMMARY_ATTRIBUTES = [
    ("from_time", TSS["from"]),                       # the summary covers [from, until)
    ("until_time", TSS.until),
    ("missing_value_policy", TSS.missingValuePolicy),  # e.g. tss:ExcludeEmptyValues
    ("value_count", TSS["count"]),                     # how many values there are
    ("missing", TSS.missing),                          # how many rows have an empty value
    ("first_time", TSS.firstTime),                     # the first and last time with a value
    ("last_time", TSS.lastTime),
    ("observed_minimum", TSS.observedMinimum),         # only for numeric fields
    ("observed_maximum", TSS.observedMaximum),
    ("mean", TSS.mean),
    ("median", TSS.median),
    ("generated_at", PROV.generatedAtTime),            # when the summary was computed
]


def read_summary(graph, field):
    """A field's stored summary (its tss:summary node) as RDF terms, e.g. {"value_count":
    Literal("94853", datatype=XSD.integer), "mean": ..., ...} (None for an attribute it does not state).
    None when the field has no tss:summary. The numbers were computed from the data when the
    metadata was made (prov:generatedAtTime); this app shows and checks them. (Only adding a point
    with the "Add data" tab recomputes them: see points_routes.summary_update.)"""
    summary = graph.value(field, TSS.summary)
    if summary is None:
        return None
    values = {}
    for name, prop in SUMMARY_ATTRIBUTES:
        values[name] = graph.value(summary, prop)
    return values


def field_order(field):
    """How the fields are sorted: by tss:columnIndex when they have one,
    otherwise the time field first and then the rest by column name"""
    if field["columnIndex"] is not None:
        return (0, field["columnIndex"], "")
    if field["isTime"]:
        return (1, 0, "")
    return (2, 0, field["columnName"] or "")


def iri_to_location(iri, data_address):
    """Where a data file (tss:dataFile) is: a file in demo 7's data folder, as "data/x.csv".
    rdflib read <../data/x.csv> against the document's IRI (see documents.local_document_address), so:
    - a document gives          http://localhost:5174/files/data/x.csv     -> "data/x.csv"
      (data_address is http://localhost:5174/files/data/; that folder is not served, the backend reads the file)
    - a document may also name  file:///C:/.../demo%207/data/x.csv          -> "data/x.csv"
    Anything else is refused (400). A web address (https://.../x.csv) too: this backend never
    downloads anything, the data of a user endpoint is read by the browser.
    Only files inside demo 7's data folder are read. The path is resolved first (resolve()
    follows "..", and links), so data/../../x can't get out of it."""
    if iri is None:
        return None
    iri = str(iri)
    parts = urlparse(iri)
    folder = urlparse(data_address)
    same_server = parts.scheme == folder.scheme and parts.netloc.lower() == folder.netloc.lower()
    if same_server and parts.path.startswith(folder.path):
        path = paths.DEMO_DIR / "data" / unquote(parts.path[len(folder.path):])  # unquote: %20 -> a space, ...
    elif parts.scheme == "file":
        path = Path(url2pathname(parts.path))
    elif parts.scheme in ("http", "https"):
        raise HTTPException(status_code=400, detail=f"tss:dataFile <{iri}> is a web address: this backend reads only "
                                                    "the demo's own data files; user endpoints are read by the browser.")
    else:
        raise HTTPException(status_code=400, detail=f"tss:dataFile <{iri}> is not a file in demo 7's data folder.")

    data_folder = (paths.DEMO_DIR / "data").resolve()
    path = path.resolve()
    if path == data_folder or not path.is_relative_to(data_folder):
        raise HTTPException(status_code=400, detail=f"tss:dataFile <{iri}> is not a file in demo 7's data folder.")
    return "data/" + path.relative_to(data_folder).as_posix()


def read_source(graph, data_address):
    """What the data routes need to know about the endpoint's tss:Source and its fields,
    read from the graph as RDF terms and Python values.
    data_address: where demo 7's data folder is for this document (see documents.local_data_address
    and iri_to_location).
    (The converter has its own read_source, in code/TSS2RDF/tss2rdf.py: it raises ValueError, and
    refuses a property stated twice, where graph.value here takes one of the values.)"""
    source = find_source(graph)
    time_field = graph.value(source, TSS.timeField)
    fields = []
    for field in graph.objects(source, TSS.hasField):
        fields.append(read_field(graph, field, time_field))
    fields.sort(key=field_order)

    delimiter = graph.value(source, TSS.delimiter)
    encoding = graph.value(source, TSS.encoding)
    return {
        "node": source,
        "embedded": embedded_mode(graph, source),       # True, False, or None (not stated)
        "format": graph.value(source, TSS["format"]),    # an IRI: compare it with TSS.CSV or TSS.Parquet
        "dataFile": iri_to_location(graph.value(source, TSS.dataFile), data_address),  # e.g. "data/x.csv", or None
        "delimiter": str(delimiter) if delimiter is not None else ",",
        "header": boolean_value(graph, source, TSS.header) is not False,  # a header line, unless it says false
        "encoding": str(encoding) if encoding is not None else "utf-8",
        "timeField": time_field,
        "fields": fields,
    }


def find_field(source, field_iri):
    """The field (one column of the source) with this IRI, from read_source"""
    for field in source["fields"]:
        if field["node"] == URIRef(field_iri):
            return field
    raise HTTPException(status_code=404, detail=f"Unknown field: {field_iri}")
