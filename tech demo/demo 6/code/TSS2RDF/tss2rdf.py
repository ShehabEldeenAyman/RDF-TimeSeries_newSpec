#!/usr/bin/env python3
"""Turn a TSS source (a tss:Source with tss:Field nodes) and its data into SOSA observations in Turtle.

Every row of the data gives one sosa:Observation for each field that has a
tss:observedProperty (except the time field). An observation says:

    [] a sosa:Observation ;
        sosa:observedProperty     <the field's tss:observedProperty> ;
        sosa:madeBySensor         <the field's tss:sensor> ;             (when stated)
        sosa:hasFeatureOfInterest <the field's tss:featureOfInterest> ;  (when stated)
        qudt:hasUnit              <the field's tss:unit> ;               (when stated)
        sosa:resultTime           "<the row's time (tss:timeField)>"^^xsd:dateTime ;
        sosa:hasSimpleResult      "<the cell>"^^<the field's tss:datatype> .

- Only rows inside the source's time window are used: tss:from included, tss:until not.
- An empty cell gives no observation.
- A cell that does not fit its field's tss:datatype stops the conversion, with the row number.
- Values keep their text exactly as it is in the data.
- Fields without tss:observedProperty (an id column, a quality code, ...) are not in the RDF.
- What the metadata says about the IRIs the observations use (a sensor, a feature, ...)
  is copied along, without the TSS vocabulary.

The data is a CSV file or a Parquet file (tss:dataFile, read as tss:format says), or the
JSON points embedded in the Turtle (tss:points). This file runs on its own: it needs rdflib,
and duckdb for Parquet. The dashboard imports generate(), so both give the same RDF.
"""
import argparse
import csv
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlparse
from urllib.request import url2pathname

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

TSS = Namespace("https://w3id.org/tss#")
SOSA = Namespace("http://www.w3.org/ns/sosa/")
QUDT = Namespace("http://qudt.org/schema/qudt/")

# How a cell must look for each datatype (XSD lexical forms). A datatype that is not
# in here (xsd:string, xsd:anyURI, ...) accepts any text.
INTEGER = r"[+-]?[0-9]+"
DECIMAL = r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)"
DOUBLE = r"[+-]?(([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?|INF)|NaN"
TIMEZONE = r"(Z|[+-][0-9]{2}:[0-9]{2})?"
LEXICAL_FORMS = {
    XSD.integer: INTEGER, XSD.int: INTEGER, XSD.long: INTEGER, XSD.short: INTEGER, XSD.byte: INTEGER,
    XSD.nonNegativeInteger: r"\+?[0-9]+", XSD.positiveInteger: r"\+?[0-9]*[1-9][0-9]*",
    XSD.nonPositiveInteger: r"(-[0-9]+|\+?0+)", XSD.negativeInteger: r"-[0-9]*[1-9][0-9]*",
    XSD.unsignedLong: r"\+?[0-9]+", XSD.unsignedInt: r"\+?[0-9]+",
    XSD.unsignedShort: r"\+?[0-9]+", XSD.unsignedByte: r"\+?[0-9]+",
    XSD.decimal: DECIMAL, XSD.double: DOUBLE, XSD.float: DOUBLE,
    XSD.boolean: r"true|false|1|0",
    XSD.dateTime: r"-?[0-9]{4,}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?" + TIMEZONE,
    XSD.date: r"-?[0-9]{4,}-[0-9]{2}-[0-9]{2}" + TIMEZONE,
}


# ---------------------------------------------------------------------------
# Reading the metadata
# ---------------------------------------------------------------------------

def single(graph, node, prop):
    """The one value of a property, or None. More than one value is an error."""
    values = list(graph.objects(node, prop))
    if len(values) > 1:
        raise ValueError(f"{node} states {len(values)} values for {prop}; only one is allowed.")
    return values[0] if values else None


def moment(text, what):
    """A date-time text as a datetime in UTC (a time without a timezone is taken as UTC)"""
    if re.fullmatch(LEXICAL_FORMS[XSD.dateTime], text) is None:
        raise ValueError(f"{what}: {text!r} is not an xsd:dateTime like 2021-03-03T23:45:00Z")
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{what}: {text!r} is not a valid date-time")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


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
        index = single(graph, field, TSS.columnIndex)
        label = single(graph, field, RDFS.label)
        fields.append({
            "node": field,
            "column": str(column) if column is not None else None,
            "index": int(index) if index is not None and str(index).isdigit() else None,
            "label": str(label) if label is not None else str(column),
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
    for field in fields:
        if field["node"] == time_field:
            time_column = field["column"]
    if time_column is None:
        raise ValueError("The tss:timeField must be one of the source's fields, with a tss:columnName.")

    window = {}
    for name in ("from", "until"):
        value = single(graph, node, TSS[name])
        window[name] = moment(str(value), f"tss:{name}") if value is not None else None

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
        "from": window["from"],
        "until": window["until"],
        "fields": fields,
    }


# ---------------------------------------------------------------------------
# Reading the data
# ---------------------------------------------------------------------------

def cell_text(value):
    """A value from a Parquet file or a JSON point as text, like it would be in a CSV file"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return json.dumps(value)  # numbers and booleans as JSON writes them: 4797.72, true


def read_rows(graph, source, data_path):
    """Every row of the data, one at a time, as (where, {column name: text}).
    where says where the row is, for error messages."""
    if source["embedded"]:
        literal = single(graph, source["node"], TSS.points)
        try:
            points = json.loads(str(literal)) if literal is not None else None
        except ValueError as error:
            raise ValueError(f"tss:points is not valid JSON: {error}")
        if not isinstance(points, list):
            raise ValueError("tss:points must be a JSON array of objects (tss:pointLayout tss:Object).")
        for number, point in enumerate(points, start=1):
            if not isinstance(point, dict):
                raise ValueError(f"Point {number} in tss:points is not a JSON object.")
            row = {}
            for key, value in point.items():
                row[key] = cell_text(value)
            yield f"point {number}", row
        return

    if source["format"] == TSS.Parquet:
        import duckdb  # only needed for Parquet files
        with duckdb.connect() as connection:
            result = connection.execute("SELECT * FROM read_parquet(?, hive_partitioning = false)", [str(data_path)])
            names = [column[0] for column in result.description]
            number = 0
            while True:
                batch = result.fetchmany(10000)
                if len(batch) == 0:
                    break
                for found in batch:
                    number += 1
                    row = {}
                    for index in range(len(names)):
                        row[names[index]] = cell_text(found[index])
                    yield f"Parquet row {number}", row
        return

    if source["format"] == TSS.CSV:
        if len(source["delimiter"]) != 1:
            raise ValueError(f"tss:delimiter must be one character, not {source['delimiter']!r}.")
        with open(data_path, encoding=source["encoding"], newline="") as f:
            reader = csv.reader(f, delimiter=source["delimiter"])
            # The name of each column: from the header line, or, without one (tss:header false),
            # the column name of the field with that tss:columnIndex
            names = {}
            if source["header"]:
                header = next(reader, [])
                for index in range(len(header)):
                    names[index] = header[index]
            else:
                for field in source["fields"]:
                    if field["index"] is not None and field["column"] is not None:
                        names[field["index"]] = field["column"]
            for cells in reader:
                row = {}
                for index in range(len(cells)):
                    row[names.get(index, f"column {index}")] = cells[index]
                yield f"CSV line {reader.line_num}", row
        return

    raise ValueError(f"Can't read data in tss:format {source['format']}; this converter reads tss:CSV, tss:Parquet and embedded points.")


# ---------------------------------------------------------------------------
# Writing the observations
# ---------------------------------------------------------------------------

def value_text(text, field, where, manager):
    """A cell as an RDF literal in Turtle, with the field's datatype. Stops when the text does not fit."""
    datatype = field["datatype"]
    if datatype == RDF.langString:
        if field["language"] is None:
            raise ValueError(f"Field {field['column']} is rdf:langString but has no tss:language.")
        return Literal(text, lang=str(field["language"])).n3()
    if datatype is None:
        return Literal(text).n3()
    form = LEXICAL_FORMS.get(datatype)
    if form is not None and re.fullmatch(form, text) is None:
        raise ValueError(f"{where}: column {field['column']} has {text!r}, which is not a valid {datatype.n3(manager)}.")
    return Literal(text).n3() + "^^" + datatype.n3(manager)


def generate(graph, data_path, target, preview_limit=200):
    """Write the observations of the source as Turtle to target (a file path), and return statistics
    plus a preview: the prefixes and the first preview_limit observations.
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

    # The IRIs the observations use: their sensors, features, ... and the datatypes of their values
    referenced = set()
    for field in measured:
        for key in ("observedProperty", "sensor", "featureOfInterest", "unit"):
            if isinstance(field[key], URIRef):
                referenced.add(field[key])
    used = set(referenced)
    for field in measured:
        if isinstance(field["datatype"], URIRef):
            used.add(field["datatype"])

    # The prefixes: sosa, qudt and xsd, plus the metadata's own prefixes that these IRIs use
    manager = Graph(bind_namespaces="none").namespace_manager
    manager.bind("sosa", SOSA)
    manager.bind("qudt", QUDT)
    manager.bind("xsd", XSD)
    for prefix, namespace in graph.namespaces():
        if prefix == "" or str(namespace) == str(TSS):
            continue
        for iri in used:
            if str(iri).startswith(str(namespace)):
                manager.bind(prefix, namespace)
                break

    # What every observation of a field says, whatever the row: written once per field
    fixed_lines = {}
    for field in measured:
        lines = ["a sosa:Observation"]
        for prop, key in [(SOSA.observedProperty, "observedProperty"), (SOSA.madeBySensor, "sensor"),
                          (SOSA.hasFeatureOfInterest, "featureOfInterest"), (QUDT.hasUnit, "unit")]:
            if field[key] is not None:
                lines.append(f"{prop.n3(manager)} {field[key].n3(manager)}")
        fixed_lines[field["column"]] = lines

    series = []
    for field in measured:
        series.append({"label": field["label"], "column": field["column"], "observations": 0})

    prefixes = ""
    for prefix, namespace in manager.namespaces():
        prefixes += f"@prefix {prefix}: <{namespace}> .\n"
    prefixes += "\n"

    observations = 0
    triples = 0
    preview = []
    time_column = source["timeColumn"]
    with open(target, "w", encoding="utf-8", newline="\n") as output:
        output.write(prefixes)
        for where, row in read_rows(graph, source, data_path):
            time = row.get(time_column, "")
            if time == "":
                raise ValueError(f"{where}: no time in column {time_column} (the tss:timeField).")
            when = moment(time, f"{where}, column {time_column}")
            if source["from"] is not None and when < source["from"]:
                continue
            if source["until"] is not None and when >= source["until"]:
                continue
            time_literal = Literal(time).n3() + "^^" + XSD.dateTime.n3(manager)

            for index, field in enumerate(measured):
                text = row.get(field["column"], "")
                if text == "":
                    continue  # an empty cell: no observation
                lines = fixed_lines[field["column"]] + [
                    f"{SOSA.resultTime.n3(manager)} {time_literal}",
                    f"{SOSA.hasSimpleResult.n3(manager)} {value_text(text, field, where, manager)}",
                ]
                block = "[] " + " ;\n    ".join(lines) + " .\n\n"
                output.write(block)
                observations += 1
                triples += len(lines)
                series[index]["observations"] += 1
                if len(preview) < preview_limit:
                    preview.append(block)

        # What the metadata says about the sensors, features, ... the observations point to,
        # one triple per line (an IRI without a prefix is written in full, <like this>)
        lines = []
        for resource in sorted(referenced):
            for prop, value in sorted(graph.predicate_objects(resource)):
                if str(prop).startswith(str(TSS)) or str(value).startswith(str(TSS)):
                    continue  # the TSS vocabulary stays in the metadata
                lines.append(f"{resource.n3(manager)} {prop.n3(manager)} {value.n3(manager)} .\n")
        if len(lines) > 0:
            text = "".join(lines) + "\n"
            output.write(text)
            preview.append(text)
            triples += len(lines)

    return {
        "turtle": prefixes + "".join(preview),
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

def convert(tss_path, output_path="observations.ttl"):
    """Convert one TSS Turtle file and its data into a Turtle file of observations.
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
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".tss2rdf-", suffix=".ttl", delete=False) as temporary:
        target = Path(temporary.name)
    try:
        result = generate(graph, data_path, target, preview_limit=0)
        target.replace(output)
    finally:
        target.unlink(missing_ok=True)
    return {
        "output": str(output),
        "data": str(data_path) if data_path else "tss:points",
        "observations": result["observations"],
        "triples": result["triples"],
        "series": result["series"],
        "bytes": output.stat().st_size,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Convert a TSS source and its data into SOSA observations in Turtle (the dashboard's rules).",
    )
    parser.add_argument("--tss", required=True, type=Path, help="The TSS Turtle file with one tss:Source.")
    parser.add_argument("-o", "--output", type=Path, default=Path("observations.ttl"),
                        help="The Turtle file to write (default: observations.ttl).")
    args = parser.parse_args(argv)
    try:
        stats = convert(args.tss, args.output)
    except Exception as error:
        parser.exit(1, f"Conversion failed: {error}\n")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
