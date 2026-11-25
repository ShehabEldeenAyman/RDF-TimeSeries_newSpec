"""Lifecycle and message-sequence checks of demo 11 (the paper's Table 5).

    python ../waterinfo/fetch_waterinfo.py     # first: the Waterinfo data
    python check_lifecycle.py                  # writes results/lifecycle_report.md and .json, and fixtures/

Three growing sources are built from the same real readings, the 46 EC20 readings of
Zevenaarshaven from 2021-03-03T23:15Z to 2021-03-04T23:00Z (data/waterinfo_289441042.csv), added one
Write at a time. They are the three representations that may state tss:rangeRequests true
(§range-opt-in), and all three state it:

  log    the RDF Message Log of ex:zevenaarshaven-tv-source. Its first six states are the published
         metadata document waterinfo_289441042_tss2_points_time_value.ttl, message by message (the
         description and five row messages: the sixth state is the document, byte for byte); then
         one row message per further reading, and the ending message, with tss:immutable true and
         the stored summary (§ending).
  csv    the data file of a tss:CSVSource (fixtures/zevenaarshaven_csv_growing.ttl): the header
         line, then one record per reading. The writer ends the source by adding tss:immutable true
         and the summary to its metadata document (fixtures/zevenaarshaven_csv_ended.ttl).
  arrow  the data file of a tss:ArrowSource in the IPC streaming format: the schema message, then
         one record batch per reading. The writer ends the source as for the CSV, and then appends
         the end-of-stream marker.

Every state after a Write is kept, and the sequence of states is checked as it is (merging the
messages of a log loses their order). The checks follow Table 5 of the paper:

  L0 structure       every state conforms to the SHACL shapes (a log as the merge of its messages);
                     read through its description, the last state holds the readings written, and
                     its stored summary equals the summary recomputed from them
  L1 interpretation  the description of the source (class, fields, window, data file, time grid,
                     tss:rangeRequests), the columns of the CSV header line and the Arrow schema do
                     not change; ending the source only adds tss:immutable true and summaries
  L2 dependencies    (log) a row message, or an ending message, comes after the description of its
                     source, and is read with the prefixes in force before it
  L3 units           (log) every message ends with a delimiter, and a row message holds one row of
                     one source, its points at one time, and nothing else; (csv) the file ends with
                     a line break; (arrow) the IPC streaming format, complete messages, and no
                     end-of-stream marker while the source grows
  L4 finalisation    tss:immutable true and summaries only when the source ends, never
                     tss:immutable false in a log, and no row after the end
  L5 byte prefix     when the source states tss:rangeRequests true, every state starts with the
                     bytes of the state before it (§range-opt-in)

The same checks run on variants with one deliberate error each, which must be detected by the
check they target, and on variants that the specification allows (rows written out of time order;
a CSV file without tss:rangeRequests that is written anew), which must pass. Then a consumer
follows the sources over HTTP as §following says, from a local server that answers Range and
conditional requests as RFC 9110 says. The server is local: other servers are not tested.

The factory document of the metadata endpoint is NDA-derived and is never used here.
"""
import csv
import hashlib
import http.server
import io
import json
import platform
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyshacl
import rdflib
from rdflib import Graph, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import RDF, SH, XSD

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tss_reader as tr   # noqa: E402
from tss_reader import TSS   # noqa: E402

DEMO = HERE.parent.parent
LOG_DOCUMENT = DEMO / "metadata endpoint" / "waterinfo_289441042_tss2_points_time_value.ttl"
SERIES = DEMO / "data" / "waterinfo_289441042.csv"
MANIFEST = HERE.parent / "waterinfo" / "manifest.json"
SHAPES = {k: Graph().parse(DEMO / "SHACL" / f"{k}_shapes.ttl") for k in ("embedded", "external")}
FIXTURES, RESULTS = HERE / "fixtures", HERE / "results"
EX = "https://example.org/ex#"
CHECKS = ("L0 structure", "L1 interpretation", "L2 dependencies", "L3 units", "L4 finalisation", "L5 byte prefix")
DELIMITER = re.compile(rb"^[ \t]*(?:MESSAGE|@message[ \t]*\.)[ \t]*\r?\n", re.M)
PREFIX_LINE = re.compile(r"^\s*PREFIX\s+(\S*)\s+(<[^>]*>)\s*$", re.I)
VERSION_LINE = re.compile(r"^\s*(VERSION|@version)\s")
HEADER = re.compile(rb"(?:(?:VERSION|PREFIX)[^\n]*\n)+")
EOS = b"\xff\xff\xff\xff\x00\x00\x00\x00"
K = 10                                              # the variants change the sequence after this many Writes
RANGE_LINE = "    tss:rangeRequests true ;\n"


# --- The readings, and their stored summary ----------------------------------------------------

def readings() -> list:
    with SERIES.open(newline="", encoding="utf-8") as file:
        return [(r["Timestamp"], r["Value"]) for r in csv.DictReader(file)]


def summary_text(field: str, rows: list, generated: str) -> str:
    """The stored summary of a value field over the readings (§summaries), as Turtle."""
    values = sorted(Decimal(v) for _, v in rows)
    n = len(values)
    median = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
    first = min(rows, key=lambda r: tr.parse_time(r[0]))[0]
    last = max(rows, key=lambda r: tr.parse_time(r[0]))[0]
    return (f"{field} tss:summary [\n"
            f"    tss:missingValuePolicy tss:ExcludeEmptyValues ;\n"
            f'    tss:count "{n}"^^xsd:integer ;\n'
            f'    tss:missing "0"^^xsd:integer ;\n'
            f'    tss:firstTime "{first}"^^xsd:dateTime ;\n'
            f'    tss:lastTime "{last}"^^xsd:dateTime ;\n'
            f'    tss:observedMinimum "{values[0]}"^^xsd:double ;\n'
            f'    tss:observedMaximum "{values[-1]}"^^xsd:double ;\n'
            f'    tss:mean "{float(sum(values) / n)!r}"^^xsd:double ;\n'
            f'    tss:median "{float(median)!r}"^^xsd:double ;\n'
            f'    tss:generatedAtTime "{generated}"^^xsd:dateTime\n'
            f"] .\n")


def cumulative(chunks: list) -> list:
    """The states of a resource to which the chunks are appended one by one."""
    states, acc = [], b""
    for chunk in chunks:
        acc += chunk
        states.append(acc)
    return states


# --- The RDF Message Log -----------------------------------------------------------------------

LOG_SOURCE, LOG_FIELD = "ex:zevenaarshaven-tv-source", "ex:zevenaarshaven-tv-value-field"


def row_message(time: str, value: str) -> bytes:
    """A Write to the log (§updating-embedded): one row message, as in the published document."""
    return (f"{LOG_SOURCE} tss:hasPoint [\n    tss:field {LOG_FIELD} ;\n"
            f'    tss:time "{time}"^^xsd:dateTime ;\n    tss:value "{value}"^^xsd:double\n] .\nMESSAGE\n').encode()


def ending_message(rows: list, generated: str) -> bytes:
    return (f"{LOG_SOURCE} tss:immutable true .\n\n" + summary_text(LOG_FIELD, rows, generated) + "MESSAGE\n").encode()


def log_messages(rows: list, generated: str) -> tuple:
    """The messages of the log, each with its delimiter: those of the published document, a row
    message for each further reading, and the ending message; and the number of published rows."""
    published = LOG_DOCUMENT.read_bytes()
    ends = [0] + [m.end() for m in DELIMITER.finditer(published)]
    messages = [published[a:b] for a, b in zip(ends, ends[1:])]
    if b"".join(messages) != published:
        raise SystemExit(f"{LOG_DOCUMENT.name} does not end with a message delimiter")
    done = len(messages) - 1
    return messages + [row_message(t, v) for t, v in rows[done:]] + [ending_message(rows, generated)], done


_parsed = {}


def log_units(state: bytes) -> list:
    """The messages of a log state, each parsed on its own with the prefixes in force before it
    (blank nodes are scoped to their message), with its kind and whether a delimiter ends it."""
    parts = tr.split_messages(state.decode("utf-8"))
    last_end = max((m.end() for m in DELIMITER.finditer(state)), default=0)
    incomplete = bool(state[last_end:].strip())
    prefixes, units = [], []
    for i, part in enumerate(parts):
        body = []
        for line in part.splitlines():
            m = PREFIX_LINE.match(line)
            if m:
                prefixes.append(f"@prefix {m.group(1)} {m.group(2)} .")
            elif not VERSION_LINE.match(line):
                body.append(line)
        key = (i, tuple(prefixes), "\n".join(body))   # two equal messages are two graphs: their blank nodes differ
        if key not in _parsed:
            try:
                g = Graph().parse(data="\n".join(prefixes + body), format="turtle", publicID=LOG_DOCUMENT.resolve().as_uri())
                _parsed[key] = (g, None)
            except Exception as error:   # noqa: BLE001 (any parse error makes the message unreadable)
                _parsed[key] = (Graph(), str(error).strip().splitlines()[-1][:120])
        g, error = _parsed[key]
        flags = {str(o) for o in g.objects(None, TSS.immutable)}
        kind = ("unreadable" if error else
                "row" if (None, TSS.hasPoint, None) in g else
                "description" if any(c in tr.SOURCE_CLASSES for c in g.objects(None, RDF.type)) else
                "ending" if flags & {"true", "1"} else
                "summary" if (None, TSS.summary, None) in g else "description")
        units.append({"graph": g, "kind": kind, "error": error, "flags": flags,
                      "complete": not (incomplete and i == len(parts) - 1)})
    return units


def interpretation(g: Graph) -> Graph:
    """The description of the sources in a graph: all but the points, tss:immutable true and the
    summaries, the only statements that a Write or the ending adds (§change)."""
    drop = {t for t in g.triples((None, TSS.immutable, None)) if str(t[2]) in ("true", "1")}
    for link in (TSS.hasPoint, TSS.summary):
        for s, p, o in g.triples((None, link, None)):
            drop.add((s, p, o))
            drop.update(g.triples((o, None, None)))
    out = Graph()
    for t in g:
        if t not in drop:
            out.add(t)
    return out


def ranged(interp: Graph) -> bool:
    return any(str(o) in ("true", "1") for o in interp.objects(None, TSS.rangeRequests))


def row_problem(g: Graph) -> str:
    """Why a row message is not one row of one source (§message-profile), or ''."""
    points = set(g.objects(None, TSS.hasPoint))
    sources = set(g.subjects(TSS.hasPoint, None))
    times = set()
    for p in points:
        t = g.value(p, TSS.time)
        try:
            times.add(tr.parse_time(str(t)))
        except Exception:   # noqa: BLE001
            times.add(str(t))
    fields = [g.value(p, TSS.field) for p in points]
    extra = [t for t in g if t[1] != TSS.hasPoint and t[0] not in points]
    if len(sources) != 1:
        return f"holds points of {len(sources)} sources"
    if len(times) != 1:
        return f"holds points at {len(times)} times"
    if len(fields) != len(set(fields)):
        return "holds two points of one field"
    if extra:
        return f"holds {len(extra)} statements besides its row"
    return ""


class Findings(dict):
    """The findings of each check; a finding is kept for the first state it is found in."""
    def __init__(self):
        super().__init__({c: [] for c in CHECKS})
        self.seen = set()

    def add(self, check: str, state: int, text: str) -> None:
        if (check, text) not in self.seen:
            self.seen.add((check, text))
            self[check].append(f"state {state}: {text}")


def violations(graph: Graph, kind: str) -> list:
    conforms, report, _ = tr.shacl_validate(graph, SHAPES[kind], inference="none",
                                            allow_warnings=True, abort_on_first=False)
    out = set()
    for result in report.objects(None, SH.result):
        if report.value(result, SH.resultSeverity) != SH.Violation:
            continue
        name = SHAPES[kind].value(report.value(result, SH.sourceShape), SH.name) or report.value(result, SH.resultMessage)
        m = re.match(r"\s*([SFDPQ]\d+)\b", str(name))
        out.add(m.group(1) if m else str(name)[:60])
    return sorted(out)


def summary_differences(source, rows) -> list:
    out = []
    for f in source.fields:
        stored = tr.stored_summary(source, f)
        if stored is None:
            continue
        for key, value in tr.summarise(source, rows, f).items():
            if key not in stored:
                continue
            if key in ("mean", "median"):
                same = abs(stored[key] - value) <= Decimal("1e-12") * max(abs(value), 1)
            else:
                same = stored[key] == value
            if not same:
                out.append(f"stored {key} {stored[key]}, recomputed {value}")
    return out


def content_problems(graph: Graph, document: Path, source_iri, written: list) -> list:
    """Read through its description, does the state hold the readings written, and does its
    stored summary describe them?"""
    source = next((s for s in tr.sources(graph, document) if s.iri == source_iri), None)
    if source is None:
        return ["the source is not described"]
    try:
        rows = tr.read_rows(source)
    except Exception as error:   # noqa: BLE001
        return [f"the rows cannot be read: {error}"]
    field = source.value_fields[0]
    got = sorted((r.time, float(r.values[field])) for r in rows if r.values.get(field) not in (None, ""))
    want = sorted((tr.parse_time(t), float(v)) for t, v in written)
    if got == want:
        problems = []
    elif len(got) == len(want):
        problems = [f"read through its description, {sum(a != b for a, b in zip(got, want))} of its "
                    f"{len(got)} readings differ from those written"]
    else:
        problems = [f"read through its description, it holds {len(got)} readings, not the {len(want)} written"]
    return problems + [f"stored summary: {d}" for d in summary_differences(source, rows)]


def check_log(states: list, written: list, structural: str = "last") -> dict:
    """The checks L0-L5 on the states of a log after each Write."""
    found, previous = Findings(), None
    source_iri = URIRef(EX + "zevenaarshaven-tv-source")
    for n, state in enumerate(states):
        units = log_units(state)
        described, ended = set(), False
        for i, u in enumerate(units):
            g, kind = u["graph"], u["kind"]
            # L2: a row or ending message is read with the description of its source, and the prefixes before it
            if kind == "unreadable":
                found.add("L2 dependencies", n, f"message {i} cannot be read with the prefixes in force before it ({u['error']})")
            for s in sorted(set(g.subjects(TSS.hasPoint, None)) - described):
                found.add("L2 dependencies", n, f"message {i} holds a row of {s.n3()} before its description")
            if kind == "ending":
                for s in sorted(set(g.subjects(TSS.immutable, None)) - described):
                    found.add("L2 dependencies", n, f"message {i} ends {s.n3()} before its description")
            described |= {s for s, c in g.subject_objects(RDF.type) if c in tr.SOURCE_CLASSES}
            # L3: complete messages; one row of one source per row message
            if not u["complete"]:
                found.add("L3 units", n, f"message {i} has no message delimiter")
            if kind == "row" and row_problem(g):
                found.add("L3 units", n, f"row message {i} {row_problem(g)}")
            # L4: finalisation
            true = bool(u["flags"] & {"true", "1"})
            if u["flags"] & {"false", "0"}:
                found.add("L4 finalisation", n, f"message {i} states tss:immutable false")
            if true and kind != "ending":
                found.add("L4 finalisation", n, f"message {i}, a {kind} message, states tss:immutable true")
            if (None, TSS.summary, None) in g and not (true or ended):
                found.add("L4 finalisation", n, f"message {i} adds a summary while the source grows")
            if kind == "row" and ended:
                found.add("L4 finalisation", n, f"message {i} adds a row after the ending message")
            ended = ended or true
        merged = Graph()
        for u in units:
            merged += u["graph"]
        current = interpretation(merged)
        if previous is not None:
            # L1: the description does not change
            if not isomorphic(previous[0], current):
                found.add("L1 interpretation", n, "the description of the source differs from that of the previous state")
            # L5: byte prefix, when the source states tss:rangeRequests true
            if previous[1] and not state.startswith(states[n - 1]):
                found.add("L5 byte prefix", n, "the log does not start with the bytes of the previous state")
        previous = (current, ranged(current))
        # L0: SHACL, and the content of the last state
        if structural == "all" or n == len(states) - 1:
            for v in violations(merged, "embedded"):
                found.add("L0 structure", n, f"SHACL violation {v}")
        if n == len(states) - 1:
            for problem in content_problems(merged, LOG_DOCUMENT, source_iri, written):
                found.add("L0 structure", n, problem)
    return dict(found)


# --- The CSV and Arrow data files --------------------------------------------------------------

METADATA = """@prefix tss: <https://w3id.org/tss#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix quantitykind: <http://qudt.org/vocab/quantitykind/> .
@prefix unit: <http://qudt.org/vocab/unit/> .
@prefix ex: <https://example.org/ex#> .

# A fixture of the lifecycle checks (evaluation/lifecycle/check_lifecycle.py): the EC20 readings
# of Zevenaarshaven (data/waterinfo_289441042.csv), appended to {what} one {unit} per reading.
# The source states tss:rangeRequests true: the data file only grows, byte for byte, so a
# consumer fetches only the new bytes with HTTP Range requests.
ex:zevenaarshaven-{id}-source
    a tss:{cls} ;
    rdfs:label "Waterinfo EC20, Kanaal Gent-Terneuzen Zevenaarshaven (NL), growing {name}"@en ;
    tss:producedBy ex:289441042 ;
    tss:from "2021-03-03T23:15:00Z"^^xsd:dateTime ;
    tss:rangeRequests true ;
    tss:dataFile <{file}> ;
    tss:timeField ex:zevenaarshaven-{id}-time-field ;
    tss:key ex:zevenaarshaven-{id}-time-field ;
    tss:hasField ex:zevenaarshaven-{id}-time-field, ex:zevenaarshaven-{id}-value-field .

ex:zevenaarshaven-{id}-time-field
    a tss:ContextField ;
    rdfs:label "Observation time"@en ;
    tss:columnName "time" ;
    tss:datatype xsd:dateTime .

ex:zevenaarshaven-{id}-value-field
    a tss:ValueField ;
    rdfs:label "Electrical conductivity at 20 °C, Kanaal Gent-Terneuzen Zevenaarshaven (NL)"@en ;
    tss:columnName "value" ;
    tss:datatype xsd:double ;
    tss:observedProperty quantitykind:ElectricConductivity ;
    tss:unit unit:MicroS-PER-CentiM ;
    tss:sensor ex:sensor-zevenaarshaven-ec20 ;
    tss:featureOfInterest ex:water-zevenaarshaven .
"""
EXTERNAL = {
    "csv": dict(id="gc", cls="CSVSource", name="CSV", file="zevenaarshaven_growing.csv",
                what="a CSV data file", unit="record"),
    "arrow": dict(id="ga", cls="ArrowSource", name="Arrow IPC stream", file="zevenaarshaven_growing.arrows",
                  what="an Arrow data file in the IPC streaming format", unit="record batch"),
}
ARROW_SCHEMA = pa.schema([("time", pa.timestamp("us", tz="UTC")), ("value", pa.float64())])


def metadata(kind: str) -> str:
    return METADATA.format(**EXTERNAL[kind])


def ended_metadata(kind: str, meta: str, rows: list, generated: str) -> str:
    """The metadata document after the writer ended the source (§ending, step 2)."""
    x = EXTERNAL[kind]["id"]
    return (meta + "\n# The writer ended the source: it added tss:immutable true and the summary, and changed nothing else.\n"
            f"ex:zevenaarshaven-{x}-source tss:immutable true .\n\n"
            + summary_text(f"ex:zevenaarshaven-{x}-value-field", rows, generated))


def csv_states(rows: list) -> list:
    return cumulative([b"time,value\n"] + [f"{t},{v}\n".encode() for t, v in rows])


def arrow_batch(time: str, value: str, schema=ARROW_SCHEMA):
    return pa.record_batch([pa.array([tr.parse_time(time)], type=schema.field("time").type),
                            pa.array([float(value)], type=schema.field("value").type)], schema=schema)


def arrow_states(rows: list, schema=ARROW_SCHEMA) -> tuple:
    """The stream after each Write (the schema message first), and the stream with the end-of-stream marker."""
    sink = io.BytesIO()
    writer = pa.ipc.new_stream(sink, schema)
    states = [schema.serialize().to_pybytes()]      # the writer writes the schema message with the first batch
    for t, v in rows:
        writer.write_batch(arrow_batch(t, v, schema))
        states.append(sink.getvalue())
    writer.close()
    return states, sink.getvalue()


def arrow_file(rows: list) -> bytes:
    sink = io.BytesIO()
    writer = pa.ipc.new_file(sink, ARROW_SCHEMA)
    for t, v in rows:
        writer.write_batch(arrow_batch(t, v))
    writer.close()
    return sink.getvalue()


def arrow_units(data: bytes) -> list:
    """The complete encapsulated messages at the start of an IPC stream: (kind, start, end, message)."""
    units, pos = [], 0
    while pos < len(data):
        if data[pos:pos + 8] == EOS:
            units.append(("end-of-stream", pos, pos + 8, None))
            pos += 8
            continue
        reader = pa.BufferReader(pa.py_buffer(data[pos:]))
        try:
            message = pa.ipc.read_message(reader)
        except (OSError, EOFError, pa.ArrowInvalid):
            break                                    # not complete yet
        units.append((message.type, pos, pos + reader.tell(), message))
        pos += reader.tell()
    return units


def csv_complete(data: bytes) -> int:
    """The length of the complete records: up to the last line break outside a quoted cell."""
    end, quoted = 0, False
    for i, b in enumerate(data):
        if b == 0x22:
            quoted = not quoted
        elif b == 0x0A and not quoted:
            end = i + 1
    return end


def layout(kind: str, data: bytes) -> dict:
    """The columns, the length of the complete units, the number of rows, and the end-of-stream marker."""
    if kind == "csv":
        complete = csv_complete(data)
        lines = data[:complete].decode("utf-8").splitlines()
        return {"columns": next(csv.reader(lines[:1]), None), "complete": complete,
                "rows": max(len(lines) - 1, 0), "eos": None, "file_format": False}
    if data.startswith(b"ARROW1"):
        reader = pa.ipc.open_file(pa.py_buffer(data))
        return {"columns": reader.schema, "complete": len(data), "rows": reader.num_record_batches,
                "eos": None, "file_format": True}
    units = arrow_units(data)
    schema = pa.ipc.read_schema(units[0][3]) if units and units[0][0] == "schema" else None
    eos = next((u for u in units if u[0] == "end-of-stream"), None)
    return {"columns": schema, "complete": units[-1][2] if units else 0,
            "rows": sum(u[0] == "record batch" for u in units), "eos": eos and eos[2], "file_format": False}


def external_content(kind: str, meta: str, data: bytes, written: list) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        document = Path(tmp) / f"zevenaarshaven_{kind}.ttl"
        document.write_text(meta, encoding="utf-8")
        (Path(tmp) / EXTERNAL[kind]["file"]).write_bytes(data)
        graph = Graph().parse(document)
        source_iri = URIRef(EX + f"zevenaarshaven-{EXTERNAL[kind]['id']}-source")
        return content_problems(graph, document, source_iri, written)


def check_external(kind: str, states: list, written: list) -> dict:
    """The checks on the states (metadata document, data file) of a CSV or Arrow source after each
    Write, and after the ending. L2 is about messages: it does not apply."""
    found, previous, ended_rows, shapes = Findings(), None, None, {}
    base = (FIXTURES / f"zevenaarshaven_{kind}.ttl").as_uri()
    for n, (meta, data) in enumerate(states):
        g = Graph().parse(data=meta, format="turtle", publicID=base)
        source = URIRef(EX + f"zevenaarshaven-{EXTERNAL[kind]['id']}-source")
        immutable = bool({str(o) for o in g.objects(source, TSS.immutable)} & {"true", "1"})
        current, shape = interpretation(g), layout(kind, data)
        if previous is not None:
            if not isomorphic(previous["interpretation"], current):
                found.add("L1 interpretation", n, "the description of the source differs from that of the previous state")
            if shape["columns"] != previous["layout"]["columns"]:
                found.add("L1 interpretation", n, "the header line names other columns" if kind == "csv" else "the schema changed")
        if shape["file_format"]:
            found.add("L3 units", n, "the data file is in the IPC file format, not the streaming format")
        elif shape["complete"] != len(data):
            found.add("L3 units", n, "the data file ends with an incomplete " + ("record" if kind == "csv" else "message"))
        if shape["eos"] is not None and not immutable:
            found.add("L3 units", n, "the end-of-stream marker is written while the source grows")
        if shape["eos"] is not None and shape["eos"] != shape["complete"]:
            found.add("L3 units", n, "messages follow the end-of-stream marker")
        if (None, TSS.summary, None) in g and not immutable:
            found.add("L4 finalisation", n, "a summary while the source grows")
        if previous is not None and previous["immutable"] and not immutable:
            found.add("L4 finalisation", n, "the source grows again after it ended")
        if immutable and ended_rows is None:
            ended_rows = shape["rows"]
        elif ended_rows is not None and shape["rows"] > ended_rows:
            found.add("L4 finalisation", n, "a row is added after the source ended")
        if previous is not None and ranged(previous["interpretation"]) and not data.startswith(previous["data"]):
            found.add("L5 byte prefix", n, "the data file does not start with the bytes of the previous state")
        if meta not in shapes:
            shapes[meta] = violations(g, "external")
        for v in shapes[meta]:
            found.add("L0 structure", n, f"SHACL violation {v}")
        if n == len(states) - 1:
            for problem in external_content(kind, meta, data, written):
                found.add("L0 structure", n, problem)
        previous = {"interpretation": current, "layout": shape, "immutable": immutable, "data": data}
    out = dict(found)
    out["L2 dependencies"] = None
    return out


# --- Following over HTTP -----------------------------------------------------------------------

class Server(http.server.ThreadingHTTPServer):
    """Serves one resource from memory: Range requests and byte ranges [RFC 9110, §14], strong
    ETags and If-None-Match. With ignore_range, it answers every request with 200 and the whole
    representation."""
    daemon_threads = True

    def __init__(self, ignore_range: bool = False):
        super().__init__(("127.0.0.1", 0), Handler)
        self.body, self.ignore_range = b"", ignore_range


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, status: int, headers: dict, body: bytes = b"") -> None:
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        if status != 304:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        body, ignore = self.server.body, self.server.ignore_range
        tag = '"' + hashlib.sha256(body).hexdigest()[:20] + '"'
        headers = {"ETag": tag, "Accept-Ranges": "none" if ignore else "bytes"}
        if self.headers.get("If-None-Match") == tag:
            return self.answer(304, headers)
        m = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if m is None or ignore:
            return self.answer(200, headers, body)
        start = int(m.group(1))
        end = min(int(m.group(2)), len(body) - 1) if m.group(2) else len(body) - 1
        if start >= len(body):
            return self.answer(416, {**headers, "Content-Range": f"bytes */{len(body)}"})
        return self.answer(206, {**headers, "Content-Range": f"bytes {start}-{end}/{len(body)}"}, body[start:end + 1])


class LogUnits:
    """RDF messages; the state to keep: the prefixes in force (§range-requests)."""
    def reset(self):
        self.prefixes = []

    def complete(self, data: bytes) -> int:
        return max((m.end() for m in DELIMITER.finditer(data)), default=0)

    def read(self, data: bytes) -> tuple:
        rows, ended = [], False
        for part in tr.split_messages(data.decode("utf-8")):
            body = []
            for line in part.splitlines():
                m = PREFIX_LINE.match(line)
                if m:
                    self.prefixes.append(f"@prefix {m.group(1)} {m.group(2)} .")
                elif not VERSION_LINE.match(line):
                    body.append(line)
            g = Graph().parse(data="\n".join(self.prefixes + body), format="turtle")
            rows += [(str(g.value(p, TSS.time)), str(g.value(p, TSS.value))) for p in g.objects(None, TSS.hasPoint)]
            ended = ended or any(str(o) in ("true", "1") for o in g.objects(None, TSS.immutable))
        return rows, ended


class CsvUnits:
    """CSV records; the state to keep: the header line, and so the columns of the fields."""
    def reset(self):
        self.header = None

    def complete(self, data: bytes) -> int:
        return csv_complete(data)

    def read(self, data: bytes) -> tuple:
        records = list(csv.reader(data.decode("utf-8").splitlines()))
        if self.header is None and records:
            self.header, records = records[0], records[1:]
        rows = []
        for record in records:
            time, value = record[self.header.index("time")], record[self.header.index("value")]
            if not (tr.valid_lexical(time, XSD.dateTime) and tr.valid_lexical(value, XSD.double)):
                raise ValueError(f"not a record of this source: {record}")
            rows.append((time, value))
        return rows, False


class ArrowUnits:
    """Encapsulated IPC messages; the state to keep: the schema (and dictionaries: none here)."""
    def reset(self):
        self.schema = None

    def complete(self, data: bytes) -> int:
        units = arrow_units(data)
        return units[-1][2] if units else 0

    def read(self, data: bytes) -> tuple:
        rows, ended = [], False
        for kind, _, _, message in arrow_units(data):
            if kind == "schema":
                self.schema = pa.ipc.read_schema(message)
            elif kind == "record batch":
                batch = pa.ipc.read_record_batch(message, self.schema)
                for r in batch.to_pylist():
                    rows.append((tr._columnar_text(r["time"], self.schema.field("time").type),
                                 tr._columnar_text(r["value"], self.schema.field("value").type)))
            elif kind == "end-of-stream":
                ended = True
        return rows, ended


class Consumer:
    """Follows a growing resource as §following says. mode "range": one full read, then
    `Range: bytes=N-` (§range-requests); mode "full": the whole representation again when its ETag
    changed (§following-full). With overlap k, it asks for `bytes=(N-k)-` and checks that the k
    bytes it read last did not change. A row whose time was emitted is not emitted again."""
    def __init__(self, url: str, units, mode: str = "range", overlap: int = 0):
        self.url, self.units, self.mode, self.k = url, units, mode, overlap
        self.n, self.tail, self.etag, self.ended = None, b"", None, False
        self.emitted, self.repeated, self.fallbacks, self.received, self.statuses = {}, 0, 0, 0, Counter()

    def request(self, headers: dict) -> tuple:
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url, headers=headers)) as response:
                status, body, answer = response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            status, body, answer = error.code, error.read(), error.headers
        self.received += len(body)
        self.statuses[status] += 1
        return status, body, answer

    def take(self, data: bytes) -> None:
        """Reads the complete units at the start of data, which starts at offset N."""
        length = self.units.complete(data)
        rows, ended = self.units.read(data[:length])
        for time, value in rows:
            key = tr.parse_time(time)
            if key in self.emitted:
                self.repeated += 1
            else:
                self.emitted[key] = float(value)
        self.ended = self.ended or ended
        self.n += length
        self.tail = (self.tail + data[:length])[-self.k:] if self.k else b""

    def full(self, body: bytes = None) -> None:
        if body is None:
            headers = {"Accept-Encoding": "identity"}
            if self.mode == "full" and self.etag:
                headers["If-None-Match"] = self.etag
            status, body, answer = self.request(headers)
            if status == 304:
                return
            self.etag = answer.get("ETag")
        self.units.reset()
        self.n, self.tail = 0, b""
        self.take(body)

    def fallback(self, body: bytes = None) -> None:
        self.fallbacks += 1
        self.full(body)

    def poll(self) -> None:
        if self.ended:
            return
        if self.n is None or self.mode == "full":
            return self.full()
        start = self.n - len(self.tail)
        status, body, answer = self.request({"Range": f"bytes={start}-", "Accept-Encoding": "identity"})
        if status == 206:
            first = int(re.match(r"bytes (\d+)-", answer["Content-Range"]).group(1))
            if first != start or body[:len(self.tail)] != self.tail:
                return self.fallback()
            try:
                return self.take(body[len(self.tail):])
            except Exception:   # noqa: BLE001 (new bytes that do not parse with the state kept)
                return self.fallback()
        if status == 416:
            if int(answer["Content-Range"].rsplit("/", 1)[1]) == self.n:
                return                                   # nothing was added
            return self.fallback()                       # the resource shrank
        if status == 200:
            return self.fallback(body)
        self.ended = True                                # 404 or 410: no longer served


def follow(states: list, units, mode: str = "range", overlap: int = 0, ignore_range: bool = False,
           mid_write: bool = False) -> dict:
    """Serves the states one after the other, and polls after each Write; in the middle of the
    sequence it polls once more, with nothing new. With mid_write, it also polls while half of
    the new bytes are written."""
    server = Server(ignore_range)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    consumer = Consumer(f"http://127.0.0.1:{server.server_address[1]}/resource", units, mode, overlap)
    whole = polls = 0
    try:
        for i, state in enumerate(states):
            bodies = [state]
            if mid_write and i:
                bodies.insert(0, state[:(len(states[i - 1]) + len(state)) // 2])
            if i == len(states) // 2:
                bodies.append(state)
            for body in bodies:
                if consumer.ended:
                    break
                server.body = body
                consumer.poll()
                polls += 1
                whole += len(body)
    finally:
        server.shutdown()
        server.server_close()
    return {"polls": polls, "requests": sum(consumer.statuses.values()),
            "statuses": {str(k): v for k, v in sorted(consumer.statuses.items())},
            "bytes_received": consumer.received, "bytes_whole_representation_every_poll": whole,
            "fallbacks": consumer.fallbacks, "rows_read_again": consumer.repeated,
            "points": len(consumer.emitted), "ended": consumer.ended, "emitted": consumer.emitted}


# --- Main --------------------------------------------------------------------------------------

def main() -> None:
    if not SERIES.is_file():
        raise SystemExit("run ../waterinfo/fetch_waterinfo.py first")
    rows = readings()
    generated = json.loads(MANIFEST.read_text())["retrieved"]

    # The three progressions
    messages, published_rows = log_messages(rows, generated)
    log = cumulative(messages)
    published = LOG_DOCUMENT.read_bytes()
    published_points = sorted((str(u["graph"].value(p, TSS.time)), str(u["graph"].value(p, TSS.value)))
                              for u in log_units(published) for p in u["graph"].objects(None, TSS.hasPoint))
    csv_meta, arrow_meta = metadata("csv"), metadata("arrow")
    csv_data = csv_states(rows)
    csv_seq = [(csv_meta, d) for d in csv_data] + [(ended_metadata("csv", csv_meta, rows, generated), csv_data[-1])]
    arrow_data, arrow_final = arrow_states(rows)
    arrow_ended = ended_metadata("arrow", arrow_meta, rows, generated)
    arrow_seq = [(arrow_meta, d) for d in arrow_data] + [(arrow_ended, arrow_data[-1]), (arrow_ended, arrow_final)]

    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / "zevenaarshaven_log_ended.ttl").write_bytes(log[-1])
    for kind, seq in (("csv", csv_seq), ("arrow", arrow_seq)):
        (FIXTURES / f"zevenaarshaven_{kind}_growing.ttl").write_text(seq[0][0], encoding="utf-8")
        (FIXTURES / f"zevenaarshaven_{kind}_ended.ttl").write_text(seq[-1][0], encoding="utf-8")
        (FIXTURES / EXTERNAL[kind]["file"]).write_bytes(seq[-1][1])

    report = {
        "readings": len(rows),
        "progressions": {
            "log": {"states": len(log), "bytes": len(log[-1]), "published_rows": published_rows,
                    "published_document_is_a_state": log[published_rows] == published,
                    "published_rows_are_the_first_readings": published_points == sorted(rows[:published_rows])},
            "csv": {"states": len(csv_seq), "bytes": len(csv_data[-1])},
            "arrow": {"states": len(arrow_seq), "bytes": len(arrow_final)},
        },
    }
    print("valid sequences ...", flush=True)
    report["valid"] = {"log": check_log(log, rows, structural="all"),
                       "csv": check_external("csv", csv_seq, rows),
                       "arrow": check_external("arrow", arrow_seq, rows)}

    # Variants: (id, error, target check or None for a valid variant, states)
    header = HEADER.match(messages[0]).group(0)
    assert RANGE_LINE.encode() in messages[0] and b'tss:from "2021-03-03T23:15:00Z"' in messages[0]
    no_range = cumulative([m.replace(RANGE_LINE.encode(), b"") for m in messages])
    lv1 = no_range[:K + 1] + [s.replace(b'tss:from "2021-03-03T23:15:00Z"', b'tss:from "2021-03-03T00:00:00Z"')
                              for s in no_range[K + 1:]]
    lv8 = log[:K + 1] + [s.replace(b"\n    tss:", b"\n\ttss:") for s in log[K + 1:]]
    end_only = messages[-1][:-len(b"MESSAGE\n")]
    log_variants = [
        ("LV1", "the window of the source moves after 10 rows (tss:from earlier, every row still inside); "
                "the log states no tss:rangeRequests, so it may be written anew", "L1 interpretation", lv1),
        ("LV2", "a row message comes before the description message", "L2 dependencies",
         cumulative([header + messages[1], messages[0][len(header):]] + messages[2:])),
        ("LV3", "a row message comes after the ending message", "L4 finalisation",
         cumulative(messages[:-2] + [messages[-1], messages[-2]])),
        ("LV4", "a summary message after 10 rows, while the source grows", "L4 finalisation",
         cumulative(messages[:K + 1] + [summary_text(LOG_FIELD, rows[:K], generated).encode() + b"MESSAGE\n"] + messages[K + 1:])),
        ("LV5", "two rows in one row message", "L3 units",
         cumulative(messages[:K + 1] + [messages[K + 1][:-len(b"MESSAGE\n")] + messages[K + 2]] + messages[K + 3:])),
        ("LV6", "the ending message has no message delimiter", "L3 units", cumulative(messages[:-1] + [end_only])),
        ("LV7", "the description states tss:immutable false", "L4 finalisation",
         cumulative([messages[0].replace(RANGE_LINE.encode(), b"    tss:immutable false ;\n" + RANGE_LINE.encode())] + messages[1:])),
        ("LV8", "the log is written anew after 10 rows: the same messages, indented with tabs", "L5 byte prefix", lv8),
        ("LV9", "a Write is repeated: the same row is appended twice", "L0 structure",
         cumulative(messages[:K + 1] + [messages[K]] + messages[K + 1:])),
        ("VV1", "rows are written out of time order (two Writes swapped)", None,
         cumulative(messages[:K + 1] + [messages[K + 2], messages[K + 1]] + messages[K + 3:])),
    ]
    csv_ended = csv_seq[-1][0]
    csv_nr, csv_ended_nr = csv_meta.replace(RANGE_LINE, ""), csv_ended.replace(RANGE_LINE, "")

    def newest_first(d: bytes) -> bytes:
        return b"time,value\n" + b"".join(sorted(d.split(b"\n", 1)[1].splitlines(keepends=True), reverse=True))

    csv_ended_45 = ended_metadata("csv", csv_meta, rows[:-1], generated)
    csv_variants = [
        ("CV1", "ending the source also adds tss:until", "L1 interpretation",
         csv_seq[:-1] + [(csv_ended + 'ex:zevenaarshaven-gc-source tss:until "2021-03-05T00:00:00Z"^^xsd:dateTime .\n', csv_data[-1])]),
        ("CV2", "the value column is renamed in the header line after 10 records; the source states no "
                "tss:rangeRequests, so the file may be written anew", "L1 interpretation",
         [(csv_nr, d if n <= K else d.replace(b"time,value\n", b"time,ec20\n", 1)) for n, d in enumerate(csv_data)]
         + [(csv_ended_nr, csv_data[-1].replace(b"time,value\n", b"time,ec20\n", 1))]),
        ("CV3", "the file is sorted anew (newest first) after 10 records", "L5 byte prefix",
         [(csv_meta, d if n <= K else newest_first(d)) for n, d in enumerate(csv_data)] + [(csv_ended, newest_first(csv_data[-1]))]),
        ("CV4", "a state in which the last record has no line break", "L3 units",
         [(csv_meta, d[:-1] if n == K else d) for n, d in enumerate(csv_data)] + [csv_seq[-1]]),
        ("CV5", "the source is ended after 45 records, and the 46th is appended after that", "L4 finalisation",
         [(csv_meta, d) for d in csv_data[:-1]] + [(csv_ended_45, csv_data[-2]), (csv_ended_45, csv_data[-1])]),
        ("VV2", "the source states no tss:rangeRequests, and the file is written anew (newest first) on every Write", None,
         [(csv_nr, newest_first(d)) for d in csv_data] + [(csv_ended_nr, newest_first(csv_data[-1]))]),
    ]
    arrow_nr, arrow_ended_nr = arrow_meta.replace(RANGE_LINE, ""), arrow_ended.replace(RANGE_LINE, "")
    float32 = pa.schema([("time", pa.timestamp("us", tz="UTC")), ("value", pa.float32())])
    as_float32 = arrow_states(rows, float32)
    arrow_variants = [
        ("AV1", "the data file is in the IPC file format, written anew on every Write", "L3 units",
         [(arrow_meta, arrow_file(rows[:n])) for n in range(len(rows) + 1)] + [(arrow_ended, arrow_file(rows))]),
        ("AV2", "the end-of-stream marker is written after 10 record batches, while the source grows", "L3 units",
         [(arrow_meta, d if n <= K else arrow_data[K] + EOS + d[len(arrow_data[K]):]) for n, d in enumerate(arrow_data)]
         + [(arrow_ended, arrow_data[K] + EOS + arrow_final[len(arrow_data[K]):])]),
        ("AV3", "after 10 record batches, the stream is written anew with a float32 value column; the source "
                "states no tss:rangeRequests, so it may be written anew", "L1 interpretation",
         [(arrow_nr, d if n <= K else as_float32[0][n]) for n, d in enumerate(arrow_data)] + [(arrow_ended_nr, as_float32[1])]),
    ]
    report["variants"] = []
    for representation, variants in (("log", log_variants), ("csv", csv_variants), ("arrow", arrow_variants)):
        for vid, what, target, states in variants:
            print(f"variant {vid} ...", flush=True)
            found = check_log(states, rows) if representation == "log" else check_external(representation, states, rows)
            failed = [c for c, v in found.items() if v]
            report["variants"].append({
                "id": vid, "representation": representation, "what": what, "target": target, "failed": failed,
                "as_expected": (target in failed) if target else not failed,
                "findings": {c: v for c, v in found.items() if v}})

    # Following over HTTP
    print("HTTP ...", flush=True)
    vv2 = [d for _, d in csv_variants[-1][3][:-1]]
    http_cases = [
        ("H1", "log", "the RDF Message Log, followed with Range requests", log, LogUnits, {}, "no fall-back; 416 when nothing was added"),
        ("H2", "csv", "the CSV data file, followed with Range requests", csv_data, CsvUnits, {}, "no fall-back; 416 when nothing was added"),
        ("H3", "arrow", "the Arrow IPC stream, followed with Range requests, until the end-of-stream marker",
         arrow_data + [arrow_final], ArrowUnits, {}, "no fall-back; the consumer stops at the end-of-stream marker"),
        ("H4", "log", "the log is written anew after 10 rows (LV8); the consumer checks the last 32 bytes it read",
         lv8, LogUnits, {"overlap": 32}, "one fall-back, and no row emitted twice"),
        ("H5", "log", "the consumer also polls in the middle of every Write: half a message is served",
         log, LogUnits, {"mid_write": True}, "a message is read only once its delimiter is there; no fall-back"),
        ("H6", "csv", "the server ignores Range requests: 200 with the whole file",
         csv_data, CsvUnits, {"ignore_range": True}, "a fall-back on every 200 after the first read; no row emitted twice"),
        ("H7", "csv", "VV2: no tss:rangeRequests, the file written anew on every Write; the consumer reads the whole "
                      "file again when its ETag changed (§following-full)", vv2, CsvUnits, {"mode": "full"},
         "no Range request; 304 when nothing was added"),
    ]
    expected_rows = {tr.parse_time(t): float(v) for t, v in rows}
    report["http"] = []
    for hid, representation, what, states, units, options, expected in http_cases:
        result = follow(states, units(), **options)
        result["correct"] = result.pop("emitted") == expected_rows
        report["http"].append({"id": hid, "representation": representation, "what": what, "expected": expected, **result})

    report["versions"] = {"python": platform.python_version(), "pyshacl": pyshacl.__version__,
                          "rdflib": rdflib.__version__, "pyarrow": pa.__version__}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "lifecycle_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    write_markdown(report)


def write_markdown(report: dict) -> None:
    p, valid = report["progressions"], report["valid"]
    variants_ok = sum(v["as_expected"] for v in report["variants"])
    lines = [
        "# Lifecycle and message-sequence checks", "",
        f"Generated by `check_lifecycle.py` (Python {report['versions']['python']}, pyshacl {report['versions']['pyshacl']}, "
        f"rdflib {report['versions']['rdflib']}, pyarrow {report['versions']['pyarrow']}).", "",
        f"{report['readings']} Zevenaarshaven EC20 readings, written one at a time to three growing sources that state "
        "`tss:rangeRequests true`:", "",
        f"- **RDF Message Log** of `ex:zevenaarshaven-tv-source`: {p['log']['states']} states (the description, "
        f"{report['readings']} row messages, the ending message), {p['log']['bytes']} bytes at the end. The first "
        f"{p['log']['published_rows'] + 1} states are the published document, message by message "
        f"(state {p['log']['published_rows']} is the document itself: {p['log']['published_document_is_a_state']}; "
        f"its rows are the first readings: {p['log']['published_rows_are_the_first_readings']}).",
        f"- **CSV data file**: {p['csv']['states']} states (the header line, one record per reading, then the metadata "
        f"document gains `tss:immutable true` and the summary), {p['csv']['bytes']} bytes.",
        f"- **Arrow IPC stream**: {p['arrow']['states']} states (the schema message, one record batch per reading, the "
        f"ending in the metadata document, then the end-of-stream marker), {p['arrow']['bytes']} bytes.", "",
        "## The valid sequences", "",
        "| Check | RDF Message Log | CSV data file | Arrow IPC stream |", "|---|---|---|---|"]
    for check in CHECKS:
        cells = []
        for r in ("log", "csv", "arrow"):
            v = valid[r][check]
            cells.append("n/a" if v is None else "passed" if not v else f"FAILED: {'; '.join(v[:2])}")
        lines.append(f"| {check} | " + " | ".join(cells) + " |")
    lines += ["", "## Variants", "",
              f"**{variants_ok} of {len(report['variants'])} variants behaved as expected**: each variant with an error "
              "fails the check it targets, and each valid variant passes every check.", "",
              "| Variant | Source | Change | Expected | Failed checks | Result |", "|---|---|---|---|---|---|"]
    for v in report["variants"]:
        lines.append(f"| {v['id']} | {v['representation']} | {v['what']} | {v['target'] or 'valid: no check fails'} | "
                     f"{', '.join(v['failed']) or '-'} | {'as expected' if v['as_expected'] else 'UNEXPECTED'} |")
    lines += ["", "## Following over HTTP", "",
              "A local server answers Range requests (206, 416 with `Content-Range: bytes */L`), conditional requests "
              "(ETag, 304) and plain requests (200), as RFC 9110 says. The consumer follows §following: one full read, "
              "then `Range: bytes=N-` with `Accept-Encoding: identity`, only complete units, and a fall-back to a full "
              "read when the new bytes cannot be read; it never emits a row twice. It polls after every Write, and once "
              "more in the middle of the sequence, when nothing was added. Other servers are not tested.", "",
              "| Case | Source | What | Expected | Requests | Statuses | Bytes received | Bytes, whole representation at every poll | Fall-backs | Rows | Correct |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for h in report["http"]:
        statuses = ", ".join(f"{k}: {v}" for k, v in h["statuses"].items())
        lines.append(f"| {h['id']} | {h['representation']} | {h['what']} | {h['expected']} | {h['requests']} | {statuses} | "
                     f"{h['bytes_received']} | {h['bytes_whole_representation_every_poll']} | {h['fallbacks']} | "
                     f"{h['points']} | {h['correct']} |")
    lines += ["", "## Findings of the variants", ""]
    for v in report["variants"]:
        for check, found in v["findings"].items():
            lines.append(f"- {v['id']}, {check}: {'; '.join(found[:3])}")
    (RESULTS / "lifecycle_report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
