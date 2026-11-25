"""SHACL validation of metadata documents, with the shapes of the specification.

>>> report = rdftss.validate(document)       # or document.validate()           # doctest: +SKIP
>>> report.conforms                                                              # doctest: +SKIP
>>> print(report)                                                                # doctest: +SKIP
>>> report.to_turtle("report.ttl")           # the sh:ValidationReport as RDF   # doctest: +SKIP

The package holds the two shapes files of the specification, in ``rdftss/shapes/``:

- ``embedded_shapes.ttl``, for documents whose sources hold their rows
  (``tss:EmbeddedRDFSource``, ``tss:EmbeddedJSONSource``). They also check the RDF points.
- ``external_shapes.ttl``, for documents whose sources have a data file (``tss:CSVSource``,
  ``tss:ParquetSource``, ``tss:ArrowSource``). They check the metadata, not the data file.

By default the source classes of the document choose the file. A document with both kinds of
sources, or with no source class at all, is checked with both files, so that S24 (one kind of
source) or S1 (the source) says what is wrong.

Every check has an ID: S for the source, F for a field, P for an RDF point, D for a value
encoding and Q for a summary, such as S23 (every time has a timezone). The IDs are those of the
shapes files. The two files number some source checks differently (the source class is S3 in
the embedded file and S7 in the external one), so an ID goes with its file: ``result.shapes``.

The document is validated as it was read, with every literal's text exactly as written, so that
P4 sees a value such as ``" 12 "^^xsd:double``, which rdflib would otherwise read as 12.0.

A document conforms when the report holds no ``sh:Violation``. Warnings point at things that
look wrong, but don't change that.
"""
from __future__ import annotations

import functools
import logging
import re
import warnings
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, SH

from .errors import RDFTSSError, require
from .rdf import NORMALIZE_LOCK, exact_turtle, parse_rdf
from .vocab import TSS

SHAPES_FOLDER = Path(__file__).resolve().parent / "shapes"
#: The bundled shapes files, by kind of source.
SHAPES_FILES = {"embedded": "embedded_shapes.ttl", "external": "external_shapes.ttl"}
EMBEDDED_CLASSES = (TSS.EmbeddedRDFSource, TSS.EmbeddedJSONSource)
EXTERNAL_CLASSES = (TSS.CSVSource, TSS.ParquetSource, TSS.ArrowSource)
SEVERITIES = {SH.Violation: "Violation", SH.Warning: "Warning", SH.Info: "Info"}
_CHECK_ID = re.compile(r"^[A-Z][0-9]+$")


class ValidationError(RDFTSSError):
    """The shapes could not be read or run."""


def shapes_path(kind: str) -> Path:
    """The path of a bundled shapes file: ``kind`` is ``"embedded"`` or ``"external"``."""
    if kind not in SHAPES_FILES:
        raise ValueError(f"No bundled shapes for {kind!r}: use 'embedded' or 'external'.")
    return SHAPES_FOLDER / SHAPES_FILES[kind]


@functools.lru_cache(maxsize=None)
def shapes_graph(kind: str) -> Graph:
    """A bundled shapes file as an rdflib Graph (read once)."""
    return parse_rdf(str(shapes_path(kind)), format="turtle")


def shapes_for(document) -> list[str]:
    """Which bundled shapes files check a document, from the source classes it states."""
    classes = set(document.graph.objects(None, RDF.type))
    embedded = any(kind in classes for kind in EMBEDDED_CLASSES)
    external = any(kind in classes for kind in EXTERNAL_CLASSES)
    if embedded != external:
        return ["embedded"] if embedded else ["external"]
    return ["embedded", "external"]


@dataclass
class ValidationResult:
    """One result of the validation.

    - ``severity``: ``"Violation"``, ``"Warning"`` or ``"Info"``.
    - ``check``: the check's ID, such as ``"S23"`` (``""`` for a shape without one), and
      ``name``, such as ``"S23 timezone"``; ``group``: ``"Required"``, ``"Optional"`` or
      ``"Not allowed"`` for a property check (``None`` for a check written in SPARQL).
    - ``focus``: the node the result is about (a source, a field, a point, ...), ``path``: the
      property, and ``value``: the value at fault, when the check gives them.
    - ``message``: what is wrong; ``shapes``: which shapes file found it.
    """

    severity: str
    check: str
    name: str
    group: str | None
    focus: object
    path: object
    value: object
    message: str
    shapes: str
    where: str = dataclass_field(default="", repr=False)

    def __str__(self) -> str:
        return f"{self.severity:<9}  {self.check or '-':<4}  {self.where}  {self.message}"


@dataclass
class ValidationReport:
    """The outcome of :func:`validate`.

    - ``conforms``: no result is a violation.
    - ``results``: every :class:`ValidationResult`, the most severe first; ``violations``,
      ``warnings``: those of one severity; ``counts``: how many there are of each severity.
    - ``checks``: the IDs of the checks that failed, such as ``{"S23", "P4"}``.
    - ``shapes``: the shapes files used; ``graph``: the ``sh:ValidationReport`` as RDF.
    """

    conforms: bool
    results: list
    shapes: list
    graph: Graph = dataclass_field(repr=False)
    location: str = ""

    @property
    def violations(self) -> list:
        return [result for result in self.results if result.severity == "Violation"]

    @property
    def warnings(self) -> list:
        return [result for result in self.results if result.severity == "Warning"]

    @property
    def counts(self) -> dict:
        counts = {"Violation": 0, "Warning": 0, "Info": 0}
        for result in self.results:
            counts[result.severity] = counts.get(result.severity, 0) + 1
        return counts

    @property
    def checks(self) -> set:
        return {result.check for result in self.results if result.check}

    def __bool__(self) -> bool:
        return self.conforms

    def __str__(self) -> str:
        counts = self.counts
        def many(count: int, word: str) -> str:
            return f"{count} {word}{'' if count == 1 else 's'}"

        lines = [f"{self.location or 'The document'}: {'CONFORMS' if self.conforms else 'DOES NOT CONFORM'} "
                 f"({many(counts['Violation'], 'violation')}, {many(counts['Warning'], 'warning')}) "
                 f"with the {' and '.join(self.shapes)} shapes"]
        lines += [f"  {result}" for result in self.results[:50]]
        if len(self.results) > 50:
            lines.append(f"  ... and {len(self.results) - 50} more (see report.results)")
        return "\n".join(lines)

    def to_turtle(self, path=None) -> str:
        """The report as Turtle, literals as written; with ``path``, also saved there."""
        text = exact_turtle(self.graph)
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
        return text


def _first_word(text) -> str:
    words = str(text or "").replace(":", " ").split()
    return words[0] if words else ""


def _results_of(report_graph: Graph, shapes: Graph, shapes_name: str, short) -> tuple:
    node = report_graph.value(predicate=RDF.type, object=SH.ValidationReport)
    found = []
    for result in report_graph.objects(node, SH.result):
        severity = report_graph.value(result, SH.resultSeverity)
        shape = report_graph.value(result, SH.sourceShape)
        name = shapes.value(shape, SH.name) or report_graph.value(shape, SH.name)
        message = str(report_graph.value(result, SH.resultMessage) or "")
        check = _first_word(name)
        if not _CHECK_ID.match(check):
            check = _first_word(message) if _CHECK_ID.match(_first_word(message)) else ""
        if not name:
            name = message.split(":")[0] if check and message.startswith(check) else check
        group_node = shapes.value(shape, SH.group)
        group = str(shapes.value(group_node, RDFS.label)) if group_node is not None else None
        focus = report_graph.value(result, SH.focusNode)
        found.append(ValidationResult(
            severity=SEVERITIES.get(severity, str(severity)), check=check, name=str(name or ""), group=group,
            focus=focus, path=report_graph.value(result, SH.resultPath), value=report_graph.value(result, SH.value),
            message=message, shapes=shapes_name, where=short(focus) if focus is not None else "-"))
    return node, found


def _rank(result: ValidationResult):
    order = {"Violation": 0, "Warning": 1, "Info": 2}
    number = int(result.check[1:]) if result.check else 0
    return order.get(result.severity, 3), result.check[:1], number, result.where


def validate(document, *, shapes=None) -> ValidationReport:
    """Validate a metadata document with SHACL shapes, and give a :class:`ValidationReport`.

    - ``document``: a :class:`~rdftss.document.Document`, or a path or URL to load.
    - ``shapes``: ``None`` (the bundled shapes that fit the document's source classes),
      ``"embedded"`` or ``"external"`` (one bundled file), or your own shapes: a path or an
      rdflib Graph.

    Needs the package ``pyshacl`` (``pip install 'rdftss[shacl]'``)."""
    pyshacl = require("pyshacl", "SHACL validation", "shacl")
    from .document import Document, load

    if not isinstance(document, Document):
        document = load(document)
    if shapes is None:
        chosen = [(kind, shapes_graph(kind)) for kind in shapes_for(document)]
    elif isinstance(shapes, str) and shapes in SHAPES_FILES:
        chosen = [(shapes, shapes_graph(shapes))]
    elif isinstance(shapes, Graph):
        chosen = [("custom", shapes)]
    else:
        chosen = [(Path(shapes).name, parse_rdf(str(shapes)))]

    merged = Graph()
    for prefix, namespace in document.graph.namespaces():
        merged.bind(prefix, namespace)
    merged.bind("sh", SH)
    report = BNode()
    merged.add((report, RDF.type, SH.ValidationReport))
    results, conforms = [], True
    for name, graph in chosen:
        try:
            with NORMALIZE_LOCK, warnings.catch_warnings():
                warnings.simplefilter("ignore")
                logger = logging.getLogger("rdflib.term")
                level = logger.level
                logger.setLevel(logging.CRITICAL)   # rdflib logs every value that misses its datatype
                try:
                    ok, report_graph, _ = pyshacl.validate(document.graph, shacl_graph=graph, inference="none",
                                                           allow_warnings=True)
                finally:
                    logger.setLevel(level)
        except Exception as error:
            raise ValidationError(f"The {name} shapes could not be run: {error}") from error
        conforms = conforms and bool(ok)
        node, found = _results_of(report_graph, graph, name, document.short)
        results.extend(found)
        for subject, predicate, value in report_graph:
            if subject != node:
                merged.add((subject, predicate, value))
        for result in report_graph.objects(node, SH.result):
            merged.add((report, SH.result, result))
        ontology = next(graph.subjects(RDF.type, OWL.Ontology), None)
        merged.add((report, DCTERMS.source, ontology if isinstance(ontology, URIRef) else Literal(name)))
    merged.add((report, SH.conforms, Literal(conforms)))
    results.sort(key=_rank)
    return ValidationReport(conforms=conforms, results=results, shapes=[name for name, _ in chosen], graph=merged,
                            location=str(document.location or ""))
