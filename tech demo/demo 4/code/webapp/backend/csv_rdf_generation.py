"""Stream external CSV readings to standalone, TSS-free Turtle observations."""
import csv
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from csvw.metadata import Datatype, NAMESPACES, Table
from rdflib import BNode, Graph, Literal, RDF, RDFS, URIRef
from rdflib.namespace import XSD

from rdf_generation import effective, single
from tss2 import TSS


def generate(source, load_files, target, preview_limit=200):
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
            dialect = metadata.get("dialect")
            delimiter = dialect.get("delimiter") if isinstance(dialect, dict) else None
            if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in "\r\n\0":
                raise ValueError("The CSVW must explicitly define a single-character dialect.delimiter.")
            table = Table.fromvalue(dict(metadata, url=str(csv_iri)))
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
                        raise ValueError(f"CSV row at line {reader.line_num} has {len(raw)} columns; CSVW describes {len(physical)}. The declared delimiter is {delimiter!r}.")
                    if header_pending:
                        for cell, column in zip(raw, physical):
                            titles = column.titles
                            accepted = {title for values in titles.values() for title in values} if titles else {column.name}
                            if cell.strip() not in accepted:
                                raise ValueError(f"CSV header {cell!r} does not match CSVW column {column.name!r}; the declared delimiter is {delimiter!r}.")
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
