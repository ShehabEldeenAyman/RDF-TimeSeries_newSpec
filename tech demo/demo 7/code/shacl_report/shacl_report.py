"""Validate a TSS2 metadata endpoint against SHACL shapes, and write the validation report as RDF.

Everything is read as RDF (rdflib): the metadata, the shapes, and for an embedded source
(tss:embedded true) also its points. tss:points is an rdf:JSON literal; lift_points
(code/TSS2RDF/tss2rdf.py) reads it as JSON-LD, with a context made from the source's
fields, so each value becomes a triple the shapes can check:
    _:p1  ex:zevenaarshaven-ec20-value-field  "4797.72"^^xsd:double .
This is the same check the dashboard's SHACL tab does (code/webapp/backend/main.py).

The report is the sh:ValidationReport graph pyshacl gives, saved as Turtle.
A short summary is printed in the terminal.

Examples:
    python shacl_report.py "../../metadata endpoint/waterinfo_289423042_tss2.ttl"
    python shacl_report.py "../../metadata endpoint/waterinfo_289441042_tss2_points_embedded.ttl" ../../SHACL/embedded_shapes.ttl
    python shacl_report.py endpoint.ttl shapes.ttl --out my_report.ttl

Without shapes, the bundled shapes for the source's tss:embedded are used, like the dashboard does:
SHACL/embedded_shapes.ttl (true) or SHACL/external_shapes.ttl (false).
Without --out, the report goes to reports/<endpoint>__<shapes>.ttl next to this script.
"""
import argparse
import logging
from pathlib import Path
import sys
import warnings

from pyshacl import validate
from rdflib import BNode, Graph, Literal, RDF, URIRef
from rdflib.namespace import DCTERMS, SH
from rdflib.util import guess_format

# The code directory (one folder up) contains the TSS2RDF converter. Its lift_points reads
# embedded points as RDF, and TSS is the TSS vocabulary (tss:Source, tss:points, ...).
code_directory = str(Path(__file__).resolve().parents[1])
if code_directory not in sys.path:
    sys.path.insert(0, code_directory)

# exact_turtle writes the report as Turtle with every literal's text exactly as it is
# (rdflib's own short form would write a wrong "yes"^^xsd:boolean as yes, which is broken Turtle).
# NORMALIZE_LOCK: pyshacl's validate() holds it, so it takes turns with lift_points (see validation_report).
from TSS2RDF.tss2rdf import NORMALIZE_LOCK, TSS, exact_turtle, lift_points

# This file lives in demo 7/code/shacl_report/, so the SHACL folder is two folders up
SHACL_DIR = Path(__file__).resolve().parents[2] / "SHACL"
# The bundled shapes for each storage profile: tss:embedded true or false
DEFAULT_SHAPES = {True: "embedded_shapes.ttl", False: "external_shapes.ttl"}

# The three SHACL severities, most severe first, and how the summary writes them
SEVERITIES = [SH.Violation, SH.Warning, SH.Info]
SEVERITY_NAMES = {SH.Violation: "Violation", SH.Warning: "Warning", SH.Info: "Info"}
# The summary lists at most this many results; the report file has all of them
MAX_LISTED_RESULTS = 20


def read_rdf(path):
    """An RDF file as an rdflib graph. The syntax comes from the file extension
    (.ttl, .jsonld, .rdf, ...); a file with another extension is read as Turtle.
    Relative IRIs in it (like <../data/x.csv>) are read against the file's own address."""
    graph = Graph()
    syntax = guess_format(str(path)) or "turtle"
    graph.parse(path, format=syntax, publicID=path.resolve().as_uri())
    return graph


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


def default_shapes(graph):
    """The bundled shapes file for the metadata's storage profile (the tss:embedded of its sources).
    None when that can't be told: no source, a source without one boolean tss:embedded,
    or sources that disagree."""
    modes = set()
    for source in graph.subjects(RDF.type, TSS.Source):
        modes.add(embedded_mode(graph, source))
    if len(modes) != 1:
        return None
    mode = modes.pop()
    if mode is None:
        return None
    return SHACL_DIR / DEFAULT_SHAPES[mode]


def data_graph(graph):
    """The graph SHACL validates: the metadata, plus the points of each embedded source
    read as RDF (lift_points). Gives (the data graph, the number of point triples, problems):
    problems lists (source, reason) for the sources whose tss:points can't be read."""
    data = Graph()
    for triple in graph:
        data.add(triple)

    point_triples = 0
    problems = []
    for source in graph.subjects(RDF.type, TSS.Source):
        if embedded_mode(graph, source) is not True:
            continue  # an external data file is not read by SHACL
        if (source, TSS.points, None) not in graph:
            continue  # the shapes report a missing tss:points themselves (S6)
        try:
            points = lift_points(graph, source)
        except ValueError as error:
            problems.append((source, str(error)))
            continue
        for triple in points:
            data.add(triple)
        point_triples += len(points)
    return data, point_triples, problems


def add_points_problem(report, report_node, source, reason):
    """Add a result for points that can't be read as JSON-LD at all (bad JSON, not rdf:JSON, ...).
    SHACL can't look inside the rdf:JSON literal, so the report gets this result here,
    like the shapes' own SPARQL checks give it (and like the dashboard does)."""
    result = BNode()
    report.add((report_node, SH.result, result))
    report.add((result, RDF.type, SH.ValidationResult))
    report.add((result, SH.focusNode, source))
    report.add((result, SH.resultPath, TSS.points))
    report.add((result, SH.resultSeverity, SH.Violation))
    report.add((result, SH.resultMessage, Literal(f"S6 points: {reason}")))
    report.add((result, SH.sourceConstraintComponent, SH.SPARQLConstraintComponent))
    # A violation: the metadata does not conform
    report.set((report_node, SH.conforms, Literal(False)))


def validation_report(graph, shapes, shapes_path):
    """Validate the metadata (and its embedded points) with these shapes (pyshacl).
    Gives the report as an RDF graph, its sh:ValidationReport node, and the number of point triples."""
    data, point_triples, problems = data_graph(graph)

    # allow_warnings: warnings alone don't make the metadata fail; only violations do.
    # pyshacl gives (conforms, the report graph, the report as text); only the graph is used.
    # pyshacl switches rdflib.NORMALIZE_LITERALS off and on again while it reads the shapes, and
    # so does lift_points: the lock makes them take turns when they run in threads at the same
    # time (the dashboard's backend does the same in main.validation_report).
    with NORMALIZE_LOCK:
        conforms, report, report_text = validate(data, shacl_graph=shapes, allow_warnings=True)
    report_node = report.value(predicate=RDF.type, object=SH.ValidationReport)

    for source, reason in problems:
        add_points_problem(report, report_node, source, reason)

    # Which shapes were used: the file:/// address of the shapes file
    report.add((report_node, DCTERMS.source, URIRef(shapes_path.resolve().as_uri())))

    # The metadata's prefixes (tss:, ex:, ...), so the Turtle of the report is easier to read
    for prefix, namespace in graph.namespaces():
        report.bind(prefix, namespace, override=False)
    report.bind("dcterms", DCTERMS)
    return report, report_node, point_triples


def count_severities(report, report_node):
    """How many results of each severity the report holds, e.g. {SH.Violation: 2, SH.Warning: 1, SH.Info: 0}.
    The severities are compared as RDF terms (IRIs)."""
    counts = {}
    for severity in SEVERITIES:
        counts[severity] = 0
    for result in report.objects(report_node, SH.result):
        severity = report.value(result, SH.resultSeverity)
        if severity in counts:
            counts[severity] += 1
    return counts


def first_word(text):
    """The first word of a text, or "" for no text"""
    if text is None:
        return ""
    words = str(text).split()
    if len(words) == 0:
        return ""
    return words[0]


def check_id(report, shapes, result):
    """Which check gave a result (S6, F9, P4, ...): the first word of the sh:name of its shape
    (sh:sourceShape), or else the first word of its message"""
    shape = report.value(result, SH.sourceShape)
    if shape is not None:
        # pyshacl may copy a shape's description into the report; else it is in the shapes
        name = report.value(shape, SH.name)
        if name is None:
            name = shapes.value(shape, SH.name)
        if name is not None:
            return first_word(name)
    return first_word(report.value(result, SH.resultMessage))


def result_lines(report, report_node, shapes):
    """One line of text per result: severity, check id, focus node and message,
    most severe first (RDF results have no order of their own)"""
    lines = []
    for result in report.objects(report_node, SH.result):
        severity = report.value(result, SH.resultSeverity)
        if severity in SEVERITIES:
            name = SEVERITY_NAMES[severity]
            rank = SEVERITIES.index(severity)
        else:
            # SHACL allows other severities too: show their IRI, after the three above
            name = str(severity)
            rank = len(SEVERITIES)

        focus = report.value(result, SH.focusNode)
        focus_text = focus.n3(report.namespace_manager) if focus is not None else "-"
        message = report.value(result, SH.resultMessage)
        message_text = str(message) if message is not None else ""

        identifier = check_id(report, shapes, result)
        line = f"  {name:<9}  {identifier:<4}  {focus_text}  {message_text}"
        lines.append((rank, identifier, line))

    lines.sort()
    texts = []
    for rank, identifier, line in lines:
        texts.append(line)
    return texts


def main():
    parser = argparse.ArgumentParser(description="Validate a TSS2 file (and its embedded points) against "
                                                 "SHACL shapes, and write the validation report as Turtle.")
    parser.add_argument("tss2_file", help="the TSS2 metadata endpoint (Turtle)")
    parser.add_argument("shapes_file", nargs="?",
                        help="the SHACL shapes (default: SHACL/embedded_shapes.ttl or SHACL/external_shapes.ttl, "
                             "from the source's tss:embedded)")
    parser.add_argument("--out", help="the Turtle file for the report (default: reports/<endpoint>__<shapes>.ttl)")
    args = parser.parse_args()

    # rdflib logs a long traceback for every value that does not fit its datatype
    # (like "abc"^^xsd:double), warns about a boolean like "yes", and warns again when it
    # writes such a value in the report. The report lists those values itself, so these
    # messages are left out (the dashboard's backend leaves out the tracebacks too).
    logging.getLogger("rdflib.term").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", message="Parsing weird boolean")
    warnings.filterwarnings("ignore", message="Serializing weird numerical")

    tss2_path = Path(args.tss2_file)
    if not tss2_path.is_file():
        parser.error(f"File not found: {tss2_path}")
    try:
        graph = read_rdf(tss2_path)
    except Exception as error:  # for example a typo in the Turtle
        parser.exit(1, f"Could not read {tss2_path}: {error}\n")

    if args.shapes_file:
        shapes_path = Path(args.shapes_file)
    else:
        shapes_path = default_shapes(graph)
        if shapes_path is None:
            parser.error("Cannot pick the default shapes: the metadata needs a tss:Source with tss:embedded "
                         "true or false. Give a shapes file instead.")
    if not shapes_path.is_file():
        parser.error(f"File not found: {shapes_path}")
    try:
        shapes = read_rdf(shapes_path)
    except Exception as error:
        parser.exit(1, f"Could not read {shapes_path}: {error}\n")

    try:
        report, report_node, point_triples = validation_report(graph, shapes, shapes_path)
    except Exception as error:  # for example a SPARQL check in the shapes that pyshacl can't run
        parser.exit(1, f"Could not validate with {shapes_path}: {error}\n")

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = Path(__file__).parent / "reports" / f"{tss2_path.stem}__{shapes_path.stem}.ttl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(exact_turtle(report), encoding="utf-8", newline="\n")

    # The summary: read from the report graph, so it says what the saved report says
    conforms = report.value(report_node, SH.conforms).toPython()
    counts = count_severities(report, report_node)
    print(f"Endpoint: {tss2_path}")
    print(f"Shapes:   {shapes_path}")
    if point_triples > 0:
        print(f"Points:   {point_triples} triples read from tss:points, validated with the metadata")
    print(f"Result:   {'CONFORMS' if conforms else 'DOES NOT CONFORM'}"
          f" ({counts[SH.Violation]} violations, {counts[SH.Warning]} warnings, {counts[SH.Info]} infos)")
    lines = result_lines(report, report_node, shapes)
    for line in lines[:MAX_LISTED_RESULTS]:
        print(line)
    if len(lines) > MAX_LISTED_RESULTS:
        print(f"  ... and {len(lines) - MAX_LISTED_RESULTS} more results (see the report)")
    print(f"Report saved to {out_path}")


if __name__ == "__main__":
    main()
