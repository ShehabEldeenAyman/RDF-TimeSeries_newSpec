"""Route for the "Add data" tab: add one point to the embedded points (tss:points) of a bundled
endpoint, with SPARQL Update, and write the file only when SHACL says the result conforms.
main.py adds this router to the app.

    POST /api/endpoints/{id}/points     body {"values": {"<field IRI>": "<text>", ...}}

What happens, step by step (add_point, all of it while holding POINTS_LOCK):
 1. Checks: a bundled endpoint (a .ttl file in "metadata endpoint/"), whose tss:Source states
    tss:embedded true and tss:mutable true (tss:mutable false means: this data never changes);
    every value names a field of the source (by its IRI) that has a tss:columnName; the time is
    an xsd:dateTime with a timezone; every value has the XSD form of its field's datatype.
 2. The file is read with rdflib, every literal keeping its text exactly (read_exactly). A
    document with relative IRIs is refused: they would be written back as absolute IRIs.
 3. The points already there are read as RDF (lift_points, like every other tab), and the new
    point is added: a point with the same key (the same time) is refused. The new tss:points
    text is written: one JSON object per point, in time order, each value a JSON string.
 4. The graph is changed with SPARQL Update (graph.update): tss:points, the source's window
    (tss:from, tss:until), tss:modifiedAt and tss:committedUntil (both: the time of this write,
    not the time of the new point), and every stored summary (tss:summary).
 5. SHACL (the SHACL tab's own validation, with SHACL/embedded_shapes.ttl) checks the result.
    No conformance: nothing is written, the answer is 422 with the report. Conformance: the
    graph is written as Turtle (exact_turtle) to a temporary file in the same folder, read back
    and compared with the graph that was checked, and then put in place of the file (os.replace).

The file is written again by rdflib: its comments and its layout are lost (the triples stay).
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
import statistics
import tempfile
import threading
import time
from urllib.parse import quote, urljoin

import rdflib
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from rdflib import Graph, Literal, URIRef, RDF
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, SH, XSD

import paths
import documents
import source_reading
import value_reading
import data_reading
import query_helpers
import sql_box
from rdf_generation import NORMALIZE_LOCK, TSS, embedded_mode, exact_turtle, lift_points, shacl_report

# The routes of this file, all under /api (main.py adds them to the app with include_router)
router = APIRouter(prefix="/api")

# One append at a time. An append reads the file, changes it and writes it back: two appends
# at the same time would both read the old file, and the second write would lose the first
# point. (FastAPI runs each request of a plain "def" route in a thread of its own.)
POINTS_LOCK = threading.Lock()

# A second base IRI, to find relative IRIs in a document (see has_relative_iris). It has
# another scheme and another host than any file: IRI, so every relative reference
# (<#x>, <x.csv>, <../data/x.csv>, <//host/x>, <>) gives another IRI with it.
CHECK_BASE = "https://relative-iri-check.invalid/folder/document.ttl"

# How the new tss:points text is laid out, like the bundled files:
#   [
#           { "time": "2021-03-03T23:15:00Z", "value": "4797.72"},
#           ...
#         ]
POINT_INDENT = " " * 8
CLOSING_INDENT = " " * 6

# The prefixes the SPARQL updates use (the browser shows the updates as they ran)
TSS_PREFIX = "PREFIX tss: <https://w3id.org/tss#>\n"
PROV_PREFIX = "PREFIX prov: <http://www.w3.org/ns/prov#>\n"


class NewPoint(BaseModel):
    """The body of the request: the new point's values, by field IRI, each as text.
    The time field's value is needed; a missing value, null or "" means: no value."""
    values: dict[str, str | None]


# ---------------------------------------------------------------------------
# Reading the file with every literal as it is
# ---------------------------------------------------------------------------

def read_exactly(text, base, name):
    """Parse a Turtle document with rdflib, every literal keeping its text exactly as written.
    rdflib normally rewrites a literal's text ("2021-03-03T23:15:00Z" becomes
    "2021-03-03T23:15:00+00:00"); the file is written back from this graph, so nothing may change
    that was not changed on purpose. rdflib.NORMALIZE_LITERALS is one switch for the whole program,
    so it is turned off only during this parse, while holding NORMALIZE_LOCK (the lock lift_points
    and the SHACL validation take too), and put back afterwards (finally).
    name: how error messages call the document"""
    graph = Graph()
    with NORMALIZE_LOCK:
        normalize = rdflib.NORMALIZE_LITERALS
        rdflib.NORMALIZE_LITERALS = False
        try:
            graph.parse(data=text, format="turtle", publicID=base)
        except Exception as error:  # for example a typo in the Turtle
            raise HTTPException(status_code=500, detail=f"Could not read {name}: {error}")
        finally:
            rdflib.NORMALIZE_LITERALS = normalize
    return graph


def graph_iris(graph):
    """Every IRI in a graph: in the triples, and as the datatype of a literal"""
    found = set()
    for triple in graph:
        for term in triple:
            if isinstance(term, URIRef):
                found.add(term)
            elif isinstance(term, Literal) and term.datatype is not None:
                found.add(term.datatype)
    return found


def has_relative_iris(graph, text, name):
    """Does the document use relative IRIs (<#value>, <../data/x.csv>, <>, ...)?
    rdflib writes Turtle with absolute IRIs only, so writing such a document back would turn
    <#value> into <file:///C:/.../x.ttl#value>, another IRI than the browser reads
    (http://localhost:5174/files/...#value). Such a document is refused instead.
    How: the document is read a second time, with another base IRI (CHECK_BASE). An absolute IRI
    is the same in both graphs; a relative one is not. (Looking for IRIs that start with the
    file's own IRI would miss <../data/x.csv>, which resolves to the folder next to it.)"""
    other = read_exactly(text, CHECK_BASE, name)
    return graph_iris(graph) != graph_iris(other)


# ---------------------------------------------------------------------------
# The new point and the new tss:points text
# ---------------------------------------------------------------------------

def check_values(graph, source, values):
    """Check the values of the new point (field IRI -> text). Gives the new point as a row, like
    data_reading.point_rows makes them: field (its IRI) -> a literal with the field's datatype.
    A missing value, None or "" is no value. Raises an HTTP 400 error that says what is wrong."""
    fields = {}  # the fields a point can hold a value for: those with a tss:columnName
    for field in source["fields"]:
        if field["columnName"] is not None:
            fields[str(field["node"])] = field
    for iri in values:
        if iri not in fields:
            raise HTTPException(status_code=400, detail=f"<{iri}> is not a field of this source with a tss:columnName, "
                                                        "so a point can't hold a value for it.")

    time_field = source["timeField"]
    if time_field is None or str(time_field) not in fields:
        raise HTTPException(status_code=400, detail="The source has no tss:timeField with a tss:columnName, "
                                                    "so a point can't have a time.")
    time_text = values.get(str(time_field)) or ""
    if time_text == "":
        raise HTTPException(status_code=400, detail=f"The new point needs a time: a value for "
                                                    f"'{fields[str(time_field)]['columnName']}' (the tss:timeField).")
    # Always an xsd:dateTime with a timezone (the form of xsd:dateTimeStamp), so it is one moment
    if value_reading.data_time(time_text, XSD.dateTimeStamp) is None:
        raise HTTPException(status_code=400, detail=f"The time {time_text!r} is not a valid xsd:dateTime with a timezone, "
                                                    "like 2021-03-04T01:15:00Z.")

    row = {}
    for iri, field in fields.items():
        text = values.get(iri) or ""
        if text == "":
            continue  # no value: the point gets no key for it (like "" or a missing key)
        datatype = field["datatype"]
        # The XSD form of the datatype (value_reading.DATA_FORMS), the check SHACL's P4 makes too.
        # (A datatype without a known form, like xsd:string, takes any text.) The range
        # (tss:minimum, tss:maximum) is left to SHACL (P5).
        form = value_reading.DATA_FORMS.get(datatype)
        if field["node"] != time_field and form is not None and form.fullmatch(text) is None:
            raise HTTPException(status_code=400, detail=f"'{field['columnName']}': {text!r} is not a valid "
                                                        f"{source_reading.message_text(graph, datatype)} value.")
        # The literal the points will give for this value (see lift_points): its text exactly,
        # typed with the field's datatype. Only used here to sort and summarise.
        if datatype is None:
            row[field["node"]] = Literal(text)
        elif datatype == RDF.langString:
            language = graph.value(field["node"], TSS.language)  # the language of the text, e.g. "en"
            row[field["node"]] = Literal(text, lang=str(language) if language is not None else None)
        else:
            row[field["node"]] = Literal(text, datatype=datatype, normalize=False)
    return row


def check_existing_points(graph, source):
    """The points already in tss:points, as rows (data_reading.point_rows: field IRI -> literal),
    in time order. They are written back as they are read, so the read must lose nothing: a key
    that is no field's tss:columnName (SHACL's P6 warning) would be lost, so it is refused."""
    try:
        lifted = lift_points(graph, source["node"])
        rows = data_reading.point_rows(graph, source)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"The points already in the document can't be read: {error}")
    field_iris = set()
    for field in source["fields"]:
        field_iris.add(field["node"])
    for predicate in set(lifted.predicates()):
        if predicate not in field_iris:
            raise HTTPException(status_code=400, detail=f"A point in tss:points has the key <{predicate}>, which is no "
                                                        "field's tss:columnName. Writing the points again would lose it, "
                                                        "so no point is added. Fix the points first.")
    return rows


def key_fields(graph, source):
    """The fields whose values identify a point (tss:key); the time field when there is none"""
    keys = list(graph.objects(source["node"], TSS.key))
    if len(keys) == 0:
        keys = [source["timeField"]]
    return keys


def same_key(row, new_row, keys, time_field):
    """Do two points have the same key? The time is compared as a moment (...Z and ...+00:00 are
    the same time), any other key field by its text."""
    for field in keys:
        if field == time_field:
            if data_reading.point_time(row, time_field) != data_reading.point_time(new_row, time_field):
                return False
        elif value_reading.value_text(row, field) != value_reading.value_text(new_row, field):
            return False
    return True


def point_object(row, source):
    """One point as a JSON object in the style of the bundled files:
    { "time": "2021-03-03T23:15:00Z", "value": "4797.72"}
    The keys are the fields' tss:columnName, in the order of the fields (the time first); each
    value is a JSON string with the literal's text exactly as it is. json.dumps writes each key
    and value with the right JSON escapes (a quote as \\", ...): this WRITES the lexical form of
    the rdf:JSON literal; the points are never read as JSON (lift_points reads them as RDF)."""
    parts = []
    for field in source["fields"]:
        if field["columnName"] is not None and field["node"] in row:
            key = json.dumps(field["columnName"], ensure_ascii=False)
            value = json.dumps(str(row[field["node"]]), ensure_ascii=False)
            parts.append(f"{key}: {value}")
    return "{ " + ", ".join(parts) + "}"


def points_text(rows, source):
    """The text of the new tss:points literal: a JSON array, one point per line"""
    lines = []
    for row in rows:
        lines.append(POINT_INDENT + point_object(row, source))
    return "[\n" + ",\n".join(lines) + "\n" + CLOSING_INDENT + "]"


# ---------------------------------------------------------------------------
# SPARQL Update
# ---------------------------------------------------------------------------

def shortened_points(text):
    """The tss:points literal for people to read (in the list of updates that ran): with many
    points, the first two and the last two, and how many are left out in between"""
    lines = text.split("\n")
    if len(lines) > 8:  # "[", the points, "]"
        left_out = len(lines) - 6
        lines = lines[:3] + [f"{POINT_INDENT}… ({left_out} more points)"] + lines[-3:]
    return '"""' + "\n".join(lines) + '"""^^rdf:JSON'


def term_text(graph, term):
    """An RDF term as it is written in SPARQL or Turtle, with the document's prefixes (for people)"""
    return term.n3(graph.namespace_manager)


def run_update(graph, text, bindings, shown):
    """Run one SPARQL Update on the graph. bindings: variable name -> RDF term, given to rdflib
    with initBindings (so no value has to be written into the text, where the SPARQL parser
    could change its text). Gives what the browser shows: the update's text exactly as it ran,
    and each binding as text (shown: variable name -> text, for terms that are too long or
    have no name, like the tss:points literal or a blank node)."""
    graph.update(text, initBindings=bindings)
    listed = []
    for name, term in bindings.items():
        value = shown[name] if name in shown else term_text(graph, term)
        listed.append({"variable": "?" + name, "value": value})
    return {"update": text, "bindings": listed}


def replace_values_update(subject_variable, subject_pattern, properties):
    """The text of a DELETE/INSERT that gives one node new values for some properties:
        DELETE { ?s p1 ?old_x ; p2 ?old_y }
        INSERT { ?s p1 ?x ; p2 ?y }
        WHERE  { <subject_pattern> OPTIONAL { ?s p1 ?old_x } OPTIONAL { ?s p2 ?old_y } }
    properties: [(the property as written in SPARQL, e.g. "tss:from", the variable name, insert?)];
    insert False only deletes the old value (a property the node must not state any more).
    OPTIONAL: a value that is not there yet is simply added."""
    deletes = []
    inserts = []
    optionals = []
    for prop, name, insert in properties:
        deletes.append(f"{prop} ?old_{name}")
        if insert:
            inserts.append(f"{prop} ?{name}")
        optionals.append(f"  OPTIONAL {{ ?{subject_variable} {prop} ?old_{name} }}\n")
    text = TSS_PREFIX
    for prop, name, insert in properties:
        if prop.startswith("prov:"):
            text += PROV_PREFIX  # only when the update uses prov: (a summary's prov:generatedAtTime)
            break
    text += f"DELETE {{ ?{subject_variable} " + " ;\n    ".join(deletes) + " }\n"
    if inserts:
        text += f"INSERT {{ ?{subject_variable} " + " ;\n    ".join(inserts) + " }\n"
    text += "WHERE {\n" + subject_pattern + "".join(optionals) + "}"
    return text


# ---------------------------------------------------------------------------
# The window and the summaries
# ---------------------------------------------------------------------------

def time_literal(moment):
    """A moment as an xsd:dateTime literal in UTC, like 2021-03-04T01:15:01Z"""
    return Literal(query_helpers.time_text(moment), datatype=XSD.dateTime, normalize=False)


def new_window(graph, source, rows):
    """The source's new window: tss:from becomes the earlier of the current tss:from and the
    earliest point, tss:until the later of the current tss:until and the newest point plus one
    second (tss:until is not included). So the new point always fits, and the window never shrinks.
    Gives (from, until): the literal to state (the current one when it does not change), or None
    when the source does not state it (an open side stays open)."""
    node = source["node"]
    moments = []
    for row in rows:
        moment = value_reading.literal_time(row[source["timeField"]]) if source["timeField"] in row else None
        if moment is not None:
            moments.append(moment)
    current_from = value_reading.window_moment(graph, node, "from")    # a datetime in UTC, or None
    current_until = value_reading.window_moment(graph, node, "until")
    window_from = graph.value(node, TSS["from"])
    window_until = graph.value(node, TSS.until)
    if current_from is not None and len(moments) > 0 and min(moments) < current_from:
        window_from = time_literal(min(moments))
    if current_until is not None and len(moments) > 0:
        after_newest = max(moments) + timedelta(seconds=1)
        if after_newest > current_until:
            window_until = time_literal(after_newest)
    return window_from, window_until


def number_literal(value, stored):
    """A number of a summary as a literal with the datatype the summary used for it before
    (stored: its literal, or None): xsd:double unless it said otherwise. A value that does not fit
    an integer type (a mean of 2.5) becomes an xsd:double. The text is exact: repr() gives the
    shortest text that reads back as the same float (4752.156666666667)."""
    datatype = stored.datatype if isinstance(stored, Literal) and stored.datatype is not None else XSD.double
    if sql_box.duckdb_type(datatype) == "BIGINT":
        if float(value).is_integer():
            return Literal(str(int(value)), datatype=datatype, normalize=False)
        datatype = XSD.double
    if datatype == XSD.decimal:
        return Literal(format(Decimal(repr(float(value))), "f"), datatype=datatype, normalize=False)
    return Literal(repr(float(value)), datatype=datatype, normalize=False)


def summary_values(graph, source, field, rows, window_from, window_until):
    """The numbers of a field's stored summary, computed from the points in the new window
    [from, until), with the rule tss:ExcludeEmptyValues (the rule the bundled summaries state):
    count: the points with a value for the field; missing: the points without one;
    firstTime, lastTime: the first and last time with a value; for a numeric field also the
    observed minimum and maximum, the mean (not rounded) and the median.
    Gives {variable name: the number (an int, a float or a datetime), or None when there is none}.
    The same numbers as /query's summary and the SQL box's summary check."""
    time_field = source["timeField"]
    start = value_reading.literal_time(window_from) if window_from is not None else None
    end = value_reading.literal_time(window_until) if window_until is not None else None
    numeric = value_reading.is_numeric(field["datatype"])
    count = 0
    missing = 0
    moments = []
    numbers = []
    for row in rows:
        moment = value_reading.literal_time(row[time_field]) if time_field in row else None
        if moment is None or (start is not None and moment < start) or (end is not None and moment >= end):
            continue  # outside the window (a point without a time is outside every window)
        if field["node"] not in row:
            missing += 1
            continue
        count += 1
        moments.append(moment)
        if numeric:
            numbers.append(value_reading.literal_number(row[field["node"]], field["datatype"], graph))
    values = {
        "value_count": count,
        "missing": missing,
        "first_time": min(moments) if moments else None,
        "last_time": max(moments) if moments else None,
    }
    if numeric:
        values["observed_minimum"] = min(numbers) if numbers else None
        values["observed_maximum"] = max(numbers) if numbers else None
        values["mean"] = sum(numbers) / len(numbers) if numbers else None
        values["median"] = statistics.median(numbers) if numbers else None
    return values


def summary_update(graph, source, field, rows, window_from, window_until, now):
    """The SPARQL Update that recomputes one field's stored summary (tss:summary), and its bindings.
    Its window becomes the source's new window, and prov:generatedAtTime becomes now; the numbers
    are recomputed, but only those the summary already states (tss:missingValuePolicy is kept).
    None when the field has no summary."""
    summary = graph.value(field["node"], TSS.summary)
    if summary is None:
        return None
    computed = summary_values(graph, source, field, rows, window_from, window_until)
    # The prefixed name of each property in the update text (SUMMARY_ATTRIBUTES gives the names)
    written = {
        "from_time": "tss:from", "until_time": "tss:until", "value_count": "tss:count", "missing": "tss:missing",
        "first_time": "tss:firstTime", "last_time": "tss:lastTime", "observed_minimum": "tss:observedMinimum",
        "observed_maximum": "tss:observedMaximum", "mean": "tss:mean", "median": "tss:median",
        "generated_at": "prov:generatedAtTime",
    }
    properties = []
    bindings = {"field": field["node"], "summary": summary}
    for name, prop in source_reading.SUMMARY_ATTRIBUTES:
        if name not in written:
            continue  # tss:missingValuePolicy stays as it is
        stored = graph.value(summary, prop)
        if name == "from_time":
            value = window_from          # the source's new window (None: the source states none)
        elif name == "until_time":
            value = window_until
        elif name == "generated_at":
            value = time_literal(now)
        elif stored is None or name not in computed:
            continue  # a number the summary does not state is not added (and the numbers only for numeric fields)
        elif computed[name] is None:
            value = None                 # nothing to compute it from (no values): the old one goes
        elif name in ("value_count", "missing"):
            value = Literal(str(computed[name]), datatype=XSD.integer, normalize=False)
        elif name in ("first_time", "last_time"):
            value = time_literal(computed[name])
        else:
            value = number_literal(computed[name], stored)
        if value is None and stored is None:
            continue  # not stated, and nothing to state
        properties.append((written[name], name, value is not None))
        if value is not None:
            bindings[name] = value
    text = replace_values_update("summary", "  ?field tss:summary ?summary .\n", properties)
    return text, bindings


# ---------------------------------------------------------------------------
# Writing the file
# ---------------------------------------------------------------------------

def replace_file(temporary, path):
    """Put the temporary file in place of the endpoint's file, in one step (os.replace: a reader
    sees the old file or the new one, never half of it). On Windows that fails while another
    program has the file open (the backend sending it to the browser, a virus scanner), so it is
    tried again a few times."""
    for attempt in range(10):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def write_document(graph, path, base, name):
    """Write the graph as Turtle to the endpoint's file. exact_turtle writes every literal with
    its text as it is ("4797.72"^^xsd:double, not 4.79772e+03). The text goes to a temporary file
    in the same folder first; that file is read back and must give the same graph (isomorphic:
    the same triples, blank nodes may have other names), and only then it replaces the file."""
    text = exact_turtle(graph)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(text.encode("utf-8"))
            out.flush()
            os.fsync(out.fileno())
        with open(temporary, "rb") as written:
            again = read_exactly(written.read().decode("utf-8"), base, name)
        if not isomorphic(again, graph):
            raise HTTPException(status_code=500, detail=f"Writing {name} would change its RDF; the file was not changed.")
        replace_file(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------

@router.post("/endpoints/{endpoint_id}/points")
def add_point(endpoint_id, body: NewPoint, request: Request = None):
    """Add one point to the embedded points of a bundled endpoint (see the top of this file).
    Answers (JSON):
      200 {"conforms": true, "points": the number of points now, "from", "until", "modifiedAt",
           "committedUntil" (the source's new tss:from, tss:until, tss:modifiedAt and
           tss:committedUntil, as text; from and until are null when not stated),
           "sparql": the updates that ran, "report": the SHACL report as Turtle}
      422 {"conforms": false, "report": the SHACL report as Turtle, "sparql": the updates that ran
           on the graph in memory}: nothing was written
      400, 404, 409 {"detail": why}: nothing was checked or written
    Each update in "sparql" is {"update": its text exactly as it ran, "bindings": [{"variable",
    "value"}]}: the values given to it with initBindings, written as in Turtle (the tss:points
    literal shortened)."""
    with POINTS_LOCK:
        return append_point(endpoint_id, body.values, documents.browser_base(request))


def append_point(endpoint_id, values, base):
    """The work of add_point, while it holds POINTS_LOCK.
    base: the address of this backend as the browser sees it (for the shapes' address in the report)"""
    # 1. The file of a bundled endpoint (404 for any other id), read with every literal as it is.
    #    Its base IRI is its own file: address; documents without relative IRIs (see below) don't need any other.
    location = documents.endpoint_file(endpoint_id)
    path = documents.document_path(location)
    text, media_type = documents.read_document(location)
    graph = read_exactly(text, path.resolve().as_uri(), location)

    # 2. Only an embedded source whose data may change
    source_node = source_reading.find_source(graph)
    if embedded_mode(graph, source_node) is not True:
        raise HTTPException(status_code=400, detail="Points can only be added to embedded points (tss:embedded true). "
                                                    "This source's data is in an external file.")
    if source_reading.boolean_value(graph, source_node, TSS.mutable) is not True:
        raise HTTPException(status_code=409, detail="The source does not state tss:mutable true: its data never changes, "
                                                    "so no point can be added.")
    if has_relative_iris(graph, text, location):
        raise HTTPException(status_code=400, detail=f"{location} uses relative IRIs (like <#value> or <../data/x.csv>). "
                                                    "Writing it again would make them absolute, and so change them. "
                                                    "Write its IRIs in full to add points here.")
    source = source_reading.read_source(graph, documents.local_data_address(base))

    # 3. The new point, the points already there, and the new tss:points text
    new_row = check_values(graph, source, values)
    rows = check_existing_points(graph, source)
    keys = key_fields(graph, source)
    for row in rows:
        if same_key(row, new_row, keys, source["timeField"]):
            raise HTTPException(status_code=409, detail=f"There is already a point at {new_row[source['timeField']]} "
                                                        "(the same tss:key). Nothing was changed.")
    all_rows = rows + [new_row]
    all_rows.sort(key=lambda row: data_reading.point_time(row, source["timeField"]))
    new_points = points_text(all_rows, source)

    # 4. SPARQL Update, on the graph in memory. First the points ...
    sparql = []
    sparql.append(run_update(
        graph,
        TSS_PREFIX
        + "DELETE { ?source tss:points ?old }\n"
          "INSERT { ?source tss:points ?new }\n"
          "WHERE { ?source a tss:Source ; tss:points ?old }",
        {"source": source["node"], "new": Literal(new_points, datatype=RDF.JSON)},
        {"new": shortened_points(new_points)},
    ))
    # ... read again as RDF, as every tab reads them: the summaries are computed from these
    try:
        rows = data_reading.point_rows(graph, source)
    except ValueError as error:
        raise HTTPException(status_code=500, detail=f"The new points can't be read: {error}")

    # ... then the source's window, tss:modifiedAt and tss:committedUntil ...
    now = datetime.now(timezone.utc).replace(microsecond=0)
    window_from, window_until = new_window(graph, source, rows)
    properties = []
    bindings = {"source": source["node"]}
    if window_from is not None:
        properties.append(("tss:from", "from", True))
        bindings["from"] = window_from
    if window_until is not None:
        properties.append(("tss:until", "until", True))
        bindings["until"] = window_until
    properties.append(("tss:modifiedAt", "modifiedAt", True))
    bindings["modifiedAt"] = time_literal(now)
    # tss:committedUntil moves with the write, not with the new point's time
    properties.append(("tss:committedUntil", "committedUntil", True))
    bindings["committedUntil"] = time_literal(now)
    sparql.append(run_update(graph, replace_values_update("source", "  ?source a tss:Source .\n", properties),
                             bindings, {}))

    # ... and every stored summary
    for field in source["fields"]:
        update = summary_update(graph, source, field, rows, window_from, window_until, now)
        if update is not None:
            update_text, update_bindings = update
            shown = {"summary": f"(the tss:summary node of {term_text(graph, field['node'])})"}
            sparql.append(run_update(graph, update_text, update_bindings, shown))

    # 5. SHACL: the SHACL tab's own validation, with the default shapes (SHACL/embedded_shapes.ttl)
    shapes_path = shacl_report.default_shapes(graph)
    shapes = Graph().parse(shapes_path, format="turtle")
    try:
        report, report_node, point_triples = shacl_report.validation_report(graph, shapes)
    except ValueError as error:
        raise HTTPException(status_code=500, detail=f"Could not validate with {shapes_path.name}: {error}")
    # Which shapes were used: their address on this backend (as the SHACL tab's report says it)
    address = urljoin(base, quote(f"{paths.SHACL_FILES_PATH.lstrip('/')}/{shapes_path.name}"))
    report.add((report_node, DCTERMS.source, URIRef(address)))
    report_text = exact_turtle(report)
    conforms = report.value(report_node, SH.conforms)
    if not (isinstance(conforms, Literal) and conforms.toPython() is True):
        # Nothing is written: the file stays as it was
        return JSONResponse(status_code=422, content={"conforms": False, "report": report_text, "sparql": sparql})

    write_document(graph, path, path.resolve().as_uri(), location)
    return {
        "conforms": True,
        "points": len(rows),
        "from": str(window_from) if window_from is not None else None,
        "until": str(window_until) if window_until is not None else None,
        "modifiedAt": query_helpers.time_text(now),
        "committedUntil": query_helpers.time_text(now),
        "sparql": sparql,
        "report": report_text,
    }
