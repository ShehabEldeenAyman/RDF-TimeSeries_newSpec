#!/usr/bin/env python3
"""Turn a TSS source (a tss:Source with tss:Field nodes) and its data into SOSA observations,
written as N-Triples (one triple per line) by rdflib.

Every row of the data gives one sosa:Observation for each field that has a
tss:observedProperty (except the time field). An observation says (here in Turtle):

    _:o1 rdf:type sosa:Observation ;
        dcterms:source            <the tss:Source> ;                     (when it has an IRI)
        sosa:observedProperty     <the field's tss:observedProperty> ;
        sosa:madeBySensor         <the field's tss:sensor> ;             (when stated)
        sosa:hasFeatureOfInterest <the field's tss:featureOfInterest> ;  (when stated)
        qudt:hasUnit              <the field's tss:unit> ;               (when stated)
        sosa:resultTime           "<the row's time (tss:timeField)>"^^xsd:dateTime ;
        sosa:hasSimpleResult      "<the value>"^^<the field's tss:datatype> .

- Every observation links back to its source (dcterms:source): only the link, not what the
  metadata says about the source.
- Only rows inside the source's time window are used: tss:from included, tss:until not.
- An empty value gives no observation.
- A value that does not fit its field's tss:datatype stops the conversion, and says where it is.
- CSV values keep their text exactly as it is in the file.
- Fields without tss:observedProperty (an id column, a quality code, ...) are not in the RDF.
- What the metadata says about the IRIs the observations use (a sensor, a feature, ...)
  is copied along, without the TSS vocabulary.
- All RDF syntax is written by rdflib's serializers: the observations as N-Triples, in batches
  of small graphs (see generate), and a preview of the first observations as Turtle.

The data is a CSV file or a Parquet file (tss:dataFile, read as tss:format says), or the
points embedded in the Turtle (tss:points). Embedded points are read as RDF: as JSON-LD,
with a context made from the source's fields (see points_context and lift_points).
This file runs on its own: it needs rdflib, and duckdb for Parquet. The dashboard imports
(through code/webapp/backend/rdf_generation.py) generate, lift_points and points_context,
TSS, LEXICAL_FORMS, TIME_DATATYPES and NORMALIZE_LOCK, and exact_turtle, column_index and
embedded_mode, so both give the same RDF.
"""
import argparse
import csv
from datetime import datetime, timezone
import io
import logging
import math
from pathlib import Path
import re
import sys
import tempfile
import threading
from urllib.parse import urlparse
from urllib.request import url2pathname

import rdflib
from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef
from rdflib.namespace import DCTERMS, XSD
from rdflib.plugins.serializers.turtle import TurtleSerializer

TSS = Namespace("https://w3id.org/tss#")
SOSA = Namespace("http://www.w3.org/ns/sosa/")
QUDT = Namespace("http://qudt.org/schema/qudt/")

# Keys of embedded points that are no field's tss:columnName get a predicate in here.
# (Without it, JSON-LD would drop them without a word.) The converter ignores them.
UNMAPPED = Namespace("urn:tss:unmapped:")

# A JSON key that starts with "@" (or with \u0040, the same character written as a JSON
# escape), such as "@context" or "@id": a JSON-LD keyword. Embedded points may not have
# them (see lift_points). The text between the quotes may hold escapes like \" (that is
# the (\\.|[^"\\])* part), and the key is the text that is followed by a colon.
KEYWORD_KEY = r'"(@|\\u0040)(\\.|[^"\\])*"[ \t\r\n]*:'

# The characters an IRI can't hold (Turtle does not allow them between < and >):
# the control characters, the space, and < > " { } | \ ^ `
NOT_IN_IRI = r'[\x00-\x20<>"{}|\\^`]'

# rdflib.NORMALIZE_LITERALS is one switch for the whole program. Two things write it:
# lift_points (see there), and pyshacl, whose validate() turns it off and on again while it
# reads the shapes. This lock makes them take turns: the lifts hold it here, and
# shacl_report.validation_report holds it around pyshacl's validate(); the web app's SHACL tab
# runs that same function.
# (The web app answers requests in threads, so a lift and a validation can run at the same time.)
NORMALIZE_LOCK = threading.Lock()

# How a value must look for each datatype (XSD lexical forms). A datatype that is not
# in here (xsd:string, xsd:anyURI, ...) accepts any text.
INTEGER = r"[+-]?[0-9]+"
DECIMAL = r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)"
DOUBLE = r"[+-]?(([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?|INF)|NaN"
DATE = r"-?[0-9]{4,}-[0-9]{2}-[0-9]{2}"
DATE_TIME = DATE + r"T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?"
TIMEZONE = r"(Z|[+-][0-9]{2}:[0-9]{2})"
LEXICAL_FORMS = {
    XSD.integer: INTEGER, XSD.int: INTEGER, XSD.long: INTEGER, XSD.short: INTEGER, XSD.byte: INTEGER,
    XSD.nonNegativeInteger: r"\+?[0-9]+", XSD.positiveInteger: r"\+?[0-9]*[1-9][0-9]*",
    XSD.nonPositiveInteger: r"(-[0-9]+|\+?0+)", XSD.negativeInteger: r"-[0-9]*[1-9][0-9]*",
    XSD.unsignedLong: r"\+?[0-9]+", XSD.unsignedInt: r"\+?[0-9]+",
    XSD.unsignedShort: r"\+?[0-9]+", XSD.unsignedByte: r"\+?[0-9]+",
    XSD.decimal: DECIMAL, XSD.double: DOUBLE, XSD.float: DOUBLE,
    XSD.boolean: r"true|false|1|0",
    XSD.dateTime: DATE_TIME + TIMEZONE + "?",       # the timezone may be left out
    XSD.dateTimeStamp: DATE_TIME + TIMEZONE,        # an xsd:dateTime that must have a timezone
    XSD.date: DATE + TIMEZONE + "?",
}

# The datatypes a time (a value of the tss:timeField) may have. xsd:dateTimeStamp is an
# xsd:dateTime with a timezone it can't leave out, so it is read as one; its observations
# get an xsd:dateTime sosa:resultTime, like the others.
TIME_DATATYPES = (XSD.dateTime, XSD.dateTimeStamp)


# ---------------------------------------------------------------------------
# Reading the metadata
# ---------------------------------------------------------------------------

def single(graph, node, prop):
    """The one value of a property, or None. More than one value is an error."""
    values = list(graph.objects(node, prop))
    if len(values) > 1:
        raise ValueError(f"{node} states {len(values)} values for {prop}; only one is allowed.")
    return values[0] if values else None


def read_time(text, where, datatype=XSD.dateTime):
    """A date-time text as an xsd:dateTime literal (its text kept as it is), and as a datetime
    in UTC to compare with the time window (a time without a timezone is taken as UTC).
    datatype: the time field's tss:datatype. An xsd:dateTimeStamp time must have a timezone;
    any other datatype is read as xsd:dateTime."""
    if datatype == XSD.dateTimeStamp:
        if re.fullmatch(LEXICAL_FORMS[XSD.dateTimeStamp], text) is None:
            raise ValueError(f"{where}: {text!r} is not an xsd:dateTimeStamp (a date-time with a timezone) "
                             "like 2021-03-03T23:45:00Z")
    if re.fullmatch(LEXICAL_FORMS[XSD.dateTime], text) is None:
        raise ValueError(f"{where}: {text!r} is not an xsd:dateTime like 2021-03-03T23:45:00Z")
    literal = Literal(text, datatype=XSD.dateTime, normalize=False)
    value = literal.toPython()  # a datetime, or the literal itself when rdflib can't read it
    if not isinstance(value, datetime):
        raise ValueError(f"{where}: {text!r} is not a valid date-time")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return literal, value


# The web app has its own window_moment (backend/value_reading.py): it answers an HTTP error
# instead of raising ValueError.
def window_moment(graph, node, name):
    """tss:from or tss:until of the source as a datetime in UTC, or None when it is not stated"""
    value = single(graph, node, TSS[name])
    if value is None:
        return None
    moment = None
    if isinstance(value, Literal):
        moment = value.toPython()  # rdflib reads an xsd:dateTime literal as a datetime
    if not isinstance(moment, datetime):
        raise ValueError(f'tss:{name} must be an xsd:dateTime like "2021-03-03T23:45:00Z"^^xsd:dateTime, not {value.n3()}.')
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def column_index(value):
    """A tss:columnIndex as a number (zero-based), or None when it is not a whole number of 0 or more"""
    if not isinstance(value, Literal):
        return None
    number = value.toPython()  # rdflib reads an xsd:integer literal as an int
    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
        return None
    return number


# Shared by the SHACL command line and the web app (they pick the shapes by it)
def embedded_mode(graph, source):
    """The source's tss:embedded as True or False, read by rdflib.
    None when it does not have exactly one boolean tss:embedded."""
    values = list(graph.objects(source, TSS.embedded))
    if len(values) != 1 or not isinstance(values[0], Literal):
        return None
    value = values[0].toPython()  # rdflib reads an xsd:boolean literal as True or False
    if not isinstance(value, bool):
        return None
    return value


# The web app has its own read_source (backend/source_reading.py): it answers HTTP errors and
# takes one value where this one refuses two.
def read_source(graph):
    """The source, and everything the conversion needs from it and its fields"""
    sources = list(graph.subjects(RDF.type, TSS.Source))
    if len(sources) != 1:
        raise ValueError(f"The metadata must describe exactly one tss:Source (found {len(sources)}).")
    node = sources[0]

    embedded = single(graph, node, TSS.embedded)
    if not isinstance(embedded, Literal) or not isinstance(embedded.toPython(), bool):
        raise ValueError("The source must state tss:embedded true or false.")
    time_field = single(graph, node, TSS.timeField)
    if time_field is None:
        raise ValueError("The source has no tss:timeField, so the observations would have no time.")

    fields = []
    for field in graph.objects(node, TSS.hasField):
        column = single(graph, field, TSS.columnName)
        fields.append({
            "node": field,
            "column": str(column) if column is not None else None,
            "index": column_index(single(graph, field, TSS.columnIndex)),
            "datatype": single(graph, field, TSS.datatype),
            "language": single(graph, field, TSS.language),
            "observedProperty": single(graph, field, TSS.observedProperty),
            "sensor": single(graph, field, TSS.sensor),
            "featureOfInterest": single(graph, field, TSS.featureOfInterest),
            "unit": single(graph, field, TSS.unit),
        })
    # Always the same order: by tss:columnIndex, then by column name
    fields.sort(key=lambda field: (field["index"] is None, field["index"] or 0, field["column"] or ""))

    time_column = None
    time_datatype = None
    for field in fields:
        if field["node"] == time_field:
            time_column = field["column"]
            time_datatype = field["datatype"]
    if time_column is None:
        raise ValueError("The tss:timeField must be one of the source's fields, with a tss:columnName.")

    delimiter = single(graph, node, TSS.delimiter)
    header = single(graph, node, TSS.header)
    encoding = single(graph, node, TSS.encoding)
    return {
        "node": node,
        "embedded": embedded.toPython(),
        "format": single(graph, node, TSS["format"]),  # TSS["format"]: TSS.format is Python's str.format
        "dataFile": single(graph, node, TSS.dataFile),
        "delimiter": str(delimiter) if delimiter is not None else ",",
        "header": header.toPython() is not False if header is not None else True,
        "encoding": str(encoding) if encoding is not None else "utf-8",
        "timeField": time_field,
        "timeColumn": time_column,
        "timeDatatype": time_datatype,  # xsd:dateTime, or xsd:dateTimeStamp (a timezone is required)
        "from": window_moment(graph, node, "from"),
        "until": window_moment(graph, node, "until"),
        "fields": fields,
    }


# ---------------------------------------------------------------------------
# Embedded points as RDF
# ---------------------------------------------------------------------------

def points_context(graph, source):
    """The JSON-LD context that turns the source's tss:points into RDF, made from its fields.
    Each field's tss:columnName is a key: its values get the field's IRI as predicate and
    the field's tss:datatype as datatype (tss:language instead, for rdf:langString).
    Keys that are no column name get a predicate under urn:tss:unmapped:."""
    context = {"@vocab": str(UNMAPPED)}
    for field in sorted(graph.objects(source, TSS.hasField)):
        column = single(graph, field, TSS.columnName)
        if column is None:
            continue  # a field without a column name has no key in the points
        name = str(column)
        if not isinstance(field, URIRef):
            raise ValueError(f"The field with tss:columnName {name!r} is a blank node, but the values of "
                             "embedded points need a field IRI as their predicate.")
        if name in context:
            raise ValueError(f"Two fields have tss:columnName {name!r}, so a point's {name!r} value "
                             "could belong to either.")
        if name.startswith("@"):
            # In the context, a name that starts with @ would be read as a JSON-LD keyword
            raise ValueError(f"The field {field} has tss:columnName {name!r}: a key that starts with @ "
                             "is a JSON-LD keyword, so it can't be the key of a value in tss:points.")

        term = {"@id": str(field)}
        datatype = single(graph, field, TSS.datatype)
        if datatype == RDF.langString:
            language = single(graph, field, TSS.language)
            if language is not None:
                term["@language"] = str(language)
        elif datatype is not None:
            if not isinstance(datatype, URIRef):
                raise ValueError(f"The tss:datatype of field {field} must be an IRI, not {datatype.n3()}.")
            term["@type"] = str(datatype)
        context[name] = term
    return context


def percent_encoded(found):
    """%20 for a space, %3C for <, ...: a character written as % and its number (for re.sub)"""
    return f"%{ord(found.group()):02X}"


def lift_points(graph, source, base=None):
    """The source's embedded points (tss:points) as an RDF graph: one blank node per point, and
    one triple per value, with the field's IRI as predicate and the value as a typed literal.
    The rdf:JSON literal is read by rdflib's JSON-LD parser, with points_context() as context.
    A value written as a JSON string keeps its text exactly ("...Z" stays "...Z").
    An empty value ("") gives no triple, just like null or a missing key.
    base: the IRI of the metadata document, for relative IRIs (points rarely have any);
    by default the source's IRI."""
    values = list(graph.objects(source, TSS.points))
    if len(values) == 0:
        raise ValueError("The source has no tss:points, so there are no embedded points to read.")
    if len(values) > 1:
        raise ValueError(f"The source has {len(values)} tss:points values; only one is allowed.")
    literal = values[0]
    if not isinstance(literal, Literal):
        raise ValueError(f"tss:points must be a literal typed rdf:JSON, not {literal.n3()}.")
    if literal.datatype != RDF.JSON:
        datatype = literal.datatype
        if datatype is None:  # rdflib leaves out the datatype of plain and language-tagged strings
            datatype = RDF.langString if literal.language is not None else XSD.string
        raise ValueError(f"tss:points must be a literal typed rdf:JSON, but it is typed <{datatype}>.")

    # Points hold values only. A JSON-LD keyword as a key would change how they are read:
    # "@context" would replace the context made from the fields (and make the parser download
    # the address it names), "@id" would turn a point into some other resource, and so on.
    # So a key that starts with @ is refused. This is a safety check on the literal's text,
    # not a way of reading the points: it only looks for such a key (KEYWORD_KEY), and the
    # points themselves are read by the JSON-LD parser below.
    text = str(literal)
    if re.search(KEYWORD_KEY, text) is not None:
        raise ValueError("tss:points has a JSON key that starts with @ (a JSON-LD keyword such as "
                         "@context or @id). Points only hold values, keyed by the fields' tss:columnName.")

    context = points_context(graph, source)
    if base is None and isinstance(source, URIRef):
        base = str(source)
    points = Graph()
    # rdflib normally rewrites the text of a literal while it makes it: "yes"^^xsd:boolean
    # becomes "false", " 12 " and "1_000" as xsd:double become "12.0" and "1000.0", and
    # "...Z" becomes "...+00:00". Then SHACL could not see the wrong texts, and the
    # browser's lift (which keeps them) would disagree. With rdflib.NORMALIZE_LITERALS
    # False, every literal keeps its text. rdflib reads this switch each time it makes a
    # Literal (Literal.__new__ in rdflib/term.py), and the JSON-LD parser makes its
    # literals with Literal(value, datatype=...), so the switch reaches it.
    # The switch is one for the whole program: it is turned off only during this parse,
    # and put back afterwards, also when the parse fails (finally). The lock (NORMALIZE_LOCK)
    # makes the writers of the switch take turns: lifts that run at the same time, and
    # pyshacl's validate(), which switches it off and on again itself. That only works when
    # every caller of validate() holds the lock too, as the web app and shacl_report.py do.
    # This is safe enough: the parse of the points takes milliseconds, and a literal that
    # another thread makes in the meantime only keeps its text as it was written.
    with NORMALIZE_LOCK:
        normalize = rdflib.NORMALIZE_LITERALS
        rdflib.NORMALIZE_LITERALS = False
        try:
            # The literal's text, exactly as it is, read as JSON-LD with the context given separately
            points.parse(data=text, format="json-ld", context=context, base=base)
        except Exception as error:  # bad JSON, a JSON-LD error, ...: the parser says what is wrong
            raise ValueError(f"tss:points could not be read as JSON-LD ({type(error).__name__}: {error}). "
                             "It must be a JSON array of objects, keyed by the fields' tss:columnName.")
        finally:
            rdflib.NORMALIZE_LITERALS = normalize

    for subject, predicate, value in list(points):
        # "" means no value, like null or a missing key (and like an empty cell in a CSV file)
        if isinstance(value, Literal) and str(value) == "":
            points.remove((subject, predicate, value))
            continue
        # A key that is no field's column name, with a space in it (or another character an
        # IRI can't hold), gives a predicate like <urn:tss:unmapped:Some Key>, which rdflib
        # can't write as Turtle (the SHACL report, ...). Such characters are percent-encoded:
        # <urn:tss:unmapped:Some%20Key>. (The field IRIs come from the Turtle, so they never change.)
        if not isinstance(predicate, URIRef):
            continue
        valid = URIRef(re.sub(NOT_IN_IRI, percent_encoded, str(predicate)))
        if valid != predicate:
            points.remove((subject, predicate, value))
            points.add((subject, valid, value))
    return points


# rdflib writes literals of these datatypes in Turtle in a short form made from their value:
# true, 12, 1.2e+01. For a text that is not in the XSD form, that gives broken Turtle ("yes"
# as an xsd:boolean is written as yes) or another text (" 12 " as an xsd:double becomes 1.2e+01).
# The lifted points keep such texts (see lift_points), so a graph with them (like a SHACL
# report, which copies the wrong values) is written with ExactTurtleSerializer.
SHORT_FORM_DATATYPES = (XSD.integer, XSD.boolean, XSD.double, XSD.decimal)


class ExactTurtleSerializer(TurtleSerializer):
    """rdflib's Turtle writer, except that literals of SHORT_FORM_DATATYPES are written in full,
    "text"^^datatype (by rdflib's own term.n3()), so their text stays exactly as it is"""

    def label(self, node, position):
        if isinstance(node, Literal) and node.datatype in SHORT_FORM_DATATYPES:
            return node.n3(self.store.namespace_manager)
        return super().label(node, position)


def exact_turtle(graph):
    """A graph as Turtle text, written by rdflib, with the text of every literal kept exactly
    (see ExactTurtleSerializer): "yes"^^xsd:boolean, " 12 "^^xsd:double, "4797.72"^^xsd:double"""
    stream = io.BytesIO()
    ExactTurtleSerializer(graph).serialize(stream, encoding="utf-8")
    return stream.getvalue().decode("utf-8")


def read_points(graph, source):
    """The embedded points as rows, like read_rows makes them, in time order.
    The points are read as RDF first (lift_points), and each row is made from a point's triples."""
    points = lift_points(graph, source["node"])
    columns = {}  # the column name of each field, for error messages
    for field in source["fields"]:
        columns[field["node"]] = field["column"]
    time_field = source["timeField"]

    # A value can itself be a JSON object (a node inside a point); such nodes are not points
    inner = set()
    for value in points.objects():
        if not isinstance(value, Literal):
            inner.add(value)

    found = []
    for point in points.subjects(unique=True):
        if point in inner:
            continue
        row = {}
        for predicate, value in points.predicate_objects(point):
            if predicate not in columns:
                continue  # a key that is no field's tss:columnName (urn:tss:unmapped:...)
            if not isinstance(value, Literal):
                raise ValueError(f"A point in tss:points has a JSON object as its {columns[predicate]!r} value, not a value.")
            if predicate in row:
                raise ValueError(f"A point in tss:points has more than one {columns[predicate]!r} value.")
            row[predicate] = str(value)  # the literal's lexical form, like the text of a CSV cell
        # A point with no value for any field (only keys that are no column name, or only empty
        # values) has nothing to convert: it is skipped, as the browser does (SHACL's P6 still
        # warns about its keys)
        if len(row) == 0:
            continue
        # A point without a time, like a CSV row with an empty time cell, stops the conversion
        if time_field not in row:
            raise ValueError(f"A point in tss:points has no time: no {source['timeColumn']!r} value (the tss:timeField).")
        time_literal, when = read_time(row[time_field], f"The point at {row[time_field]}", source["timeDatatype"])
        found.append((when, row))

    # Points have no order in RDF, so put them in time order
    found.sort(key=lambda point: point[0])
    rows = []
    for when, row in found:
        rows.append((f"The point at {row[time_field]}", row))
    return rows


# ---------------------------------------------------------------------------
# Reading the data
# ---------------------------------------------------------------------------

# The web app has its own cell_text (backend/data_reading.py) for its previews and tables.
def cell_text(value):
    """A value from a Parquet file as text, like it would be in a CSV file. rdflib writes numbers,
    booleans, decimals, times, ... in their XSD form: 4797.72, true, 1.50, 12:30:00"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")  # UTC as ...Z, like the CSV files
    if isinstance(value, float) and math.isnan(value):
        return "NaN"  # (rdflib would write nan and inf, which are not XSD forms)
    if isinstance(value, float) and math.isinf(value):
        return "INF" if value > 0 else "-INF"
    if isinstance(value, bytes):
        return value.hex().upper()  # binary data in the xsd:hexBinary form
    return str(Literal(value))  # the lexical form rdflib gives a Python value


def read_rows(graph, source, data_path):
    """Every row of the data, one at a time, as (where, {field: text}): the text of each field's
    value in that row, by the field's node. where says where the row is, for error messages."""
    if source["embedded"]:
        for where, row in read_points(graph, source):
            yield where, row
        return

    if source["format"] == TSS.Parquet:
        import duckdb  # only needed for Parquet files
        with duckdb.connect() as connection:
            result = connection.execute("SELECT * FROM read_parquet(?, hive_partitioning = false)", [str(data_path)])
            names = [column[0] for column in result.description]
            # Where each field's value is: the Parquet column with its tss:columnName
            positions = {}
            for field in source["fields"]:
                if field["column"] in names:
                    positions[field["node"]] = names.index(field["column"])
            number = 0
            while True:
                batch = result.fetchmany(10000)
                if len(batch) == 0:
                    break
                for found in batch:
                    number += 1
                    row = {}
                    for field, index in positions.items():
                        row[field] = cell_text(found[index])
                    yield f"Parquet row {number}", row
        return

    if source["format"] == TSS.CSV:
        if len(source["delimiter"]) != 1:
            raise ValueError(f"tss:delimiter must be one character, not {source['delimiter']!r}.")
        with open(data_path, encoding=source["encoding"], newline="") as f:
            reader = csv.reader(f, delimiter=source["delimiter"])
            # Where each field's cell is: the column with its tss:columnName in the header line,
            # or, without one (tss:header false), the column at its tss:columnIndex
            positions = {}
            if source["header"]:
                header = next(reader, [])
                for field in source["fields"]:
                    if field["column"] in header:
                        positions[field["node"]] = header.index(field["column"])
            else:
                for field in source["fields"]:
                    if field["index"] is not None:
                        positions[field["node"]] = field["index"]
            for cells in reader:
                row = {}
                for field, index in positions.items():
                    if index < len(cells):
                        row[field] = cells[index]
                yield f"CSV line {reader.line_num}", row
        return

    raise ValueError(f"Can't read data in tss:format {source['format']}; this converter reads tss:CSV, tss:Parquet and embedded points.")


# ---------------------------------------------------------------------------
# Writing the observations
# ---------------------------------------------------------------------------

def value_literal(text, field, where, manager):
    """A value as an RDF literal with the field's datatype, its text kept as it is.
    Stops when the text does not fit the datatype."""
    datatype = field["datatype"]
    if datatype == RDF.langString:
        if field["language"] is None:
            raise ValueError(f"Field {field['column']} is rdf:langString but has no tss:language.")
        return Literal(text, lang=str(field["language"]))
    if datatype is None:
        return Literal(text)
    form = LEXICAL_FORMS.get(datatype)
    if form is not None and re.fullmatch(form, text) is None:
        raise ValueError(f"{where}: column {field['column']} has {text!r}, which is not a valid {datatype.n3(manager)}.")
    # normalize=False: rdflib would otherwise rewrite the text, e.g. "3.0E1" as "30.0"
    return Literal(text, datatype=datatype, normalize=False)


def describe(graph, resources):
    """What the metadata says about these resources, without the TSS vocabulary, as a graph.
    It holds each resource's concise bounded description (graph.cbd), so the blank nodes
    a description points to come along."""
    # Leave out the TSS vocabulary first (how the data is stored stays in the metadata),
    # so a description never points to a blank node that is left out afterwards
    without_tss = Graph()
    for subject, predicate, value in graph:
        if predicate in TSS:  # (a Namespace holds every IRI that starts with it)
            continue
        if isinstance(value, URIRef) and value in TSS:
            continue  # e.g. rdf:type tss:Field
        without_tss.add((subject, predicate, value))

    description = Graph()
    for resource in resources:
        for triple in without_tss.cbd(resource):
            description.add(triple)
    return with_fresh_blank_nodes(description)


def with_fresh_blank_nodes(graph):
    """A copy of a graph in which every blank node has a new label of its own (rdflib's BNode()).
    The observations are blank nodes labelled o1, o2, ... (see generate). A blank node copied
    from the metadata keeps the label the parser gave it, and some parsers keep the label of
    the document (a JSON-LD "@id": "_:o1", or a graph made in code): such a node would merge
    with an observation in the output. A new label can never be o1, o2, ..."""
    fresh = {}  # the blank node in the metadata -> its new one
    copy = Graph()
    for triple in graph:
        terms = []
        for term in triple:
            if isinstance(term, BNode):
                if term not in fresh:
                    fresh[term] = BNode()  # rdflib makes a new, unique label, like N3f2a...
                term = fresh[term]
            terms.append(term)
        copy.add((terms[0], terms[1], terms[2]))
    return copy


# The observations are written in batches: this many observations go into one small graph,
# which rdflib then writes to the file (see generate)
BATCH_SIZE = 10000


def batch_graph():
    """An empty graph for one batch of observations. "SimpleMemory" is rdflib's plain in-memory
    store: it fills faster than the default store, which also keeps track of named graphs."""
    return Graph(store="SimpleMemory")


def write_batch(batch, output):
    """Write a batch of observations to the output file (opened as bytes) with rdflib's N-Triples
    writer: one triple per line, every IRI in full, and every literal as "text"^^<datatype>, with
    its text exactly as it is. N-Triples has no prefixes and nothing that carries over from one
    line to the next, so the batches can simply be written one after the other into one file."""
    batch.serialize(destination=output, format="nt", encoding="utf-8")


def generate(graph, data_path, target, preview_limit=200):
    """Write the observations of the source as N-Triples to target (a file path), and return
    statistics plus a preview as Turtle: the first preview_limit observations, and what the
    metadata says about the sensors, features, ... they point to.
    data_path: the local data file (CSV or Parquet), or None for embedded points."""
    source = read_source(graph)
    measured = []  # the fields that become observations
    for field in source["fields"]:
        if field["observedProperty"] is not None and field["node"] != source["timeField"]:
            if field["column"] is None:
                raise ValueError(f"Field {field['node']} has a tss:observedProperty but no tss:columnName.")
            measured.append(field)
    if len(measured) == 0:
        raise ValueError("No field has a tss:observedProperty, so there is nothing to turn into observations.")
    if not source["embedded"] and data_path is None:
        raise ValueError("The source keeps its data in a file (tss:dataFile), but no data file was given.")

    # The IRIs the observations point to: their properties, sensors, features and units,
    # and what the metadata says about them
    referenced = set()
    for field in measured:
        for key in ("observedProperty", "sensor", "featureOfInterest", "unit"):
            if isinstance(field[key], URIRef):
                referenced.add(field[key])
    description = describe(graph, referenced)

    # Every IRI in the output (and every datatype), to choose the prefixes: the source's too,
    # which every observation links back to (a blank node source gets no link)
    used = set(referenced)
    source_link = source["node"] if isinstance(source["node"], URIRef) else None
    if source_link is not None:
        used.add(source_link)
    for field in measured:
        if isinstance(field["datatype"], URIRef):
            used.add(field["datatype"])
    for triple in description:
        for term in triple:
            if isinstance(term, URIRef):
                used.add(term)
            if isinstance(term, Literal) and term.datatype is not None:
                used.add(term.datatype)

    # The preview is a small graph of its own, written as Turtle at the end. Its prefixes:
    # rdf, dcterms, sosa, qudt and xsd, plus the metadata's own prefixes that these IRIs use.
    # (rdflib writes only the prefixes the preview really uses.)
    preview = Graph(bind_namespaces="none")
    preview.bind("rdf", RDF)
    preview.bind("dcterms", DCTERMS)
    preview.bind("sosa", SOSA)
    preview.bind("qudt", QUDT)
    preview.bind("xsd", XSD)
    for prefix, namespace in graph.namespaces():
        if prefix == "" or str(namespace) == str(TSS):
            continue
        for iri in used:
            if str(iri).startswith(str(namespace)):
                preview.bind(prefix, namespace)
                break
    manager = preview.namespace_manager  # for the prefixed names in error messages (xsd:double)

    # What every observation of a field says, whatever the row: made once per field,
    # as (predicate, object) pairs. The first ones: its type, and the link back to its source.
    fixed = {}
    for field in measured:
        pairs = [(RDF.type, SOSA.Observation)]
        if source_link is not None:
            pairs.append((DCTERMS.source, source_link))
        for prop, key in [(SOSA.observedProperty, "observedProperty"), (SOSA.madeBySensor, "sensor"),
                          (SOSA.hasFeatureOfInterest, "featureOfInterest"), (QUDT.hasUnit, "unit")]:
            if field[key] is not None:
                pairs.append((prop, field[key]))
        fixed[field["node"]] = pairs
    result_time = SOSA.resultTime
    simple_result = SOSA.hasSimpleResult

    # The observations per field, by the field's IRI
    series = []
    for field in measured:
        series.append({"field": str(field["node"]), "observations": 0})

    # Each observation is a blank node: _:o001, _:o002, ... The counter gives every observation
    # a blank node of its own in the whole file, so observations in different batches never
    # merge. The blank nodes of the copied descriptions got new labels (see describe), so they
    # never merge with an observation either. (The zeros only keep the Turtle preview in data
    # order: rdflib's Turtle writer sorts blank nodes by name, and "o010" comes after "o009",
    # while "o10" would come before "o9".)
    digits = len(str(preview_limit))

    # The observations go into a small graph (a batch). Every BATCH_SIZE observations, rdflib
    # writes that graph to the file (write_batch) and a new batch starts. So rdflib writes all
    # the RDF syntax, and a big data file never has to fit in memory.
    observations = 0
    triples = 0
    batch = batch_graph()
    in_batch = 0  # the observations in the batch
    time_field = source["timeField"]
    time_column = source["timeColumn"]
    with open(target, "wb") as output:  # "wb": rdflib writes bytes (UTF-8 text)
        for where, row in read_rows(graph, source, data_path):
            time = row.get(time_field, "")
            if time == "":
                raise ValueError(f"{where}: no time in column {time_column} (the tss:timeField).")
            time_literal, when = read_time(time, f"{where}, column {time_column}", source["timeDatatype"])
            if source["from"] is not None and when < source["from"]:
                continue
            if source["until"] is not None and when >= source["until"]:
                continue

            for index, field in enumerate(measured):
                text = row.get(field["node"], "")
                if text == "":
                    continue  # an empty value: no observation
                value = value_literal(text, field, where, manager)
                observations += 1
                observation = BNode(f"o{observations:0{digits}d}")

                # The observation's triples: what every observation of this field says,
                # plus its time and its value
                statements = []
                for prop, obj in fixed[field["node"]]:
                    statements.append((observation, prop, obj))
                statements.append((observation, result_time, time_literal))
                statements.append((observation, simple_result, value))
                for statement in statements:
                    batch.add(statement)
                    if observations <= preview_limit:
                        preview.add(statement)
                triples += len(statements)
                series[index]["observations"] += 1

                in_batch += 1
                if in_batch == BATCH_SIZE:
                    write_batch(batch, output)
                    batch = batch_graph()
                    in_batch = 0

        # What the metadata says about the sensors, features, ... the observations point to:
        # in the last batch, and in the preview
        for statement in description:
            batch.add(statement)
            preview.add(statement)
        triples += len(description)
        write_batch(batch, output)

    return {
        "preview": exact_turtle(preview),  # Turtle, every literal's text kept (see exact_turtle)
        "observations": observations,
        "triples": triples,
        "series": series,
        "previewObservations": min(observations, preview_limit),
        "previewTruncated": observations > preview_limit,
        "bytes": Path(target).stat().st_size,
    }


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def convert(tss_path, output_path="observations.nt"):
    """Convert one TSS Turtle file and its data into an N-Triples file of observations.
    The data file is the source's tss:dataFile, read relative to the TSS file."""
    tss = Path(tss_path).expanduser().resolve()
    if not tss.is_file():
        raise ValueError(f"TSS file not found: {tss}")
    graph = Graph().parse(tss, format="turtle", publicID=tss.as_uri())
    source = read_source(graph)

    data_path = None
    if not source["embedded"]:
        data_iri = source["dataFile"]
        if not isinstance(data_iri, URIRef) or not str(data_iri).startswith("file:"):
            raise ValueError(f"tss:dataFile must point to a local file, not {data_iri}")
        data_path = Path(url2pathname(urlparse(str(data_iri)).path))
        if not data_path.is_file():
            raise ValueError(f"Data file not found: {data_path}")

    output = Path(output_path).expanduser().resolve()
    if output in (tss, data_path):
        raise ValueError("The output must be another file than the TSS file and the data file.")

    # Write next to the output and only replace it when everything worked,
    # so bad data never leaves half a graph or overwrites an earlier result
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".tss2rdf-", suffix=".nt", delete=False) as temporary:
        target = Path(temporary.name)
    try:
        result = generate(graph, data_path, target, preview_limit=0)
        target.replace(output)
    finally:
        target.unlink(missing_ok=True)
    return {
        "output": str(output),
        "data": str(data_path) if data_path else None,  # None: the points embedded in the TSS file
        "observations": result["observations"],
        "triples": result["triples"],
        "series": result["series"],
        "bytes": output.stat().st_size,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Convert a TSS source and its data into SOSA observations in N-Triples (the dashboard's rules).",
    )
    parser.add_argument("--tss", required=True, type=Path, help="The TSS Turtle file with one tss:Source.")
    parser.add_argument("-o", "--output", type=Path, default=Path("observations.nt"),
                        help="The N-Triples file to write (default: observations.nt).")
    args = parser.parse_args(argv)
    # rdflib logs a long traceback for every value that does not fit its datatype (also for
    # an empty "" number in tss:points); the converter says itself which value is wrong
    logging.getLogger("rdflib.term").setLevel(logging.ERROR)
    try:
        stats = convert(args.tss, args.output)
    except Exception as error:
        parser.exit(1, f"Conversion failed: {error}\n")

    # The statistics, for people to read
    if stats["data"] is None:
        print("Data:         the points embedded in the TSS file (tss:points)")
    else:
        print(f"Data:         {stats['data']}")
    print(f"Output:       {stats['output']}")
    print(f"Observations: {stats['observations']:,}")
    print(f"Triples:      {stats['triples']:,}")
    print(f"Size:         {stats['bytes']:,} bytes")
    print("Observations per field:")
    for series in stats["series"]:
        print(f"  {series['field']}: {series['observations']:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
