"""A small reader of RDF Time Series Sources for the demo 11 evaluation.

It reads a metadata document (Turtle, or an RDF Message Log) and the rows of each source, as
`spec/spec.bs` says: the five source classes, value and context fields, CSV, Parquet and Arrow
data files, a time grid (with a step field, or by position), tss:DeltaFromBaseline, the RDF points
of a tss:EmbeddedRDFSource (tss:field, tss:time, tss:value and the properties of describing
fields) and the JSON rows of a tss:EmbeddedJSONSource. It is not a complete implementation: it
reads what the evaluation needs, and reports what it cannot read.
"""
import csv
import json
import re
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import rdflib
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

rdflib.NORMALIZE_LITERALS = False   # keep every literal as it is written


def shacl_validate(graph: Graph, shapes: Graph, **options) -> tuple:
    """pyshacl.validate. pyshacl turns rdflib's literal normalisation back on when it returns
    (pyshacl.monkey.rdflib_bool_unpatch), so every document parsed after it would be read with
    normalised literals ("1"^^xsd:boolean as "true", "...Z" as "...+00:00"). This turns it off
    again, so that every document is read as it is written, whatever was validated before it."""
    import pyshacl
    try:
        return pyshacl.validate(graph, shacl_graph=shapes, **options)
    finally:
        rdflib.NORMALIZE_LITERALS = False


TSS = Namespace("https://w3id.org/tss#")
SOURCE_CLASSES = {TSS.EmbeddedRDFSource, TSS.EmbeddedJSONSource, TSS.CSVSource, TSS.ParquetSource,
                  TSS.ArrowSource}
NUMERIC = {XSD.double, XSD.float, XSD.decimal, XSD.integer, XSD.long, XSD.int, XSD.short, XSD.byte,
           XSD.nonNegativeInteger, XSD.positiveInteger, XSD.nonPositiveInteger, XSD.negativeInteger,
           XSD.unsignedLong, XSD.unsignedInt, XSD.unsignedShort, XSD.unsignedByte}
DELIMITER = re.compile(r"^[ \t]*(MESSAGE|@message[ \t]*\.)[ \t]*$")
TIMEZONE = re.compile(r"(Z|[+-][0-9]{2}:[0-9]{2})$")


# --- Documents ---------------------------------------------------------------------------------

def split_messages(text: str) -> list:
    """The messages of an RDF Message Log: the text between delimiter lines. A delimiter inside a
    long string ('''…''' or \"\"\"…\"\"\") is text, not a delimiter."""
    messages, current, quote = [], [], None
    for line in text.splitlines(keepends=True):
        if quote is None and DELIMITER.match(line.rstrip("\r\n")):
            messages.append("".join(current))
            current = []
            continue
        current.append(line)
        for q in ('"""', "'''"):
            if line.count(q) % 2:
                quote = None if quote == q else (q if quote is None else quote)
    if "".join(current).strip():
        messages.append("".join(current))      # a last message without delimiter: still being written
    return messages


def is_log(text: str) -> bool:
    return bool(re.search(r'^\s*(VERSION|@version)\s+"1\.2-messages"', text, re.M))


def parse_document(path: Path) -> tuple:
    """The RDF graph of a metadata document, and its messages when it is an RDF Message Log. The
    graph of a log is the merge of its messages: each message is parsed on its own, so its blank
    node labels are its own, while the prefixes of earlier messages stay in force."""
    text = path.read_text(encoding="utf-8")
    base = path.resolve().as_uri()
    graph = Graph()
    if not is_log(text):
        graph.parse(data=text, format="turtle", publicID=base)
        return graph, None
    messages, prefixes = [], []
    for message in split_messages(text):
        body = []
        for line in message.splitlines():
            stripped = line.strip()
            if re.match(r'^(VERSION|@version)\s', stripped):
                continue
            m = re.match(r"^PREFIX\s+(\S*)\s+(<[^>]*>)\s*$", stripped, re.I)
            if m:
                prefixes.append(f"@prefix {m.group(1)} {m.group(2)} .")
                continue
            if stripped.startswith("@prefix"):
                prefixes.append(stripped)
                continue
            body.append(line)
        graph.parse(data="\n".join(prefixes + body), format="turtle", publicID=base)
        messages.append("\n".join(body))
    return graph, messages


# --- Sources and fields ------------------------------------------------------------------------

@dataclass
class Row:
    time: datetime
    values: dict = field(default_factory=dict)      # field -> text, None for an empty value


@dataclass
class Source:
    graph: Graph
    iri: object
    document: Path

    @property
    def kind(self):
        kinds = [k for k in self.graph.objects(self.iri, RDF.type) if k in SOURCE_CLASSES]
        return kinds[0] if len(kinds) == 1 else None

    def value(self, prop, subject=None):
        return self.graph.value(subject if subject is not None else self.iri, prop)

    @property
    def fields(self) -> list:
        return list(self.graph.objects(self.iri, TSS.hasField))

    @property
    def time_field(self):
        return self.value(TSS.timeField)

    @property
    def step_field(self):
        return self.value(TSS.stepField)

    def is_value_field(self, f) -> bool:
        return (f, RDF.type, TSS.ValueField) in self.graph

    @property
    def value_fields(self) -> list:
        return [f for f in self.fields if self.is_value_field(f)]

    def datatype(self, f):
        return self.value(TSS.datatype, f)

    def column(self, f):
        name = self.value(TSS.columnName, f)
        return None if name is None else str(name)

    def describing(self) -> dict:
        """Each describing field and its field property (stated, or the default)."""
        out = {}
        for f in self.fields:
            if self.value(TSS.describesField, f) is None:
                continue
            prop = self.value(TSS.property, f)
            if prop is None and self.column(f) is not None:
                base = self.value(TSS.dataFile) or URIRef(self.document.resolve().as_uri())
                prop = URIRef(str(base) + "#" + _fragment(self.column(f)))
            out[f] = prop
        return out

    @property
    def window(self) -> tuple:
        start, end = self.value(TSS["from"]), self.value(TSS.until)
        return (parse_time(str(start)) if start else None, parse_time(str(end)) if end else None)

    @property
    def data_path(self):
        iri = self.value(TSS.dataFile)
        if iri is None:
            return None
        parsed = urlparse(str(iri))
        return Path(url2pathname(parsed.path)) if parsed.scheme == "file" else None


def sources(graph: Graph, document: Path) -> list:
    nodes = {s for s in graph.subjects(TSS.hasField, None)} | {
        s for c in SOURCE_CLASSES for s in graph.subjects(RDF.type, c)}
    return [Source(graph, s, document) for s in sorted(nodes, key=str)]


def _fragment(name: str) -> str:
    from urllib.parse import quote
    return quote(name, safe="!$&'()*+,;=:@/?-._~")


# --- Times, durations and values ---------------------------------------------------------------

def parse_time(text: str):
    """A time with a timezone, as a moment; None when the text is not one."""
    if not TIMEZONE.search(text):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def add_duration(start: datetime, duration: str, n: int) -> datetime:
    """start + n × duration, as XML Schema adds a duration: each part multiplied by n."""
    m = re.fullmatch(r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?",
                     duration)
    if not m:
        raise ValueError(f"unsupported duration {duration}")
    y, mo, d, h, mi, s = (float(g) if g else 0 for g in m.groups())
    months = start.month - 1 + int(mo * n) + 12 * int(y * n)
    year, month = start.year + months // 12, months % 12 + 1
    day = min(start.day, monthrange(year, month)[1])
    shifted = start.replace(year=year, month=month, day=day)
    return shifted + timedelta(days=d * n, hours=h * n, minutes=mi * n, seconds=s * n)


def valid_lexical(text: str, datatype) -> bool:
    """The lexical forms that the specification lists (§datatypes); other datatypes accept text."""
    if datatype in (XSD.double, XSD.float):
        return bool(re.fullmatch(r"[+-]?(([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?|INF)|NaN", text))
    if datatype == XSD.decimal:
        return bool(re.fullmatch(r"[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)", text))
    if datatype in NUMERIC:
        return bool(re.fullmatch(r"[+-]?[0-9]+", text))
    if datatype == XSD.boolean:
        return text in ("true", "false", "1", "0")
    if datatype in (XSD.dateTime, XSD.dateTimeStamp):
        return bool(re.fullmatch(r"-?[0-9]{4,}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]+)?"
                                 r"(Z|[+-][0-9]{2}:[0-9]{2})", text)) and parse_time(text) is not None
    return True


def number(text: str):
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


# --- Reading rows ------------------------------------------------------------------------------

class ReadError(Exception):
    pass


def decode(source: Source, f, text):
    """The decoded value of a value: the value itself, or tss:baseline + the value."""
    if text is None or source.value(TSS.valueEncoding, f) != TSS.DeltaFromBaseline:
        return text
    baseline = source.value(TSS.baseline, f)
    total = number(str(baseline)) + number(text)
    return format(total.normalize(), "f") if total == total.to_integral() else format(total, "f")


def read_rows(source: Source) -> list:
    """All rows of a source, with their times and their decoded values, in file order."""
    kind = source.kind
    if kind == TSS.CSVSource:
        rows = _read_csv(source)
    elif kind in (TSS.ParquetSource, TSS.ArrowSource):
        rows = _read_columnar(source)
    elif kind == TSS.EmbeddedJSONSource:
        rows = _read_json(source)
    elif kind == TSS.EmbeddedRDFSource:
        rows = _read_rdf(source)
    else:
        raise ReadError(f"{source.iri}: no single source class")
    for row in rows:
        for f in source.fields:
            if f in row.values:
                row.values[f] = decode(source, f, row.values[f])
    return rows


def in_window(source: Source, rows: list) -> list:
    start, end = source.window
    return [r for r in rows if r.time is not None and (start is None or r.time >= start)
            and (end is None or r.time < end)]


def _grid_time(source: Source, step: int) -> datetime:
    base = parse_time(str(source.value(TSS.timeBase)))
    return add_duration(base, str(source.value(TSS.timeStep)), step)


def _row_time(source: Source, values: dict, position: int):
    if source.value(TSS.timeBase) is not None:             # a time grid
        if source.step_field is not None:
            step = values.get(source.step_field)
            return None if step in (None, "") else _grid_time(source, int(step))
        return _grid_time(source, position)
    text = values.get(source.time_field)
    return None if text in (None, "") else parse_time(text)


def _read_csv(source: Source) -> list:
    path = source.data_path
    if path is None or not path.is_file():
        raise ReadError(f"data file not found: {source.value(TSS.dataFile)}")
    delimiter = str(source.value(TSS.delimiter) or ",")
    encoding = str(source.value(TSS.encoding) or "utf-8")
    header = source.value(TSS.header)
    has_header = header is None or str(header) in ("true", "1")
    with path.open(newline="", encoding=encoding) as file:
        records = list(csv.reader(file, delimiter=delimiter))
    names = records[0] if has_header else None
    body = records[1:] if has_header else records
    index = {}
    for f in source.fields:
        if has_header and source.column(f) is not None:
            if source.column(f) not in names:
                raise ReadError(f"no column {source.column(f)!r} for {f}")
            index[f] = names.index(source.column(f))
        elif source.value(TSS.columnIndex, f) is not None:
            index[f] = int(source.value(TSS.columnIndex, f))
    rows = []
    for position, record in enumerate(body):
        values = {f: (record[i] if i < len(record) and record[i] != "" else None) for f, i in index.items()}
        rows.append(Row(_row_time(source, values, position), values))
    return rows


def _columnar_text(value, arrow_type) -> str:
    """A Parquet or Arrow value in the lexical form of its type (§parquet, §arrow)."""
    import pyarrow as pa
    if value is None:
        return None
    if pa.types.is_timestamp(arrow_type):
        moment = value.astimezone(timezone.utc)
        text = moment.strftime("%Y-%m-%dT%H:%M:%S")
        if moment.microsecond:
            text += f".{moment.microsecond:06d}".rstrip("0")
        return text + "Z"
    if pa.types.is_floating(arrow_type):
        if value != value:
            return "NaN"
        if value in (float("inf"), float("-inf")):
            return "INF" if value > 0 else "-INF"
        return repr(float(value))
    if pa.types.is_integer(arrow_type):
        return str(int(value))
    if pa.types.is_boolean(arrow_type):
        return "true" if value else "false"
    return str(value)


def _read_columnar(source: Source) -> list:
    import pyarrow as pa
    import pyarrow.parquet as pq
    path = source.data_path
    if path is None or not path.is_file():
        raise ReadError(f"data file not found: {source.value(TSS.dataFile)}")
    if source.kind == TSS.ParquetSource:
        table = pq.read_table(path)
    else:
        with pa.memory_map(str(path)) as stream:
            head = stream.read(6)
        table = (pa.ipc.open_file(str(path)) if head == b"ARROW1" else pa.ipc.open_stream(str(path))).read_all()
    columns = {}
    for f in source.fields:
        name = source.column(f)
        if name is None:
            continue
        if name not in table.column_names:
            raise ReadError(f"no column {name!r} for {f}")
        arrow_type = table.schema.field(name).type
        columns[f] = [_columnar_text(v, arrow_type) for v in table.column(name).to_pylist()]
    rows = []
    for position in range(table.num_rows):
        values = {f: col[position] for f, col in columns.items()}
        rows.append(Row(_row_time(source, values, position), values))
    return rows


def _read_json(source: Source) -> list:
    literal = source.value(TSS.points)
    rows = []
    for obj in json.loads(str(literal)):
        values = {}
        for f in source.fields:
            raw = obj.get(source.column(f)) if source.column(f) is not None else None
            values[f] = None if raw in (None, "") else (raw if isinstance(raw, str) else json.dumps(raw))
        rows.append(Row(_row_time(source, values, 0), values))
    return rows


def _read_rdf(source: Source) -> list:
    """The rows of an embedded RDF source: its points, grouped by time (§rdf-points)."""
    g = source.graph
    value_fields = source.value_fields
    describing = source.describing()
    by_time = {}
    for point in g.objects(source.iri, TSS.hasPoint):
        time_text = g.value(point, TSS.time)
        when = parse_time(str(time_text)) if time_text is not None else None
        stated = g.value(point, TSS.field)
        vf = stated if stated is not None else (value_fields[0] if len(value_fields) == 1 else None)
        row = by_time.setdefault(when, Row(when, {source.time_field: None if time_text is None else str(time_text)}))
        reading = g.value(point, TSS.value)
        if vf is not None:
            row.values[vf] = None if reading is None else str(reading)
        for f, prop in describing.items():
            v = g.value(point, prop)
            if v is not None:
                row.values[f] = str(v)
    return sorted(by_time.values(), key=lambda r: (r.time is None, r.time or datetime.min.replace(tzinfo=timezone.utc)))


# --- Summaries ---------------------------------------------------------------------------------

def summarise(source: Source, rows: list, f) -> dict:
    """The stored summary of a field, recomputed from the rows in the window (§summaries)."""
    if f == source.time_field:      # on a time grid, the time field has no column: its value is the row's time
        present = [r for r in rows if r.time is not None]
    else:
        present = [r for r in rows if r.values.get(f) not in (None, "")]
    out = {"count": len(present), "missing": len(rows) - len(present)}
    if present:
        out["firstTime"] = min(r.time for r in present)
        out["lastTime"] = max(r.time for r in present)
    if source.datatype(f) in NUMERIC and f != source.time_field and present:
        values = sorted(Decimal(r.values[f]) for r in present)
        n = len(values)
        out["observedMinimum"], out["observedMaximum"] = values[0], values[-1]
        out["mean"] = sum(values) / n
        out["median"] = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
    return out


def stored_summary(source: Source, f) -> dict:
    node = source.value(TSS.summary, f)
    if node is None:
        return None
    out = {}
    for p, o in source.graph.predicate_objects(node):
        key = str(p).split("#")[-1]
        if key in ("count", "missing"):
            out[key] = int(str(o))
        elif key in ("firstTime", "lastTime", "generatedAtTime"):
            out[key] = parse_time(str(o))
        elif key in ("observedMinimum", "observedMaximum", "mean", "median"):
            out[key] = Decimal(str(o))
    return out
