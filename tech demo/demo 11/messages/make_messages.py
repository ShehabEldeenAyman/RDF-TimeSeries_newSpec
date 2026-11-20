"""Stream a TSS source as RDF messages, following the RDF Message Stream Profile.

    python make_messages.py zevenaarshaven_ec20.ttl
    python make_messages.py zevenaarshaven_ec20.ttl --output other.ttl

Reads the metadata document of one tss:CSVSource and its data file, and writes an RDF Message
Log (version label "1.2-messages"). Only a tss:EmbeddedRDFSource links to points with
tss:hasPoint, so the log describes a tss:EmbeddedRDFSource of its own, <#source>, that holds
the same readings and states prov:wasDerivedFrom the CSV source. Its fields are IRIs in the log
too, named after their columns, such as <#ec20>. The log holds:

  1. a description message: the new source and its fields, with what the CSV source states
     about its readings, without what describes the data file (tss:dataFile, tss:delimiter,
     ...), and without tss:immutable and the stored summaries;
  2. one point message per row in the time window: <#source> tss:hasPoint one point, with
     the IRI of each field as predicate and a literal with the field's tss:datatype;
  3. when the CSV source is immutable, an ending message: tss:immutable true and the stored
     summaries, which describe the same readings.

The merge of the messages is the metadata document of an immutable tss:EmbeddedRDFSource. Every
message ends with the delimiter MESSAGE. Literals keep the text they have in the metadata
document and the data file. Only CSV sources without a time grid and without a value encoding
are supported.
"""
import argparse
import csv
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import rdflib
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import PROV, RDF, RDFS, XSD

rdflib.NORMALIZE_LITERALS = False  # keep every literal as it is written

TSS = Namespace("https://w3id.org/tss#")
DCTERMS = Namespace("http://purl.org/dc/terms/")
DELIMITER = "MESSAGE"
# What describes a data file or JSON points: an embedded RDF source does not state it
DATA_FILE = {TSS[name] for name in (
    "dataFile delimiter encoding header points timeBase timeStep stepField columnIndex "
    "valueEncoding baseline").split()}
# What the ending message holds
ENDING = {TSS.immutable, TSS.summary}
# The order in which the statements of a node are written: as in the specification
ORDER = [RDF.type, PROV.wasDerivedFrom, RDFS.label, RDFS.comment] + [TSS[name] for name in (
    "sourceDescription immutable producedBy from until timeField key hasField columnName "
    "datatype language minimum maximum observedProperty unit sensor featureOfInterest "
    "describesField fieldDescription summary missingValuePolicy count missing firstTime "
    "lastTime observedMinimum observedMaximum mean median generatedAtTime").split()]


class Writer:
    """Writes Turtle statements with the prefixes of the metadata document, and keeps track of
    the prefixes it used. IRIs in the log itself (<log#name>) are written with the prefix ':'."""

    def __init__(self, graph: Graph, log: str):
        self.graph, self.log, self.used = graph, log, {}
        self.objects = {}  # the order of objects, such as the fields of tss:hasField

    def term(self, term) -> str:
        if term == RDF.type:
            return "a"
        if isinstance(term, URIRef):
            if str(term).startswith(self.log + "#"):
                self.used[""] = "#"
                return ":" + str(term)[len(self.log) + 1:]
            for prefix, namespace in list(self.graph.namespaces()) + [("prov", PROV)]:
                local = str(term)[len(namespace):]
                if prefix and str(term).startswith(str(namespace)) and local and all(c.isalnum() or c in "-_." for c in local):
                    self.used[prefix] = str(namespace)
                    return f"{prefix}:{local}"
            return f"<{term}>"
        if isinstance(term, Literal) and term.datatype == XSD.boolean and str(term) in ("true", "false"):
            return str(term)
        if isinstance(term, Literal) and term.datatype is not None:
            self.term(term.datatype)  # registers the prefix of the datatype
        return term.n3(self.graph.namespace_manager)

    def node(self, graph: Graph, subject, indent: int) -> str:
        """The predicates and objects of subject, with its blank nodes written inline as [ ]."""
        pad = " " * indent
        by_predicate = {}
        for p, o in graph.predicate_objects(subject):
            by_predicate.setdefault(p, []).append(o)
        rank = lambda p: (ORDER.index(p), "") if p in ORDER else (len(ORDER), str(p))
        lines = []
        for p, objects in sorted(by_predicate.items(), key=lambda item: rank(item[0])):
            written = []
            for o in sorted(objects, key=lambda o: self.objects.get(o, len(self.objects))):
                if isinstance(o, BNode):
                    written.append("[\n" + self.node(graph, o, indent + 4) + f"\n{pad}]")
                else:
                    written.append(self.term(o))
            lines.append(f"{pad}{self.term(p)} " + f",\n{pad}    ".join(written))
        return " ;\n".join(lines)

    def statements(self, graph: Graph, first: list) -> str:
        """Every IRI subject of graph: those in first, in that order, then the others."""
        others = sorted({s for s, _, _ in graph if not isinstance(s, BNode)} - set(first), key=str)
        subjects = [s for s in first if (s, None, None) in graph] + others
        return "\n\n".join(f"{self.term(s)}\n" + self.node(graph, s, 4) + " ." for s in subjects)


def describe(graph: Graph, node, into: Graph) -> None:
    """Copy the concise bounded description of node: its statements, and those of its blank nodes."""
    for p, o in graph.predicate_objects(node):
        into.add((node, p, o))
        if isinstance(o, BNode):
            describe(graph, o, into)


def moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def stream(metadata: Path, output: Path) -> str:
    graph = Graph()
    graph.parse(metadata, format="turtle", publicID=metadata.resolve().as_uri())
    log = output.resolve().as_uri()
    writer = Writer(graph, log)

    sources = list(graph.subjects(RDF.type, TSS.CSVSource))
    if len(sources) != 1:
        raise SystemExit("the metadata document must describe exactly one tss:CSVSource")
    original = sources[0]
    if graph.value(original, TSS.timeBase) is not None or any(graph.objects(None, TSS.valueEncoding)):
        raise SystemExit("time grids and value encodings are not supported by this example")
    original_time = graph.value(original, TSS.timeField)
    data = Path(url2pathname(urlparse(str(graph.value(original, TSS.dataFile))).path))
    delimiter = str(graph.value(original, TSS.delimiter) or ",")
    encoding = str(graph.value(original, TSS.encoding) or "utf-8")
    with open(data, newline="", encoding=encoding) as file:
        header = next(csv.reader(file, delimiter=delimiter))

    # The fields in the order of their columns, the time field first
    column_of = {f: str(graph.value(f, TSS.columnName)) for f in graph.objects(original, TSS.hasField)}
    position = lambda f: header.index(column_of[f]) if column_of[f] in header else len(header)
    originals = sorted(column_of, key=lambda f: (f != original_time, position(f), str(f)))

    # The new source and its fields: IRIs in the log, the fields named after their columns
    source = URIRef(f"{log}#source")
    rename = {original: source}
    for field in originals:
        name = re.sub(r"[^A-Za-z0-9_-]", "_", column_of[field]) or "field"
        while URIRef(f"{log}#{name}") in rename.values():
            name += "_"
        rename[field] = URIRef(f"{log}#{name}")
    new = lambda term: rename.get(term, term)
    fields = [rename[f] for f in originals]
    writer.objects = {field: i for i, field in enumerate(fields)}

    # The ending statements: tss:immutable, and every summary with its blank node
    ending = Graph()
    ending.add((source, TSS.immutable, Literal(True)))
    for field in originals:
        for summary in graph.objects(field, TSS.summary):
            ending.add((new(field), TSS.summary, summary))
            describe(graph, summary, ending)
    summaries = {s for s, _, _ in ending if isinstance(s, BNode)}

    # 1. The description message: the new source, read from the original one
    description = Graph()
    description.add((source, RDF.type, TSS.EmbeddedRDFSource))
    description.add((source, PROV.wasDerivedFrom, original))
    for s, p, o in graph:
        if p in DATA_FILE or p in ENDING or s in summaries or (s == original and p == RDF.type):
            continue
        description.add((new(s), p, new(o)))
    messages = ["<> dcterms:conformsTo tss:MessageStreamProfile .\n\n"
                + writer.statements(description, [source] + fields)]
    writer.used.update({"dcterms": str(DCTERMS), "tss": str(TSS)})

    # 2. One point message per row in the time window
    start, end = graph.value(original, TSS["from"]), graph.value(original, TSS.until)
    with open(data, newline="", encoding=encoding) as file:
        for number, row in enumerate(csv.DictReader(file, delimiter=delimiter), start=2):
            time = row[column_of[original_time]]
            if (start is not None and moment(time) < moment(str(start))) or \
                    (end is not None and moment(time) >= moment(str(end))):
                continue  # outside the time window
            values = []
            for field in originals:
                text = row.get(column_of[field], "")
                if text == "":
                    continue  # an empty value has no triple
                literal = Literal(text, datatype=graph.value(field, TSS.datatype))
                if literal.ill_typed:
                    raise SystemExit(f"line {number}: {text!r} is not a valid {writer.term(literal.datatype)}")
                values.append(f"    {writer.term(new(field))} {writer.term(literal)}")
            messages.append(f"{writer.term(source)} tss:hasPoint [\n" + " ;\n".join(values) + "\n] .")

    # 3. The ending message
    if graph.value(original, TSS.immutable) == Literal(True):
        messages.append(writer.statements(ending, [source] + fields))

    lines = ['VERSION "1.2-messages"']
    lines += [f"PREFIX {prefix}: <{namespace}>" for prefix, namespace in sorted(writer.used.items())]
    lines += [
        "",
        f"# Generated by make_messages.py from {metadata.name}: an RDF Message Log that follows the",
        "# RDF Message Stream Profile. It describes a tss:EmbeddedRDFSource of its own, :source, with",
        "# the readings of the CSV source it was derived from. Message 1 describes :source and its",
        "# fields; each next message holds one point (one row of the data file); the last message",
        "# ends the source. The timestamp of a point message is the point's value for :time.",
        "",
    ]
    return "\n".join(lines) + "\n" + "".join(f"{message}\n{DELIMITER}\n" for message in messages)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("metadata", type=Path, help="the metadata document of a tss:CSVSource")
    parser.add_argument("--output", type=Path, help="default: <metadata>_messages.ttl")
    args = parser.parse_args()
    output = args.output or args.metadata.with_name(args.metadata.stem + "_messages.ttl")
    output.write_text(stream(args.metadata, output), encoding="utf-8")
    print(f"written {output}")


if __name__ == "__main__":
    main()
