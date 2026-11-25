"""Converting sources: to other storage classes, time grids and value encodings; subsets,
splits and merges; and sources made from observations.

Every function writes a new metadata document (and data files) and returns it as a
:class:`~rdftss.document.Document`, read back from what was written::

    rdftss.convert(source, "parquet", document="out/readings.ttl")        # data: out/readings.parquet
    rdftss.convert(source, "json", document="out/readings-json.ttl")      # rows in the metadata
    rdftss.convert(source, "csv", document="out/grid.ttl", time_grid="auto")
    rdftss.convert(source, "csv", document="out/delta.ttl", encode={"value": "4750"})
    rdftss.subset(source, ["value"], document="out/values.ttl")
    rdftss.split(source, "day", document="out/days.ttl")
    rdftss.merge([january, february], document="out/merged.ttl")
    rdftss.from_observations("observations.nt", iri="https://example.org/s", document="out/s.ttl")

What stays the same: the rows in the time window, their times (as moments) and their decoded
values; the source's labels, window, summaries and every field's kind and meaning (datatype,
unit, observed property, sensor, ...) and the field property of each describing field; whether
it is immutable (final) or growing; and what the document says about other things, such as the
sensors. What changes is how the rows are stored:

- **Names.** By default the source and its fields get new IRIs, with a suffix such as
  ``-parquet``, so the new document never describes the same node in another way than the old
  one (``iri_suffix=None`` keeps the IRIs).
- **Embedded classes** state every reading itself: a time grid becomes a time column and value
  encodings are decoded. A ``tss:EmbeddedRDFSource`` gets one point per value of a value field,
  with ``tss:field``, ``tss:time`` and ``tss:value``; a point keeps the IRI that ``tss:aboutUrl``
  made for it.
- **Describing fields** state their field property with ``tss:property``: a default property is
  made from the data file or the document, so it would change with them.
- **A time grid without a step field** (rows by position) gets a step field, so that the steps
  stay right whatever rows the window leaves out.
- **Parquet and Arrow** store values, not texts: a value reads back with the lexical form of its
  column type (``3.0E1`` becomes ``30.0``, times become UTC ``...Z``). With ``keep_texts=True``
  every column is a string column, and every text stays exactly as it is.
- **Immutable or growing.** The copy is immutable when the source is. A growing source has no
  stored summaries, and a growing ``tss:EmbeddedRDFSource`` is written as an RDF Message Log. An
  Arrow source that grows or states ``tss:rangeRequests true`` is written in the Arrow IPC
  streaming format (``.arrows``), an immutable one in the IPC file format.
- **Range requests.** ``tss:rangeRequests true`` is kept for the classes that allow it (RDF, CSV,
  Arrow) and left out for the others (JSON, Parquet).
"""
from __future__ import annotations

import csv as csv_module
import json
import math
import re
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urljoin

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from . import xsd
from .analysis import mean_of, median_of, parse_period, period_bounds, period_key
from .document import Document, load
from .encodings import DeltaFromBaseline
from .errors import DataError, require
from .expansion import _base_iri
from .model import SOURCE_CLASSES, CSVDialect, Source, SourceKind, TimeWindow, local_name
from .templates import expand_template
from .times import Duration, Time, TimeGridMath, parse_time, time_from_instant
from .vocab import QUDT, SOSA, TSS, TSS_FROM, XSD
from .writing import document_turtle

#: The short names of the source classes, used in IRI suffixes and file names.
SHORT_NAMES = {SourceKind.EMBEDDED_RDF: "rdf", SourceKind.EMBEDDED_JSON: "json", SourceKind.CSV: "csv",
               SourceKind.PARQUET: "parquet", SourceKind.ARROW: "arrow"}

STORAGE_PROPERTIES = (TSS.dataFile, TSS.delimiter, TSS.encoding, TSS.header, TSS.hasPoint, TSS.points,
                      TSS.timeBase, TSS.timeStep, TSS.stepField)
COLUMN_PROPERTIES = (TSS.columnName, TSS.columnIndex, TSS.valueEncoding, TSS.baseline)
#: Statements about changes that every target states afresh (and terms of earlier drafts, dropped).
CHANGE_PROPERTIES = (TSS.immutable, TSS.rangeRequests, TSS.mutable, TSS.committedUntil)


def describe_iris(graph: Graph, iris) -> Graph:
    """What ``graph`` says about ``iris``: the concise bounded description of each, without the
    statements that use the TSS vocabulary, and with new blank node labels."""
    tss = str(TSS)
    without_tss = Graph()
    for subject, predicate, value in graph:
        if str(predicate).startswith(tss) or (isinstance(value, URIRef) and str(value).startswith(tss)):
            continue
        without_tss.add((subject, predicate, value))
    description = Graph()
    for iri in iris:
        for triple in without_tss.cbd(iri):
            description.add(triple)
    fresh: dict = {}
    copy = Graph()
    for triple in description:
        copy.add(tuple(fresh.setdefault(term, BNode()) if isinstance(term, BNode) else term for term in triple))
    return copy


# ---------------------------------------------------------------------------------------------
# The plan of a target source
# ---------------------------------------------------------------------------------------------

@dataclass
class _Column:
    """One field of the target source. ``origins`` maps each original source (by id) to the field
    that holds this column's values there (several only when merging)."""

    role: str                          # "time", "step" or "value"
    node: object                       # the field's node in the new document
    origins: dict                      # id(source) -> Field
    datatype: URIRef
    name: str | None = None            # the column name or JSON key
    index: int | None = None           # the column index (a CSV file without a header line)
    encoding: object = None            # the value encoding in the new document
    keep_raw: bool = False             # write the text of the data file as it is (same encoding)
    summary: bool = False              # recompute a stored summary


@dataclass
class _Target:
    kind: SourceKind
    node: object
    columns: list
    grid: TimeGridMath | None = None
    grid_from_source: bool = False
    csv: CSVDialect | None = None
    window: TimeWindow | None = None   # None: the original window, unchanged
    rename: dict = dataclass_field(default_factory=dict)
    removed: set = dataclass_field(default_factory=set)
    recompute_summaries: bool = False
    data_iri: URIRef | None = None
    immutable: bool = True
    range_requests: bool = False
    keep_iris: bool = False            # the source and its fields keep their IRIs (and so do IRI points)


def _iri_with(node, suffix):
    return URIRef(str(node) + suffix) if suffix and isinstance(node, URIRef) else node


def _unique(name: str, used: set) -> str:
    candidate, number = name, 2
    while candidate in used:
        candidate = f"{name}_{number}"
        number += 1
    used.add(candidate)
    return candidate


def _plan(source: Source, kind: SourceKind, points, *, fields=None, decode=False, encode=None, time_grid=None,
          csv=None, iri_suffix="auto") -> _Target:
    suffix = f"-{SHORT_NAMES[kind]}" if iri_suffix == "auto" else (iri_suffix or "")
    kept = list(source.fields)
    if fields is not None:
        wanted = {source.field(key) for key in fields} | {source.time_field}
        if source.step_field is not None:
            wanted.add(source.step_field)
        kept = [field for field in source.fields if field in wanted]
    if kind.is_embedded and encode:
        raise ValueError("An embedded source states its readings: it can't have a value encoding (encode=...)")
    if kind.is_embedded and time_grid not in (None, False):
        raise ValueError("An embedded source states its own times: it can't have a time grid (time_grid=...)")

    # The time grid of the target
    grid, grid_from_source = None, False
    if kind.is_external:
        if time_grid is None and source.time_grid is not None:
            grid = TimeGridMath(source.time_grid.base, source.time_grid.step)
            # A grid by position gets a step field: the window may leave rows out
            grid_from_source = source.time_grid.step_field is not None
        elif time_grid == "auto":
            grid = detect_grid(points, source.window)
        elif time_grid not in (None, False):
            base, step = time_grid
            base = base if isinstance(base, Time) else parse_time(str(base))
            step = step if isinstance(step, Duration) else Duration.parse(str(step))
            grid = TimeGridMath(base, step)
    removed = {field for field in source.fields if field not in kept}
    if source.step_field is not None and not grid_from_source:
        removed.add(source.step_field)
        kept = [field for field in kept if field is not source.step_field]

    # The encodings of the target
    decode_all = decode is True
    decoded = set() if decode in (None, False, True) else {source.field(key) for key in decode}
    encodings = {}
    for key, baseline in (encode or {}).items():
        field = source.field(key)
        if not field.is_numeric or field.is_time:
            raise ValueError(f"{field.name!r} is not a numeric field, so it can't be encoded as differences")
        if baseline == "auto":
            values = [point.text(field) for point in points if field in point.values]
            baseline = auto_baseline(values, field.datatype)
        baseline_literal = Literal(str(baseline), datatype=field.datatype, normalize=False)
        encodings[field] = DeltaFromBaseline(field.datatype, baseline_literal)

    def new_node(node):
        # Blank nodes get new blank nodes, so the sources made by splitting one source never share one
        return BNode() if isinstance(node, BNode) else _iri_with(node, suffix)

    rename = {source.node: new_node(source.node)}
    columns = []
    used_names: set = set()
    for field in kept:
        node = new_node(field.node)
        rename[field.node] = node
        role = "time" if field.is_time else "step" if field.is_step else "value"
        column = _Column(role, node, {id(source): field}, field.datatype, summary=False)
        if role == "value":
            if field in encodings:
                column.encoding = encodings[field]
            elif kind.is_external and field.value_encoding is not None and not decode_all and field not in decoded:
                column.encoding = field.value_encoding
                column.keep_raw = True
        columns.append(column)

    # A new step field
    if grid is not None and not grid_from_source:
        new_source = rename[source.node]
        step_node = URIRef(f"{new_source}-step-field") if isinstance(new_source, URIRef) else BNode()
        columns.insert(1, _Column("step", step_node, {}, XSD.integer))

    # Column names and indexes
    has_header = True
    if kind == SourceKind.CSV:
        dialect = csv if isinstance(csv, CSVDialect) else CSVDialect(**(csv or {}))
        has_header = dialect.header
    else:
        dialect = None
    position = 0
    for column in columns:
        origin = next(iter(column.origins.values()), None)
        if column.role == "time" and grid is not None:
            continue  # on a time grid, the time field has no column
        # Every field gets a column name (also RDF points, where it is optional), so the names of
        # the fields stay the same in the new document
        default = "step" if column.role == "step" else "time" if column.role == "time" else "value"
        column.name = _unique((origin.column_name or origin.name) if origin is not None else default, used_names)
        if kind == SourceKind.CSV and not has_header:
            column.index = position
        position += 1
    return _Target(kind, rename[source.node], columns, grid, grid_from_source, dialect, None, rename, removed,
                   immutable=source.immutable, range_requests=source.range_requests and kind.allows_range_requests,
                   keep_iris=not suffix)


def auto_baseline(texts, datatype) -> str:
    """A baseline for ``tss:DeltaFromBaseline``: the median of the values, rounded to a whole number."""
    numbers = sorted(Decimal(text) for text in texts if text not in ("NaN", "INF", "-INF", "+INF"))
    if not numbers:
        return "0"
    middle = numbers[len(numbers) // 2]
    return str(int(middle.to_integral_value()))


def detect_grid(points, window: TimeWindow | None = None) -> TimeGridMath:
    """A time grid on which every point lies: the step is the greatest common divisor of the
    intervals between the points, and step 0 is the start of the window (when the points lie on
    the grid from there) or the first point. Raises :class:`ValueError` without two points."""
    instants = sorted(point.instant for point in points)
    if len(instants) < 2:
        raise ValueError("A time grid needs at least two points to find its step")
    scale = 10 ** max((max(0, -instant.as_tuple().exponent) for instant in instants), default=0)
    step_units = 0
    for before, after in zip(instants, instants[1:]):
        step_units = math.gcd(step_units, int((after - before) * scale))
    step = Decimal(step_units) / scale
    base = points[0].moment if points[0].instant == instants[0] else min(points, key=lambda p: p.instant).moment
    if window is not None and window.start is not None and window.start.instant <= instants[0]:
        if ((instants[0] - window.start.instant) / step) == ((instants[0] - window.start.instant) / step).to_integral_value():
            base = window.start
    return TimeGridMath(base, Duration.of(seconds=step))


# ---------------------------------------------------------------------------------------------
# The texts and values of the target
# ---------------------------------------------------------------------------------------------

def _text(point, column: _Column, target: _Target) -> str | None:
    """The text to store for one point in one column."""
    if column.role == "time":
        return point.time_text
    if column.role == "step":
        if target.grid_from_source:
            origin = column.origins[id(point.source)]
            return point.texts.get(origin)
        step = target.grid.step_of(point.moment)
        if step is None:
            raise DataError(f"lies at {point.time_text}, which is not on the time grid "
                            f"{target.grid.base.text} + n × {target.grid.step.text}", point.where)
        return str(step)
    origin = column.origins[id(point.source)]
    if origin not in point.values:
        return None
    if column.keep_raw:
        return point.texts.get(origin)
    decoded = point.text(origin)
    return column.encoding.encode(decoded) if column.encoding is not None else decoded


def _arrow_array(pa, texts: list, column: _Column, keep_texts: bool):
    """An Arrow array for the texts of one column, typed as the specification's tables say."""
    if keep_texts:
        return pa.array(texts, type=pa.string())
    datatype = column.datatype
    present = [text for text in texts if text is not None]
    try:
        if column.role == "time" or (datatype in xsd.DATE_TIME_TYPES and present):
            times = [None if text is None else parse_time(text, require_timezone=column.role == "time") for text in texts]
            zoned = {time.instant is not None for time in times if time is not None}
            if len(zoned) > 1:
                raise ValueError("mixed timezones")
            fraction_digits = max((_fraction_digits(text) for text in present), default=0)
            unit, per = ("us", 10**6) if fraction_digits <= 6 else ("ns", 10**9)
            if fraction_digits > 9:
                raise ValueError("more than 9 fraction digits")
            if zoned == {False}:  # times without a timezone
                counts = [None if time is None else _local_count(time, per) for time in times]
                return pa.array(counts, type=pa.int64()).cast(pa.timestamp(unit))
            counts = [None if time is None else int(time.instant * per) for time in times]
            return pa.array(counts, type=pa.int64()).cast(pa.timestamp(unit, tz="UTC"))
        if column.role == "step":
            return pa.array([None if text is None else int(text) for text in texts], type=pa.int64())
        if datatype == XSD.double:
            return pa.array([None if text is None else float(text) for text in texts], type=pa.float64())
        if datatype == XSD.float:
            return pa.array([None if text is None else float(text) for text in texts], type=pa.float32())
        if datatype == XSD.decimal:
            values = [None if text is None else Decimal(text) for text in texts]
            scale = max((max(0, -value.as_tuple().exponent) for value in values if value is not None), default=0)
            digits = max((len(value.as_tuple().digits) - (-value.as_tuple().exponent if value.as_tuple().exponent < 0 else 0)
                          for value in values if value is not None), default=1)
            if scale + digits > 38:
                raise ValueError("too many digits for a DECIMAL")
            return pa.array(values, type=pa.decimal128(max(1, scale + max(digits, 1)), scale))
        if datatype in xsd.INTEGER_RANGES:
            kinds = {XSD.int: pa.int32(), XSD.short: pa.int16(), XSD.byte: pa.int8(),
                     XSD.unsignedLong: pa.uint64(), XSD.unsignedInt: pa.uint32(),
                     XSD.unsignedShort: pa.uint16(), XSD.unsignedByte: pa.uint8()}
            return pa.array([None if text is None else int(text) for text in texts], type=kinds.get(datatype, pa.int64()))
        if datatype == XSD.boolean:
            return pa.array([None if text is None else text in ("true", "1") for text in texts], type=pa.bool_())
        if datatype == XSD.date and not any(len(text) > 10 for text in present):
            return pa.array([None if text is None else xsd.read_value(text, XSD.date) for text in texts], type=pa.date32())
        if datatype == XSD.time and not any(text.endswith("Z") or "+" in text or text.count("-") for text in present):
            return pa.array([None if text is None else xsd.read_value(text, XSD.time) for text in texts], type=pa.time64("us"))
    except (ValueError, OverflowError, pa.ArrowInvalid, pa.ArrowTypeError, ArithmeticError):
        if column.role in ("time", "step"):
            raise
    return pa.array(texts, type=pa.string())


def _fraction_digits(text: str) -> int:
    """How many fraction digits the seconds of a time text have."""
    match = re.search(r"T[0-9]{2}:[0-9]{2}:[0-9]{2}[.]([0-9]+)", text)
    return len(match.group(1)) if match else 0


def _local_count(time: Time, per: int) -> int:
    """The wall-clock time of a time without a timezone, counted from 1970-01-01T00:00:00."""
    from .times import _local_seconds

    return int(_local_seconds(time.parts) * per)


# ---------------------------------------------------------------------------------------------
# Building the new document
# ---------------------------------------------------------------------------------------------

def _closure(graph: Graph, starts) -> set:
    """The triples of ``starts`` and of the blank nodes they lead to."""
    triples, seen, queue = set(), set(), list(starts)
    while queue:
        node = queue.pop()
        if node in seen:
            continue
        seen.add(node)
        for predicate, value in graph.predicate_objects(node):
            triples.add((node, predicate, value))
            if isinstance(value, BNode):
                queue.append(value)
    return triples


def _source_closure(source: Source) -> set:
    graph = source.graph
    starts = [source.node] + [field.node for field in source.fields] + list(graph.objects(source.node, TSS.hasPoint))
    for field in source.fields:
        starts.extend(graph.objects(field.node, TSS.summary))
        starts.extend(graph.objects(field.node, TSS.fieldDescription))
    return _closure(graph, starts)


def _new_graph(document: Document) -> Graph:
    graph = Graph()
    for prefix, namespace in document.graph.namespaces():
        graph.bind(prefix, namespace, override=True, replace=True)
    return graph


def _copy_context(document: Document, graph: Graph) -> None:
    """Copy what the document says about everything but its sources (sensors, features, ...)."""
    owned = set()
    for source in document.sources:
        owned |= _source_closure(source)
    for triple in document.graph:
        if triple not in owned:
            graph.add(triple)


def _add_source(source: Source, target: _Target, points, graph: Graph, keep_texts: bool, data_path) -> None:
    """Add the target source's statements (and write its data file) into ``graph``."""
    source_graph = source.graph
    own = _source_closure(source)
    point_nodes = set(source_graph.objects(source.node, TSS.hasPoint))
    skip_subjects = {subject for subject, _, _ in _closure(source_graph, point_nodes)} | point_nodes
    removed_nodes = {field.node for field in target.removed}
    for field in target.removed:
        skip_subjects |= {subject for subject, _, _ in _closure(source_graph, [field.node])}
    field_nodes = {field.node for field in source.fields}
    summary_columns = {column.origins[id(source)].node for column in target.columns
                       if column.summary and id(source) in column.origins}
    if not target.immutable:  # a growing source has no stored summaries
        summary_nodes = [summary for field in source.fields for summary in source_graph.objects(field.node, TSS.summary)]
        skip_subjects |= {subject for subject, _, _ in _closure(source_graph, summary_nodes)}
    if target.recompute_summaries:
        for field in source.fields:
            if field.node in summary_columns:
                skip_subjects |= {subject for subject, _, _ in _closure(source_graph, list(source_graph.objects(field.node, TSS.summary)))}

    fresh: dict = {}  # every target gets its own copies of the blank nodes (descriptions, summaries)

    def rename(term):
        if term in target.rename:
            return target.rename[term]
        if isinstance(term, BNode):
            return fresh.setdefault(term, BNode())
        return term

    for subject, predicate, value in own:
        if subject in skip_subjects:
            continue
        if subject == source.node:
            if (predicate == RDF.type and value in SOURCE_CLASSES) or predicate in STORAGE_PROPERTIES:
                continue
            if predicate == TSS.hasField and value in removed_nodes:
                continue
            if target.window is not None and predicate in (TSS_FROM, TSS.until):
                continue
            if predicate in CHANGE_PROPERTIES:
                continue
        if subject in field_nodes:
            if predicate in COLUMN_PROPERTIES:
                continue
            if predicate == TSS.aboutUrl and target.kind == SourceKind.EMBEDDED_RDF:
                continue  # the points of an RDF source are nodes already: they keep the IRIs it made
            if predicate == TSS.describesField and value in removed_nodes:
                continue
            if target.recompute_summaries and predicate == TSS.summary and subject in summary_columns:
                continue
            if not target.immutable and predicate == TSS.summary:
                continue
        graph.add((rename(subject), rename(predicate), rename(value)))

    node = target.node
    graph.add((node, RDF.type, target.kind.iri))
    if target.window is not None:
        if target.window.start is not None:
            graph.add((node, TSS_FROM, Literal(target.window.start.text, datatype=XSD.dateTime, normalize=False)))
        if target.window.end is not None:
            graph.add((node, TSS.until, Literal(target.window.end.text, datatype=XSD.dateTime, normalize=False)))
    if target.immutable:
        graph.add((node, TSS.immutable, Literal("true", datatype=XSD.boolean)))
    if target.range_requests:
        graph.add((node, TSS.rangeRequests, Literal("true", datatype=XSD.boolean)))
    # A describing field keeps its field property: a default one is made from the data file or
    # the document, so it would change with them
    for column in target.columns:
        origin = next(iter(column.origins.values()), None)
        if origin is not None and origin.describes and origin.property is None and origin.field_property is not None:
            graph.add((column.node, TSS.property, origin.field_property))
    if target.data_iri is not None:
        graph.add((node, TSS.dataFile, target.data_iri))
    if target.csv is not None:
        graph.add((node, TSS.delimiter, Literal(target.csv.delimiter)))
        graph.add((node, TSS.encoding, Literal(target.csv.encoding)))
        graph.add((node, TSS.header, Literal("true" if target.csv.header else "false", datatype=XSD.boolean)))
    if target.grid is not None:
        graph.add((node, TSS.timeBase, Literal(target.grid.base.text, datatype=XSD.dateTime, normalize=False)))
        graph.add((node, TSS.timeStep, Literal(target.grid.step.text, datatype=XSD.duration)))
    for column in target.columns:
        if column.role == "step":
            graph.add((node, TSS.stepField, column.node))
            if not column.origins:  # a new step field
                graph.add((node, TSS.hasField, column.node))
                graph.add((column.node, RDF.type, TSS.ContextField))
                graph.add((column.node, RDFS.label, Literal("Step", lang="en")))
                graph.add((column.node, TSS.datatype, XSD.integer))
        if column.name is not None:
            graph.add((column.node, TSS.columnName, Literal(column.name)))
        if column.index is not None:
            graph.add((column.node, TSS.columnIndex, Literal(str(column.index), datatype=XSD.integer)))
        if column.encoding is not None:
            for predicate, value in column.encoding.statements():
                graph.add((column.node, predicate, value))

    table = [[_text(point, column, target) for column in target.columns] for point in points]
    if target.recompute_summaries and target.immutable:
        for index, column in enumerate(target.columns):
            if column.summary:
                _add_summary(graph, column, points, [row[index] for row in table])
    if target.kind == SourceKind.EMBEDDED_RDF:
        _add_rdf_points(node, target, points, table, graph)
    elif target.kind == SourceKind.EMBEDDED_JSON:
        objects = []
        for row in table:
            item = {column.name: text for column, text in zip(target.columns, row) if text not in (None, "")}
            objects.append(json.dumps(item, ensure_ascii=False))
        text = "[\n" + ",\n".join(f"  {item}" for item in objects) + "\n]" if objects else "[]"
        graph.add((node, TSS.points, Literal(text, datatype=RDF.JSON, normalize=False)))
    else:
        stored = [column for column in target.columns if column.name is not None]
        positions = [target.columns.index(column) for column in stored]
        _write_data(target, stored, [[row[i] for i in positions] for row in table], data_path, keep_texts)


def _add_rdf_points(node, target: _Target, rows, table: list, graph: Graph) -> None:
    """The points of a tss:EmbeddedRDFSource: one per value of a value field in a row, with its
    value field, the time of its row, its reading, and the values of the describing fields that
    describe its value field."""
    time_column = next(column for column in target.columns if column.role == "time")
    first = {id(column): next(iter(column.origins.values()), None) for column in target.columns}
    value_columns = [(index, column) for index, column in enumerate(target.columns)
                     if column.role == "value" and first[id(column)] is not None and first[id(column)].is_value]
    by_field = {first[id(column)]: column for _, column in value_columns}
    describing: dict = {}  # value column -> [(index, describing column, its field property)]
    for index, column in enumerate(target.columns):
        origin = first[id(column)]
        if column.role == "value" and origin is not None and origin.describes and origin.field_property is not None:
            for described in origin.describes:
                if described in by_field:
                    describing.setdefault(id(by_field[described]), []).append((index, column, origin.field_property))
    for row, texts in zip(rows, table):
        for index, column in value_columns:
            text = texts[index]
            if text is None or text == "":
                continue
            point = _point_node(row, column, target)
            graph.add((node, TSS.hasPoint, point))
            graph.add((point, TSS.field, column.node))
            graph.add((point, TSS.time, _literal(row.time_text, time_column)))
            graph.add((point, TSS.value, _literal(text, column)))
            for other, describer, prop in describing.get(id(column), []):
                if texts[other] not in (None, ""):
                    graph.add((point, prop, _literal(texts[other], describer)))


def _point_node(row, column: _Column, target: _Target):
    """The node of a new point: the IRI that tss:aboutUrl makes for it, the IRI of the point it
    copies (when the IRIs are kept), or a new blank node."""
    origin = column.origins.get(id(row.source))
    if origin is not None and origin.about_url is not None:
        texts = {field.column_name: row.raw(field) for field in row.source.fields if field.column_name is not None}
        base = _base_iri(row.source)
        made = expand_template(origin.about_url, texts)
        return URIRef(urljoin(base, made) if base else made)
    if target.keep_iris and row.nodes and isinstance(row.nodes.get(origin), URIRef):
        return row.nodes[origin]
    return BNode()


def _literal(text: str, column: _Column) -> Literal:
    origin = next(iter(column.origins.values()), None)
    if column.datatype == RDF.langString:
        language = origin.language if origin is not None else None
        return Literal(text, lang=language) if language else Literal(text)
    return Literal(text, datatype=column.datatype, normalize=False)


def _write_data(target: _Target, columns: list, rows: list, path: Path, keep_texts: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if target.kind == SourceKind.CSV:
        dialect = target.csv
        with open(path, "w", encoding=dialect.encoding, newline="") as file:
            writer = csv_module.writer(file, delimiter=dialect.delimiter)
            if dialect.header:
                writer.writerow([column.name for column in columns])
            for row in rows:
                writer.writerow(["" if text is None else text for text in row])
        return
    pa = require("pyarrow", "Writing Parquet and Arrow files", "files")
    arrays = [_arrow_array(pa, [row[index] for row in rows], column, keep_texts) for index, column in enumerate(columns)]
    table = pa.table(arrays, names=[column.name for column in columns])
    if target.kind == SourceKind.PARQUET:
        import pyarrow.parquet as pq

        pq.write_table(table, str(path))
    else:
        import pyarrow.ipc

        # The data file of a growing source, or of one that states tss:rangeRequests true, is an
        # IPC stream, to which record batches can be appended
        new_writer = pyarrow.ipc.new_stream if _streamed(target) else pyarrow.ipc.new_file
        with pa.OSFile(str(path), "wb") as sink, new_writer(sink, table.schema) as writer:
            writer.write_table(table)


def _number_literal(value, datatype) -> Literal:
    if isinstance(value, float):
        return Literal(xsd.float_text(value), datatype=XSD.double, normalize=False)
    if isinstance(value, int):
        return Literal(str(value), datatype=datatype if xsd.is_integer(datatype) else XSD.integer, normalize=False)
    text = xsd.decimal_text(value.normalize() if value == value.to_integral_value() else value)
    if datatype in xsd.FLOATING_TYPES:
        return Literal(text, datatype=datatype, normalize=False)
    return Literal(text, datatype=XSD.decimal, normalize=False)


def _add_summary(graph: Graph, column: _Column, points, texts: list) -> None:
    """A new stored summary of one column, over the given points (which lie in the window)."""
    count = missing = 0
    first = last = None
    numbers = []
    numeric = xsd.is_numeric(column.datatype)
    for point, text in zip(points, texts):
        if text is None:
            missing += 1
            continue
        count += 1
        first = point if first is None or point.instant < first.instant else first
        last = point if last is None or point.instant > last.instant else last
        if numeric:
            origin = column.origins.get(id(point.source))
            value = point.values.get(origin) if origin is not None else None
            if value is not None:
                numbers.append(value)
    summary = BNode()
    graph.add((column.node, TSS.summary, summary))
    graph.add((summary, TSS.missingValuePolicy, TSS.ExcludeEmptyValues))
    graph.add((summary, TSS["count"], Literal(str(count), datatype=XSD.integer)))
    graph.add((summary, TSS.missing, Literal(str(missing), datatype=XSD.integer)))
    if first is not None:
        graph.add((summary, TSS.firstTime, Literal(first.time_text, datatype=XSD.dateTime, normalize=False)))
        graph.add((summary, TSS.lastTime, Literal(last.time_text, datatype=XSD.dateTime, normalize=False)))
    if numbers and not any(isinstance(value, float) and math.isnan(value) for value in numbers):
        floating = column.datatype in xsd.FLOATING_TYPES
        graph.add((summary, TSS.observedMinimum, _number_literal(min(numbers), column.datatype)))
        graph.add((summary, TSS.observedMaximum, _number_literal(max(numbers), column.datatype)))
        graph.add((summary, TSS.mean, _number_literal(mean_of(numbers, floating), column.datatype)))
        graph.add((summary, TSS.median, _number_literal(median_of(numbers, floating), column.datatype)))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    graph.add((summary, TSS.generatedAtTime, Literal(now.isoformat().replace("+00:00", "Z"), datatype=XSD.dateTime, normalize=False)))


def _streamed(target: _Target) -> bool:
    """Whether an Arrow target is written in the IPC streaming format: when it grows, or when it
    states tss:rangeRequests true."""
    return not target.immutable or target.range_requests


def _suffix(target: _Target) -> str:
    """The file name suffix of a target's data file: ``.arrows`` for an Arrow IPC stream."""
    return ".arrows" if target.kind == SourceKind.ARROW and _streamed(target) else target.kind.suffix


def _paths(target: _Target, document, data_file, stem: str):
    """(the document path, the data file path) for a target."""
    kind = target.kind
    document_path = Path(document) if document is not None else None
    if kind.is_embedded:
        return document_path, None
    if data_file is not None:
        return document_path, Path(data_file)
    if document_path is None:
        raise ValueError("Say where to write the data file: data_file=... (or document=..., to write it next to it)")
    return document_path, document_path.with_name(stem + _suffix(target))


def _finish(graph: Graph, document_path: Path | None, original: Document) -> Document:
    """Write the new document (when a path is given) and return it, read back."""
    if document_path is None:
        return Document(graph, location="(converted)", access=original.access)
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document_path.write_text(document_turtle(graph, document_path=document_path), encoding="utf-8")
    return load(document_path, allow_network=original.access.allow_network)


# ---------------------------------------------------------------------------------------------
# The functions
# ---------------------------------------------------------------------------------------------

def convert(source: Source, to=None, *, document=None, data_file=None, iri_suffix="auto", fields=None,
            decode=False, encode=None, time_grid=None, csv=None, keep_texts: bool = False,
            strict: bool = True) -> Document:
    """The source in another storage class, as a new document.

    - ``to``: ``"rdf"``, ``"json"``, ``"csv"``, ``"parquet"`` or ``"arrow"`` (or a
      :class:`~rdftss.model.SourceKind`); by default the source's own class.
    - ``document``: where to write the new metadata document (Turtle). Without it, the new
      document is only returned, in memory.
    - ``data_file``: where to write the data file of an external target; by default next to
      ``document``, with the same name and the class's suffix (``.csv``, ``.parquet``, ``.arrow``).
    - ``iri_suffix``: added to the IRIs of the source and its fields; ``"auto"`` is ``-csv``,
      ``-parquet``, ...; ``None`` keeps the IRIs.
    - ``fields``: keep only these fields (and the time and step fields).
    - ``decode``: ``True`` to write the decoded values of every field with a value encoding (and
      drop the encoding), or a list of fields.
    - ``encode``: ``{field: baseline}`` to state those fields as differences from a baseline
      (``tss:DeltaFromBaseline``); the baseline ``"auto"`` is the median, rounded.
    - ``time_grid``: by default an external target keeps the source's time grid (a grid by
      position gets a step field); ``False`` writes a time column instead; ``"auto"`` puts the
      rows on a grid whose step is found from the data; ``(base, step)``, such as
      ``("2024-05-12T00:00:00Z", "PT15M")``, sets it.
    - ``csv``: the CSV settings of a CSV target: ``{"delimiter": ";", "header": False, "encoding": "utf-8"}``.
    - ``keep_texts``: Parquet and Arrow: store every value as text, exactly as it is.
    - ``strict``: read the source strictly (stop at the first data error).
    """
    source.check_readable()
    kind = SourceKind.parse(to) if to is not None else source.kind
    points = source.read(strict=strict)
    target = _plan(source, kind, points, fields=fields, decode=decode, encode=encode, time_grid=time_grid,
                   csv=csv, iri_suffix=iri_suffix)
    document_path, data_path = _paths(target, document, data_file, document and Path(document).stem or source.name)
    if data_path is not None:
        target.data_iri = URIRef(data_path.resolve().as_uri())
    graph = _new_graph(source.document)
    _copy_context(source.document, graph)
    _add_source(source, target, points, graph, keep_texts, data_path)
    return _finish(graph, document_path, source.document)


def subset(source: Source, fields, **options) -> Document:
    """The source with only some of its fields (the time field, and the step field of a time
    grid, are always kept). The options are those of :func:`convert`; the IRI suffix is
    ``-subset`` unless you give another."""
    options.setdefault("iri_suffix", "-subset")
    return convert(source, options.pop("to", None), fields=fields, **options)


def _period_label(start: datetime, months: int, seconds: Decimal) -> str:
    if months and months % 12 == 0:
        return f"{start.year:04d}"
    if months:
        return f"{start.year:04d}-{start.month:02d}"
    if seconds % 86400 == 0:
        return start.strftime("%Y-%m-%d")
    if seconds % 60 == 0:
        return start.strftime("%Y-%m-%dT%H%M")
    return start.strftime("%Y-%m-%dT%H%M%S")


def split(source: Source, every="month", *, to=None, document=None, folder=None, strict: bool = True,
          keep_texts: bool = False) -> Document:
    """Split a source into one source per period (``"day"``, ``"month"``, ``"year"``, ``"1h"``,
    ``"P7D"``, ...), in one new document.

    Only periods with points become parts. Each part gets the part of the window that lies in its
    period, the points in it, its own IRIs
    (the source's and fields' IRIs with a suffix such as ``-2024-05``) and new stored summaries
    for the fields that had one. External parts get one data file each, in ``folder`` (by
    default the folder of ``document``). Periods are in UTC; months and years are calendar ones.
    """
    source.check_readable()
    kind = SourceKind.parse(to) if to is not None else source.kind
    if isinstance(every, str) and every in ("day", "month", "year", "hour", "week"):
        every = {"day": "1d", "month": "1 month", "year": "1y", "hour": "1h", "week": "1w"}[every]
    months, seconds = parse_period(every)
    points = source.read(strict=strict)
    groups: dict = {}
    for point in points:
        groups.setdefault(period_key(point, months, seconds), []).append(point)
    document_path = Path(document) if document is not None else None
    folder = Path(folder) if folder is not None else (document_path.parent if document_path else None)
    if kind.is_external and folder is None:
        raise ValueError("Say where to write the data files: folder=... or document=...")
    graph = _new_graph(source.document)
    _copy_context(source.document, graph)
    stem = document_path.stem if document_path else source.name
    for key, part_points in sorted(groups.items()):
        start, end = period_bounds(key, months, seconds)
        label = _period_label(start, months, seconds)
        target = _plan(source, kind, part_points, iri_suffix=f"-{label}")
        target.window = _part_window(source.window, start, end)
        target.recompute_summaries = True
        for column in target.columns:
            origin = next(iter(column.origins.values()), None)
            column.summary = origin is not None and origin.summary is not None
        data_path = folder / f"{stem}-{label}{_suffix(target)}" if kind.is_external else None
        if data_path is not None:
            target.data_iri = URIRef(data_path.resolve().as_uri())
        _add_source(source, target, part_points, graph, keep_texts, data_path)
    return _finish(graph, document_path, source.document)


def _part_window(window: TimeWindow, start: datetime, end: datetime) -> TimeWindow:
    begin = time_from_instant(_instant(start))
    finish = time_from_instant(_instant(end))
    if window.start is not None and window.start.instant > begin.instant:
        begin = window.start
    if window.end is not None and window.end.instant < finish.instant:
        finish = window.end
    return TimeWindow(begin, finish)


def _instant(moment: datetime) -> Decimal:
    from .times import to_instant

    return to_instant(moment)


def merge(sources, *, iri=None, to=None, document=None, data_file=None, strict: bool = True,
          keep_texts: bool = False) -> Document:
    """One source with the points of several sources that have the same fields (the same
    names and datatypes), such as the parts of :func:`split`.

    The merged source takes its description from the first source, with the IRI ``iri``
    (by default the first source's IRI with ``-merged``), the smallest window that holds all
    windows, decoded values and a time column, and new stored summaries for the fields that had
    one. It is immutable when every source is, else growing. Two rows with the same time are an error."""
    sources = list(sources)
    if not sources:
        raise ValueError("merge needs at least one source")
    first = sources[0]
    for source in sources:
        source.check_readable()
    kind = SourceKind.parse(to) if to is not None else first.kind

    def signature(source):
        return sorted((field.role, field.name, str(field.datatype)) for field in source.fields if not field.is_step)

    for other in sources[1:]:
        if signature(other) != signature(first):
            raise ValueError(f"{other.name} and {first.name} do not have the same fields (names, roles and "
                             "datatypes), so they can't be merged")
    points = []
    seen = {}
    for source in sources:
        for point in source.read(strict=strict):
            if point.instant in seen:
                raise DataError(f"has the same time as a point of {seen[point.instant]}; no two points may have "
                                "the same time", f"{source.name}, {point.where}")
            seen[point.instant] = source.name
            points.append(point)
    points.sort(key=lambda point: point.instant)

    new_node = URIRef(iri) if iri is not None else _iri_with(first.node, "-merged")
    target = _plan(first, kind, [p for p in points if p.source is first], decode=True, time_grid=False,
                   iri_suffix=None)
    target.rename[first.node] = new_node
    target.node = new_node
    for column in target.columns:
        origin = next(iter(column.origins.values()))
        new_field = URIRef(f"{new_node}-{origin.name}") if isinstance(new_node, URIRef) else column.node
        target.rename[origin.node] = new_field
        column.node = new_field
        for source in sources[1:]:
            column.origins[id(source)] = next(field for field in source.fields
                                               if (field.role, field.name) == (origin.role, origin.name))
        column.summary = origin.summary is not None
    target.recompute_summaries = True
    starts = [source.window.start for source in sources]
    ends = [source.window.end for source in sources]
    target.window = TimeWindow(None if None in starts else min(starts, key=lambda t: t.instant),
                               None if None in ends else max(ends, key=lambda t: t.instant))
    target.immutable = all(source.immutable for source in sources)
    target.range_requests = all(source.range_requests for source in sources) and kind.allows_range_requests
    document_path, data_path = _paths(target, document, data_file, Path(document).stem if document else "merged")
    if data_path is not None:
        target.data_iri = URIRef(data_path.resolve().as_uri())
    graph = _new_graph(first.document)
    _copy_context(first.document, graph)
    _add_source(first, target, points, graph, keep_texts, data_path)
    return _finish(graph, document_path, first.document)


def from_observations(data, *, iri: str, to="rdf", document=None, data_file=None, label: str | None = None,
                      summaries: bool = True) -> Document:
    """A source made from SOSA observations: nodes with ``sosa:resultTime`` and ``sosa:hasSimpleResult``.

    - ``data``: an rdflib graph, or a path or URL of an RDF document with the observations
      (nodes with ``sosa:resultTime`` and ``sosa:hasSimpleResult``).
    - ``iri``: the IRI of the new source; its fields get IRIs that start with it.
    - ``to``, ``document``, ``data_file``: the storage class and where to write (see :func:`convert`).

    Observations with the same observed property, sensor, feature of interest, unit and datatype
    become one field; all fields share one time field. ``tss:from`` is the first time and
    ``tss:until`` one second after the last; the source is immutable (``tss:immutable true``). Every result time
    needs a timezone, and two different results of one field at the same time are an error.
    """
    from .rdf import parse_rdf

    graph = data if isinstance(data, Graph) else parse_rdf(str(data))
    groups: dict = {}
    rows: dict = {}
    times: dict = {}
    for node in set(graph.subjects(SOSA.hasSimpleResult, None)):
        result = graph.value(node, SOSA.hasSimpleResult)
        result_time = graph.value(node, SOSA.resultTime)
        if not isinstance(result, Literal) or not isinstance(result_time, Literal):
            raise DataError("an observation needs one literal sosa:resultTime and one literal sosa:hasSimpleResult",
                            str(node))
        try:
            moment = parse_time(str(result_time), require_timezone=True)
        except ValueError as error:
            raise DataError(f"its sosa:resultTime is not a time with a timezone: {error}", str(node)) from None
        datatype = RDF.langString if result.language else (result.datatype or XSD.string)
        key = (graph.value(node, SOSA.observedProperty), graph.value(node, SOSA.madeBySensor),
               graph.value(node, SOSA.hasFeatureOfInterest), graph.value(node, QUDT.hasUnit), datatype, result.language)
        groups.setdefault(key, len(groups))
        row = rows.setdefault(moment.instant, {})
        times.setdefault(moment.instant, moment)
        if key in row and row[key] != str(result):
            raise DataError(f"two observations of the same field at {moment.text} have different results "
                            f"({row[key]!r} and {str(result)!r})", str(node))
        row[key] = str(result)
    if not rows:
        raise DataError("found no observations (nodes with sosa:hasSimpleResult)")

    source = URIRef(iri)
    out = Graph()
    for prefix, namespace in graph.namespaces():
        out.bind(prefix, namespace, override=False)
    out.bind("tss", TSS)
    out.bind("xsd", XSD)
    time_field = URIRef(f"{iri}-time")
    ordered = sorted(times.values(), key=lambda time: time.instant)
    until = time_from_instant(ordered[-1].instant + 1)
    for triple in [(source, RDF.type, SourceKind.EMBEDDED_RDF.iri), (source, TSS.immutable, Literal("true", datatype=XSD.boolean)),
                   (source, TSS_FROM, Literal(ordered[0].text, datatype=XSD.dateTime, normalize=False)),
                   (source, TSS.until, Literal(until.text, datatype=XSD.dateTime, normalize=False)),
                   (source, TSS.timeField, time_field), (source, TSS.hasField, time_field),
                   (time_field, RDF.type, TSS.ContextField), (time_field, TSS.datatype, XSD.dateTime),
                   (time_field, TSS.columnName, Literal("time")), (time_field, RDFS.label, Literal("Time", lang="en"))]:
        out.add(triple)
    if label:
        out.add((source, RDFS.label, Literal(label)))
    nodes, used_names, used_iris = {}, {"time"}, set()
    for key, number in sorted(groups.items(), key=lambda item: item[1]):
        observed, sensor, feature, unit, datatype, language = key
        base = local_name(observed) or "value"
        name = _unique(base if len(groups) == 1 or sum(1 for k in groups if k[0] == observed) == 1
                       else f"{base}-{local_name(sensor) or local_name(feature) or number}", used_names)
        field = URIRef(f"{iri}-{name}")
        nodes[key] = field
        out.add((source, TSS.hasField, field))
        out.add((field, RDF.type, TSS.ValueField))
        out.add((field, TSS.columnName, Literal(name)))
        out.add((field, TSS.datatype, datatype))
        if language:
            out.add((field, TSS.language, Literal(language)))
        for predicate, value in ((TSS.observedProperty, observed), (TSS.sensor, sensor),
                                 (TSS.featureOfInterest, feature), (TSS.unit, unit)):
            if value is not None:
                out.add((field, predicate, value))
                if isinstance(value, URIRef):
                    used_iris.add(value)
    for instant, row in rows.items():
        for key, text in row.items():
            datatype, language = key[4], key[5]
            point = BNode()
            out.add((source, TSS.hasPoint, point))
            out.add((point, TSS.field, nodes[key]))
            out.add((point, TSS.time, Literal(times[instant].text, datatype=XSD.dateTime, normalize=False)))
            out.add((point, TSS.value, Literal(text, lang=language) if language else
                     Literal(text, datatype=datatype, normalize=False)))
    for triple in describe_iris(graph, used_iris):
        out.add(triple)
    made = Document(out, location="(from observations)")
    kind = SourceKind.parse(to)
    if summaries or kind != SourceKind.EMBEDDED_RDF:
        new = made.source()
        points = new.read()
        target = _plan(new, kind, points, iri_suffix=None)
        if summaries:
            target.recompute_summaries = True
            for column in target.columns:
                column.summary = column.role != "step"
        document_path, data_path = _paths(target, document, data_file, Path(document).stem if document else new.name)
        if data_path is not None:
            target.data_iri = URIRef(data_path.resolve().as_uri())
        result = _new_graph(made)
        _copy_context(made, result)
        _add_source(new, target, points, result, False, data_path)
        return _finish(result, document_path, made)
    return _finish(out, Path(document) if document else None, made)
