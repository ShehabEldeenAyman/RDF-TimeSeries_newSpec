#!/usr/bin/env python3
"""Convert embedded or CSV-backed RDFTSS files into domain Turtle observations.

This file runs independently of the dashboard. The dashboard imports its
conversion functions to keep both entry points' RDF semantics identical.
"""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from csvw.metadata import Datatype, NAMESPACES, Table
from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD
from rdflib.plugins.shared.jsonld.context import Context

TSS = Namespace("https://w3id.org/tss#")


def csvw_delimiter(metadata, described_by=None):
    """Use the CSVW delimiter, or comma when the dialect/field is omitted."""
    label = f"The CSVW file {described_by}" if described_by is not None else "The CSVW"
    dialect = metadata.get("dialect", {})
    if not isinstance(dialect, dict):
        raise ValueError(f"{label} must define dialect as an object.")
    delimiter = dialect.get("delimiter", ",")
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in "\r\n\0":
        raise ValueError(f"{label} must define dialect.delimiter as one character (for example ';', ',' or a tab).")
    return delimiter


def single(graph, node, prop, required=False):
    values = list(graph.objects(node, prop))
    if len(values) > 1 or required and not values:
        raise ValueError(f"{node} must state {'exactly' if required else 'at most'} one {prop}.")
    return values[0] if values else None


def effective(graph, snippet, template, prop):
    own = single(graph, snippet, prop)
    return own if own is not None else single(graph, template, prop)


def read_json(literal, name):
    if not isinstance(literal, Literal):
        raise ValueError(f"{name} must be a JSON literal.")
    try:
        return json.loads(str(literal))
    except (ValueError, TypeError) as error:
        raise ValueError(f"Invalid JSON in {name}: {error}") from error


# The type of a group's data file: dcterms:format on the group, from the EU file-type
# vocabulary or as an IANA media type. A group without dcterms:format keeps the original
# behaviour: its data file is a CSV described by CSVW.
DCTERMS_FORMAT = URIRef("http://purl.org/dc/terms/format")
FILE_TYPES = {
    "http://publications.europa.eu/resource/authority/file-type/CSV": "csv",
    "http://publications.europa.eu/resource/authority/file-type/PARQUET": "parquet",
    "http://www.iana.org/assignments/media-types/text/csv": "csv",
    "https://www.iana.org/assignments/media-types/text/csv": "csv",
    "http://www.iana.org/assignments/media-types/application/vnd.apache.parquet": "parquet",
    "https://www.iana.org/assignments/media-types/application/vnd.apache.parquet": "parquet",
}


def data_format(graph, group):
    """'csv' or 'parquet' for the group's data file, or None when its dcterms:format
    names another file type or several. Without dcterms:format the file is CSV."""
    values = list(graph.objects(group, DCTERMS_FORMAT))
    if not values:
        return "csv"
    if len(values) > 1:
        return None
    return FILE_TYPES.get(str(values[0]))


def parquet_column(names, wanted):
    """The Parquet column that a TSS column binding (tss:timeColumn, tss:valueColumn) means.
    An exact match wins. Otherwise one column whose name differs only in upper/lower case
    is accepted, as SQL engines such as DuckDB do. None when no column (or several) match."""
    if wanted is None:
        return None
    wanted = str(wanted)
    if wanted in names:
        return wanted
    matches = [name for name in names if name.lower() == wanted.lower()]
    return matches[0] if len(matches) == 1 else None


def generate_parquet(source, load_files, target, preview_limit=200):
    """load_files(group) returns (local Parquet path, original data file IRI)."""
    raise NotImplementedError("Parquet RDF generation is not implemented yet.")


def generate_embedded(graph, base):
    groups = sorted(set(graph.subjects(RDF.type, TSS.SnippetGroup)), key=str)
    if not groups:
        raise ValueError("No tss:SnippetGroup found.")
    members = []
    for group in groups:
        flag = single(graph, group, TSS.embedded, required=True)
        if not isinstance(flag, Literal) or flag.toPython() is not True:
            raise ValueError("RDF generation currently supports only groups with tss:embedded true.")
        template = single(graph, group, TSS.snippetTemplate, required=True)
        snippets = sorted(set(graph.objects(group, TSS.hasSnippet)), key=str)
        if not snippets:
            raise ValueError(f"{group} has no member snippets.")
        for snippet in snippets:
            if (snippet, RDF.type, TSS.Snippet) not in graph:
                raise ValueError(f"{snippet} is not described as a tss:Snippet.")
            if len(set(graph.subjects(TSS.hasSnippet, snippet))) != 1:
                raise ValueError(f"{snippet} must belong to exactly one group.")
            members.append((snippet, template))

    output = Graph(bind_namespaces="none")
    for prefix, namespace in graph.namespaces():
        if not str(namespace).startswith(str(TSS)) and prefix != "tss":
            output.bind(prefix, namespace)
    controls = (set(groups) | {template for _, template in members}
                | set(graph.subjects(RDF.type, TSS.Snippet)) | set(graph.objects(None, TSS.about)))
    referenced_resources = set()

    def is_tss(term):
        return (isinstance(term, URIRef) and str(term).startswith(str(TSS))
                or isinstance(term, Literal) and term.datatype is not None
                and str(term.datatype).startswith(str(TSS)))

    def add_domain_triple(subject, predicate, value):
        if any(is_tss(term) for term in (subject, predicate, value)):
            return
        if any(term in controls for term in (subject, predicate, value)):
            return
        output.add((subject, predicate, value))
        if isinstance(value, (BNode, URIRef)):
            referenced_resources.add(value)

    series = []
    for snippet, template in members:
        points = read_json(single(graph, snippet, TSS.points, required=True), f"tss:points on {snippet}")
        if not isinstance(points, list) or any(
            not isinstance(point, dict) or "time" not in point or "value" not in point
            for point in points
        ):
            raise ValueError(f"tss:points on {snippet} must be an array of objects with time and value keys.")
        context = read_json(effective(graph, snippet, template, TSS.context), f"tss:context on {snippet}")
        if isinstance(context, dict) and "@context" in context:
            context = context["@context"]
        try:
            active = Context(context, base=base)
            for key in ("time", "value"):
                predicate = active.expand(key)
                if not predicate or predicate.startswith("@") or ":" not in predicate:
                    raise ValueError(f"tss:context on {snippet} does not map the {key!r} key to an RDF property.")
                if predicate.startswith(str(TSS)):
                    raise ValueError(f"The {key!r} mapping on {snippet} uses the TSS vocabulary. Map it to a domain RDF property to generate a TSS-free graph.")
        except ValueError:
            raise
        except Exception as error:
            raise ValueError(f"Could not read the JSON-LD context on {snippet}: {error}") from error

        point_type = effective(graph, snippet, template, TSS.pointType)
        if not isinstance(point_type, URIRef):
            raise ValueError(f"{snippet} needs a tss:pointType class IRI, own or inherited.")
        if is_tss(point_type):
            raise ValueError(f"The point type on {snippet} must be a domain class, not a TSS class.")
        about = effective(graph, snippet, template, TSS.about)
        if not isinstance(about, (URIRef, BNode)):
            raise ValueError(f"{snippet} needs a tss:about node, own or inherited.")

        # A temporary wrapper identifies only the top-level point. Decorating all
        # JSON-LD subjects would also decorate nested results and other resources.
        root_property = URIRef(f"urn:rdftss-generation:{uuid4()}:root")
        for index, point in enumerate(points, 1):
            wrapper = URIRef(f"urn:rdftss-generation:{uuid4()}:wrapper")
            document = {"@context": context, "@id": str(wrapper), str(root_property): point}
            expanded = Graph()
            try:
                expanded.parse(data=json.dumps(document), format="json-ld", publicID=base)
            except Exception as error:
                raise ValueError(f"Could not expand point {index} of {snippet}: {error}") from error
            roots = list(expanded.objects(wrapper, root_property))
            if len(roots) != 1 or not isinstance(roots[0], (URIRef, BNode)):
                raise ValueError(f"Point {index} of {snippet} did not expand into one RDF node.")
            root = roots[0]
            if is_tss(root) or root in controls:
                raise ValueError(f"Point {index} of {snippet} uses a TSS container or vocabulary IRI as its identifier.")
            expanded.remove((wrapper, root_property, root))
            for subject, predicate, value in expanded:
                if isinstance(value, Literal) and value.ill_typed:
                    raise ValueError(f"Point {index} of {snippet} has an invalid {value.datatype} literal: {value}.")
                add_domain_triple(subject, predicate, value)
            add_domain_triple(root, RDF.type, point_type)
            for predicate, value in graph.predicate_objects(about):
                if predicate != RDF.type:
                    add_domain_triple(root, predicate, value)
        series.append({"label": str(graph.value(snippet, RDFS.label) or "Series"),
                       "points": len(points)})

    # Keep descriptions of referenced sensors/features/nested metadata, rather
    # than copying the complete source graph and its TSS scaffolding.
    visited = set()
    while referenced_resources - visited:
        resource = next(iter(referenced_resources - visited))
        visited.add(resource)
        for predicate, value in graph.predicate_objects(resource):
            add_domain_triple(resource, predicate, value)

    return {"turtle": output.serialize(format="turtle"), "triples": len(output),
            "points": sum(entry["points"] for entry in series), "series": series}


def generate_external(source, load_files, target, preview_limit=200):
    """load_files(group) returns (metadata, local CSV path, original CSV IRI)."""
    groups = sorted(set(source.subjects(RDF.type, TSS.SnippetGroup)), key=str)
    if not groups:
        raise ValueError("No snippet group found.")
    controls = (set(groups) | set(source.objects(None, TSS.snippetTemplate))
                | set(source.subjects(RDF.type, TSS.Snippet)) | set(source.objects(None, TSS.about)))
    file_inputs = {group: load_files(group) for group in groups}
    mapped_terms = [str(term) for _, predicate, value in source
                    if not str(predicate).startswith(str(TSS))
                    for term in (predicate, value) if isinstance(term, URIRef)
                    and term not in controls and not str(term).startswith(str(TSS))]
    mapped_terms.extend(str(value) for value in source.objects(None, TSS.pointType))
    for metadata, _, _ in file_inputs.values():
        for column in metadata.get("tableSchema", {}).get("columns", []):
            mapped_terms.extend(column[key] for key in ("propertyUrl", "valueUrl")
                                if isinstance(column.get(key), str))
    namespace_graph = Graph(bind_namespaces="none")
    for prefix, namespace in source.namespaces():
        if (prefix != "tss" and not str(namespace).startswith(str(TSS))
                and any(term.startswith(str(namespace)) or term.startswith(prefix + ":") for term in mapped_terms)):
            namespace_graph.bind(prefix, namespace)
    namespace_graph.bind("rdf", RDF)
    namespace_graph.bind("xsd", XSD)
    manager = namespace_graph.namespace_manager
    prefixes = dict(NAMESPACES)
    prefixes.update({prefix: str(namespace) for prefix, namespace in source.namespaces()})
    header = "".join(f"@prefix {prefix}: <{namespace}> .\n" for prefix, namespace in manager.namespaces()) + "\n"
    preview = [header]
    preview_points = 0
    point_count = 0
    triple_count = 0
    series = []
    referenced = set()

    def allowed(subject, predicate, value):
        return not any(term in controls or isinstance(term, URIRef) and str(term).startswith(str(TSS))
                       or isinstance(term, Literal) and term.datatype is not None
                       and str(term.datatype).startswith(str(TSS))
                       for term in (subject, predicate, value))

    def expand(template, row, row_number, source_row, column, csv_iri):
        variables = dict(row, _row=row_number, _sourceRow=source_row, _name=column.name,
                         _column=names.index(column.name) + 1)
        value = template.expand(**variables)
        prefix, separator, local = value.partition(":")
        if separator and prefix in prefixes:
            value = prefixes[prefix] + local
        value = urljoin(str(csv_iri), value)
        if not urlparse(value).scheme:
            raise ValueError(f"Column {column.name}: URL mapping does not resolve to an absolute IRI.")
        return URIRef(value)

    def literal(value, column):
        datatype = column.inherit("datatype") or Datatype.fromvalue("string")
        base = {"number": "double", "datetime": "dateTime", "binary": "base64Binary",
                "any": "anyAtomicType"}.get(datatype.base, datatype.base)
        rdf_type = {"json": RDF.JSON, "xml": RDF.XMLLiteral, "html": RDF.HTML}.get(base, URIRef(str(XSD) + base))
        lang = column.inherit("lang")
        if base == "string" and lang and lang != "und":
            return Literal(value, lang=lang)
        result = Literal(value, datatype=rdf_type)
        if result.ill_typed:
            raise ValueError(f"Column {column.name}: {value!r} is not a valid {datatype.base} value.")
        return result

    def moment(value, what):
        if value is None:
            return None
        if isinstance(value, Literal):
            value = value.toPython()
        if not isinstance(value, datetime):
            raise ValueError(f"{what} must contain dateTime values.")
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    with open(target, "w", encoding="utf-8", newline="\n") as output:
        output.write(header)
        for group in groups:
            flag = single(source, group, TSS.embedded, required=True)
            if not isinstance(flag, Literal) or flag.toPython() is not False:
                raise ValueError("External RDF generation requires tss:embedded false.")
            # Read the producer as source metadata; never export group attributes.
            single(source, group, TSS.producedBy)
            template = single(source, group, TSS.snippetTemplate, required=True)
            metadata, csv_path, csv_iri = file_inputs[group]
            delimiter = csvw_delimiter(metadata)
            dialect = metadata.get("dialect", {})
            table = Table.fromvalue(dict(metadata, url=str(csv_iri), dialect=dialect))
            columns = table.tableSchema.columns
            physical = [column for column in columns if not column.virtual]
            names = [column.name for column in columns]
            if not physical or len(set(names)) != len(names) or any(not name for name in names):
                raise ValueError("CSVW columns need distinct names for the TSS column bindings.")
            if any(column.inherit("aboutUrl") for column in columns):
                raise ValueError("aboutUrl subject mappings are not supported by this standalone anonymous-reading export.")
            header_rows = dialect.get("headerRowCount", 1 if dialect.get("header", True) else 0)
            if header_rows not in (0, 1) or table.dialect.skipColumns:
                raise ValueError("RDF generation supports zero or one header row and no skipColumns.")
            if any(column.inherit("separator") and column.inherit("ordered") for column in columns):
                raise ValueError("Ordered separator lists are not supported by this RDF export.")
            by_name = {column.name: column for column in columns}
            bindings = []
            for snippet in sorted(set(source.objects(group, TSS.hasSnippet)), key=str):
                if (snippet, RDF.type, TSS.Snippet) not in source or len(set(source.subjects(TSS.hasSnippet, snippet))) != 1:
                    raise ValueError(f"{snippet} must be a described snippet in exactly one group.")
                time_name = str(effective(source, snippet, template, TSS.timeColumn) or "")
                value_name = str(effective(source, snippet, template, TSS.valueColumn) or "")
                for name in (time_name, value_name):
                    if name not in by_name or by_name[name].virtual:
                        raise ValueError(f"{snippet}: column {name!r} is not a physical CSVW column.")
                    if not by_name[name].inherit("propertyUrl") or by_name[name].suppressOutput:
                        raise ValueError(f"CSVW column {name!r} needs a propertyUrl mapping for RDF generation.")
                point_type = effective(source, snippet, template, TSS.pointType)
                if not isinstance(point_type, URIRef) or str(point_type).startswith(str(TSS)):
                    raise ValueError(f"{snippet} needs a domain pointType class IRI.")
                about = effective(source, snippet, template, TSS.about)
                if not isinstance(about, (BNode, URIRef)):
                    raise ValueError(f"{snippet} needs an about metadata node.")
                start = moment(effective(source, snippet, template, TSS["from"]), "tss:from")
                end = moment(effective(source, snippet, template, TSS.until), "tss:until")
                if start and end and start >= end:
                    raise ValueError(f"{snippet} has an empty time window.")
                entry = {"label": str(source.value(snippet, RDFS.label) or "Series"), "points": 0}
                series.append(entry)
                bindings.append((time_name, value_name, point_type, about, start, end, entry))
            if not bindings:
                raise ValueError(f"{group} has no member snippets.")
            value_columns = {binding[1] for binding in bindings}
            selected_columns = {
                value_name: [column for column in columns if not column.suppressOutput
                             and column.inherit("propertyUrl")
                             and (column.name not in value_columns or column.name == value_name)]
                for value_name in value_columns
            }
            d = table.dialect
            with open(csv_path, encoding=d.encoding, newline="") as csv_file:
                reader = csv.reader(csv_file, delimiter=delimiter, quotechar=d.quoteChar or '"',
                                    quoting=csv.QUOTE_NONE if d.quoteChar is None else csv.QUOTE_MINIMAL,
                                    doublequote=d.doubleQuote, escapechar=None if d.doubleQuote else "\\",
                                    skipinitialspace=d.skipInitialSpace)
                header_pending = bool(header_rows)
                row_number = 0
                for index, raw in enumerate(reader):
                    if index < d.skipRows:
                        continue
                    if d.commentPrefix and raw and raw[0].startswith(d.commentPrefix):
                        continue
                    if not raw and d.skipBlankRows:
                        continue
                    if len(raw) != len(physical):
                        raise ValueError(f"CSV row at line {reader.line_num} has {len(raw)} columns; CSVW describes {len(physical)}. The effective delimiter is {delimiter!r}.")
                    if header_pending:
                        for cell, column in zip(raw, physical):
                            titles = column.titles
                            accepted = {title for values in titles.values() for title in values} if titles else {column.name}
                            if cell.strip() not in accepted:
                                raise ValueError(f"CSV header {cell!r} does not match CSVW column {column.name!r}; the effective delimiter is {delimiter!r}.")
                        header_pending = False
                        continue
                    row_number += 1
                    values = {}
                    canonical = {}
                    try:
                        for cell, column in zip(raw, physical):
                            if d.trim in (True, "true"):
                                cell = cell.strip()
                            elif d.trim == "start":
                                cell = cell.lstrip()
                            elif d.trim == "end":
                                cell = cell.rstrip()
                            values[column.name] = column.read(cell)
                        for column in columns:
                            if column.virtual:
                                values[column.name] = column.read("")
                            value = values[column.name]
                            dt = column.inherit("datatype")
                            canonical[column.name] = dt.basetype.to_string(value) if dt and value is not None and not isinstance(value, list) else value
                    except (ValueError, TypeError) as error:
                        raise ValueError(f"CSV line {reader.line_num}: {error}") from error
                    for time_name, value_name, point_type, about, start, end, entry in bindings:
                        time_value = moment(values[time_name], f"CSV column {time_name}")
                        if time_value is None:
                            raise ValueError(f"CSV line {reader.line_num}: missing timestamp.")
                        if start and time_value < start or end and time_value >= end:
                            continue
                        if values[value_name] is None:
                            continue
                        root = BNode()
                        pairs = {(RDF.type, point_type)}
                        for column in selected_columns[value_name]:
                            predicate = expand(column.inherit("propertyUrl"), canonical, row_number, reader.line_num, column, csv_iri)
                            value_url = column.inherit("valueUrl")
                            value = values[column.name]
                            if value_url:
                                objects = [expand(value_url, canonical, row_number, reader.line_num, column, csv_iri)] if column.virtual or value is not None else []
                            else:
                                objects = [literal(v, column) for v in (value if isinstance(value, list) else [value]) if v is not None]
                            for obj in objects:
                                if not allowed(root, predicate, obj):
                                    raise ValueError(f"Column {column.name}: the RDF mapping uses TSS vocabulary or a source container.")
                                pairs.add((predicate, obj))
                        for predicate, obj in source.predicate_objects(about):
                            if predicate != RDF.type and allowed(root, predicate, obj):
                                pairs.add((predicate, obj))
                                if isinstance(obj, (BNode, URIRef)):
                                    referenced.add(obj)
                        ordered = sorted(pairs, key=lambda pair: (pair[0] != RDF.type, str(pair[0]), str(pair[1])))
                        text = "[] " + " ;\n    ".join(f"{predicate.n3(manager)} {obj.n3(manager)}" for predicate, obj in ordered) + " .\n\n"
                        output.write(text)
                        point_count += 1
                        triple_count += len(pairs)
                        entry["points"] += 1
                        if preview_points < preview_limit:
                            preview.append(text)
                            preview_points += 1
        descriptions = Graph(bind_namespaces="none")
        visited = set()
        while referenced - visited:
            resource = next(iter(referenced - visited))
            visited.add(resource)
            for predicate, obj in source.predicate_objects(resource):
                if allowed(resource, predicate, obj):
                    descriptions.add((resource, predicate, obj))
                    if isinstance(obj, (BNode, URIRef)):
                        referenced.add(obj)
        if descriptions:
            text = descriptions.serialize(format="turtle")
            output.write(text)
            preview.append(text)
            triple_count += len(descriptions)
    return {"turtle": "".join(preview), "triples": triple_count, "points": point_count,
            "series": series, "previewPoints": preview_points,
            "previewTruncated": preview_points < point_count, "bytes": Path(target).stat().st_size}


def convert(tss_path, mode, output_path="observations.ttl", csv_path=None, csvw_path=None):
    """Write a complete Turtle graph and return conversion statistics.

    Paths are local files, relative to the caller's working directory. Explicit
    CSV/CSVW paths supply the physical inputs; the TSS dataFile IRI remains the
    semantic base for relative CSVW URL mappings, as it is in the dashboard.
    """
    if mode not in ("embedded", "external"):
        raise ValueError("Mode must be 'embedded' or 'external'.")
    if mode == "external" and (csv_path is None or csvw_path is None):
        raise ValueError("External mode requires both CSV and CSVW file paths.")
    if mode == "embedded" and (csv_path is not None or csvw_path is not None):
        raise ValueError("CSV and CSVW file paths apply only to external mode.")

    inputs = {"TSS": Path(tss_path).expanduser().resolve()}
    if mode == "external":
        inputs.update(CSV=Path(csv_path).expanduser().resolve(),
                      CSVW=Path(csvw_path).expanduser().resolve())
    for kind, path in inputs.items():
        if not path.is_file():
            raise ValueError(f"{kind} file not found: {path}")
    output = Path(output_path).expanduser().resolve()
    for path in inputs.values():
        if output == path or output.exists() and output.samefile(path):
            raise ValueError("The output must be different from every input file.")

    graph = Graph().parse(inputs["TSS"], format="turtle", publicID=inputs["TSS"].as_uri())
    groups = list(graph.subjects(RDF.type, TSS.SnippetGroup))
    if not groups:
        raise ValueError("No tss:SnippetGroup found.")
    for group in groups:
        flag = single(graph, group, TSS.embedded, required=True)
        if not isinstance(flag, Literal) or not isinstance(flag.toPython(), bool):
            raise ValueError(f"{group} must declare a boolean tss:embedded.")
        if flag.toPython() != (mode == "embedded"):
            raise ValueError(f"Requested {mode} mode conflicts with tss:embedded {str(flag.toPython()).lower()} on {group}.")

    if mode == "external":
        file_pairs = set()
        for group in groups:
            csv_iri = single(graph, group, TSS.dataFile, required=True)
            csvw_iri = single(graph, group, TSS.describedby, required=True)
            if not isinstance(csv_iri, URIRef) or not isinstance(csvw_iri, URIRef):
                raise ValueError("tss:dataFile and tss:describedby must be IRIs.")
            file_pairs.add((csv_iri, csvw_iri))
        if len(file_pairs) != 1:
            raise ValueError("This command accepts one CSV/CSVW pair per invocation. Split TSS files that reference multiple file pairs.")
        metadata = json.loads(inputs["CSVW"].read_text(encoding="utf-8-sig"))
        schema = metadata.get("tableSchema") if isinstance(metadata, dict) else None
        if not isinstance(schema, dict) or not isinstance(schema.get("columns"), list):
            raise ValueError("The CSVW file needs a tableSchema with a list of columns.")
        if any(not isinstance(column, dict) or "name" not in column for column in schema["columns"]):
            raise ValueError("Every CSVW column needs a name for the TSS column bindings.")
        csv_iri, _ = next(iter(file_pairs))

    # Write beside the destination and replace it only after successful conversion.
    # Invalid data cannot leave a partial graph or overwrite an existing result.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".tss2rdf-", suffix=".ttl", delete=False) as temporary:
        target = Path(temporary.name)
    try:
        if mode == "embedded":
            result = generate_embedded(graph, inputs["TSS"].as_uri())
            target.write_text(result.pop("turtle"), encoding="utf-8", newline="\n")
        else:
            result = generate_external(graph, lambda group: (metadata, inputs["CSV"], csv_iri),
                                       target, preview_limit=0)
        stats = {"mode": mode, "output": str(output), "points": result["points"],
                 "triples": result["triples"], "series": result["series"],
                 "bytes": target.stat().st_size}
        target.replace(output)
        return stats
    finally:
        target.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Convert RDFTSS to standalone domain RDF readings in Turtle, using the dashboard's conversion rules.",
    )
    parser.add_argument("--mode", required=True, choices=("embedded", "external"),
                        help="Must match tss:embedded in the TSS file.")
    parser.add_argument("--tss", required=True, type=Path, help="Input RDFTSS Turtle file.")
    parser.add_argument("--csv", type=Path, help="CSV data file; required in external mode.")
    parser.add_argument("--csvw", type=Path, help="CSVW JSON description; required in external mode.")
    parser.add_argument("-o", "--output", type=Path, default=Path("observations.ttl"),
                        help="Output Turtle file (default: observations.ttl).")
    args = parser.parse_args(argv)
    if args.mode == "external" and (args.csv is None or args.csvw is None):
        parser.error("--mode external requires both --csv and --csvw.")
    if args.mode == "embedded" and (args.csv is not None or args.csvw is not None):
        parser.error("--csv and --csvw apply only to --mode external.")
    try:
        stats = convert(args.tss, args.mode, args.output, args.csv, args.csvw)
    except Exception as error:
        parser.exit(1, f"Conversion failed: {error}\n")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
